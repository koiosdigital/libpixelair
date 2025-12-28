Type Reference
==============

This page documents all type aliases and callback types used in libpixelair.

Callback Types
--------------

DiscoveryCallback
^^^^^^^^^^^^^^^^^

.. code-block:: python

   type DiscoveryCallback = (
       Callable[[DiscoveredDevice], None]
       | Callable[[DiscoveredDevice], Awaitable[None]]
   )

Callback for device discovery. Can be synchronous or asynchronous.

StateChangeCallback
^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   type StateChangeCallback = (
       Callable[[PixelAirDevice, DeviceState], None]
       | Callable[[PixelAirDevice, DeviceState], Awaitable[None]]
   )

Callback for state changes. Called when device state changes during polling.

CompletionCallback
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   type CompletionCallback = (
       Callable[[bytes], None]
       | Callable[[bytes], Awaitable[None]]
   )

Callback for completed packet assembly.

Data Classes
------------

All data classes use Python's ``@dataclass`` decorator and are immutable
where appropriate.

DeviceState Fields
^^^^^^^^^^^^^^^^^^

==================================  ==============  =====================================
Field                               Type            Description
==================================  ==============  =====================================
``serial_number``                   ``str | None``  Device serial number
``model``                           ``str | None``  Model name (e.g., "Fluora")
``nickname``                        ``str | None``  User-assigned name
``firmware_version``                ``str | None``  Current firmware version
``is_on``                           ``bool``        Display power state
``brightness``                      ``float``       Brightness level (0.0-1.0)
``mode``                            ``DeviceMode``  Current display mode
``rssi``                            ``int``         WiFi signal strength (dBm)
``ip_address``                      ``str | None``  Device IP address
``mac_address``                     ``str | None``  Device MAC address
``scenes``                          ``list``        Available scenes
``active_scene_index``              ``int``         Currently active scene
``manual_animations``               ``list``        Available animations
``active_manual_animation_index``   ``int``         Currently active animation
==================================  ==============  =====================================

Enums
-----

DeviceMode Values
^^^^^^^^^^^^^^^^^

==========  =====  ==========================
Name        Value  Description
==========  =====  ==========================
``AUTO``    0      Automatic light adjustment
``SCENE``   1      Pre-defined scenes
``MANUAL``  2      Direct animation control
==========  =====  ==========================
