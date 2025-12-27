"""
PixelAir Device public API.

This module provides the PixelAirDevice class - the main interface for
controlling PixelAir devices (Fluora, Monos, etc.).

Example:
    ```python
    async with UDPListener() as listener:
        device = await PixelAirDevice.from_identifiers(
            mac_address="aa:bb:cc:dd:ee:ff",
            serial_number="abc123",
            listener=listener,
        )
        if device:
            async with device:
                await device.turn_on()
                await device.set_brightness(0.75)
    ```
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .udp_listener import UDPListener
from .discovery import DiscoveredDevice
from .arp import normalize_mac, lookup_ip_by_mac
from ._types import (
    DeviceState,
    DeviceMode,
    SceneInfo,
    EffectInfo,
    PaletteState,
    PaletteRoutes,
    ControlRoutes,
)
from ._internal import (
    DeviceConnection,
    DEVICE_COMMAND_PORT,
    DEVICE_CONTROL_PORT,
    GET_STATE_ROUTE,
    DEFAULT_STATE_TIMEOUT,
)

# Re-export types for backward compatibility
__all__ = [
    "PixelAirDevice",
    "DeviceState",
    "DeviceMode",
    "SceneInfo",
    "EffectInfo",
    "PaletteState",
    "PaletteRoutes",
    "ControlRoutes",
    "StateChangeCallback",
    "DEVICE_COMMAND_PORT",
    "DEVICE_CONTROL_PORT",
    "GET_STATE_ROUTE",
]

# Type alias for state change callbacks (Python 3.12+)
type StateChangeCallback = (
    Callable[[PixelAirDevice, DeviceState], None]
    | Callable[[PixelAirDevice, DeviceState], Awaitable[None]]
)


class PixelAirDevice:
    """
    A PixelAir device controller.

    Provides methods to control device power, brightness, hue, saturation,
    and effects. Devices are identified by MAC address and serial number
    for reliable reconnection.

    Use classmethods to create instances:
    - from_identifiers(): From stored MAC/serial (Home Assistant)
    - from_discovered(): From a discovery result
    - from_mac_address(): From MAC only (will discover serial)

    Example:
        ```python
        async with UDPListener() as listener:
            device = await PixelAirDevice.from_identifiers(
                mac_address="aa:bb:cc:dd:ee:ff",
                serial_number="abc123",
                listener=listener
            )
            if device:
                async with device:
                    state = await device.get_state()
                    print(f"Brightness: {state.brightness}")
                    await device.set_brightness(0.5)
        ```
    """

    def __init__(
        self,
        ip_address: str,
        listener: UDPListener,
        serial_number: str,
        mac_address: str,
        _internal: bool = False
    ):
        """
        Initialize a PixelAirDevice.

        Note: Prefer using classmethods from_identifiers(), from_discovered(),
        or from_mac_address() instead of direct construction.

        Args:
            ip_address: The device's IP address.
            listener: The shared UDP listener.
            serial_number: The device's serial number.
            mac_address: The device's MAC address.
            _internal: Bypass validation (internal use only).

        Raises:
            ValueError: If serial_number or mac_address is missing.
        """
        if not _internal:
            if not serial_number:
                raise ValueError(
                    "serial_number is required. Use from_identifiers() or from_discovered()."
                )
            if not mac_address:
                raise ValueError(
                    "mac_address is required. Use from_identifiers() or from_discovered()."
                )

        self._listener = listener
        self._logger = logging.getLogger(f"pixelair.device.{serial_number or ip_address}")

        # Internal connection manager
        self._conn = DeviceConnection(
            ip_address=ip_address,
            listener=listener,
            serial_number=serial_number,
            mac_address=mac_address,
            logger=self._logger
        )

    # =========================================================================
    # Factory Methods
    # =========================================================================

    @classmethod
    def from_discovered(
        cls,
        discovered: DiscoveredDevice,
        listener: UDPListener
    ) -> PixelAirDevice:
        """
        Create a device from a discovery result.

        Args:
            discovered: Discovery result (must have mac_address).
            listener: The shared UDP listener.

        Returns:
            A new PixelAirDevice instance.

        Raises:
            ValueError: If discovered device lacks MAC address.
        """
        if not discovered.mac_address:
            raise ValueError(
                "Discovered device lacks MAC address. "
                "Use DiscoveryService.discover_with_info() to get full device info."
            )

        return cls(
            ip_address=discovered.ip_address,
            listener=listener,
            serial_number=discovered.serial_number,
            mac_address=discovered.mac_address,
            _internal=True
        )

    @classmethod
    async def from_identifiers(
        cls,
        mac_address: str,
        serial_number: str,
        listener: UDPListener,
        timeout: float = 5.0
    ) -> PixelAirDevice | None:
        """
        Create a device from stored MAC and serial number.

        This is the preferred method for Home Assistant. Uses a bulletproof
        resolution strategy:
        1. Try ARP table lookup using MAC address (fast)
        2. If not found, broadcast discovery using serial number

        Args:
            mac_address: The device's MAC address.
            serial_number: The device's serial number.
            listener: The shared UDP listener (must be running).
            timeout: Discovery timeout in seconds.

        Returns:
            A PixelAirDevice, or None if device not found.

        Raises:
            ValueError: If MAC address format is invalid.
            RuntimeError: If listener is not running.
        """
        if not listener.is_running:
            raise RuntimeError("UDP listener is not running")

        try:
            normalized_mac = normalize_mac(mac_address)
        except ValueError as e:
            raise ValueError(f"Invalid MAC address: {mac_address}") from e

        logger = logging.getLogger("pixelair.device")

        # Strategy 1: ARP table lookup
        ip_address = await lookup_ip_by_mac(normalized_mac)

        if ip_address:
            logger.debug("Resolved MAC %s to IP %s via ARP", normalized_mac, ip_address)
            from .discovery import DiscoveryService
            discovery = DiscoveryService(listener)
            discovered = await discovery.verify_device(ip_address, timeout=timeout)

            if discovered and discovered.serial_number == serial_number:
                return cls(
                    ip_address=ip_address,
                    listener=listener,
                    serial_number=serial_number,
                    mac_address=normalized_mac,
                    _internal=True
                )

        # Strategy 2: Broadcast discovery
        logger.debug("ARP failed, trying broadcast discovery for serial %s", serial_number)
        from .discovery import DiscoveryService
        discovery = DiscoveryService(listener)
        discovered = await discovery.find_device_by_serial(serial_number, timeout=timeout)

        if discovered:
            logger.info("Found device %s at %s via discovery", serial_number, discovered.ip_address)
            return cls(
                ip_address=discovered.ip_address,
                listener=listener,
                serial_number=serial_number,
                mac_address=normalized_mac,
                _internal=True
            )

        logger.warning("Could not find device MAC=%s serial=%s", normalized_mac, serial_number)
        return None

    @classmethod
    async def from_mac_address(
        cls,
        mac_address: str,
        listener: UDPListener,
        timeout: float = 5.0
    ) -> PixelAirDevice | None:
        """
        Create a device by resolving its MAC address.

        Args:
            mac_address: The device's MAC address.
            listener: The shared UDP listener (must be running).
            timeout: Discovery timeout in seconds.

        Returns:
            A PixelAirDevice, or None if device not found.

        Raises:
            ValueError: If MAC address format is invalid.
            RuntimeError: If listener is not running.
        """
        if not listener.is_running:
            raise RuntimeError("UDP listener is not running")

        try:
            normalized_mac = normalize_mac(mac_address)
        except ValueError as e:
            raise ValueError(f"Invalid MAC address: {mac_address}") from e

        logger = logging.getLogger("pixelair.device")

        # Look up IP from ARP
        ip_address = await lookup_ip_by_mac(normalized_mac)

        if not ip_address:
            # Try warming ARP with broadcast
            from .discovery import DiscoveryService
            discovery = DiscoveryService(listener)
            await discovery._broadcast_discovery()
            await asyncio.sleep(0.5)
            ip_address = await lookup_ip_by_mac(normalized_mac)

        if not ip_address:
            logger.warning("Could not resolve MAC %s to IP", normalized_mac)
            return None

        # Verify device responds
        from .discovery import DiscoveryService
        discovery = DiscoveryService(listener)
        discovered = await discovery.verify_device(ip_address, timeout=timeout)

        if not discovered:
            logger.warning("Device at %s did not respond", ip_address)
            return None

        return cls(
            ip_address=ip_address,
            listener=listener,
            serial_number=discovered.serial_number,
            mac_address=normalized_mac,
            _internal=True
        )

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def ip_address(self) -> str:
        """The device's current IP address."""
        return self._conn.ip_address

    @property
    def mac_address(self) -> str | None:
        """The device's MAC address."""
        return self._conn.mac_address

    @property
    def serial_number(self) -> str | None:
        """The device's serial number."""
        return self._conn.serial_number

    @property
    def state(self) -> DeviceState:
        """A copy of the current device state."""
        return self._conn.copy_state()

    @property
    def is_registered(self) -> bool:
        """Whether the device is registered with the listener."""
        return self._conn.is_registered

    @property
    def raw_state(self) -> object:
        """The raw FlatBuffer state object, if available."""
        return self._conn.raw_state

    @property
    def is_polling(self) -> bool:
        """Whether state polling is active."""
        return self._conn.is_polling

    @property
    def poll_interval(self) -> float:
        """The polling interval in seconds."""
        return self._conn.poll_interval

    @poll_interval.setter
    def poll_interval(self, value: float) -> None:
        self._conn.poll_interval = value

    @property
    def has_control_routes(self) -> bool:
        """Whether control routes have been loaded (via get_state)."""
        routes = self._conn.routes
        return all([routes.is_displaying, routes.brightness, routes.mode])

    # =========================================================================
    # Registration
    # =========================================================================

    async def register(self) -> None:
        """Register this device to receive state updates."""
        await self._conn.register()

    async def unregister(self) -> None:
        """Unregister this device."""
        await self._conn.unregister()

    # =========================================================================
    # State
    # =========================================================================

    async def get_state(self, timeout: float = DEFAULT_STATE_TIMEOUT) -> DeviceState:
        """
        Request and wait for the full device state.

        Args:
            timeout: Maximum wait time in seconds.

        Returns:
            The updated DeviceState.

        Raises:
            asyncio.TimeoutError: If no response within timeout.
            RuntimeError: If device is not registered.
        """
        return await self._conn.request_state(timeout)

    def add_state_callback(self, callback: StateChangeCallback) -> None:
        """Register a callback for state changes."""
        # Wrap callback to pass self instead of connection
        def wrapper(_conn: object, state: DeviceState) -> None:
            result = callback(self, state)
            if hasattr(result, "__await__"):
                import asyncio
                asyncio.create_task(result)  # type: ignore[arg-type]

        self._conn.add_state_callback(wrapper)

    def remove_state_callback(self, callback: StateChangeCallback) -> bool:
        """Remove a state change callback."""
        # Note: This won't work as expected since we wrap callbacks
        # Users should keep a reference to stop polling instead
        _ = callback  # Unused, but kept for API compatibility
        return False

    # =========================================================================
    # Polling
    # =========================================================================

    async def start_polling(self, interval: float = 2.5) -> None:
        """
        Start polling for state changes.

        Uses efficient state_counter checking - only fetches full state
        when it actually changes.

        Args:
            interval: Poll interval in seconds (min 0.5).

        Raises:
            RuntimeError: If device is not registered.
            ValueError: If interval < 0.5.
        """
        await self._conn.start_polling(interval)

    async def stop_polling(self) -> None:
        """Stop polling for state changes."""
        await self._conn.stop_polling()

    # =========================================================================
    # IP Resolution
    # =========================================================================

    async def resolve_ip(self, timeout: float = 5.0) -> bool:
        """
        Re-resolve the device's IP address.

        Uses bulletproof fallback: ARP table -> broadcast discovery.

        Args:
            timeout: Discovery timeout in seconds.

        Returns:
            True if IP was resolved, False if device not found.
        """
        return await self._conn.resolve_ip(timeout)

    async def update_ip_from_mac(self) -> bool:
        """
        Update IP from MAC via ARP table.

        Deprecated: Use resolve_ip() which includes fallback.

        Returns:
            True if IP was updated.
        """
        return await self._conn.resolve_ip(timeout=0.1)

    # =========================================================================
    # Control Methods
    # =========================================================================

    async def turn_on(self) -> None:
        """Turn on the device display."""
        await self._set_power(True)

    async def turn_off(self) -> None:
        """Turn off the device display."""
        await self._set_power(False)

    async def _set_power(self, on: bool) -> None:
        if not self._conn.is_registered:
            raise RuntimeError("Device is not registered")

        routes = self._conn.routes
        if not routes.is_displaying:
            raise RuntimeError("Power route not available. Call get_state() first.")

        await self._conn.send_command(
            routes.is_displaying,
            [1 if on else 0, 0],
            port=DEVICE_CONTROL_PORT
        )
        self._conn.state.is_on = on
        self._logger.info("Set power to %s", "ON" if on else "OFF")

    async def set_brightness(self, brightness: float) -> None:
        """
        Set the device brightness.

        Args:
            brightness: Brightness from 0.0 to 1.0.

        Raises:
            ValueError: If brightness is out of range.
            RuntimeError: If not registered or routes unavailable.
        """
        if not self._conn.is_registered:
            raise RuntimeError("Device is not registered")

        routes = self._conn.routes
        if not routes.brightness:
            raise RuntimeError("Brightness route not available. Call get_state() first.")

        if not 0.0 <= brightness <= 1.0:
            raise ValueError(f"Brightness must be 0.0-1.0, got {brightness}")

        brightness = round(brightness, 2)
        await self._conn.send_command(
            routes.brightness,
            [brightness, 0],
            port=DEVICE_CONTROL_PORT
        )
        self._conn.state.brightness = brightness
        self._logger.info("Set brightness to %.0f%%", brightness * 100)

    async def set_hue(self, hue: float) -> None:
        """
        Set the hue for the current mode's palette.

        Args:
            hue: Hue from 0.0 to 1.0.

        Raises:
            ValueError: If hue is out of range.
            RuntimeError: If not registered or routes unavailable.
        """
        if not self._conn.is_registered:
            raise RuntimeError("Device is not registered")

        if not 0.0 <= hue <= 1.0:
            raise ValueError(f"Hue must be 0.0-1.0, got {hue}")

        palette_routes = self._get_current_palette_routes()
        if not palette_routes or not palette_routes.hue:
            raise RuntimeError("Hue route not available. Call get_state() first.")

        hue = round(hue, 2)
        await self._conn.send_command(
            palette_routes.hue,
            [hue, 0],
            port=DEVICE_CONTROL_PORT
        )
        self._get_current_palette_state().hue = hue
        self._logger.info("Set hue to %.2f", hue)

    async def set_saturation(self, saturation: float) -> None:
        """
        Set the saturation for the current mode's palette.

        Args:
            saturation: Saturation from 0.0 to 1.0.

        Raises:
            ValueError: If saturation is out of range.
            RuntimeError: If not registered or routes unavailable.
        """
        if not self._conn.is_registered:
            raise RuntimeError("Device is not registered")

        if not 0.0 <= saturation <= 1.0:
            raise ValueError(f"Saturation must be 0.0-1.0, got {saturation}")

        palette_routes = self._get_current_palette_routes()
        if not palette_routes or not palette_routes.saturation:
            raise RuntimeError("Saturation route not available. Call get_state() first.")

        saturation = round(saturation, 2)
        await self._conn.send_command(
            palette_routes.saturation,
            [saturation, 0],
            port=DEVICE_CONTROL_PORT
        )
        self._get_current_palette_state().saturation = saturation
        self._logger.info("Set saturation to %.2f", saturation)

    def _get_current_palette_routes(self) -> PaletteRoutes | None:
        routes = self._conn.routes
        mode = self._conn.state.mode
        if mode == DeviceMode.AUTO:
            return routes.auto_palette
        elif mode == DeviceMode.SCENE:
            return routes.scene_palette
        elif mode == DeviceMode.MANUAL:
            return routes.manual_palette
        return None

    def _get_current_palette_state(self) -> PaletteState:
        state = self._conn.state
        if state.mode == DeviceMode.AUTO and state.auto_palette:
            return state.auto_palette
        elif state.mode == DeviceMode.SCENE and state.scene_palette:
            return state.scene_palette
        elif state.mode == DeviceMode.MANUAL and state.manual_palette:
            return state.manual_palette
        # Fallback (should not happen after state initialization)
        return state.auto_palette or PaletteState()

    async def set_mode(self, mode: DeviceMode) -> None:
        """
        Set the device display mode.

        Args:
            mode: The display mode (AUTO, SCENE, or MANUAL).

        Raises:
            RuntimeError: If not registered or routes unavailable.
        """
        if not self._conn.is_registered:
            raise RuntimeError("Device is not registered")

        routes = self._conn.routes
        if not routes.mode:
            raise RuntimeError("Mode route not available. Call get_state() first.")

        await self._conn.send_command(
            routes.mode,
            [int(mode), 0],
            port=DEVICE_CONTROL_PORT
        )
        self._conn.state.mode = mode
        self._logger.info("Set mode to %s", mode.name)

    async def set_effect(self, effect_id: str) -> None:
        """
        Set the device effect by ID.

        Effect IDs:
        - "auto": Sets mode to AUTO
        - "scene:{index}": Sets mode to SCENE and selects scene
        - "manual:{index}": Sets mode to MANUAL and selects animation

        Args:
            effect_id: The effect ID (from EffectInfo.id).

        Raises:
            ValueError: If effect ID is not recognized.
            RuntimeError: If not registered or routes unavailable.
        """
        if not self._conn.is_registered:
            raise RuntimeError("Device is not registered")

        if effect_id == "auto":
            await self.set_mode(DeviceMode.AUTO)
            return

        if effect_id.startswith("scene:"):
            try:
                scene_index = int(effect_id[6:])
                await self._set_scene(scene_index)
                return
            except ValueError:
                raise ValueError(f"Invalid scene effect ID: {effect_id}")

        if effect_id.startswith("manual:"):
            try:
                anim_index = int(effect_id[7:])
                await self._set_manual_animation(anim_index)
                return
            except ValueError:
                raise ValueError(f"Invalid manual effect ID: {effect_id}")

        raise ValueError(f"Unknown effect ID: {effect_id}")

    async def set_effect_by_name(self, display_name: str) -> None:
        """
        Set the device effect by display name.

        Args:
            display_name: The effect display name.

        Raises:
            ValueError: If effect name not found.
            RuntimeError: If not registered or routes unavailable.
        """
        for effect in self._conn.state.effects:
            if effect.display_name == display_name:
                await self.set_effect(effect.id)
                return
        raise ValueError(f"Unknown effect: {display_name}")

    async def _set_scene(self, scene_index: int) -> None:
        routes = self._conn.routes
        if not routes.mode or not routes.active_scene_index:
            raise RuntimeError("Scene routes not available. Call get_state() first.")

        # Ensure scene mode
        if self._conn.state.mode != DeviceMode.SCENE:
            await self._conn.send_command(
                routes.mode,
                [int(DeviceMode.SCENE), 0],
                port=DEVICE_CONTROL_PORT
            )
            self._conn.state.mode = DeviceMode.SCENE

        # Set scene index
        await self._conn.send_command(
            routes.active_scene_index,
            [scene_index, 0],
            port=DEVICE_CONTROL_PORT
        )
        self._conn.state.active_scene_index = scene_index
        self._logger.info("Set scene index to %d", scene_index)

    async def _set_manual_animation(self, animation_index: int) -> None:
        routes = self._conn.routes
        if not routes.mode or not routes.manual_animation_index:
            raise RuntimeError("Manual routes not available. Call get_state() first.")

        # Ensure manual mode
        if self._conn.state.mode != DeviceMode.MANUAL:
            await self._conn.send_command(
                routes.mode,
                [int(DeviceMode.MANUAL), 0],
                port=DEVICE_CONTROL_PORT
            )
            self._conn.state.mode = DeviceMode.MANUAL

        # Set animation index
        await self._conn.send_command(
            routes.manual_animation_index,
            [animation_index, 0],
            port=DEVICE_CONTROL_PORT
        )
        self._conn.state.active_manual_animation_index = animation_index
        self._logger.info("Set manual animation index to %d", animation_index)

    # =========================================================================
    # Context Manager
    # =========================================================================

    async def __aenter__(self) -> PixelAirDevice:
        await self.register()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.unregister()

    def __str__(self) -> str:
        state = self._conn.state
        name = state.nickname or state.model or "Unknown"
        return f"PixelAirDevice({name} @ {self._conn.ip_address})"

    def __repr__(self) -> str:
        return (
            f"PixelAirDevice("
            f"ip={self._conn.ip_address}, "
            f"serial={self.serial_number}, "
            f"model={self._conn.state.model}, "
            f"registered={self._conn.is_registered})"
        )
