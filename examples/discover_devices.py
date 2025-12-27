#!/usr/bin/env python3
"""
Example: Discover PixelAir devices on the local network.

This example demonstrates how to use the libpixelair discovery service
to find PixelAir devices and retrieve their full state.
"""

import asyncio
import logging

from libpixelair import (
    UDPListener,
    DiscoveryService,
    PixelAirDevice,
    DiscoveredDevice,
)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)

# Reduce noise from the library internals
logging.getLogger("pixelair").setLevel(logging.DEBUG)


async def main():
    """Main entry point."""
    print("=" * 60)
    print("PixelAir Device Discovery")
    print("=" * 60)

    # Start the UDP listener
    async with UDPListener() as listener:
        print(f"\nListening on port {listener.port}")
        print(f"Found {len(listener.interfaces)} network interface(s):")
        for iface in listener.interfaces:
            print(f"  - {iface.name}: {iface.ip_address} (broadcast: {iface.broadcast_address})")

        # Create discovery service
        discovery = DiscoveryService(listener)

        print("\nBroadcasting discovery packets...")
        print("Waiting 5 seconds for device responses...\n")

        # Perform one-shot discovery
        devices = await discovery.discover(
            timeout=5.0,
            broadcast_count=3,
            broadcast_interval=1.0
        )

        if not devices:
            print("No devices found.")
            return

        print(f"Found {len(devices)} device(s):\n")

        for discovered in devices:
            print(f"Device: {discovered.serial_number}")
            print(f"  IP Address: {discovered.ip_address}")
            print(f"  State Counter: {discovered.state_counter}")

            # Create device and get full state
            device = PixelAirDevice.from_discovered(discovered, listener)

            try:
                async with device:
                    print("  Requesting full state...")
                    state = await device.get_state(timeout=10.0)

                    print(f"  Model: {state.model}")
                    print(f"  Nickname: {state.nickname}")
                    print(f"  Firmware: {state.firmware_version}")
                    print(f"  Power: {'ON' if state.is_on else 'OFF'}")
                    print(f"  Brightness: {state.brightness * 100:.0f}%")
                    print(f"  RSSI: {state.rssi} dBm")
                    print(f"  MAC: {state.mac_address}")

            except asyncio.TimeoutError:
                print("  (Failed to get full state - timeout)")
            except Exception as e:
                print(f"  (Error getting state: {e})")

            print()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
