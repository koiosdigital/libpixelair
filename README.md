# libpixelair

A Python async client library for controlling PixelAir LED devices (Fluora, Monos, etc.) on the local network.

## Features

- **Async/await API** - Built on asyncio for non-blocking operation
- **Device Discovery** - Find devices via UDP broadcast
- **Bulletproof Identification** - MAC + serial number for reliable device tracking
- **Full Control** - Power, brightness, hue, saturation, and effect selection
- **Home Assistant Ready** - Designed for integration with Home Assistant
- **FlatBuffer Protocol** - Efficient binary state serialization
- **Fragmented Packets** - Automatic reassembly of large state payloads

## Installation

```bash
pip install libpixelair
```

Or with Poetry:

```bash
poetry add libpixelair
```

### Dependencies

- Python 3.12+
- `python-osc` - OSC message encoding
- `flatbuffers` - State serialization
- `netifaces` - Network interface discovery

## Quick Start

### Discover Devices

```python
import asyncio
from libpixelair import UDPListener, DiscoveryService

async def main():
    async with UDPListener() as listener:
        discovery = DiscoveryService(listener)

        # Discover all devices with full info (model, MAC, firmware)
        devices = await discovery.discover_with_info(timeout=5.0)

        for device in devices:
            print(f"Found: {device.display_name}")
            print(f"  Serial: {device.serial_number}")
            print(f"  MAC: {device.mac_address}")
            print(f"  IP: {device.ip_address}")
            print(f"  Model: {device.model}")
            print(f"  Firmware: {device.firmware_version}")

asyncio.run(main())
```

### Control a Device

```python
import asyncio
from libpixelair import UDPListener, PixelAirDevice

async def main():
    async with UDPListener() as listener:
        # Connect using MAC + serial (recommended for persistent identification)
        device = await PixelAirDevice.from_identifiers(
            mac_address="aa:bb:cc:dd:ee:ff",
            serial_number="abc123",
            listener=listener,
        )

        if device:
            async with device:
                # Get current state
                state = await device.get_state()
                print(f"Power: {'ON' if state.is_on else 'OFF'}")
                print(f"Brightness: {state.brightness * 100:.0f}%")
                print(f"Effect: {state.current_effect}")

                # Control the device
                await device.turn_on()
                await device.set_brightness(0.75)
                await device.set_hue(0.5)
                await device.set_saturation(0.8)

                # Set effect by ID
                await device.set_effect("auto")  # Auto mode
                await device.set_effect("scene:0")  # First scene
                await device.set_effect("manual:5")  # Manual animation at index 5

asyncio.run(main())
```

## API Reference

### Core Components

#### UDPListener

Shared UDP listener for receiving device packets on port 12345.

```python
async with UDPListener() as listener:
    # listener.interfaces - list of network interfaces
    # listener.is_running - True if active
    pass
```

#### DiscoveryService

Device discovery via UDP broadcast.

```python
discovery = DiscoveryService(listener)

# One-shot discovery
devices = await discovery.discover(timeout=5.0)

# Discovery with full device info (model, MAC, firmware)
devices = await discovery.discover_with_info(timeout=5.0)

# Find device by MAC address
device = await discovery.find_device_by_mac("aa:bb:cc:dd:ee:ff")

# Find device by serial number
device = await discovery.find_device_by_serial("abc123")

# Continuous discovery
async def on_found(device):
    print(f"Found: {device.serial_number}")

await discovery.start_continuous(on_found, interval=30.0)
await discovery.stop_continuous()
```

#### PixelAirDevice

Individual device representation and control.

