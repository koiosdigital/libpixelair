"""
Internal device management utilities.

This module contains internal implementation details for device state management,
packet handling, and FlatBuffer parsing. These are not part of the public API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pythonosc.osc_message_builder import OscMessageBuilder

from .udp_listener import UDPListener, PacketHandler
from .packet_assembler import PacketAssembler
from .arp import lookup_ip_by_mac, normalize_mac
from ._types import (
    DeviceState,
    DeviceMode,
    SceneInfo,
    PaletteState,
    PaletteRoutes,
    ControlRoutes,
)

# Import FlatBuffer generated classes
from . import pixelairfb  # noqa: F401
from .pixelairfb.PixelAir.PixelAirDevice import PixelAirDevice as PixelAirDeviceFB


# Protocol constants
DEVICE_COMMAND_PORT = 9090
DEVICE_CONTROL_PORT = 6767
GET_STATE_ROUTE = "/getState"
DEFAULT_STATE_TIMEOUT = 10.0

# Discovery response pattern
_DISCOVERY_RESPONSE_PATTERN = re.compile(rb"^\$(\{.*\})$", re.DOTALL)

# Type alias for state change callbacks (Python 3.12+ syntax)
type StateChangeCallback = (
    Callable[[DeviceConnection, DeviceState], None]
    | Callable[[DeviceConnection, DeviceState], Awaitable[None]]
)


class DevicePacketHandler(PacketHandler):
    """Packet handler that routes packets to the appropriate device."""

    def __init__(self, connection: DeviceConnection, logger: logging.Logger):
        self._connection = connection
        self._logger = logger

    async def handle_packet(
        self,
        data: bytes,
        source_address: tuple[str, int]
    ) -> bool:
        """Route fragmented state packets to the device's assembler."""
        if source_address[0] != self._connection.ip_address:
            return False

        if len(data) >= 4 and data[0] == 0x46:
            await self._connection._assembler.process_packet(data, source_address)
            return True

        return False


class DiscoveryResponseHandler(PacketHandler):
    """Packet handler for discovery responses during polling."""

    def __init__(
        self,
        target_ip: str,
        callback: Callable[[dict[str, Any]], None]
    ):
        self._target_ip = target_ip
        self._callback = callback
        self._logger = logging.getLogger("pixelair.device.discovery_handler")

    async def handle_packet(
        self,
        data: bytes,
        source_address: tuple[str, int]
    ) -> bool:
        """Handle incoming discovery response packets."""
        if source_address[0] != self._target_ip:
            return False

        match = _DISCOVERY_RESPONSE_PATTERN.match(data)
        if not match:
            return False

        try:
            json_str = match.group(1).decode("utf-8")
            response = json.loads(json_str)
            self._logger.debug("Received discovery response: %s", response)
            self._callback(response)
            return True
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._logger.warning("Failed to parse discovery response: %s", e)
            return True


