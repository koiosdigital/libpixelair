"""Tests for type definitions and data classes."""

import pytest
from libpixelair import (
    DeviceMode,
    DeviceState,
    SceneInfo,
    EffectInfo,
    PaletteState,
    PaletteRoutes,
    ControlRoutes,
)


class TestDeviceMode:
    """Tests for DeviceMode enum."""

    def test_mode_values(self) -> None:
        """Test that mode values are correct."""
        assert DeviceMode.AUTO == 0
        assert DeviceMode.SCENE == 1
        assert DeviceMode.MANUAL == 2

    def test_mode_names(self) -> None:
        """Test that mode names are correct."""
        assert DeviceMode.AUTO.name == "AUTO"
        assert DeviceMode.SCENE.name == "SCENE"
        assert DeviceMode.MANUAL.name == "MANUAL"


class TestDeviceState:
    """Tests for DeviceState data class."""

    def test_default_values(self) -> None:
        """Test default state values."""
        state = DeviceState()
        assert state.serial_number is None
        assert state.model is None
        assert state.is_on is False
        assert state.brightness == 0.0
        assert state.mode == DeviceMode.SCENE
        assert state.scenes == []
        assert state.manual_animations == []

    def test_post_init_creates_palettes(self) -> None:
        """Test that __post_init__ creates palette instances."""
        state = DeviceState()
        assert state.auto_palette is not None
        assert state.scene_palette is not None
        assert state.manual_palette is not None
        assert isinstance(state.auto_palette, PaletteState)

    def test_hue_property_auto_mode(self) -> None:
        """Test hue property returns correct value in auto mode."""
        state = DeviceState(mode=DeviceMode.AUTO)
        state.auto_palette.hue = 0.5
        assert state.hue == 0.5

    def test_hue_property_scene_mode(self) -> None:
        """Test hue property returns correct value in scene mode."""
        state = DeviceState(mode=DeviceMode.SCENE)
        state.scene_palette.hue = 0.75
        assert state.hue == 0.75

    def test_saturation_property(self) -> None:
        """Test saturation property returns correct value."""
        state = DeviceState(mode=DeviceMode.MANUAL)
        state.manual_palette.saturation = 0.8
        assert state.saturation == 0.8

    def test_effects_list(self) -> None:
        """Test effects property returns correct list."""
        state = DeviceState(
            scenes=[
                SceneInfo(label="Sunset", index=0),
                SceneInfo(label="Ocean", index=1),
            ],
            manual_animations=["generic::Rainbow", "fluora::Fire"],
            model="Fluora",
        )

        effects = state.effects
        assert len(effects) >= 3  # Auto + 2 scenes
        assert effects[0].id == "auto"
        assert effects[0].display_name == "Auto"
        assert effects[1].id == "scene:0"
        assert effects[1].display_name == "Scene: Sunset"

    def test_effect_list_property(self) -> None:
        """Test effect_list returns display names."""
        state = DeviceState(
            scenes=[SceneInfo(label="Sunset", index=0)],
        )
        effect_list = state.effect_list
        assert "Auto" in effect_list
        assert "Scene: Sunset" in effect_list

    def test_current_effect_auto_mode(self) -> None:
        """Test current_effect in auto mode."""
        state = DeviceState(mode=DeviceMode.AUTO)
        assert state.current_effect == "Auto"

    def test_current_effect_scene_mode(self) -> None:
        """Test current_effect in scene mode."""
        state = DeviceState(
            mode=DeviceMode.SCENE,
            scenes=[SceneInfo(label="Sunset", index=0)],
            active_scene_index=0,
        )
        assert state.current_effect == "Scene: Sunset"

    def test_current_effect_id(self) -> None:
        """Test current_effect_id returns correct ID."""
        state = DeviceState(mode=DeviceMode.AUTO)
        assert state.current_effect_id == "auto"

        state.mode = DeviceMode.SCENE
        state.active_scene_index = 2
        assert state.current_effect_id == "scene:2"


class TestSceneInfo:
    """Tests for SceneInfo data class."""

    def test_creation(self) -> None:
        """Test SceneInfo creation."""
        scene = SceneInfo(label="Sunset", index=5)
        assert scene.label == "Sunset"
        assert scene.index == 5


class TestEffectInfo:
    """Tests for EffectInfo data class."""

    def test_creation(self) -> None:
        """Test EffectInfo creation."""
        effect = EffectInfo(id="scene:0", display_name="Scene: Sunset")
        assert effect.id == "scene:0"
        assert effect.display_name == "Scene: Sunset"


class TestPaletteState:
    """Tests for PaletteState data class."""

    def test_defaults(self) -> None:
        """Test default values."""
        palette = PaletteState()
        assert palette.hue == 0.0
        assert palette.saturation == 0.0

    def test_custom_values(self) -> None:
        """Test custom values."""
        palette = PaletteState(hue=0.5, saturation=0.8)
        assert palette.hue == 0.5
        assert palette.saturation == 0.8


class TestControlRoutes:
    """Tests for ControlRoutes data class."""

    def test_defaults(self) -> None:
        """Test default values."""
        routes = ControlRoutes()
        assert routes.brightness is None
        assert routes.is_displaying is None
        assert routes.mode is None

    def test_post_init_creates_palettes(self) -> None:
        """Test that __post_init__ creates palette route instances."""
        routes = ControlRoutes()
        assert routes.auto_palette is not None
        assert routes.scene_palette is not None
        assert routes.manual_palette is not None
        assert isinstance(routes.auto_palette, PaletteRoutes)
