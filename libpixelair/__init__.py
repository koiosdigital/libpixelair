"""libpixelair - Python client library for PixelAir LED devices.

This library provides async APIs for discovering and controlling PixelAir
devices (Fluora, Monos, etc.) on the local network.

Key Components:
    - UDPListener: Shared UDP listener for receiving device packets
    - DiscoveryService: Device discovery via broadcast
    - PixelAirDevice: Individual device representation and control
    - PacketAssembler: Fragmented packet reassembly

Device Identification:
    Devices are identified by BOTH MAC address AND serial number for
    bulletproof identification. When a device becomes unreachable, the
    library can re-resolve the IP using:
    1. ARP table lookup (fast, uses MAC)
    2. Broadcast discovery (fallback, uses serial number)

Basic Usage:
    ```python
    import asyncio
    from libpixelair import UDPListener, DiscoveryService, PixelAirDevice

    async def main():
        async with UDPListener() as listener:
            # Discover devices with full info (includes MAC address)
            discovery = DiscoveryService(listener)
            devices = await discovery.discover_with_info(timeout=5.0)

            for discovered in devices:
                print(f"Found: {discovered.serial_number}")
                print(f"  MAC: {discovered.mac_address}")
                print(f"  IP: {discovered.ip_address}")

                # Create and register device
                async with PixelAirDevice.from_discovered(discovered, listener) as device:
                    state = await device.get_state()
                    print(f"  Model: {state.model}")
                    print(f"  Power: {'ON' if state.is_on else 'OFF'}")

    asyncio.run(main())
    ```

Home Assistant Integration:
    ```python
    # Store both MAC and serial in config for bulletproof identification
    device = await PixelAirDevice.from_identifiers(
        mac_address=config["mac_address"],
        serial_number=config["serial_number"],
        listener=listener
    )

    if device:
        async with device:
            # Device will auto-resolve IP via ARP or broadcast discovery
            state = await device.get_state()
    ```

Continuous Discovery:
    ```python
    async def on_device_found(discovered):
        print(f"Device found: {discovered.serial_number}")

    async with UDPListener() as listener:
        discovery = DiscoveryService(listener)
        await discovery.start_continuous(on_device_found, interval=30.0)

        await asyncio.sleep(300)
        await discovery.stop_continuous()
    ```
"""

__version__ = "0.3.0"

# Core components
from .arp import (
    ArpEntry,
    get_arp_table,
    lookup_ip_by_mac,
    lookup_mac_by_ip,
    normalize_mac,
    warm_arp_cache,
)
from .device import (
    DEVICE_COMMAND_PORT,
    DEVICE_CONTROL_PORT,
    GET_STATE_ROUTE,
    ControlRoutes,
    DeviceMode,
    DeviceState,
    EffectInfo,
    PaletteRoutes,
    PaletteState,
    PixelAirDevice,
    SceneInfo,
    StateChangeCallback,
)
from .discovery import (
    DISCOVERY_PORT,
    DISCOVERY_ROUTE,
    DiscoveredDevice,
    DiscoveryCallback,
    DiscoveryService,
)
from .packet_assembler import (
    FRAGMENT_HEADER_MARKER,
    FragmentBuffer,
    PacketAssembler,
)
from .udp_listener import (
    PIXELAIR_LISTEN_PORT,
    NetworkInterface,
    PacketHandler,
    UDPListener,
)

__all__ = [
    "DEVICE_COMMAND_PORT",
    "DEVICE_CONTROL_PORT",
    "DISCOVERY_PORT",
    "DISCOVERY_ROUTE",
    "FRAGMENT_HEADER_MARKER",
    "GET_STATE_ROUTE",
    "PIXELAIR_LISTEN_PORT",
    "ArpEntry",
    "ControlRoutes",
    "DeviceMode",
    "DeviceState",
    "DiscoveredDevice",
    "DiscoveryCallback",
    # Discovery
    "DiscoveryService",
    "EffectInfo",
    "FragmentBuffer",
    "NetworkInterface",
    # Packet Assembler
    "PacketAssembler",
    "PacketHandler",
    "PaletteRoutes",
    "PaletteState",
    # Device
    "PixelAirDevice",
    "SceneInfo",
    "StateChangeCallback",
    # UDP Listener
    "UDPListener",
    # Version
    "__version__",
    "get_arp_table",
    # ARP Utilities
    "lookup_ip_by_mac",
    "lookup_mac_by_ip",
    "normalize_mac",
    "warm_arp_cache",
]
