Device Discovery
================

libpixelair provides multiple ways to discover PixelAir devices on your network.

Basic Discovery
---------------

The simplest way to find devices is a one-shot broadcast discovery:

.. code-block:: python

   from libpixelair import UDPListener, DiscoveryService

   async def main():
       async with UDPListener() as listener:
           discovery = DiscoveryService(listener)

           # Basic discovery (serial, IP, state_counter only)
           devices = await discovery.discover(timeout=5.0)

           for device in devices:
               print(f"Found: {device.serial_number} at {device.ip_address}")

Full Device Info
----------------

For Home Assistant integrations, use ``discover_with_info()`` to get complete
device information including MAC address, model, and nickname:

.. code-block:: python

   # Full discovery with device state fetch
   devices = await discovery.discover_with_info(timeout=5.0)

   for device in devices:
       print(f"Name: {device.display_name}")
       print(f"Model: {device.model}")
       print(f"MAC: {device.mac_address}")
       print(f"Firmware: {device.firmware_version}")

Finding Specific Devices
------------------------

Find a device by its MAC address:

.. code-block:: python

   device = await discovery.find_device_by_mac("aa:bb:cc:dd:ee:ff")
   if device:
       print(f"Found device at {device.ip_address}")

Find a device by serial number:

.. code-block:: python

   device = await discovery.find_device_by_serial("PA-12345")

Verify a device at a known IP:

.. code-block:: python

   device = await discovery.verify_device("192.168.1.100")

Continuous Discovery
--------------------

For monitoring device availability, use continuous discovery:

.. code-block:: python

   async def on_device_found(device):
       print(f"Device found: {device.serial_number}")

   # Start background discovery
   await discovery.start_continuous(
       callback=on_device_found,
       interval=30.0,  # Broadcast every 30 seconds
       fetch_full_info=True,  # Get model, MAC, etc.
   )

   # ... later ...
   await discovery.stop_continuous()

MAC Address Resolution
----------------------

The library can resolve MAC addresses to IP addresses using the system ARP table:

.. code-block:: python

   from libpixelair import lookup_ip_by_mac, lookup_mac_by_ip, normalize_mac

   # Normalize MAC to consistent format
   mac = normalize_mac("AA-BB-CC-DD-EE-FF")  # -> "aa:bb:cc:dd:ee:ff"

   # Look up IP from MAC
   ip = await lookup_ip_by_mac(mac)

   # Look up MAC from IP
   mac = await lookup_mac_by_ip("192.168.0.110")

.. note::

   ARP lookups only work for devices that have recently communicated on the
   network. Use ``discovery.discover()`` first to "warm" the ARP cache.

Discovery Protocol
------------------

For reference, here's how the discovery protocol works:

1. Client broadcasts OSC message ``/fluoraDiscovery`` to port 9090
2. Each PixelAir device responds with JSON to client port 12345:
   ``${"serial_number":"abc123","ip_address":"192.168.0.110","state_counter":5}``
3. Client can send ``/getState`` to get full FlatBuffer state (model, MAC, etc.)

The library handles all protocol details automatically.
