Usage Guide
===========

This guide covers the basics of using libpixelair to discover and control
PixelAir devices on your network.

Basic Concepts
--------------

libpixelair is built around a few core concepts:

- **UDPListener**: A shared UDP socket for sending and receiving packets
- **DiscoveryService**: Finds devices on the network via broadcast
- **PixelAirDevice**: Represents and controls a single device

All operations are async and should be used with Python's ``asyncio``.

Setting Up the Listener
-----------------------

The :class:`~libpixelair.UDPListener` is the foundation of all communication.
It should be started before any other operations:

.. code-block:: python

   from libpixelair import UDPListener

   async def main():
       # Option 1: Context manager (recommended)
       async with UDPListener() as listener:
           # Use listener here
           pass

       # Option 2: Manual start/stop
       listener = UDPListener()
       await listener.start()
       try:
           # Use listener here
           pass
       finally:
           await listener.stop()

The listener binds to port 12345 on all network interfaces and handles
both sending broadcasts and receiving device responses.

Device Identification
---------------------

For reliable device identification (especially in Home Assistant), devices
are identified by **both** MAC address and serial number:

- **MAC address**: Used for fast ARP table lookups
- **Serial number**: Used as fallback via broadcast discovery

This dual-identification ensures devices can always be found, even if their
IP address changes.

Home Assistant Pattern
----------------------

When building a Home Assistant integration, store both identifiers:

.. code-block:: python

   from libpixelair import UDPListener, PixelAirDevice

   async def async_setup_entry(hass, entry):
       # Stored in config entry
       mac_address = entry.data["mac_address"]
       serial_number = entry.data["serial_number"]

       listener = UDPListener()
       await listener.start()

       # Uses bulletproof fallback resolution
       device = await PixelAirDevice.from_identifiers(
           mac_address=mac_address,
           serial_number=serial_number,
           listener=listener,
       )

       if device is None:
           raise ConfigEntryNotReady("Device not found on network")

       async with device:
           # Register entities...
           pass

Error Handling
--------------

The library uses standard Python exceptions:

- ``RuntimeError``: Listener not running, device not registered
- ``ValueError``: Invalid parameters (brightness out of range, etc.)
- ``TimeoutError``: Device didn't respond in time
- ``OSError``: Network issues (port in use, etc.)

.. code-block:: python

   try:
       state = await device.get_state(timeout=5.0)
   except TimeoutError:
       # Device not responding, try re-resolving IP
       if await device.resolve_ip():
           state = await device.get_state()
       else:
           raise

Logging
-------

The library uses Python's standard logging module with the following loggers:

- ``pixelair.udp_listener``: UDP socket events
- ``pixelair.discovery``: Device discovery
- ``pixelair.device.<serial>``: Per-device operations
- ``pixelair.packet_assembler``: Fragment reassembly

Enable debug logging to troubleshoot issues:

.. code-block:: python

   import logging
   logging.getLogger("pixelair").setLevel(logging.DEBUG)
