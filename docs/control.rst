Device Control
==============

Once you have a :class:`~libpixelair.PixelAirDevice`, you can control all
aspects of the device: power, brightness, color, and effects.

Creating a Device
-----------------

There are three ways to create a device instance:

From Discovery Result
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from libpixelair import UDPListener, DiscoveryService, PixelAirDevice

   async with UDPListener() as listener:
       discovery = DiscoveryService(listener)
       discovered = (await discovery.discover_with_info())[0]

       async with PixelAirDevice.from_discovered(discovered, listener) as device:
           # Control device...
           pass

From Stored Identifiers (Home Assistant)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   device = await PixelAirDevice.from_identifiers(
       mac_address="aa:bb:cc:dd:ee:ff",
       serial_number="PA-12345",
       listener=listener,
   )

From MAC Address Only
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   device = await PixelAirDevice.from_mac_address(
       mac_address="aa:bb:cc:dd:ee:ff",
       listener=listener,
   )

Power Control
-------------

.. code-block:: python

   # Turn on
   await device.turn_on()

   # Turn off
   await device.turn_off()

   # Check current state
   state = await device.get_state()
   print(f"Power: {'ON' if state.is_on else 'OFF'}")

Brightness
----------

Brightness is a float from 0.0 to 1.0:

.. code-block:: python

   # Set to 75%
   await device.set_brightness(0.75)

   # Get current brightness
   state = await device.get_state()
   print(f"Brightness: {state.brightness * 100:.0f}%")

Color (Hue & Saturation)
------------------------

Each mode (Auto, Scene, Manual) has its own palette. The library automatically
routes hue/saturation commands to the correct mode:

.. code-block:: python

   # Set hue (0.0 = red, 0.33 = green, 0.66 = blue, 1.0 = red)
   await device.set_hue(0.5)

   # Set saturation (0.0 = white, 1.0 = fully saturated)
   await device.set_saturation(0.8)

   # Get current values
   state = await device.get_state()
   print(f"Hue: {state.hue}")
   print(f"Saturation: {state.saturation}")

Effects
-------

PixelAir devices support three display modes, exposed as "effects":

- **Auto**: Automatic light changes based on time/conditions
- **Scene**: Pre-defined scenes (Sunset, Ocean, etc.)
- **Manual**: Direct animation control

List Available Effects
^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   state = await device.get_state()

   print("Available effects:")
   for effect in state.effects:
       print(f"  {effect.id}: {effect.display_name}")

   print(f"\nCurrent effect: {state.current_effect}")

Set Effect by ID
^^^^^^^^^^^^^^^^

.. code-block:: python

   # Set to Auto mode
   await device.set_effect("auto")

   # Set to a scene (index from effects list)
   await device.set_effect("scene:2")

   # Set to a manual animation
   await device.set_effect("manual:5")

Set Effect by Name
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   await device.set_effect_by_name("Scene: Sunset")
   await device.set_effect_by_name("Rainbow")

State Polling
-------------

For real-time updates, enable state polling:

.. code-block:: python

   def on_state_change(device, state):
       print(f"State changed: brightness={state.brightness}")

   device.add_state_callback(on_state_change)
   await device.start_polling(interval=2.5)

   # ... later ...
   await device.stop_polling()

The polling is efficient - it only fetches full state when the device's
``state_counter`` changes.

Handling Connection Loss
------------------------

If a device's IP address changes (DHCP), you can re-resolve it:

.. code-block:: python

   try:
       state = await device.get_state()
   except TimeoutError:
       # Device not responding, try to find it again
       success = await device.resolve_ip()
       if success:
           state = await device.get_state()
       else:
           print("Device not found on network")
