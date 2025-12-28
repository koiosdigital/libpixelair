libpixelair Documentation
=========================

**libpixelair** is a Python client library for PixelAir LED devices (Fluora, Monos lamps)
from Light+Color. It provides async APIs for device discovery and control, designed for
integration with Home Assistant and other automation platforms.

.. note::

   This library requires Python 3.12+ and uses modern Python features including
   the ``type`` statement for type aliases and union syntax (``X | None``).

Features
--------

- **Async-first design** - Built on asyncio for efficient, non-blocking I/O
- **Bulletproof device identification** - Uses both MAC address and serial number
- **Full type annotations** - Strict mypy-compatible typing throughout
- **Home Assistant ready** - Designed for high integration quality scores
- **Comprehensive control** - Power, brightness, hue, saturation, effects/scenes

Quick Start
-----------

.. code-block:: python

   import asyncio
   from libpixelair import UDPListener, DiscoveryService, PixelAirDevice

   async def main():
       async with UDPListener() as listener:
           # Discover devices with full info (includes MAC address)
           discovery = DiscoveryService(listener)
           devices = await discovery.discover_with_info(timeout=5.0)

           for discovered in devices:
               print(f"Found: {discovered.display_name}")
               print(f"  MAC: {discovered.mac_address}")
               print(f"  Model: {discovered.model}")

               # Control the device
               async with PixelAirDevice.from_discovered(discovered, listener) as device:
                   state = await device.get_state()
                   print(f"  Power: {'ON' if state.is_on else 'OFF'}")

                   await device.turn_on()
                   await device.set_brightness(0.75)

   asyncio.run(main())

Installation
------------

Install from PyPI:

.. code-block:: bash

   pip install libpixelair

Or with Poetry:

.. code-block:: bash

   poetry add libpixelair

Contents
--------

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   usage
   discovery
   control

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/device
   api/discovery
   api/listener
   api/types

.. toctree::
   :maxdepth: 1
   :caption: Development

   contributing
   changelog

Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
