"""PixelAir device data types.

This module contains all data classes and enums used to represent device state
and configuration. These are the public types exposed by the library.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class DeviceMode(IntEnum):
    """Device display mode."""
    AUTO = 0
    SCENE = 1
    MANUAL = 2


@dataclass
class SceneInfo:
    """Information about a scene available on the device.

    Attributes:
        label: The scene name (e.g., "Sunset", "Ocean").
        index: The scene index used for selection.
    """
    label: str
    index: int


@dataclass
class EffectInfo:
    """Information about an available effect.

    Effects are presented to Home Assistant and users. They abstract away
    the underlying mode (Auto/Scene/Manual) and provide a clean interface.

    Attributes:
        id: Unique identifier for this effect (used when setting).
        display_name: Human-readable name shown to users.
    """
    id: str
    display_name: str


@dataclass
class PaletteState:
    """Palette (hue/saturation) state for a mode.

    Values are floats from 0.0 to 1.0.
    """
    hue: float = 0.0
    saturation: float = 0.0


@dataclass
class PaletteRoutes:
    """OSC routes for palette (hue/saturation) control within a mode.

    Each mode (Auto, Scene, Manual) has its own palette with separate routes.
    """
    hue: str | None = None
    saturation: str | None = None


@dataclass
class ControlRoutes:
    """OSC routes for controlling device parameters.

    These routes are extracted from the device's FlatBuffer state and are
    used to send control commands. Routes are obfuscated strings that are
    unique per device/firmware.
    """
    brightness: str | None = None
    is_displaying: str | None = None
    mode: str | None = None
    active_scene_index: str | None = None
    manual_animation_index: str | None = None
    auto_palette: PaletteRoutes | None = None
    scene_palette: PaletteRoutes | None = None
    manual_palette: PaletteRoutes | None = None

    def __post_init__(self) -> None:
        """Initialize nested dataclass defaults."""
        if self.auto_palette is None:
            self.auto_palette = PaletteRoutes()
        if self.scene_palette is None:
            self.scene_palette = PaletteRoutes()
        if self.manual_palette is None:
            self.manual_palette = PaletteRoutes()


# Animation prefix to model mapping
ANIMATION_MODEL_PREFIXES = {
    "fluora": ["generic", "fluora", "fluora/audio"],
    "monos": ["generic", "monos"],
}


def _get_animation_display_name(animation_id: str) -> str:
    """Extract the display name from an animation ID.

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


def _is_animation_compatible(animation_id: str, model: str | None) -> bool:
    """Check if an animation is compatible with a device model.

    Args:
        animation_id: The animation ID (with prefix).
        model: The device model name (e.g., "Fluora", "Monos").

    Returns:
        True if the animation is compatible with this model.
    """
    if not model:
        return True

    if "::" not in animation_id:
        return True

    prefix = animation_id.split("::", 1)[0].lower()

    model_lower = model.lower()
    for model_key, allowed_prefixes in ANIMATION_MODEL_PREFIXES.items():
        if model_key in model_lower:
            return prefix in allowed_prefixes

    return prefix == "generic"


@dataclass
class DeviceState:
    """Represents the current state of a PixelAir device.

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
        active_manual_animation_index: Currently active animation index.
        auto_palette: Palette state for Auto mode.
        scene_palette: Palette state for Scene mode.
        manual_palette: Palette state for Manual mode.
    """
    serial_number: str | None = None
    model: str | None = None
    nickname: str | None = None
    firmware_version: str | None = None
    is_on: bool = False
    brightness: float = 0.0
    mode: DeviceMode = DeviceMode.SCENE
    rssi: int = 0
    ip_address: str | None = None
    mac_address: str | None = None
    scenes: list[SceneInfo] | None = None
    active_scene_index: int = 0
    manual_animations: list[str] | None = None
    active_manual_animation_index: int = 0
    auto_palette: PaletteState | None = None
    scene_palette: PaletteState | None = None
    manual_palette: PaletteState | None = None

    def __post_init__(self) -> None:
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

    def _get_palette(self) -> PaletteState:
        """Get the palette for the current mode (guaranteed non-None after __post_init__)."""
        if self.mode == DeviceMode.AUTO and self.auto_palette:
            return self.auto_palette
        elif self.mode == DeviceMode.SCENE and self.scene_palette:
            return self.scene_palette
        elif self.mode == DeviceMode.MANUAL and self.manual_palette:
            return self.manual_palette
        # Fallback (should not happen after __post_init__)
        return PaletteState()

    @property
    def hue(self) -> float:
        """Get the current hue value based on the active mode."""
        return self._get_palette().hue

    @property
    def saturation(self) -> float:
        """Get the current saturation value based on the active mode."""
        return self._get_palette().saturation

    @property
    def effects(self) -> list[EffectInfo]:
        """Get the list of available effects with IDs and display names.

        Returns:
            List of EffectInfo objects.
        """
        result = [EffectInfo(id="auto", display_name="Auto")]

        scenes = self.scenes or []
        for scene in scenes:
            result.append(EffectInfo(
                id=f"scene:{scene.index}",
                display_name=f"Scene: {scene.label}",
            ))

        animations = self.manual_animations or []
        for i, anim_id in enumerate(animations):
            if _is_animation_compatible(anim_id, self.model):
                result.append(EffectInfo(
                    id=f"manual:{i}",
                    display_name=_get_animation_display_name(anim_id),
                ))

        return result

    @property
    def effect_list(self) -> list[str]:
        """Get the list of effect display names."""
        return [e.display_name for e in self.effects]

    @property
    def current_effect(self) -> str | None:
        """Get the display name of the currently active effect."""
        if self.mode == DeviceMode.AUTO:
            return "Auto"
        elif self.mode == DeviceMode.SCENE:
            scenes = self.scenes or []
            for scene in scenes:
                if scene.index == self.active_scene_index:
                    return f"Scene: {scene.label}"
            return None
        elif self.mode == DeviceMode.MANUAL:
            animations = self.manual_animations or []
            if 0 <= self.active_manual_animation_index < len(animations):
                anim_id = animations[self.active_manual_animation_index]
                return _get_animation_display_name(anim_id)
            return None
        return None

    @property
    def current_effect_id(self) -> str | None:
        """Get the ID of the currently active effect."""
        if self.mode == DeviceMode.AUTO:
            return "auto"
        elif self.mode == DeviceMode.SCENE:
            return f"scene:{self.active_scene_index}"
        elif self.mode == DeviceMode.MANUAL:
            return f"manual:{self.active_manual_animation_index}"
        return None
