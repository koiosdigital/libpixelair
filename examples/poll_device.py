#!/usr/bin/env python3
"""
Example: Poll a PixelAir device by MAC+serial and track state changes.

This example demonstrates bulletproof device identification:
- Identifying a device by BOTH MAC address AND serial number
- Polling the device for state_counter changes
- Fetching full FlatBuffer state when state changes
- Automatic IP re-resolution using fallback strategy:
  1. ARP table lookup (fast, uses MAC)
  2. Broadcast discovery (fallback, uses serial number)
- Exponential backoff on connection failures
"""

import asyncio
import json
import logging
import re
import signal
import time
from dataclasses import asdict
from typing import Optional

from pythonosc.osc_message_builder import OscMessageBuilder

from libpixelair import (
    UDPListener,
    PacketHandler,
    PixelAirDevice,
    DeviceState,
    DiscoveryService,
    DISCOVERY_PORT,
    DISCOVERY_ROUTE,
    normalize_mac,
)
import libpixelair.pixelairfb  # noqa: F401 - sets up import path
from libpixelair.pixelairfb.PixelAir.PixelAirDevice import (
    PixelAirDevice as PixelAirDeviceFB,
)


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("poll_device")

# Target device identifiers (persistent)
# Both MAC and serial are stored for bulletproof identification
DEVICE_MAC = "D8:13:2A:25:2F:AC"
DEVICE_SERIAL = "ac2f25"  # Will be discovered on first run if not set

# Poll interval in seconds (base interval)
POLL_INTERVAL = 1.0

# Number of failed polls before attempting IP re-resolution
MAX_FAILED_POLLS = 3

# Minimum time between IP re-resolution attempts (seconds)
IP_RESOLVE_COOLDOWN = 300.0  # 5 minutes

# Maximum backoff interval (seconds)
MAX_BACKOFF = 60.0

# Regex to extract JSON from discovery response (prefixed with $)
DISCOVERY_RESPONSE_PATTERN = re.compile(rb"^\$(\{.*\})$", re.DOTALL)


def dump_flatbuffer_state(fb: PixelAirDeviceFB) -> dict:
    """
    Dump the FlatBuffer state to a dictionary for display.

    Args:
        fb: The decoded FlatBuffer state object.

    Returns:
        Dictionary representation of the state.
    """
    state = {}

    # Basic device info
    if fb.Protocol():
        state["protocol"] = fb.Protocol().decode("utf-8")
    if fb.Version():
        state["version"] = fb.Version().decode("utf-8")
    if fb.Model():
        state["model"] = fb.Model().decode("utf-8")
    if fb.MinMobileAppVersion():
        state["min_mobile_app_version"] = fb.MinMobileAppVersion().decode("utf-8")
    if fb.SerialNumber():
        state["serial_number"] = fb.SerialNumber().decode("utf-8")

    state["rssi"] = fb.Rssi()

    # Nickname
    if fb.Nickname():
        nickname = fb.Nickname()
        state["nickname"] = {
            "label": nickname.Label().decode("utf-8") if nickname.Label() else None,
            "route": nickname.Route().decode("utf-8") if nickname.Route() else None,
            "type": nickname.Type().decode("utf-8") if nickname.Type() else None,
            "value": nickname.Value().decode("utf-8") if nickname.Value() else None,
        }

    # Network
    if fb.Network():
        network = fb.Network()
        state["network"] = {
            "mac_address": (
                network.MacAddress().decode("utf-8") if network.MacAddress() else None
            ),
            "ip_address": (
                network.IpAddress().decode("utf-8") if network.IpAddress() else None
            ),
            "subnet": network.Subnet().decode("utf-8") if network.Subnet() else None,
        }

    # OTA
    if fb.Ota():
        ota = fb.Ota()
        state["ota"] = {
            "update_available": ota.UpdateAvailable(),
            "is_executing_ota": ota.IsExecutingOta(),
        }

    # Engine (main display control)
    if fb.Engine():
        engine = fb.Engine()
        engine_state = {}

        if engine.Brightness():
            b = engine.Brightness()
            engine_state["brightness"] = {
                "label": b.Label().decode("utf-8") if b.Label() else None,
                "route": b.Route().decode("utf-8") if b.Route() else None,
                "value": b.Value(),
            }

        if engine.IsDisplaying():
            d = engine.IsDisplaying()
            engine_state["is_displaying"] = {
                "label": d.Label().decode("utf-8") if d.Label() else None,
                "route": d.Route().decode("utf-8") if d.Route() else None,
                "value": d.Value(),
            }

        if engine.Mode():
            m = engine.Mode()
            engine_state["mode"] = {
                "label": m.Label().decode("utf-8") if m.Label() else None,
                "route": m.Route().decode("utf-8") if m.Route() else None,
                "value": m.Value(),
            }

        state["engine"] = engine_state

    # Audio
    if fb.Audio():
        audio = fb.Audio()
        audio_state = {}

        for param_name in ["Filter", "Release", "Gain", "Attack"]:
            param = getattr(audio, param_name, lambda: None)()
            if param:
                audio_state[param_name.lower()] = {
                    "label": param.Label().decode("utf-8") if param.Label() else None,
                    "route": param.Route().decode("utf-8") if param.Route() else None,
                    "value": param.Value(),
                }

        if audio_state:
            state["audio"] = audio_state

    # Clock
    if fb.Clock():
        clock = fb.Clock()
        clock_state = {}

        if clock.UtcOffset():
            offset = clock.UtcOffset()
            clock_state["utc_offset"] = {
                "label": offset.Label().decode("utf-8") if offset.Label() else None,
                "value": offset.Value(),
            }

        if clock_state:
            state["clock"] = clock_state

    return state


