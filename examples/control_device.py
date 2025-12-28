#!/usr/bin/env python3
"""
Example: Interactive control of a PixelAir device.

This example demonstrates controlling a PixelAir device:
- Turn on/off the display
- Adjust brightness
- Select effects (Auto, Scenes, Manual animations)

Uses bulletproof device identification with MAC + serial number.
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from libpixelair import (
    UDPListener,
    PixelAirDevice,
    DeviceState,
    EffectInfo,
)

# Configure logging (quiet by default, set to DEBUG for troubleshooting)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("control_device")

# Target device identifiers (from poll_device.py)
DEVICE_MAC = "D8:13:2A:25:2F:AC"
DEVICE_SERIAL = "ac2f25"


def clear_screen() -> None:
    """Clear the terminal screen."""
    print("\033[2J\033[H", end="")


def print_header(device: Optional[PixelAirDevice], state: Optional[DeviceState]) -> None:
    """Print the header with device info."""
    print("=" * 60)
    print("  PixelAir Device Controller")
    print("=" * 60)

    if device and state:
        name = state.nickname or state.model or "Unknown"
        print(f"  Device:     {name}")
        print(f"  Serial:     {state.serial_number}")
        print(f"  IP:         {device.ip_address}")
        print(f"  Firmware:   {state.firmware_version}")
        print("-" * 60)
        print(f"  Power:      {'ON' if state.is_on else 'OFF'}")
        print(f"  Brightness: {state.brightness * 100:.0f}%")
        print(f"  Hue:        {state.hue * 100:.0f}%")
        print(f"  Saturation: {state.saturation * 100:.0f}%")
        print(f"  Effect:     {state.current_effect or 'Unknown'}")
        print(f"  RSSI:       {state.rssi} dBm")
    else:
        print("  Device:     Not connected")
        print(f"  MAC:        {DEVICE_MAC}")
        print(f"  Serial:     {DEVICE_SERIAL}")

    print("=" * 60)


def print_menu(state: Optional[DeviceState] = None) -> None:
    """Print the control menu."""
    print()
    print("  CONTROLS:")
    print("  ---------")
    print("  [1] Turn ON")
    print("  [2] Turn OFF")
    print("  [3] Set Brightness")
    print("  [4] Set Hue")
    print("  [5] Set Saturation")
    print("  [e] Select Effect")
    print()
    print("  [r] Refresh state")
    print("  [q] Quit")
    print()

    # Show available effects if we have state
    if state and state.effects:
        print("  AVAILABLE EFFECTS:")
        print("  ------------------")
        current_id = state.current_effect_id
        for i, effect in enumerate(state.effects):
            marker = " *" if effect.id == current_id else ""
            print(f"  {i + 1:2}. {effect.display_name}{marker}")
        print()


async def get_float_input(prompt: str, min_val: float = 0, max_val: float = 100) -> Optional[float]:
    """
    Get a float input from user.

    Args:
        prompt: The prompt to display.
        min_val: Minimum allowed value.
        max_val: Maximum allowed value.

    Returns:
        Float value (0.0-1.0) or None if cancelled.
    """
    print()
    print(f"  {prompt} ({min_val:.0f}-{max_val:.0f}) or 'c' to cancel: ", end="", flush=True)

    # Read input asynchronously
    loop = asyncio.get_event_loop()
    try:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        line = line.strip().lower()

        if line == "c" or line == "":
            return None

        value = float(line)
        if value < min_val or value > max_val:
            print(f"  Error: Value must be between {min_val:.0f} and {max_val:.0f}")
            return None

        return value / 100.0

    except ValueError:
        print("  Error: Invalid number")
        return None


async def get_brightness_input() -> Optional[float]:
    """
    Get brightness input from user.

    Returns:
        Brightness value (0.0-1.0) or None if cancelled.
    """
    return await get_float_input("Enter brightness")


async def get_hue_input() -> Optional[float]:
    """
    Get hue input from user.

    Returns:
        Hue value (0.0-1.0) or None if cancelled.
    """
    return await get_float_input("Enter hue")


async def get_saturation_input() -> Optional[float]:
    """
    Get saturation input from user.

    Returns:
        Saturation value (0.0-1.0) or None if cancelled.
    """
    return await get_float_input("Enter saturation")


async def get_effect_input(state: DeviceState) -> Optional[EffectInfo]:
    """
    Get effect selection from user.

    Args:
        state: Current device state with effects.

    Returns:
        EffectInfo object or None if cancelled.
    """
    effects = state.effects
    if not effects:
        print("  No effects available")
        return None

    print()
    print(
        "  Enter effect number (1-{}) or 'c' to cancel: ".format(len(effects)), end="", flush=True
    )

    # Read input asynchronously
    loop = asyncio.get_event_loop()
    try:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        line = line.strip().lower()

        if line == "c" or line == "":
            return None

        index = int(line) - 1  # Convert to 0-based
        if index < 0 or index >= len(effects):
            print(f"  Error: Must be between 1 and {len(effects)}")
            return None

        return effects[index]

    except ValueError:
        print("  Error: Invalid number")
        return None


async def handle_input(
    key: str,
    device: PixelAirDevice,
    state: DeviceState,
) -> tuple[bool, Optional[DeviceState]]:
    """
    Handle a menu input.

    Args:
        key: The key pressed.
        device: The device to control.
        state: Current device state.

    Returns:
        Tuple of (should_quit, new_state_or_none).
    """
    try:
        if key == "1":
            print("\n  Turning ON...", end="", flush=True)
            await device.turn_on()
            print(" Done!")
            return False, await device.get_state()

        elif key == "2":
            print("\n  Turning OFF...", end="", flush=True)
            await device.turn_off()
            print(" Done!")
            return False, await device.get_state()

        elif key == "3":
            brightness = await get_brightness_input()
            if brightness is not None:
                print(f"  Setting brightness to {brightness * 100:.0f}%...", end="", flush=True)
                await device.set_brightness(brightness)
                print(" Done!")
                return False, await device.get_state()
            return False, None

        elif key == "4":
            hue = await get_hue_input()
            if hue is not None:
                print(f"  Setting hue to {hue * 100:.0f}%...", end="", flush=True)
                await device.set_hue(hue)
                print(" Done!")
                return False, await device.get_state()
            return False, None

        elif key == "5":
            saturation = await get_saturation_input()
            if saturation is not None:
                print(f"  Setting saturation to {saturation * 100:.0f}%...", end="", flush=True)
                await device.set_saturation(saturation)
                print(" Done!")
                return False, await device.get_state()
            return False, None

        elif key == "e":
            effect = await get_effect_input(state)
            if effect is not None:
                print(f"  Setting effect to '{effect.display_name}'...", end="", flush=True)
                await device.set_effect(effect.id)
                print(" Done!")
                return False, await device.get_state()
            return False, None

        elif key == "r":
            print("\n  Refreshing state...", end="", flush=True)
            new_state = await device.get_state()
            print(" Done!")
            return False, new_state

        elif key == "q":
            return True, None

    except RuntimeError as e:
        print(f"\n  Error: {e}")
        await asyncio.sleep(1)

    except asyncio.TimeoutError:
        print("\n  Error: Device did not respond (timeout)")
        await asyncio.sleep(1)

    except ValueError as e:
        print(f"\n  Error: {e}")
        await asyncio.sleep(1)

    return False, None


async def main_loop(device: PixelAirDevice) -> None:
    """
    Main interactive control loop.

    Args:
        device: The connected device.
    """
    # Get initial state
    print("Fetching device state...")
    state = await device.get_state()

    if not device.has_control_routes:
        print("Warning: Control routes not available. Some commands may fail.")

    loop = asyncio.get_event_loop()

    while True:
        clear_screen()
        print_header(device, state)
        print_menu(state)
        print("  > ", end="", flush=True)

        # Read single character (with timeout for responsiveness)
        try:
            line = await asyncio.wait_for(
                loop.run_in_executor(None, sys.stdin.readline),
                timeout=60.0,
            )
            key = line.strip().lower()

            if not key:
                continue

            should_quit, new_state = await handle_input(key, device, state)

            if should_quit:
                print("\n  Goodbye!")
                break

            if new_state:
                state = new_state

        except asyncio.TimeoutError:
            # Refresh state periodically
            try:
                state = await device.get_state()
            except (asyncio.TimeoutError, RuntimeError):
                pass


async def main() -> None:
    """Main entry point."""
    clear_screen()
    print("=" * 60)
    print("  PixelAir Device Controller")
    print("=" * 60)
    print(f"\n  Connecting to device...")
    print(f"  MAC:    {DEVICE_MAC}")
    print(f"  Serial: {DEVICE_SERIAL}")
    print()

    # Handle Ctrl+C gracefully
    def signal_handler() -> None:
        print("\n\n  Interrupted. Exiting...")
        sys.exit(0)

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, signal_handler)
    loop.add_signal_handler(signal.SIGTERM, signal_handler)

    try:
        async with UDPListener() as listener:
            # Connect to device using bulletproof identification
            device = await PixelAirDevice.from_identifiers(
                mac_address=DEVICE_MAC,
                serial_number=DEVICE_SERIAL,
                listener=listener,
                timeout=10.0,
            )

            if not device:
                print("  ERROR: Could not find device on the network.")
                print()
                print("  Troubleshooting:")
                print("  - Check that the device is powered on")
                print("  - Check that you're on the same network")
                print("  - Try running discover_devices.py first")
                print()
                return

            print(f"  Found device at {device.ip_address}")
            print()

            async with device:
                await main_loop(device)

    except Exception as e:
        logger.exception("Unexpected error")
        print(f"\n  Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