```python
# Create from discovery result
device = PixelAirDevice.from_discovered(discovered_device, listener)

# Create from stored identifiers (Home Assistant pattern)
device = await PixelAirDevice.from_identifiers(
    mac_address="aa:bb:cc:dd:ee:ff",
    serial_number="abc123",
    listener=listener,
)

async with device:
    # State
    state = await device.get_state()

    # Power control
    await device.turn_on()
    await device.turn_off()

    # Brightness (0.0 - 1.0)
    await device.set_brightness(0.75)

    # Hue and Saturation (0.0 - 1.0)
    # Routes to the current mode's palette (Auto/Scene/Manual)
    await device.set_hue(0.5)
    await device.set_saturation(0.8)

    # Effects
    await device.set_effect("auto")
    await device.set_effect("scene:0")
    await device.set_effect("manual:3")

    # Mode
    await device.set_mode(DeviceMode.AUTO)
    await device.set_mode(DeviceMode.SCENE)
    await device.set_mode(DeviceMode.MANUAL)
```

### Data Classes

#### DeviceState

Current state of a device.

```python
@dataclass
class DeviceState:
    serial_number: str
    model: str              # "Fluora", "Monos", etc.
    nickname: str           # User-assigned name
    firmware_version: str
    is_on: bool
    brightness: float       # 0.0 - 1.0
    hue: float              # 0.0 - 1.0 (from current mode's palette)
    saturation: float       # 0.0 - 1.0 (from current mode's palette)
    mode: DeviceMode        # AUTO, SCENE, or MANUAL
    rssi: int               # WiFi signal strength (dBm)
    effects: List[EffectInfo]        # Available effects
    current_effect: str              # Current effect display name
    current_effect_id: str           # Current effect ID
```

#### EffectInfo

Effect with ID and display name.

```python
@dataclass
class EffectInfo:
    id: str           # "auto", "scene:0", "manual:3"
    display_name: str # "Auto", "Scene: Sunset", "Rainbow"
```

#### DeviceMode

Display mode enumeration.

```python
class DeviceMode(Enum):
    AUTO = 0    # Automatic mode
    SCENE = 1   # Scene mode (user-defined scenes)
    MANUAL = 2  # Manual animation mode
```

### ARP Utilities

For MAC address resolution via system ARP table.

```python
from libpixelair import lookup_ip_by_mac, lookup_mac_by_ip, normalize_mac

# Normalize MAC format
mac = normalize_mac("AA-BB-CC-DD-EE-FF")  # -> "aa:bb:cc:dd:ee:ff"

# Look up IP from MAC
ip = await lookup_ip_by_mac("aa:bb:cc:dd:ee:ff")

# Look up MAC from IP
mac = await lookup_mac_by_ip("192.168.1.100")
```

## Home Assistant Integration

The library is designed for easy Home Assistant integration:

1. **Discovery**: Use `discover_with_info()` to get MAC addresses
2. **Store Identifiers**: Save both MAC and serial number in config
3. **Reconnection**: Use `from_identifiers()` with fallback resolution

```python
# Initial setup - store both identifiers
devices = await discovery.discover_with_info()
config = {
    "mac_address": device.mac_address,
    "serial_number": device.serial_number,
}

# On startup - resolve using fallback strategy
device = await PixelAirDevice.from_identifiers(
    mac_address=config["mac_address"],
    serial_number=config["serial_number"],
    listener=listener,
)
```

### Resolution Strategy

When connecting to a device:

1. **ARP Table Lookup** (fast) - Uses MAC address to find current IP
2. **Broadcast Discovery** (fallback) - Uses serial number if ARP fails

## Examples

See the `examples/` directory:

- `discover_devices.py` - Scan for devices on the network
- `poll_device.py` - Monitor a device's state changes
- `control_device.py` - Interactive device control CLI

## Network Ports

| Port  | Direction        | Purpose                         |
| ----- | ---------------- | ------------------------------- |
| 9090  | Client -> Device | Discovery and getState commands |
| 6767  | Client -> Device | Control commands                |
| 12345 | Device -> Client | Responses                       |

## License

MIT

## Author

Aiden Vigue <aiden.vigue@koiosdigital.net>