class DevicePoller:
    """
    Polls a device by MAC+serial for state changes with bulletproof IP resolution.

    Features:
    - Dual identifier storage (MAC + serial number)
    - Bulletproof IP resolution fallback:
      1. ARP table lookup using MAC (fast)
      2. Broadcast discovery using serial number (fallback)
    - Exponential backoff when device is unreachable
    - Cooldown on IP resolution attempts (max every 5 minutes)
    """

    def __init__(
        self,
        mac_address: str,
        listener: UDPListener,
        serial_number: Optional[str] = None,
        poll_interval: float = 1.0,
        max_failed_polls: int = 3,
        ip_resolve_cooldown: float = 300.0,
    ):
        """
        Initialize the device poller.

        Args:
            mac_address: The MAC address of the device to poll.
            listener: The UDP listener for network communication.
            serial_number: The device's serial number (discovered if not provided).
            poll_interval: Base time between polls in seconds.
            max_failed_polls: Failed polls before attempting IP re-resolution.
            ip_resolve_cooldown: Minimum seconds between IP resolution attempts.
        """
        self._mac_address = normalize_mac(mac_address)
        self._serial_number = serial_number
        self._listener = listener
        self._poll_interval = poll_interval
        self._max_failed_polls = max_failed_polls
        self._ip_resolve_cooldown = ip_resolve_cooldown

        # Current resolved IP address
        self._ip_address: Optional[str] = None

        # Device instance
        self._device: Optional[PixelAirDevice] = None
        self._state_counter: Optional[int] = None

        # Failure tracking
        self._consecutive_failures = 0
        self._last_ip_resolve_time = 0.0
        self._current_backoff = poll_interval

        # Running state
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._discovery_handler: Optional[PacketHandler] = None

        # Event for discovery response
        self._discovery_event = asyncio.Event()
        self._discovery_response: Optional[dict] = None

    @property
    def mac_address(self) -> str:
        """Get the device MAC address."""
        return self._mac_address

    @property
    def serial_number(self) -> Optional[str]:
        """Get the device serial number."""
        return self._serial_number

    @property
    def ip_address(self) -> Optional[str]:
        """Get the current resolved IP address."""
        return self._ip_address

    @property
    def is_connected(self) -> bool:
        """Check if device is currently reachable."""
        return self._consecutive_failures == 0 and self._ip_address is not None

    async def start(self) -> None:
        """
        Start polling the device.

        First resolves the device using MAC+serial fallback, then begins polling.
        """
        if self._running:
            return

        self._running = True

        # Initial device resolution
        logger.info(
            "Resolving device MAC=%s serial=%s...",
            self._mac_address,
            self._serial_number or "(unknown)",
        )
        await self._resolve_device()

        if not self._ip_address:
            logger.warning(
                "Could not resolve device. Will retry during polling.",
            )

        # Register discovery response handler
        self._discovery_handler = DiscoveryResponseHandler(self._on_discovery_response)
        self._listener.add_handler(self._discovery_handler)

        # Start poll loop
        self._poll_task = asyncio.create_task(self._poll_loop())

        logger.info(
            "Started polling device MAC=%s serial=%s IP=%s",
            self._mac_address,
            self._serial_number or "(unknown)",
            self._ip_address,
        )

    async def stop(self) -> None:
        """
        Stop polling the device.
        """
        if not self._running:
            return

        self._running = False

        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        if self._discovery_handler:
            self._listener.remove_handler(self._discovery_handler)
            self._discovery_handler = None

        if self._device:
            await self._device.unregister()
            self._device = None

        logger.info("Stopped polling device")

    async def _resolve_device(self) -> bool:
        """
        Resolve device IP using bulletproof fallback strategy.

        1. Try ARP table lookup using MAC address (fast)
        2. If MAC not found, broadcast discovery and find by serial number

        Returns:
            True if resolution succeeded, False otherwise.
        """
        now = time.time()

        # Check cooldown
        time_since_last = now - self._last_ip_resolve_time
        if time_since_last < self._ip_resolve_cooldown and self._last_ip_resolve_time > 0:
            logger.debug(
                "IP resolution on cooldown (%.0fs remaining)",
                self._ip_resolve_cooldown - time_since_last,
            )
            return False

        self._last_ip_resolve_time = now

        discovery = DiscoveryService(self._listener)

        # Strategy 1: Try ARP lookup with MAC
        from libpixelair import lookup_ip_by_mac

        new_ip = await lookup_ip_by_mac(self._mac_address)

        if new_ip:
            # Verify the device responds
            discovered = await discovery.verify_device(new_ip, timeout=3.0)

            if discovered:
                # Verify serial matches (if we have one)
                if self._serial_number and discovered.serial_number != self._serial_number:
                    logger.warning(
                        "Device at IP %s has serial %s, expected %s",
                        new_ip,
                        discovered.serial_number,
                        self._serial_number,
                    )
                else:
                    # Update serial if we didn't have it
                    if not self._serial_number:
                        self._serial_number = discovered.serial_number
                        logger.info(
                            "Discovered serial number: %s", self._serial_number
                        )

                    if new_ip != self._ip_address:
                        old_ip = self._ip_address
                        self._ip_address = new_ip
                        logger.info(
                            "Resolved via ARP: %s -> %s", old_ip, new_ip
                        )

                        # Re-create device with new IP
                        if self._device:
                            await self._device.unregister()
                            self._device = None

                    return True

        # Strategy 2: Broadcast discovery and find by serial
        if self._serial_number:
            logger.debug(
                "ARP lookup failed for MAC %s, trying broadcast discovery for serial %s",
                self._mac_address,
                self._serial_number,
            )

            discovered = await discovery.find_device_by_serial(
                self._serial_number, timeout=3.0
            )

            if discovered:
                new_ip = discovered.ip_address

                if new_ip != self._ip_address:
                    old_ip = self._ip_address
                    self._ip_address = new_ip
                    logger.info(
                        "Resolved via discovery: %s -> %s (serial: %s)",
                        old_ip,
                        new_ip,
                        self._serial_number,
                    )

                    # Re-create device with new IP
                    if self._device:
                        await self._device.unregister()
                        self._device = None

                return True

        # Strategy 3: If we only have MAC, try full discovery
        if not self._serial_number:
            logger.debug(
                "No serial number, trying full discovery to find MAC %s",
                self._mac_address,
            )

            devices = await discovery.discover_with_info(timeout=3.0)
            for device in devices:
                if device.mac_address:
                    try:
                        if normalize_mac(device.mac_address) == self._mac_address:
                            self._serial_number = device.serial_number
                            self._ip_address = device.ip_address
                            logger.info(
                                "Found device via full discovery: serial=%s IP=%s",
                                self._serial_number,
                                self._ip_address,
                            )
                            return True
                    except ValueError:
                        pass

        logger.warning(
            "Failed to resolve device MAC=%s serial=%s",
            self._mac_address,
            self._serial_number or "(unknown)",
        )
        return False

    async def _on_discovery_response(self, data: dict) -> None:
        """
        Handle a discovery response from the device.

        Args:
            data: The parsed JSON response.
        """
        # Only handle responses from our target device
        if self._ip_address and data.get("ip_address") != self._ip_address:
            return

        self._discovery_response = data
        self._discovery_event.set()

    async def _poll_loop(self) -> None:
        """
        Main polling loop with exponential backoff.
        """
        while self._running:
            try:
                success = await self._poll_once()

                if success:
                    # Reset backoff on success
                    self._consecutive_failures = 0
                    self._current_backoff = self._poll_interval
                else:
                    # Increment failure count
                    self._consecutive_failures += 1

                    # Check if we should try IP re-resolution
                    if self._consecutive_failures >= self._max_failed_polls:
                        logger.warning(
                            "Device unreachable after %d attempts, "
                            "attempting IP re-resolution...",
                            self._consecutive_failures,
                        )
                        resolved = await self._resolve_device()
                        if resolved:
                            # Reset failure count after successful resolution
                            self._consecutive_failures = 0
                            self._current_backoff = self._poll_interval

                    # Apply exponential backoff
                    self._current_backoff = min(
                        self._current_backoff * 2, MAX_BACKOFF
                    )
                    logger.debug(
                        "Backoff: %.1fs (failures: %d)",
                        self._current_backoff,
                        self._consecutive_failures,
                    )

            except Exception as e:
                logger.exception("Poll error: %s", e)
                self._consecutive_failures += 1

            # Wait before next poll
            await asyncio.sleep(self._current_backoff)

    async def _poll_once(self) -> bool:
        """
        Perform a single poll cycle.

        Returns:
            True if poll succeeded (got response), False otherwise.
        """
        if not self._ip_address:
            logger.debug("No IP address, skipping poll")
            return False

        # Send discovery request (no params for direct device query)
        await self._send_discovery()

        # Wait for response with timeout
        self._discovery_event.clear()
        try:
            await asyncio.wait_for(self._discovery_event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            logger.warning(
                "No response from device at %s (attempt %d)",
                self._ip_address,
                self._consecutive_failures + 1,
            )
            return False

        response = self._discovery_response
        if not response:
            return False

        serial_number = response.get("serial_number")
        state_counter = response.get("state_counter")

        # Update serial if we don't have it
        if not self._serial_number and serial_number:
            self._serial_number = serial_number
            logger.info("Discovered serial number: %s", self._serial_number)

        logger.debug(
            "Discovery response: serial=%s, counter=%s", serial_number, state_counter
        )

        # Check if state has changed
        if self._state_counter is None or state_counter != self._state_counter:
            old_counter = self._state_counter
            self._state_counter = state_counter

            logger.info(
                "State counter changed: %s -> %s, fetching new state...",
                old_counter,
                state_counter,
            )

            # Ensure device is registered
            if self._device is None and self._serial_number:
                self._device = PixelAirDevice(
                    ip_address=self._ip_address,
                    listener=self._listener,
                    serial_number=self._serial_number,
                    mac_address=self._mac_address,
                    _internal=True,
                )
                await self._device.register()

            # Fetch full state
            if self._device:
                try:
                    state = await self._device.get_state(timeout=10.0)
                    self._on_state_updated(state)
                except asyncio.TimeoutError:
                    logger.error("Timeout waiting for state from device")
                except Exception as e:
                    logger.exception("Error fetching state: %s", e)

        return True

    async def _send_discovery(self) -> None:
        """
        Send a discovery request directly to the device (no params).
        """
        if not self._ip_address:
            return

        builder = OscMessageBuilder(DISCOVERY_ROUTE)
        message = builder.build().dgram

        await self._listener.send_to(message, self._ip_address, DISCOVERY_PORT)
        logger.debug(
            "Sent discovery request to %s:%d", self._ip_address, DISCOVERY_PORT
        )

    def _on_state_updated(self, state: DeviceState) -> None:
        """
        Called when device state is updated.

        Args:
            state: The new device state.
        """
        print("\n" + "=" * 60)
        print("STATE UPDATED")
        print("=" * 60)
        print(f"MAC:    {self._mac_address}")
        print(f"Serial: {self._serial_number}")
        print(f"IP:     {self._ip_address}")

        # Print simplified state
        print("\nSimplified State:")
        print(json.dumps(asdict(state), indent=2, default=str))

        # Print full FlatBuffer dump if available
        if self._device and self._device.raw_state:
            print("\nFull FlatBuffer State:")
            fb_dump = dump_flatbuffer_state(self._device.raw_state)
            print(json.dumps(fb_dump, indent=2, default=str))

        print("=" * 60 + "\n")


class DiscoveryResponseHandler(PacketHandler):
    """
    Packet handler for discovery responses.
    """

    def __init__(self, callback):
        """
        Initialize the handler.

        Args:
            callback: Async callback to invoke with parsed response.
        """
        self._callback = callback

    async def handle_packet(self, data: bytes, source_address: tuple) -> bool:
        """
        Handle incoming packets, looking for discovery responses.
        """
        # Try to match discovery response pattern ($JSON)
        match = DISCOVERY_RESPONSE_PATTERN.match(data)
        if not match:
            return False

        try:
            json_str = match.group(1).decode("utf-8")
            response = json.loads(json_str)

            logger.debug("Received discovery response: %s", response)

            await self._callback(response)
            return True

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Failed to parse discovery response: %s", e)
            return True


async def main():
    """Main entry point."""
    print("=" * 60)
    print("PixelAir Device Poller (Bulletproof Edition)")
    print(f"MAC:    {DEVICE_MAC}")
    print(f"Serial: {DEVICE_SERIAL or '(will discover)'}")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    # Handle Ctrl+C gracefully
    running = True

    def signal_handler():
        nonlocal running
        print("\n\nShutting down...")
        running = False

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, signal_handler)
    loop.add_signal_handler(signal.SIGTERM, signal_handler)

    async with UDPListener() as listener:
        print(f"\nListening on port {listener.port}")
        print(f"Base poll interval: {POLL_INTERVAL}s")
        print(f"IP re-resolve after {MAX_FAILED_POLLS} failures")
        print(f"IP resolve cooldown: {IP_RESOLVE_COOLDOWN}s")
        print("\nResolution strategy:")
        print("  1. ARP table lookup (MAC)")
        print("  2. Broadcast discovery (serial)")
        print()

        poller = DevicePoller(
            mac_address=DEVICE_MAC,
            listener=listener,
            serial_number=DEVICE_SERIAL,
            poll_interval=POLL_INTERVAL,
            max_failed_polls=MAX_FAILED_POLLS,
            ip_resolve_cooldown=IP_RESOLVE_COOLDOWN,
        )

        await poller.start()

        # Print discovered serial if we found it
        if poller.serial_number and DEVICE_SERIAL is None:
            print(f"\n*** Discovered serial number: {poller.serial_number} ***")
            print("*** Add this to DEVICE_SERIAL for faster future resolution ***\n")

        try:
            while running:
                await asyncio.sleep(0.5)
        finally:
            await poller.stop()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
