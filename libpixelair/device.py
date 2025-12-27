"""
PixelAir Device representation and state management.

This module provides the PixelAirDevice class that represents a single
PixelAir device (Fluora, Monos, etc.). It handles:

- Device state tracking and updates
- Sending commands to the device
- Requesting and receiving full state via getState

State updates are received as fragmented FlatBuffer packets which are
assembled and decoded into the device's state.
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Awaitable, Callable, List, Optional, Tuple, Union

from pythonosc.osc_message_builder import OscMessageBuilder

from .udp_listener import UDPListener, PacketHandler
from .packet_assembler import PacketAssembler
from .discovery import DiscoveredDevice
from .arp import lookup_ip_by_mac, normalize_mac

# Import FlatBuffer generated classes - must import pixelairfb first to set up sys.path
from . import pixelairfb  # noqa: F401 - sets up import path for FlatBuffer classes
from .pixelairfb.PixelAir.PixelAirDevice import PixelAirDevice as PixelAirDeviceFB


# Device command port (discovery, getState)
DEVICE_COMMAND_PORT = 9090

# Device control port (brightness, power, mode)
DEVICE_CONTROL_PORT = 6767

# OSC route for getting full state
GET_STATE_ROUTE = "/getState"

# Default timeout for state requests
DEFAULT_STATE_TIMEOUT = 10.0


class DeviceMode(IntEnum):
    """Device display mode."""
    AUTO = 0
    SCENE = 1
    MANUAL = 2


@dataclass
class SceneInfo:
    """
    Information about a scene available on the device.

    Attributes:
        label: The scene name (e.g., "Sunset", "Ocean").
        index: The scene index used for selection.
    """
    label: str
    index: int


@dataclass
class EffectInfo:
    """
    Information about an available effect.

    Effects are presented to Home Assistant and users. They abstract away
    the underlying mode (Auto/Scene/Manual) and provide a clean interface.

    Attributes:
        id: Unique identifier for this effect (used when setting).
        display_name: Human-readable name shown to users.
    """
    id: str
    display_name: str


# Animation prefix to model mapping
# Animations are prefixed with "category::" to indicate compatibility
ANIMATION_MODEL_PREFIXES = {
    "fluora": ["generic", "fluora", "fluora/audio"],
    "monos": ["generic", "monos"],
}


def _get_animation_display_name(animation_id: str) -> str:
    """
    Extract the display name from an animation ID.

    Animation IDs have format "prefix::name" (e.g., "fluora::Rainbow").
    This extracts just the name part.

    Args:
        animation_id: The full animation ID.

    Returns:
        The display name (part after "::").
    """
    if "::" in animation_id:
        return animation_id.split("::", 1)[1]
    return animation_id


def _is_animation_compatible(animation_id: str, model: Optional[str]) -> bool:
    """
    Check if an animation is compatible with a device model.

    Args:
        animation_id: The animation ID (with prefix).
        model: The device model name (e.g., "Fluora", "Monos").

    Returns:
        True if the animation is compatible with this model.
    """
    if not model:
        return True  # If no model, show all animations

    # Extract prefix from animation ID
    if "::" not in animation_id:
        return True  # No prefix means it's compatible

    prefix = animation_id.split("::", 1)[0].lower()

    # Find allowed prefixes for this model
    model_lower = model.lower()
    for model_key, allowed_prefixes in ANIMATION_MODEL_PREFIXES.items():
        if model_key in model_lower:
            return prefix in allowed_prefixes

    # Unknown model - show generic only
    return prefix == "generic"


@dataclass
class PaletteRoutes:
    """
    OSC routes for palette (hue/saturation) control within a mode.

    Each mode (Auto, Scene, Manual) has its own palette with separate routes.
    """
    hue: Optional[str] = None
    saturation: Optional[str] = None


@dataclass
class ControlRoutes:
    """
    OSC routes for controlling device parameters.

    These routes are extracted from the device's FlatBuffer state and are
    used to send control commands. Routes are obfuscated strings that are
    unique per device/firmware.
    """
    brightness: Optional[str] = None
    is_displaying: Optional[str] = None
    mode: Optional[str] = None
    # Scene mode routes
    active_scene_index: Optional[str] = None
    # Manual mode routes
    manual_animation_index: Optional[str] = None
    # Palette routes for each mode
    auto_palette: PaletteRoutes = None  # type: ignore
    scene_palette: PaletteRoutes = None  # type: ignore
    manual_palette: PaletteRoutes = None  # type: ignore

    def __post_init__(self):
        """Initialize nested dataclass defaults."""
        if self.auto_palette is None:
            self.auto_palette = PaletteRoutes()
        if self.scene_palette is None:
            self.scene_palette = PaletteRoutes()
        if self.manual_palette is None:
            self.manual_palette = PaletteRoutes()


@dataclass
class PaletteState:
    """
    Palette (hue/saturation) state for a mode.

    Values are floats from 0.0 to 1.0.
    """
    hue: float = 0.0
    saturation: float = 0.0


@dataclass
class DeviceState:
    """
    Represents the current state of a PixelAir device.

    This is a simplified view of the device state extracted from the
    FlatBuffer state packet.

    Attributes:
        serial_number: The device's unique serial number.
        model: The device model name (e.g., "Fluora", "Monos").
        nickname: User-assigned device name.
        firmware_version: Current firmware version.
        is_on: Whether the device display is currently on.
        brightness: Current brightness level (0.0 to 1.0).
        mode: Current display mode (AUTO, SCENE, MANUAL).
        rssi: WiFi signal strength in dBm.
        ip_address: The device's IP address.
        mac_address: The device's MAC address.
        scenes: List of available scenes (for Scene mode).
        active_scene_index: Currently active scene index (for Scene mode).
        manual_animations: List of available animation names (for Manual mode).
        active_manual_animation_index: Currently active animation index (for Manual mode).
        auto_palette: Palette state for Auto mode.
        scene_palette: Palette state for Scene mode.
        manual_palette: Palette state for Manual mode.
        hue: Current hue value based on active mode (0.0 to 1.0).
        saturation: Current saturation value based on active mode (0.0 to 1.0).
        effect_list: Combined list of all available effects for Home Assistant.
        current_effect: The currently active effect name.
    """
    serial_number: Optional[str] = None
    model: Optional[str] = None
    nickname: Optional[str] = None
    firmware_version: Optional[str] = None
    is_on: bool = False
    brightness: float = 0.0
    mode: DeviceMode = DeviceMode.SCENE
    rssi: int = 0
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    # Scene mode
    scenes: List[SceneInfo] = None  # type: ignore
    active_scene_index: int = 0
    # Manual mode
    manual_animations: List[str] = None  # type: ignore
    active_manual_animation_index: int = 0
    # Palette state per mode
    auto_palette: PaletteState = None  # type: ignore
    scene_palette: PaletteState = None  # type: ignore
    manual_palette: PaletteState = None  # type: ignore

    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.scenes is None:
            self.scenes = []
        if self.manual_animations is None:
            self.manual_animations = []
        if self.auto_palette is None:
            self.auto_palette = PaletteState()
        if self.scene_palette is None:
            self.scene_palette = PaletteState()
        if self.manual_palette is None:
            self.manual_palette = PaletteState()

    @property
    def hue(self) -> float:
        """
        Get the current hue value based on the active mode.

        Returns:
            Hue value from 0.0 to 1.0.
        """
        if self.mode == DeviceMode.AUTO:
            return self.auto_palette.hue
        elif self.mode == DeviceMode.SCENE:
            return self.scene_palette.hue
        elif self.mode == DeviceMode.MANUAL:
            return self.manual_palette.hue
        return 0.0

    @property
    def saturation(self) -> float:
        """
        Get the current saturation value based on the active mode.

        Returns:
            Saturation value from 0.0 to 1.0.
        """
        if self.mode == DeviceMode.AUTO:
            return self.auto_palette.saturation
        elif self.mode == DeviceMode.SCENE:
            return self.scene_palette.saturation
        elif self.mode == DeviceMode.MANUAL:
            return self.manual_palette.saturation
        return 0.0

    @property
    def effects(self) -> List[EffectInfo]:
        """
        Get the list of available effects with IDs and display names.

        Returns a list of EffectInfo objects suitable for Home Assistant:
        - "auto" -> "Auto"
        - "scene:{index}" -> "Scene: {label}"
        - "manual:{index}" -> Animation display name (filtered by model)

        Returns:
            List of EffectInfo objects.
        """
        result = [EffectInfo(id="auto", display_name="Auto")]

        # Add scenes
        for scene in self.scenes:
            result.append(EffectInfo(
                id=f"scene:{scene.index}",
                display_name=f"Scene: {scene.label}",
            ))

        # Add manual animations (filtered by model compatibility)
        for i, anim_id in enumerate(self.manual_animations):
            if _is_animation_compatible(anim_id, self.model):
                result.append(EffectInfo(
                    id=f"manual:{i}",
                    display_name=_get_animation_display_name(anim_id),
                ))

        return result

    @property
    def effect_list(self) -> List[str]:
        """
        Get the list of effect display names for Home Assistant.

        This is a convenience property that returns just the display names
        from the effects property.

        Returns:
            List of effect display names.
        """
        return [e.display_name for e in self.effects]

    @property
    def current_effect(self) -> Optional[str]:
        """
        Get the display name of the currently active effect.

        Returns:
            The current effect display name based on mode, or None if unknown.
        """
        if self.mode == DeviceMode.AUTO:
            return "Auto"
        elif self.mode == DeviceMode.SCENE:
            for scene in self.scenes:
                if scene.index == self.active_scene_index:
                    return f"Scene: {scene.label}"
            return None
        elif self.mode == DeviceMode.MANUAL:
            if 0 <= self.active_manual_animation_index < len(self.manual_animations):
                anim_id = self.manual_animations[self.active_manual_animation_index]
                return _get_animation_display_name(anim_id)
            return None
        return None

    @property
    def current_effect_id(self) -> Optional[str]:
        """
        Get the ID of the currently active effect.

        Returns:
            The current effect ID, or None if unknown.
        """
        if self.mode == DeviceMode.AUTO:
            return "auto"
        elif self.mode == DeviceMode.SCENE:
            return f"scene:{self.active_scene_index}"
        elif self.mode == DeviceMode.MANUAL:
            return f"manual:{self.active_manual_animation_index}"
        return None


# Type alias for state change callbacks
StateChangeCallback = Union[
    Callable[["PixelAirDevice", DeviceState], None],
    Callable[["PixelAirDevice", DeviceState], Awaitable[None]]
]


class DevicePacketHandler(PacketHandler):
    """
    Packet handler that routes packets to the appropriate device.

    This handler checks if incoming packets are fragmented state packets
    (header 0x46) and routes them to the device's packet assembler.
    """

    def __init__(
        self,
        device: "PixelAirDevice",
        logger: logging.Logger
    ):
        """
        Initialize the device packet handler.

        Args:
            device: The device to route packets to.
            logger: Logger instance.
        """
        self._device = device
        self._logger = logger

    async def handle_packet(
        self,
        data: bytes,
        source_address: Tuple[str, int]
    ) -> bool:
        """
        Handle an incoming packet.

        Routes fragmented state packets from this device's IP to its
        packet assembler.

        Args:
            data: The raw packet data.
            source_address: Tuple of (ip_address, port) of the sender.

        Returns:
            True if the packet was handled, False otherwise.
        """
        source_ip = source_address[0]

        # Only handle packets from our device
        if source_ip != self._device.ip_address:
            return False

        # Check for fragmented state packet header (0x46 = 'F')
        if len(data) >= 4 and data[0] == 0x46:
            await self._device._assembler.process_packet(data, source_address)
            return True

        return False


class PixelAirDevice:
    """
    Represents a PixelAir device on the network.

    This class manages the connection to a single PixelAir device,
    handling state updates and command sending. It integrates with
    the shared UDPListener for network communication.

    Devices are identified by both MAC address AND serial number for
    bulletproof identification. When the device becomes unreachable,
    the class can re-resolve the IP using:
    1. ARP table lookup (fast, uses MAC)
    2. Broadcast discovery (fallback, uses serial number)

    Create devices using one of the classmethods:
    - from_discovered(): From a discovery result
    - from_identifiers(): From stored MAC/serial (Home Assistant)
    - from_mac_address(): From MAC only (will discover serial)

    Example:
        ```python
        async def main():
            async with UDPListener() as listener:
                # Create device from stored identifiers (Home Assistant)
                device = await PixelAirDevice.from_identifiers(
                    mac_address="aa:bb:cc:dd:ee:ff",
                    serial_number="PA-12345",
                    listener=listener
                )

                if device:
                    async with device:
                        state = await device.get_state()
                        print(f"Device: {state.model} - {state.nickname}")
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
        Initialize a PixelAir device.

        NOTE: For external use, prefer the classmethods from_discovered(),
        from_identifiers(), or from_mac_address() instead of direct construction.

        Args:
            ip_address: The IP address of the device.
            listener: The shared UDP listener for network communication.
            serial_number: The device's serial number (required for fallback).
            mac_address: The device's MAC address (required for ARP lookup).
            _internal: Set to True to bypass validation (internal use only).

        Raises:
            ValueError: If serial_number or mac_address is not provided.
        """
        if not _internal:
            if not serial_number:
                raise ValueError(
                    "serial_number is required for device identification. "
                    "Use from_identifiers() or from_discovered() to create devices."
                )
            if not mac_address:
                raise ValueError(
                    "mac_address is required for device identification. "
                    "Use from_identifiers() or from_discovered() to create devices."
                )

        self._ip_address = ip_address
        self._listener = listener
        self._serial_number = serial_number
        self._mac_address = normalize_mac(mac_address) if mac_address else None

        self._logger = logging.getLogger(f"pixelair.device.{serial_number or ip_address}")

        # State management
        self._state = DeviceState(
            serial_number=serial_number,
            ip_address=ip_address,
            mac_address=self._mac_address
        )
        self._routes = ControlRoutes()
        self._raw_state: Optional[PixelAirDeviceFB] = None
        self._state_lock = asyncio.Lock()

        # State change callbacks
        self._state_callbacks: List[StateChangeCallback] = []

        # Packet handling
        self._handler = DevicePacketHandler(self, self._logger)
        self._assembler = PacketAssembler(self._on_state_packet)
        self._registered = False

        # State request waiting
        self._state_events: List[asyncio.Event] = []
        self._state_events_lock = asyncio.Lock()

    @classmethod
    def from_discovered(
        cls,
        discovered: DiscoveredDevice,
        listener: UDPListener
    ) -> "PixelAirDevice":
        """
        Create a device from a discovery result.

        The discovery result must have both serial_number and mac_address.
        Use DiscoveryService.discover_with_info() to get full device info.

        Args:
            discovered: The discovered device information (must have MAC).
            listener: The shared UDP listener.

        Returns:
            A new PixelAirDevice instance.

        Raises:
            ValueError: If the discovered device lacks MAC address.
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
    ) -> Optional["PixelAirDevice"]:
        """
        Create a device from stored MAC and serial number identifiers.

        This is the preferred method for Home Assistant integration. It uses
        a bulletproof resolution strategy:
        1. Try ARP table lookup using MAC address (fast)
        2. If MAC not found, broadcast discovery and find by serial number

        Args:
            mac_address: The device's MAC address.
            serial_number: The device's serial number.
            listener: The shared UDP listener (must be running).
            timeout: Time to wait for device discovery.

        Returns:
            A new PixelAirDevice instance, or None if device not found.

        Raises:
            ValueError: If MAC address format is invalid.
            RuntimeError: If the listener is not running.

        Example:
            ```python
            # In Home Assistant, store both MAC and serial in config
            device = await PixelAirDevice.from_identifiers(
                mac_address=config["mac_address"],
                serial_number=config["serial_number"],
                listener=listener
            )
            ```
        """
        if not listener.is_running:
            raise RuntimeError("UDP listener is not running")

        # Normalize and validate MAC
        try:
            normalized_mac = normalize_mac(mac_address)
        except ValueError as e:
            raise ValueError(f"Invalid MAC address: {mac_address}") from e

        logger = logging.getLogger("pixelair.device")

        # Strategy 1: Try ARP table lookup
        ip_address = await lookup_ip_by_mac(normalized_mac)

        if ip_address:
            logger.debug(
                "Resolved MAC %s to IP %s via ARP table",
                normalized_mac,
                ip_address
            )
            # Verify the device is responding
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
            elif discovered:
                logger.warning(
                    "Device at IP %s has serial %s, expected %s",
                    ip_address,
                    discovered.serial_number,
                    serial_number
                )

        # Strategy 2: Broadcast discovery and find by serial number
        logger.debug(
            "ARP lookup failed for MAC %s, trying broadcast discovery for serial %s",
            normalized_mac,
            serial_number
        )

        from .discovery import DiscoveryService
        discovery = DiscoveryService(listener)
        discovered = await discovery.find_device_by_serial(serial_number, timeout=timeout)

        if discovered:
            logger.info(
                "Found device %s at IP %s via broadcast discovery",
                serial_number,
                discovered.ip_address
            )
            return cls(
                ip_address=discovered.ip_address,
                listener=listener,
                serial_number=serial_number,
                mac_address=normalized_mac,
                _internal=True
            )

        logger.warning(
            "Could not find device MAC=%s serial=%s",
            normalized_mac,
            serial_number
        )
        return None

    @classmethod
    async def from_mac_address(
        cls,
        mac_address: str,
        listener: UDPListener,
        timeout: float = 5.0
    ) -> Optional["PixelAirDevice"]:
        """
        Create a device by resolving its MAC address to IP.

        This method looks up the IP address from the system ARP table,
        then verifies the device responds to discovery requests. The
        serial number is obtained from the discovery response.

        NOTE: For Home Assistant, prefer from_identifiers() which stores
        both MAC and serial number for more reliable identification.

        Args:
            mac_address: The device's MAC address (any common format).
            listener: The shared UDP listener (must be running).
            timeout: Time to wait for device verification.

        Returns:
            A new PixelAirDevice instance, or None if the device
            could not be found or verified.

        Raises:
            ValueError: If the MAC address format is invalid.
            RuntimeError: If the listener is not running.

        Example:
            ```python
            async with UDPListener() as listener:
                # Find device by MAC
                device = await PixelAirDevice.from_mac_address(
                    "aa:bb:cc:dd:ee:ff",
                    listener
                )
                if device:
                    # Store both MAC and serial for future use
                    print(f"MAC: {device.mac_address}")
                    print(f"Serial: {device.serial_number}")
            ```
        """
        if not listener.is_running:
            raise RuntimeError("UDP listener is not running")

        # Normalize and validate MAC
        try:
            normalized_mac = normalize_mac(mac_address)
        except ValueError as e:
            raise ValueError(f"Invalid MAC address: {mac_address}") from e

        logger = logging.getLogger("pixelair.device")

        # Look up IP from ARP table
        ip_address = await lookup_ip_by_mac(normalized_mac)

        if not ip_address:
            # Try warming ARP cache with a broadcast discovery
            from .discovery import DiscoveryService
            discovery = DiscoveryService(listener)
            await discovery._broadcast_discovery()
            await asyncio.sleep(0.5)

            # Try again
            ip_address = await lookup_ip_by_mac(normalized_mac)

        if not ip_address:
            logger.warning(
                "Could not resolve MAC %s to IP address",
                normalized_mac
            )
            return None

        # Verify the device responds and get serial number
        from .discovery import DiscoveryService
        discovery = DiscoveryService(listener)
        discovered = await discovery.verify_device(ip_address, timeout=timeout)

        if not discovered:
            logger.warning(
                "Device at IP %s (MAC %s) did not respond to discovery",
                ip_address,
                normalized_mac
            )
            return None

        logger.info(
            "Found device: MAC=%s serial=%s IP=%s",
            normalized_mac,
            discovered.serial_number,
            ip_address
        )

        return cls(
            ip_address=ip_address,
            listener=listener,
            serial_number=discovered.serial_number,
            mac_address=normalized_mac,
            _internal=True
        )

    async def resolve_ip(self, timeout: float = 5.0) -> bool:
        """
        Re-resolve the device's IP address using bulletproof fallback.

        This method uses a two-stage resolution strategy:
        1. Try ARP table lookup using MAC address (fast)
        2. If MAC not found, broadcast discovery and find by serial number

        This is useful when a device's IP may have changed due to DHCP
        or when the device becomes unreachable.

        Args:
            timeout: Time to wait for broadcast discovery if needed.

        Returns:
            True if the IP was resolved/updated, False if device not found.
        """
        if not self._mac_address:
            self._logger.warning("Cannot resolve IP: no MAC address set")
            return False

        if not self._serial_number:
            self._logger.warning("Cannot resolve IP: no serial number set")
            return False

        if not self._listener.is_running:
            self._logger.warning("Cannot resolve IP: listener not running")
            return False

        # Strategy 1: Try ARP table lookup (fast)
        new_ip = await lookup_ip_by_mac(self._mac_address)

        if new_ip:
            if new_ip != self._ip_address:
                old_ip = self._ip_address
                self._ip_address = new_ip
                self._state.ip_address = new_ip
                self._logger.info(
                    "Updated IP via ARP: %s -> %s (MAC: %s)",
                    old_ip,
                    new_ip,
                    self._mac_address
                )
            return True

        # Strategy 2: Broadcast discovery and find by serial
        self._logger.debug(
            "ARP lookup failed for MAC %s, trying broadcast discovery for serial %s",
            self._mac_address,
            self._serial_number
        )

        from .discovery import DiscoveryService
        discovery = DiscoveryService(self._listener)
        discovered = await discovery.find_device_by_serial(
            self._serial_number,
            timeout=timeout
        )

        if discovered:
            if discovered.ip_address != self._ip_address:
                old_ip = self._ip_address
                self._ip_address = discovered.ip_address
                self._state.ip_address = discovered.ip_address
                self._logger.info(
                    "Updated IP via discovery: %s -> %s (serial: %s)",
                    old_ip,
                    discovered.ip_address,
                    self._serial_number
                )
            return True

        self._logger.warning(
            "Could not resolve IP for device MAC=%s serial=%s",
            self._mac_address,
            self._serial_number
        )
        return False

    async def update_ip_from_mac(self) -> bool:
        """
        Update the device's IP address by looking up its MAC in the ARP table.

        DEPRECATED: Use resolve_ip() instead, which includes fallback to
        serial number discovery.

        This is useful when a device's IP may have changed due to DHCP.
        The device must have a MAC address set.

        Returns:
            True if the IP was updated, False if lookup failed or no MAC set.
        """
        if not self._mac_address:
            self._logger.warning("Cannot update IP: no MAC address set")
            return False

        new_ip = await lookup_ip_by_mac(self._mac_address)
        if new_ip and new_ip != self._ip_address:
            old_ip = self._ip_address
            self._ip_address = new_ip
            self._state.ip_address = new_ip
            self._logger.info(
                "Updated IP address: %s -> %s (MAC: %s)",
                old_ip,
                new_ip,
                self._mac_address
            )
            return True

        return False

    @property
    def mac_address(self) -> Optional[str]:
        """
        Get the device's MAC address.

        Returns:
            The MAC address (normalized format), or None if not known.
        """
        return self._mac_address or self._state.mac_address

    @property
    def ip_address(self) -> str:
        """
        Get the device's IP address.

        Returns:
            The IP address string.
        """
        return self._ip_address

    @property
    def serial_number(self) -> Optional[str]:
        """
        Get the device's serial number.

        Returns:
            The serial number, or None if not known.
        """
        return self._serial_number or self._state.serial_number

    @property
    def state(self) -> DeviceState:
        """
        Get the current device state.

        Returns:
            A copy of the current DeviceState.
        """
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
            scenes=list(self._state.scenes),
            active_scene_index=self._state.active_scene_index,
            manual_animations=list(self._state.manual_animations),
            active_manual_animation_index=self._state.active_manual_animation_index,
            auto_palette=PaletteState(
                hue=self._state.auto_palette.hue,
                saturation=self._state.auto_palette.saturation,
            ),
            scene_palette=PaletteState(
                hue=self._state.scene_palette.hue,
                saturation=self._state.scene_palette.saturation,
            ),
            manual_palette=PaletteState(
                hue=self._state.manual_palette.hue,
                saturation=self._state.manual_palette.saturation,
            ),
        )

    @property
    def is_registered(self) -> bool:
        """
        Check if the device is registered with the listener.

        Returns:
            True if registered and receiving packets.
        """
        return self._registered

    @property
    def raw_state(self) -> Optional[PixelAirDeviceFB]:
        """
        Get the raw FlatBuffer state object.

        This provides access to the full device state including all
        fields not exposed in DeviceState.

        Returns:
            The raw FlatBuffer object, or None if no state received.
        """
        return self._raw_state

    async def register(self) -> None:
        """
        Register this device with the UDP listener.

        After registration, the device will receive state update packets.
        The packet assembler is also started.

        Raises:
            RuntimeError: If already registered or listener not running.
        """
        if self._registered:
            raise RuntimeError("Device is already registered")

        if not self._listener.is_running:
            raise RuntimeError("UDP listener is not running")

        await self._assembler.start()
        self._listener.add_handler(self._handler)
        self._registered = True

        self._logger.info("Device registered: %s", self._ip_address)

    async def unregister(self) -> None:
        """
        Unregister this device from the UDP listener.

        After unregistration, the device will no longer receive packets.
        """
        if not self._registered:
            return

        self._listener.remove_handler(self._handler)
        await self._assembler.stop()
        self._registered = False

        self._logger.info("Device unregistered: %s", self._ip_address)

    def add_state_callback(self, callback: StateChangeCallback) -> None:
        """
        Register a callback for state changes.

        The callback is invoked whenever the device state is updated.

        Args:
            callback: Function to call with (device, new_state).
        """
        if callback not in self._state_callbacks:
            self._state_callbacks.append(callback)

    def remove_state_callback(self, callback: StateChangeCallback) -> bool:
        """
        Remove a state change callback.

        Args:
            callback: The callback to remove.

        Returns:
            True if the callback was removed, False if not found.
        """
        if callback in self._state_callbacks:
            self._state_callbacks.remove(callback)
            return True
        return False

    async def get_state(self, timeout: float = DEFAULT_STATE_TIMEOUT) -> DeviceState:
        """
        Request and wait for the full device state.

        This sends a /getState command to the device and waits for the
        state response. The device will respond with fragmented packets
        containing the full state.

        Args:
            timeout: Maximum time to wait for response in seconds.

        Returns:
            The updated DeviceState.

        Raises:
            asyncio.TimeoutError: If no response within timeout.
            RuntimeError: If device is not registered.
        """
        if not self._registered:
            raise RuntimeError("Device is not registered")

        # Create event to wait for state
        event = asyncio.Event()

        async with self._state_events_lock:
            self._state_events.append(event)

        try:
            # Send getState command
            await self._send_command(GET_STATE_ROUTE)

            # Wait for state response
            await asyncio.wait_for(event.wait(), timeout)

            return self.state

        finally:
            async with self._state_events_lock:
                if event in self._state_events:
                    self._state_events.remove(event)

    async def turn_on(self) -> None:
        """
        Turn on the device display.

        Sends a command to set is_displaying to True.

        Raises:
            RuntimeError: If device is not registered or routes not available.
        """
        await self._set_power(True)

    async def turn_off(self) -> None:
        """
        Turn off the device display.

        Sends a command to set is_displaying to False.

        Raises:
            RuntimeError: If device is not registered or routes not available.
        """
        await self._set_power(False)

    async def _set_power(self, on: bool) -> None:
        """
        Set the device power state.

        Args:
            on: True to turn on, False to turn off.

        Raises:
            RuntimeError: If device is not registered or routes not available.
        """
        if not self._registered:
            raise RuntimeError("Device is not registered")

        if not self._routes.is_displaying:
            raise RuntimeError(
                "Power control route not available. "
                "Call get_state() first to retrieve device routes."
            )

        # OSC boolean is sent as int (1 or 0) based on FlatBuffer schema
        await self._send_command(
            self._routes.is_displaying,
            [1 if on else 0, 0],
            port=DEVICE_CONTROL_PORT
        )

        # Update local state optimistically
        self._state.is_on = on

        self._logger.info("Set power to %s", "ON" if on else "OFF")

    async def set_brightness(self, brightness: float) -> None:
        """
        Set the device brightness.

        Args:
            brightness: Brightness level from 0.0 to 1.0.

        Raises:
            ValueError: If brightness is out of range.
            RuntimeError: If device is not registered or routes not available.
        """
        if not self._registered:
            raise RuntimeError("Device is not registered")

        if not self._routes.brightness:
            raise RuntimeError(
                "Brightness control route not available. "
                "Call get_state() first to retrieve device routes."
            )

        if not 0.0 <= brightness <= 1.0:
            raise ValueError(f"Brightness must be between 0.0 and 1.0, got {brightness}")

        # Round to 2 decimal places
        brightness = round(brightness, 2)

        await self._send_command(
            self._routes.brightness,
            [brightness, 0],
            port=DEVICE_CONTROL_PORT
        )

        # Update local state optimistically
        self._state.brightness = brightness

        self._logger.info("Set brightness to %.0f%%", brightness * 100)

    async def set_mode(self, mode: DeviceMode) -> None:
        """
        Set the device display mode.

        Args:
            mode: The display mode (AUTO, SCENE, or MANUAL).

        Raises:
            RuntimeError: If device is not registered or routes not available.
        """
        if not self._registered:
            raise RuntimeError("Device is not registered")

        if not self._routes.mode:
            raise RuntimeError(
                "Mode control route not available. "
                "Call get_state() first to retrieve device routes."
            )

        await self._send_command(
            self._routes.mode,
            [int(mode), 0],
            port=DEVICE_CONTROL_PORT
        )

        # Update local state optimistically
        self._state.mode = mode

        self._logger.info("Set mode to %s", mode.name)

    async def set_effect(self, effect_id: str) -> None:
        """
        Set the device effect by ID.

        This is the primary method for Home Assistant integration. Effect IDs:
        - "auto": Sets mode to AUTO
        - "scene:{index}": Sets mode to SCENE and selects the scene
        - "manual:{index}": Sets mode to MANUAL and selects the animation

        Args:
            effect_id: The effect ID (from EffectInfo.id).

        Raises:
            ValueError: If the effect ID is not recognized.
            RuntimeError: If device is not registered or routes not available.
        """
        if not self._registered:
            raise RuntimeError("Device is not registered")

        # Handle "auto" effect
        if effect_id == "auto":
            await self.set_mode(DeviceMode.AUTO)
            return

        # Handle "scene:{index}" effects
        if effect_id.startswith("scene:"):
            try:
                scene_index = int(effect_id[6:])
                await self._set_scene(scene_index)
                return
            except ValueError:
                raise ValueError(f"Invalid scene effect ID: {effect_id}")

        # Handle "manual:{index}" effects
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

        This is a convenience method that looks up the effect ID by display name.

        Args:
            display_name: The effect display name (from EffectInfo.display_name).

        Raises:
            ValueError: If the effect name is not recognized.
            RuntimeError: If device is not registered or routes not available.
        """
        for effect in self._state.effects:
            if effect.display_name == display_name:
                await self.set_effect(effect.id)
                return

        raise ValueError(f"Unknown effect: {display_name}")

    async def _set_scene(self, scene_index: int) -> None:
        """
        Set the active scene by index.

        Args:
            scene_index: The scene index to activate.

        Raises:
            RuntimeError: If routes not available.
        """
        if not self._routes.mode:
            raise RuntimeError(
                "Mode control route not available. "
                "Call get_state() first to retrieve device routes."
            )

        if not self._routes.active_scene_index:
            raise RuntimeError(
                "Scene index control route not available. "
                "Call get_state() first to retrieve device routes."
            )

        # First ensure we're in scene mode
        if self._state.mode != DeviceMode.SCENE:
            await self._send_command(
                self._routes.mode,
                [int(DeviceMode.SCENE), 0],
                port=DEVICE_CONTROL_PORT
            )
            self._state.mode = DeviceMode.SCENE

        # Then set the scene index
        await self._send_command(
            self._routes.active_scene_index,
            [scene_index, 0],
            port=DEVICE_CONTROL_PORT
        )

        # Update local state optimistically
        self._state.active_scene_index = scene_index

        self._logger.info("Set scene index to %d", scene_index)

    async def _set_manual_animation(self, animation_index: int) -> None:
        """
        Set the active manual animation by index.

        Args:
            animation_index: The animation index to activate.

        Raises:
            RuntimeError: If routes not available.
        """
        if not self._routes.mode:
            raise RuntimeError(
                "Mode control route not available. "
                "Call get_state() first to retrieve device routes."
            )

        if not self._routes.manual_animation_index:
            raise RuntimeError(
                "Manual animation index control route not available. "
                "Call get_state() first to retrieve device routes."
            )

        # First ensure we're in manual mode
        if self._state.mode != DeviceMode.MANUAL:
            await self._send_command(
                self._routes.mode,
                [int(DeviceMode.MANUAL), 0],
                port=DEVICE_CONTROL_PORT
            )
            self._state.mode = DeviceMode.MANUAL

        # Then set the animation index
        await self._send_command(
            self._routes.manual_animation_index,
            [animation_index, 0],
            port=DEVICE_CONTROL_PORT
        )

        # Update local state optimistically
        self._state.active_manual_animation_index = animation_index

        self._logger.info("Set manual animation index to %d", animation_index)

    @property
    def has_control_routes(self) -> bool:
        """
        Check if control routes are available.

        Control routes are extracted from the device state. Call get_state()
        to populate them.

        Returns:
            True if all control routes are available.
        """
        return all([
            self._routes.is_displaying,
            self._routes.brightness,
            self._routes.mode
        ])

    async def _send_command(
        self,
        route: str,
        params: Optional[List[Any]] = None,
        port: int = DEVICE_COMMAND_PORT
    ) -> None:
        """
        Send an OSC command to the device.

        Args:
            route: The OSC route (e.g., "/getState").
            params: Optional list of parameters.
            port: The port to send to (default: DEVICE_COMMAND_PORT).

        Raises:
            RuntimeError: If listener is not running.
        """
        if not self._listener.is_running:
            raise RuntimeError("UDP listener is not running")

        # Build OSC message
        builder = OscMessageBuilder(route)

        if params:
            for param in params:
                if isinstance(param, int):
                    builder.add_arg(param, "i")
                elif isinstance(param, float):
                    builder.add_arg(param, "f")
                elif isinstance(param, str):
                    builder.add_arg(param, "s")
                elif isinstance(param, bool):
                    builder.add_arg(param, "T" if param else "F")
                else:
                    builder.add_arg(str(param), "s")

        message = builder.build().dgram

        # Send to device
        await self._listener.send_to(
            message,
            self._ip_address,
            port
        )

        self._logger.debug(
            "Sent command %s to %s:%d",
            route,
            self._ip_address,
            port
        )

    async def _on_state_packet(self, payload: bytes) -> None:
        """
        Handle a complete assembled state packet.

        This method is called by the PacketAssembler when all fragments
        have been received and assembled.

        Args:
            payload: The complete FlatBuffer payload.
        """
        try:
            # Decode FlatBuffer
            device_state = PixelAirDeviceFB.GetRootAs(payload)
            self._raw_state = device_state

            # Update simplified state
            async with self._state_lock:
                self._update_state_from_fb(device_state)

            self._logger.debug(
                "State updated: model=%s, nickname=%s, on=%s, brightness=%.1f%%",
                self._state.model,
                self._state.nickname,
                self._state.is_on,
                self._state.brightness * 100
            )

            # Notify state waiters
            async with self._state_events_lock:
                for event in self._state_events:
                    event.set()

            # Invoke callbacks
            await self._invoke_state_callbacks()

        except Exception as e:
            self._logger.exception(
                "Failed to decode state packet: %s",
                e
            )

    def _update_state_from_fb(self, fb: PixelAirDeviceFB) -> None:
        """
        Update DeviceState from FlatBuffer object.

        Args:
            fb: The decoded FlatBuffer state.
        """
        # Basic info
        if fb.SerialNumber():
            self._state.serial_number = fb.SerialNumber().decode("utf-8")

        if fb.Model():
            self._state.model = fb.Model().decode("utf-8")

        if fb.Version():
            self._state.firmware_version = fb.Version().decode("utf-8")

        self._state.rssi = fb.Rssi()

        # Nickname
        if fb.Nickname() and fb.Nickname().Value():
            self._state.nickname = fb.Nickname().Value().decode("utf-8")

        # Network info
        if fb.Network():
            network = fb.Network()
            if network.IpAddress():
                self._state.ip_address = network.IpAddress().decode("utf-8")
            if network.MacAddress():
                self._state.mac_address = network.MacAddress().decode("utf-8")

        # Engine state (power, brightness, mode) and control routes
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

            # Scene mode - extract scenes and active scene index
            if engine.SceneMode():
                scene_mode = engine.SceneMode()

                # Get active scene index route
                if scene_mode.ActiveSceneIndex():
                    active_idx = scene_mode.ActiveSceneIndex()
                    self._state.active_scene_index = active_idx.Value()
                    if active_idx.Route():
                        self._routes.active_scene_index = active_idx.Route().decode("utf-8")

                # Extract all scenes
                self._state.scenes = []
                for i in range(scene_mode.ScenesLength()):
                    scene = scene_mode.Scenes(i)
                    if scene and scene.Label():
                        self._state.scenes.append(SceneInfo(
                            label=scene.Label().decode("utf-8"),
                            index=scene.Index(),
                        ))

            # Manual mode - extract animations and active animation index
            if engine.ManualMode():
                manual_mode = engine.ManualMode()

                # Get active animation index route
                if manual_mode.ActiveAnimationIndex():
                    active_anim = manual_mode.ActiveAnimationIndex()
                    self._state.active_manual_animation_index = active_anim.Value()
                    if active_anim.Route():
                        self._routes.manual_animation_index = active_anim.Route().decode("utf-8")

                # Extract all animation names
                self._state.manual_animations = []
                for i in range(manual_mode.AnimationsLength()):
                    anim = manual_mode.Animations(i)
                    if anim:
                        self._state.manual_animations.append(anim.decode("utf-8"))

                # Extract manual mode palette
                if manual_mode.Palette():
                    self._extract_palette(
                        manual_mode.Palette(),
                        self._state.manual_palette,
                        self._routes.manual_palette
                    )

            # Auto mode - extract palette
            if engine.AutoMode():
                auto_mode = engine.AutoMode()
                if auto_mode.Palette():
                    self._extract_palette(
                        auto_mode.Palette(),
                        self._state.auto_palette,
                        self._routes.auto_palette
                    )

            # Scene mode palette (separate from individual scene palettes)
            if engine.SceneMode():
                scene_mode = engine.SceneMode()
                if scene_mode.Palette():
                    self._extract_palette(
                        scene_mode.Palette(),
                        self._state.scene_palette,
                        self._routes.scene_palette
                    )

    async def _invoke_state_callbacks(self) -> None:
        """
        Invoke all registered state change callbacks.
        """
        state_copy = self.state

        for callback in self._state_callbacks:
            try:
                result = callback(self, state_copy)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                self._logger.exception(
                    "State callback raised exception: %s",
                    e
                )

    async def __aenter__(self) -> "PixelAirDevice":
        """
        Async context manager entry.

        Returns:
            The device after registering.
        """
        await self.register()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Async context manager exit.

        Unregisters the device.
        """
        await self.unregister()

    def __str__(self) -> str:
        """
        Get string representation.

        Returns:
            String describing the device.
        """
        name = self._state.nickname or self._state.model or "Unknown"
        return f"PixelAirDevice({name} @ {self._ip_address})"

    def __repr__(self) -> str:
        """
        Get detailed string representation.

        Returns:
            Detailed string with all identifying info.
        """
        return (
            f"PixelAirDevice("
            f"ip={self._ip_address}, "
            f"serial={self.serial_number}, "
            f"model={self._state.model}, "
            f"registered={self._registered})"
        )