class DeviceConnection:
    """
    Internal device connection manager.

    Handles low-level device communication: packet assembly, state parsing,
    command sending, and polling. This is used by PixelAirDevice.
    """

    def __init__(
        self,
        ip_address: str,
        listener: UDPListener,
        serial_number: str,
        mac_address: str | None,
        logger: logging.Logger
    ):
        self._ip_address = ip_address
        self._listener = listener
        self._serial_number = serial_number
        self._mac_address = normalize_mac(mac_address) if mac_address else None
        self._logger = logger

        # State
        self._state = DeviceState(
            serial_number=serial_number,
            ip_address=ip_address,
            mac_address=self._mac_address
        )
        self._routes = ControlRoutes()
        self._raw_state: PixelAirDeviceFB | None = None
        self._state_lock = asyncio.Lock()

        # Callbacks - use list[Any] to avoid forward reference issues
        self._state_callbacks: list[Any] = []

        # Packet handling
        self._handler = DevicePacketHandler(self, self._logger)
        self._assembler = PacketAssembler(self._on_state_packet)
        self._registered = False

        # State request waiting
        self._state_events: list[asyncio.Event] = []
        self._state_events_lock = asyncio.Lock()

        # Polling
        self._state_counter: int | None = None
        self._polling_task: asyncio.Task[None] | None = None
        self._polling_running = False
        self._poll_interval: float = 2.5
        self._discovery_handler: PacketHandler | None = None
        self._discovery_event = asyncio.Event()
        self._discovery_response: dict[str, Any] | None = None

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def ip_address(self) -> str:
        return self._ip_address

    @ip_address.setter
    def ip_address(self, value: str) -> None:
        self._ip_address = value
        self._state.ip_address = value

    @property
    def mac_address(self) -> str | None:
        return self._mac_address or self._state.mac_address

    @property
    def serial_number(self) -> str | None:
        return self._serial_number or self._state.serial_number

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def routes(self) -> ControlRoutes:
        return self._routes

    @property
    def raw_state(self) -> PixelAirDeviceFB | None:
        return self._raw_state

    @property
    def is_registered(self) -> bool:
        return self._registered

    @property
    def is_polling(self) -> bool:
        return self._polling_running

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    @poll_interval.setter
    def poll_interval(self, value: float) -> None:
        if value < 0.5:
            raise ValueError("Poll interval must be at least 0.5 seconds")
        self._poll_interval = value

    # =========================================================================
    # Registration
    # =========================================================================

    async def register(self) -> None:
        """Register with the UDP listener to receive packets."""
        if self._registered:
            raise RuntimeError("Already registered")

        if not self._listener.is_running:
            raise RuntimeError("UDP listener is not running")

        await self._assembler.start()
        self._listener.add_handler(self._handler)
        self._registered = True
        self._logger.info("Device registered: %s", self._ip_address)

    async def unregister(self) -> None:
        """Unregister from the UDP listener."""
        if not self._registered:
            return

        await self.stop_polling()
        self._listener.remove_handler(self._handler)
        await self._assembler.stop()
        self._registered = False
        self._logger.info("Device unregistered: %s", self._ip_address)

    # =========================================================================
    # State Management
    # =========================================================================

    def add_state_callback(self, callback: StateChangeCallback) -> None:
        if callback not in self._state_callbacks:
            self._state_callbacks.append(callback)

    def remove_state_callback(self, callback: StateChangeCallback) -> bool:
        if callback in self._state_callbacks:
            self._state_callbacks.remove(callback)
            return True
        return False

    def copy_state(self) -> DeviceState:
        """Create a copy of the current state."""
        return DeviceState(
            serial_number=self._state.serial_number,
            model=self._state.model,
            nickname=self._state.nickname,
            firmware_version=self._state.firmware_version,
            is_on=self._state.is_on,
            brightness=self._state.brightness,
            mode=self._state.mode,
            rssi=self._state.rssi,
            ip_address=self._state.ip_address,
            mac_address=self._state.mac_address,
            scenes=list(self._state.scenes or []),
            active_scene_index=self._state.active_scene_index,
            manual_animations=list(self._state.manual_animations or []),
            active_manual_animation_index=self._state.active_manual_animation_index,
            auto_palette=PaletteState(
                hue=self._state.auto_palette.hue if self._state.auto_palette else 0.0,
                saturation=self._state.auto_palette.saturation if self._state.auto_palette else 0.0,
            ),
            scene_palette=PaletteState(
                hue=self._state.scene_palette.hue if self._state.scene_palette else 0.0,
                saturation=self._state.scene_palette.saturation if self._state.scene_palette else 0.0,
            ),
            manual_palette=PaletteState(
                hue=self._state.manual_palette.hue if self._state.manual_palette else 0.0,
                saturation=self._state.manual_palette.saturation if self._state.manual_palette else 0.0,
            ),
        )

    # =========================================================================
    # Commands
    # =========================================================================

    async def send_command(
        self,
        route: str,
        params: list[Any] | None = None,
        port: int = DEVICE_COMMAND_PORT
    ) -> None:
        """Send an OSC command to the device."""
        if not self._listener.is_running:
            raise RuntimeError("UDP listener is not running")

        builder = OscMessageBuilder(route)

        if params:
            for param in params:
                if isinstance(param, bool):
                    # Check bool before int since bool is subclass of int
                    builder.add_arg(param, "T" if param else "F")
                elif isinstance(param, int):
                    builder.add_arg(param, "i")
                elif isinstance(param, float):
                    builder.add_arg(param, "f")
                elif isinstance(param, str):
                    builder.add_arg(param, "s")
                else:
                    builder.add_arg(str(param), "s")

        message = builder.build().dgram
        await self._listener.send_to(message, self._ip_address, port)

        self._logger.debug("Sent command %s to %s:%d", route, self._ip_address, port)

    async def request_state(self, timeout: float = DEFAULT_STATE_TIMEOUT) -> DeviceState:
        """Request and wait for full device state."""
        if not self._registered:
            raise RuntimeError("Device is not registered")

        event = asyncio.Event()

        async with self._state_events_lock:
            self._state_events.append(event)

        try:
            await self.send_command(GET_STATE_ROUTE)
            await asyncio.wait_for(event.wait(), timeout)
            return self.copy_state()
        finally:
            async with self._state_events_lock:
                if event in self._state_events:
                    self._state_events.remove(event)

    # =========================================================================
    # Polling
    # =========================================================================

    async def start_polling(self, interval: float = 2.5) -> None:
        """Start polling the device for state changes."""
        if not self._registered:
            raise RuntimeError("Device must be registered before starting polling")

        if self._polling_running:
            return

        self.poll_interval = interval
        self._polling_running = True

        self._discovery_handler = DiscoveryResponseHandler(
            self._ip_address,
            self._on_discovery_response
        )
        self._listener.add_handler(self._discovery_handler)

        self._polling_task = asyncio.create_task(self._polling_loop())
        self._logger.info("Started polling (interval=%.1fs)", self._poll_interval)

    async def stop_polling(self) -> None:
        """Stop polling."""
        if not self._polling_running:
            return

        self._polling_running = False

        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None

        if self._discovery_handler:
            self._listener.remove_handler(self._discovery_handler)
            self._discovery_handler = None

        self._logger.info("Stopped polling")

    async def _polling_loop(self) -> None:
        """Background polling task with efficient timing."""
        from .discovery import DISCOVERY_ROUTE, DISCOVERY_PORT

        while self._polling_running:
            loop = asyncio.get_running_loop()
            poll_start = loop.time()

            try:
                builder = OscMessageBuilder(DISCOVERY_ROUTE)
                message = builder.build().dgram
                await self._listener.send_to(message, self._ip_address, DISCOVERY_PORT)

                self._discovery_event.clear()
                try:
                    await asyncio.wait_for(self._discovery_event.wait(), timeout=2.0)
                except TimeoutError:
                    self._logger.debug("No poll response from device")
                else:
                    # Got response - check if state changed
                    response = self._discovery_response
                    if response:
                        state_counter = response.get("state_counter")
                        if state_counter is not None:
                            if self._state_counter is None or state_counter != self._state_counter:
                                self._state_counter = state_counter
                                self._logger.debug("State counter changed, fetching state...")
                                try:
                                    await self.request_state(timeout=10.0)
                                except Exception as e:
                                    self._logger.warning("Error fetching state: %s", e)

            except Exception as e:
                self._logger.warning("Poll error: %s", e)

            # Sleep only the remaining time until next poll
            elapsed = loop.time() - poll_start
            sleep_time = max(0, self._poll_interval - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    def _on_discovery_response(self, data: dict[str, Any]) -> None:
        """Handle discovery response during polling."""
        self._discovery_response = data
        self._discovery_event.set()

    # =========================================================================
    # IP Resolution
    # =========================================================================

    async def resolve_ip(self, timeout: float = 5.0) -> bool:
        """Re-resolve the device's IP address."""
        if not self._mac_address or not self._serial_number:
            return False

        if not self._listener.is_running:
            return False

        # Try ARP table first
        new_ip = await lookup_ip_by_mac(self._mac_address)
        if new_ip:
            if new_ip != self._ip_address:
                self._logger.info("Updated IP via ARP: %s -> %s", self._ip_address, new_ip)
                self.ip_address = new_ip
            return True

        # Fall back to broadcast discovery
        from .discovery import DiscoveryService
        discovery = DiscoveryService(self._listener)
        discovered = await discovery.find_device_by_serial(self._serial_number, timeout=timeout)

        if discovered:
            if discovered.ip_address != self._ip_address:
                self._logger.info("Updated IP via discovery: %s -> %s", self._ip_address, discovered.ip_address)
                self.ip_address = discovered.ip_address
            return True

        return False

    # =========================================================================
    # State Packet Handling
    # =========================================================================

    async def _on_state_packet(self, payload: bytes) -> None:
        """Handle a complete assembled state packet."""
        try:
            device_state = PixelAirDeviceFB.GetRootAs(payload)  # type: ignore[no-untyped-call]
            self._raw_state = device_state

            async with self._state_lock:
                self._update_state_from_fb(device_state)

            self._logger.debug(
                "State updated: model=%s, on=%s, brightness=%.1f%%",
                self._state.model,
                self._state.is_on,
                self._state.brightness * 100
            )

            # Notify waiters
            async with self._state_events_lock:
                for event in self._state_events:
                    event.set()

            # Invoke callbacks
            await self._invoke_state_callbacks()

        except Exception as e:
            self._logger.exception("Failed to decode state packet: %s", e)

    def _update_state_from_fb(self, fb: Any) -> None:
        """Update DeviceState from FlatBuffer object.

        Note: FlatBuffer generated code has no type annotations, so we use Any.
        """
        if fb.SerialNumber():
            self._state.serial_number = fb.SerialNumber().decode("utf-8")

        if fb.Model():
            self._state.model = fb.Model().decode("utf-8")

        if fb.Version():
            self._state.firmware_version = fb.Version().decode("utf-8")

        self._state.rssi = fb.Rssi()

        if fb.Nickname() and fb.Nickname().Value():
            self._state.nickname = fb.Nickname().Value().decode("utf-8")

        if fb.Network():
            network = fb.Network()
            if network.IpAddress():
                self._state.ip_address = network.IpAddress().decode("utf-8")
            if network.MacAddress():
                self._state.mac_address = network.MacAddress().decode("utf-8")

        if fb.Engine():
            engine = fb.Engine()

            if engine.IsDisplaying():
                is_displaying = engine.IsDisplaying()
                self._state.is_on = is_displaying.Value()
                if is_displaying.Route():
                    self._routes.is_displaying = is_displaying.Route().decode("utf-8")

            if engine.Brightness():
                brightness = engine.Brightness()
                self._state.brightness = brightness.Value()
                if brightness.Route():
                    self._routes.brightness = brightness.Route().decode("utf-8")

            if engine.Mode():
                mode = engine.Mode()
                self._state.mode = DeviceMode(mode.Value())
                if mode.Route():
                    self._routes.mode = mode.Route().decode("utf-8")

            if engine.SceneMode():
                scene_mode = engine.SceneMode()

                if scene_mode.ActiveSceneIndex():
                    active_idx = scene_mode.ActiveSceneIndex()
                    self._state.active_scene_index = active_idx.Value()
                    if active_idx.Route():
                        self._routes.active_scene_index = active_idx.Route().decode("utf-8")

                self._state.scenes = []
                for i in range(scene_mode.ScenesLength()):
                    scene = scene_mode.Scenes(i)
                    if scene and scene.Label():
                        self._state.scenes.append(SceneInfo(
                            label=scene.Label().decode("utf-8"),
                            index=scene.Index(),
                        ))

                if scene_mode.Palette():
                    if self._state.scene_palette and self._routes.scene_palette:
                        self._extract_palette(
                            scene_mode.Palette(),
                            self._state.scene_palette,
                            self._routes.scene_palette
                        )

            if engine.ManualMode():
                manual_mode = engine.ManualMode()

                if manual_mode.ActiveAnimationIndex():
                    active_anim = manual_mode.ActiveAnimationIndex()
                    self._state.active_manual_animation_index = active_anim.Value()
                    if active_anim.Route():
                        self._routes.manual_animation_index = active_anim.Route().decode("utf-8")

                self._state.manual_animations = []
                for i in range(manual_mode.AnimationsLength()):
                    anim = manual_mode.Animations(i)
                    if anim:
                        self._state.manual_animations.append(anim.decode("utf-8"))

                if manual_mode.Palette():
                    if self._state.manual_palette and self._routes.manual_palette:
                        self._extract_palette(
                            manual_mode.Palette(),
                            self._state.manual_palette,
                            self._routes.manual_palette
                        )

            if engine.AutoMode():
                auto_mode = engine.AutoMode()
                if auto_mode.Palette():
                    if self._state.auto_palette and self._routes.auto_palette:
                        self._extract_palette(
                            auto_mode.Palette(),
                            self._state.auto_palette,
                            self._routes.auto_palette
                        )

    def _extract_palette(
        self,
        palette_fb: Any,
        palette_state: PaletteState,
        palette_routes: PaletteRoutes
    ) -> None:
        """Extract palette values and routes from a FlatBuffer Palette."""
        if palette_fb.Hue():
            hue = palette_fb.Hue()
            palette_state.hue = hue.Value()
            if hue.Route():
                palette_routes.hue = hue.Route().decode("utf-8")

        if palette_fb.Saturation():
            saturation = palette_fb.Saturation()
            palette_state.saturation = saturation.Value()
            if saturation.Route():
                palette_routes.saturation = saturation.Route().decode("utf-8")

    async def _invoke_state_callbacks(self) -> None:
        """Invoke all registered state change callbacks."""
        state_copy = self.copy_state()

        for callback in self._state_callbacks:
            try:
                result = callback(self, state_copy)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                self._logger.exception("State callback raised exception: %s", e)
