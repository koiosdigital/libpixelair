"""PixelAir Device Discovery Service.

This module provides the discovery service for finding PixelAir devices
on the local network. Discovery works by:

1. Broadcasting an OSC message to /fluoraDiscovery on port 9090 with
   the listener's IP address encoded as ASCII character values.

2. Devices respond with a JSON message prefixed with '$' containing:
   {"serial_number": "...", "ip_address": "...", "state_counter": N}

3. Discovered devices can be verified by sending /fluoraDiscovery
   directly to their IP (no params needed).

4. Full device info (model, nickname, firmware, MAC) is fetched via
   /getState which returns a FlatBuffer payload.

The discovery service integrates with the UDPListener and provides
async APIs for one-shot discovery or continuous background discovery.

For Home Assistant integration, devices can be identified by MAC address
and resolved to IP via ARP table lookups.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from pythonosc.osc_message_builder import OscMessageBuilder

from .arp import lookup_ip_by_mac, normalize_mac
from .udp_listener import PacketHandler, UDPListener

# Discovery broadcast port
DISCOVERY_PORT = 9090

# OSC route for discovery
DISCOVERY_ROUTE = "/fluoraDiscovery"

# Regex to extract JSON from discovery response (prefixed with $)
DISCOVERY_RESPONSE_PATTERN = re.compile(rb'^\$(\{.*\})$', re.DOTALL)


@dataclass
class DiscoveredDevice:
    """Represents a discovered PixelAir device.

    Basic discovery returns serial_number, ip_address, and state_counter.
    Additional fields (model, nickname, firmware_version, mac_address) are
    populated after fetching the full device state via get_device_info().

    For Home Assistant, the mac_address is the preferred persistent identifier
    since IP addresses may change with DHCP.

    Attributes:
        serial_number: The device's unique serial number.
        ip_address: The IP address of the device.
        state_counter: The current state counter from the device.
        mac_address: The device's MAC address (populated after state fetch).
        model: The device model name, e.g., "Fluora" (populated after state fetch).
        nickname: User-assigned device name (populated after state fetch).
        firmware_version: Current firmware version (populated after state fetch).
    """
    serial_number: str
    ip_address: str
    state_counter: int
    mac_address: str | None = None
    model: str | None = None
    nickname: str | None = None
    firmware_version: str | None = None

    def __hash__(self) -> int:
        """Return hash based on serial_number for use in sets/dicts."""
        return hash(self.serial_number)

    def __eq__(self, other: object) -> bool:
        """Check equality based on serial_number."""
        if not isinstance(other, DiscoveredDevice):
            return False
        return self.serial_number == other.serial_number

    @property
    def has_full_info(self) -> bool:
        """Check if full device info has been fetched.

        Returns:
            True if model and mac_address are populated.
        """
        return self.model is not None and self.mac_address is not None

    @property
    def display_name(self) -> str:
        """Get a human-readable display name for the device.

        Returns:
            The nickname if set, otherwise model, otherwise serial number.
        """
        return self.nickname or self.model or self.serial_number


# Type alias for discovery callbacks (Python 3.12+ syntax)
type DiscoveryCallback = (
    Callable[[DiscoveredDevice], None]
    | Callable[[DiscoveredDevice], Awaitable[None]]
)


class DiscoveryHandler(PacketHandler):
    """Packet handler for processing discovery responses.

    This handler parses the JSON discovery response from PixelAir devices
    and invokes the registered callback for each discovered device.
    """

    def __init__(
        self,
        callback: DiscoveryCallback,
        logger: logging.Logger
    ) -> None:
        """Initialize the discovery handler.

        Args:
            callback: Function to call when a device is discovered.
            logger: Logger instance for logging events.
        """
        self._callback = callback
        self._logger = logger

    async def handle_packet(
        self,
        data: bytes,
        source_address: tuple[str, int],  # noqa: ARG002
    ) -> bool:
        """Handle an incoming UDP packet.

        Attempts to parse the packet as a discovery response. If successful,
        invokes the discovery callback.

        Args:
            data: The raw packet data.
            source_address: Tuple of (ip_address, port) of the sender (unused but
                required by PacketHandler protocol).

        Returns:
            True if this was a discovery response, False otherwise.
        """
        # Try to match discovery response pattern
        match = DISCOVERY_RESPONSE_PATTERN.match(data)
        if not match:
            return False

        try:
            json_str = match.group(1).decode("utf-8")
            response = json.loads(json_str)

            # Validate required fields
            serial_number = response.get("serial_number")
            ip_address = response.get("ip_address")
            state_counter = response.get("state_counter")

            if not all([serial_number, ip_address, state_counter is not None]):
                self._logger.warning(
                    "Discovery response missing required fields: %s",
                    response
                )
                return True  # Still consumed as discovery packet

            device = DiscoveredDevice(
                serial_number=str(serial_number),
                ip_address=str(ip_address),
                state_counter=int(state_counter)
            )

            self._logger.debug(
                "Discovered device: serial=%s, ip=%s, counter=%d",
                device.serial_number,
                device.ip_address,
                device.state_counter
            )

            # Invoke callback
            await self._invoke_callback(device)
            return True

        except json.JSONDecodeError as e:
            self._logger.warning(
                "Failed to parse discovery response JSON: %s",
                e
            )
            return True
        except Exception as e:
            self._logger.exception(
                "Error processing discovery response: %s",
                e
            )
            return True

    async def _invoke_callback(self, device: DiscoveredDevice) -> None:
        """Invoke the discovery callback.

        Args:
            device: The discovered device.
        """
        try:
            result = self._callback(device)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            self._logger.exception(
                "Discovery callback raised exception: %s",
                e
            )


class DiscoveryService:
    """Service for discovering PixelAir devices on the local network.

    This service broadcasts discovery messages on all available network
    interfaces and collects responses from PixelAir devices. It provides
    both one-shot discovery (scan once) and continuous discovery modes.

    The service requires a UDPListener to be provided for sending and
    receiving packets. This allows multiple services to share the same
    listener.

    For Home Assistant integration, use ``discover_with_info()`` to get full
    device information including MAC addresses, or use ``resolve_mac_to_ip()``
    to find devices by their MAC address.

    Example::

        async def main():
            async with UDPListener() as listener:
                discovery = DiscoveryService(listener)

                # Full discovery with device info
                devices = await discovery.discover_with_info(timeout=5.0)
                for device in devices:
                    print(f"Found: {device.display_name}")
                    print(f"  MAC: {device.mac_address}")
                    print(f"  Model: {device.model}")

                # Find device by MAC address
                ip = await discovery.resolve_mac_to_ip("aa:bb:cc:dd:ee:ff")
                if ip:
                    device = await discovery.verify_device(ip)
    """

    def __init__(self, listener: UDPListener) -> None:
        """Initialize the discovery service.

        Args:
            listener: The UDPListener to use for sending and receiving packets.
                Must be started before calling discovery methods.
        """
        self._listener = listener
        self._logger = logging.getLogger("pixelair.discovery")

        # Continuous discovery state
        self._continuous_handler: DiscoveryHandler | None = None
        self._continuous_task: asyncio.Task[None] | None = None
        self._continuous_running = False
        self._continuous_interval: float = 30.0

        # Discovered devices cache (for continuous mode deduplication)
        # Keyed by serial_number
        self._discovered_devices: dict[str, DiscoveredDevice] = {}
        self._devices_lock = asyncio.Lock()

        # MAC to serial number mapping for fast lookups
        self._mac_to_serial: dict[str, str] = {}

    @property
    def discovered_devices(self) -> list[DiscoveredDevice]:
        """Get list of devices discovered during continuous discovery.

        Returns:
            List of discovered devices (may be empty if not in continuous mode).
        """
        return list(self._discovered_devices.values())

    async def discover(
        self,
        timeout: float = 5.0,
        broadcast_count: int = 3,
        broadcast_interval: float = 1.0
    ) -> list[DiscoveredDevice]:
        """Perform a one-shot discovery scan.

        This method broadcasts discovery messages and waits for responses.
        Multiple broadcasts are sent to improve reliability.

        Note: This returns basic device info only. Use discover_with_info()
        to also fetch model, nickname, firmware, and MAC address.

        Args:
            timeout: Total time to wait for responses in seconds.
            broadcast_count: Number of discovery broadcasts to send.
            broadcast_interval: Time between broadcasts in seconds.

        Returns:
            List of discovered devices (basic info only).

        Raises:
            RuntimeError: If the UDP listener is not running.
        """
        if not self._listener.is_running:
            raise RuntimeError("UDP listener is not running")

        devices: dict[str, DiscoveredDevice] = {}

        async def on_discovered(device: DiscoveredDevice) -> None:
            # Use serial_number as unique key
            devices[device.serial_number] = device

        # Register handler
        handler = DiscoveryHandler(on_discovered, self._logger)
        self._listener.add_handler(handler)

        try:
            # Send discovery broadcasts
            broadcast_task = asyncio.create_task(
                self._send_discovery_broadcasts(
                    count=broadcast_count,
                    interval=broadcast_interval
                )
            )

            # Wait for timeout
            await asyncio.sleep(timeout)

            # Cancel broadcast task if still running
            broadcast_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await broadcast_task

        finally:
            self._listener.remove_handler(handler)

        self._logger.info(
            "Discovery complete: found %d device(s)",
            len(devices)
        )

        return list(devices.values())

    async def discover_with_info(
        self,
        timeout: float = 5.0,
        broadcast_count: int = 3,
        broadcast_interval: float = 1.0,
        state_timeout: float = 10.0
    ) -> list[DiscoveredDevice]:
        """Perform discovery and fetch full device info for each device.

        This method first discovers devices, then fetches the full state
        from each to populate model, nickname, firmware_version, and mac_address.

        Args:
            timeout: Time to wait for discovery responses in seconds.
            broadcast_count: Number of discovery broadcasts to send.
            broadcast_interval: Time between broadcasts in seconds.
            state_timeout: Time to wait for each device's state response.

        Returns:
            List of discovered devices with full info populated.

        Raises:
            RuntimeError: If the UDP listener is not running.
        """
        # First do basic discovery
        devices = await self.discover(
            timeout=timeout,
            broadcast_count=broadcast_count,
            broadcast_interval=broadcast_interval
        )

        # Fetch full info for each device
        enriched_devices = []
        for device in devices:
            try:
                enriched = await self.get_device_info(
                    device,
                    timeout=state_timeout
                )
                enriched_devices.append(enriched)
            except Exception as e:
                self._logger.warning(
                    "Failed to get full info for device %s: %s",
                    device.serial_number,
                    e
                )
                enriched_devices.append(device)

        return enriched_devices

    async def get_device_info(
        self,
        device: DiscoveredDevice,
        timeout: float = 10.0
    ) -> DiscoveredDevice:
        """Fetch full device information via /getState.

        This method sends a getState command to the device and parses the
        FlatBuffer response to extract model, nickname, firmware, and MAC.

        Args:
            device: The discovered device to get info for.
            timeout: Time to wait for state response in seconds.

        Returns:
            Updated DiscoveredDevice with full info populated.

        Raises:
            asyncio.TimeoutError: If no response within timeout.
            RuntimeError: If the UDP listener is not running.
        """
        if not self._listener.is_running:
            raise RuntimeError("UDP listener is not running")

        # Import here to avoid circular imports
        from .device import PixelAirDevice

        # Create a temporary device to fetch state
        # Use _internal=True since we don't have MAC yet (we're discovering it)
        temp_device = PixelAirDevice(
            ip_address=device.ip_address,
            listener=self._listener,
            serial_number=device.serial_number,
            mac_address="",  # Will be populated from state
            _internal=True
        )

        try:
            await temp_device.register()
            state = await temp_device.get_state(timeout=timeout)

            # Create enriched device with full info
            enriched = replace(
                device,
                model=state.model,
                nickname=state.nickname,
                firmware_version=state.firmware_version,
                mac_address=state.mac_address
            )

            # Update MAC mapping
            if enriched.mac_address:
                async with self._devices_lock:
                    try:
                        normalized = normalize_mac(enriched.mac_address)
                        self._mac_to_serial[normalized] = enriched.serial_number
                    except ValueError:
                        pass

            self._logger.debug(
                "Got full info for %s: model=%s, mac=%s",
                device.serial_number,
                enriched.model,
                enriched.mac_address
            )

            return enriched

        finally:
            await temp_device.unregister()

    async def verify_device(
        self,
        ip_address: str,
        timeout: float = 5.0
    ) -> DiscoveredDevice | None:
        """Verify a device at a specific IP address.

        Sends a discovery message directly to the device and waits for
        a response. This can be used to confirm a device is reachable
        or to get updated device information.

        Args:
            ip_address: The IP address of the device to verify.
            timeout: Time to wait for response in seconds.

        Returns:
            The discovered device information, or None if no response.

        Raises:
            RuntimeError: If the UDP listener is not running.
        """
        if not self._listener.is_running:
            raise RuntimeError("UDP listener is not running")

        result: DiscoveredDevice | None = None
        response_received = asyncio.Event()

        async def on_discovered(device: DiscoveredDevice) -> None:
            nonlocal result
            if device.ip_address == ip_address:
                result = device
                response_received.set()

        handler = DiscoveryHandler(on_discovered, self._logger)
        self._listener.add_handler(handler)

        try:
            # Build discovery message (no params for direct verification)
            message = self._build_discovery_message()

            # Send to specific device
            await self._listener.send_to(message, ip_address, DISCOVERY_PORT)
            self._logger.debug(
                "Sent verification request to %s:%d",
                ip_address,
                DISCOVERY_PORT
            )

            # Wait for response
            try:
                await asyncio.wait_for(response_received.wait(), timeout)
            except TimeoutError:
                self._logger.debug(
                    "Verification timeout for device at %s",
                    ip_address
                )

        finally:
            self._listener.remove_handler(handler)

        return result

    async def resolve_mac_to_ip(
        self,
        mac_address: str,
        use_cache: bool = True
    ) -> str | None:
        """Resolve a MAC address to an IP address.

        First checks the internal cache of discovered devices, then falls
        back to the system ARP table.

        Args:
            mac_address: The MAC address to resolve (any common format).
            use_cache: Whether to check internal device cache first.

        Returns:
            The IP address if found, None otherwise.

        Note:
            ARP table lookups only work for devices that have recently
            communicated on the network. You may need to do a broadcast
            discovery first to warm the ARP cache.
        """
        try:
            normalized = normalize_mac(mac_address)
        except ValueError:
            self._logger.warning("Invalid MAC address: %s", mac_address)
            return None

        # Check internal cache first
        if use_cache:
            async with self._devices_lock:
                serial = self._mac_to_serial.get(normalized)
                if serial and serial in self._discovered_devices:
                    device = self._discovered_devices[serial]
                    return device.ip_address

        # Fall back to ARP table
        return await lookup_ip_by_mac(normalized)

    async def find_device_by_mac(
        self,
        mac_address: str,
        timeout: float = 5.0,
        warm_arp: bool = True
    ) -> DiscoveredDevice | None:
        """Find and verify a device by its MAC address.

        This method resolves the MAC to IP via ARP table, then verifies
        the device is a PixelAir device by sending a discovery request.

        Args:
            mac_address: The MAC address to find (any common format).
            timeout: Time to wait for device response.
            warm_arp: If True, broadcast discovery first to warm ARP cache.

        Returns:
            The discovered device if found and verified, None otherwise.
        """
        if not self._listener.is_running:
            raise RuntimeError("UDP listener is not running")

        # Optionally warm ARP cache with broadcast discovery
        if warm_arp:
            await self._broadcast_discovery()
            await asyncio.sleep(0.5)  # Give devices time to respond

        # Resolve MAC to IP
        ip_address = await self.resolve_mac_to_ip(mac_address)
        if not ip_address:
            self._logger.debug(
                "Could not resolve MAC %s to IP address",
                mac_address
            )
            return None

        # Verify the device
        device = await self.verify_device(ip_address, timeout=timeout)
        if device:
            # Ensure MAC is set on the device
            try:
                normalized = normalize_mac(mac_address)
                device = replace(device, mac_address=normalized)
            except ValueError:
                pass

        return device

    async def find_device_by_serial(
        self,
        serial_number: str,
        timeout: float = 5.0
    ) -> DiscoveredDevice | None:
        """Find a device by its serial number via broadcast discovery.

        This method broadcasts discovery messages and waits for a response
        from a device with the matching serial number.

        Args:
            serial_number: The serial number to find.
            timeout: Time to wait for discovery responses.

        Returns:
            The discovered device if found, None otherwise.

        Raises:
            RuntimeError: If the UDP listener is not running.
        """
        if not self._listener.is_running:
            raise RuntimeError("UDP listener is not running")

        result: DiscoveredDevice | None = None
        found_event = asyncio.Event()

        async def on_discovered(device: DiscoveredDevice) -> None:
            nonlocal result
            if device.serial_number == serial_number:
                result = device
                found_event.set()

        handler = DiscoveryHandler(on_discovered, self._logger)
        self._listener.add_handler(handler)

        try:
            # Send broadcast discovery
            await self._broadcast_discovery()

            # Wait for matching device
            try:
                await asyncio.wait_for(found_event.wait(), timeout)
            except TimeoutError:
                self._logger.debug(
                    "Device with serial %s not found within timeout",
                    serial_number
                )

        finally:
            self._listener.remove_handler(handler)

        return result

    async def start_continuous(
        self,
        callback: DiscoveryCallback,
        interval: float = 30.0,
        initial_scan: bool = True,
        fetch_full_info: bool = False
    ) -> None:
        """Start continuous device discovery.

        This method starts a background task that periodically broadcasts
        discovery messages and invokes the callback for each discovered device.

        The callback is only invoked for newly discovered devices or when
        a device's state_counter changes.

        Args:
            callback: Function to call when a device is discovered.
            interval: Time between discovery broadcasts in seconds.
            initial_scan: Whether to perform an immediate discovery scan.
            fetch_full_info: If True, fetch full device info before callback.

        Raises:
            RuntimeError: If continuous discovery is already running.
        """
        if self._continuous_running:
            raise RuntimeError("Continuous discovery is already running")

        self._continuous_running = True
        self._continuous_interval = interval

        async def on_discovered(device: DiscoveredDevice) -> None:
            async with self._devices_lock:
                existing = self._discovered_devices.get(device.serial_number)

                # Only notify if new or state changed
                if existing is None or existing.state_counter != device.state_counter:
                    # Optionally fetch full info
                    if fetch_full_info and not device.has_full_info:
                        try:
                            device = await self.get_device_info(device)
                        except Exception as e:
                            self._logger.warning(
                                "Failed to get full info for %s: %s",
                                device.serial_number,
                                e
                            )

                    self._discovered_devices[device.serial_number] = device

                    # Update MAC mapping
                    if device.mac_address:
                        try:
                            normalized = normalize_mac(device.mac_address)
                            self._mac_to_serial[normalized] = device.serial_number
                        except ValueError:
                            pass

                    # Invoke user callback
                    try:
                        result = callback(device)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        self._logger.exception(
                            "Discovery callback raised exception: %s",
                            e
                        )

        self._continuous_handler = DiscoveryHandler(on_discovered, self._logger)
        self._listener.add_handler(self._continuous_handler)

        # Start background task
        self._continuous_task = asyncio.create_task(
            self._continuous_discovery_loop(initial_scan)
        )

        self._logger.info(
            "Started continuous discovery (interval=%ds)",
            interval
        )

    async def stop_continuous(self) -> None:
        """Stop continuous device discovery.

        This method stops the background discovery task and removes the
        packet handler. Discovered devices are preserved.
        """
        if not self._continuous_running:
            return

        self._continuous_running = False

        if self._continuous_task:
            self._continuous_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._continuous_task
            self._continuous_task = None

        if self._continuous_handler:
            self._listener.remove_handler(self._continuous_handler)
            self._continuous_handler = None

        self._logger.info("Stopped continuous discovery")

    async def clear_discovered_devices(self) -> None:
        """Clear the list of discovered devices.

        This resets the deduplication cache, so the next discovery scan
        will re-notify for all devices found.
        """
        async with self._devices_lock:
            self._discovered_devices.clear()
            self._mac_to_serial.clear()

    def _build_discovery_message(
        self,
        source_ip: str | None = None
    ) -> bytes:
        """Build an OSC discovery message.

        Args:
            source_ip: If provided, encode this IP address as ASCII int params.
                If None, no params are added (for direct verification).

        Returns:
            The OSC message bytes.
        """
        builder = OscMessageBuilder(DISCOVERY_ROUTE)

        if source_ip:
            # Convert IP to ASCII character codes
            for char in source_ip:
                builder.add_arg(ord(char), "i")

        return builder.build().dgram

    async def _send_discovery_broadcasts(
        self,
        count: int,
        interval: float
    ) -> None:
        """Send multiple discovery broadcasts.

        Args:
            count: Number of broadcasts to send.
            interval: Time between broadcasts in seconds.
        """
        for i in range(count):
            await self._broadcast_discovery()
            if i < count - 1:
                await asyncio.sleep(interval)

    async def _broadcast_discovery(self) -> None:
        """Broadcast discovery message on all interfaces."""
        for interface in self._listener.interfaces:
            try:
                # Build message with this interface's IP
                message = self._build_discovery_message(interface.ip_address)

                # Send to broadcast address
                await self._listener.send_to(
                    message,
                    interface.broadcast_address,
                    DISCOVERY_PORT
                )

                self._logger.debug(
                    "Sent discovery broadcast to %s:%d (from %s via %s)",
                    interface.broadcast_address,
                    DISCOVERY_PORT,
                    interface.ip_address,
                    interface.name
                )

            except Exception as e:
                self._logger.warning(
                    "Failed to send discovery on interface %s: %s",
                    interface.name,
                    e
                )

    async def _continuous_discovery_loop(self, initial_scan: bool) -> None:
        """Background task for continuous discovery.

        Args:
            initial_scan: Whether to perform an immediate scan.
        """
        if initial_scan:
            await self._broadcast_discovery()

        while self._continuous_running:
            try:
                await asyncio.sleep(self._continuous_interval)
                if self._continuous_running:
                    await self._broadcast_discovery()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.exception(
                    "Error in continuous discovery loop: %s",
                    e
                )
