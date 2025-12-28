Discovery API
=============

Device discovery and ARP utilities.

DiscoveryService
----------------

.. autoclass:: libpixelair.DiscoveryService
   :members:
   :show-inheritance:

DiscoveredDevice
----------------

.. autoclass:: libpixelair.DiscoveredDevice
   :members:
   :show-inheritance:

ARP Utilities
-------------

.. autofunction:: libpixelair.lookup_ip_by_mac
.. autofunction:: libpixelair.lookup_mac_by_ip
.. autofunction:: libpixelair.normalize_mac
.. autofunction:: libpixelair.get_arp_table

ArpEntry
--------

.. autoclass:: libpixelair.ArpEntry
   :members:
   :show-inheritance:

Constants
---------

.. autodata:: libpixelair.DISCOVERY_PORT
.. autodata:: libpixelair.DISCOVERY_ROUTE

Type Aliases
------------

.. py:data:: libpixelair.DiscoveryCallback

   Type alias for discovery callbacks.

   Can be sync or async: ``Callable[[DiscoveredDevice], None]`` or
   ``Callable[[DiscoveredDevice], Awaitable[None]]``
