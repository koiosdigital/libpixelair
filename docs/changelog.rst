Changelog
=========

All notable changes to libpixelair are documented here.

Version 0.3.0
-------------

*Released: 2024-12*

**Major Changes**

- Modernized to Python 3.12+ with strict type checking
- Added comprehensive Sphinx documentation
- Added ``mypy`` strict mode compatibility
- Added ``ruff`` linting configuration

**Type System**

- All type hints use modern Python 3.12+ syntax (``X | None`` instead of ``Optional[X]``)
- Added ``type`` statements for type aliases
- All public APIs have complete type annotations
- Added ``py.typed`` marker for PEP 561 compliance

**Performance**

- Optimized packet assembly using ``b"".join()`` instead of concatenation
- Polling loop uses monotonic time for accurate intervals
- Only sleeps remaining time in polling loop

**Documentation**

- Full Sphinx documentation with autodoc
- API reference for all public classes and functions
- Usage guides for discovery, control, and Home Assistant integration
- Contributing guide with code quality requirements

Version 0.2.2
-------------

*Released: 2024-12*

**Changes**

- Split ``device.py`` into public API and internal modules
- Added ``_types.py`` for data classes
- Added ``_internal.py`` for low-level communication

Version 0.2.1
-------------

*Released: 2024-12*

**Changes**

- Added hue and saturation control
- Palette routes per mode (Auto/Scene/Manual)

Version 0.2.0
-------------

*Released: 2024-12*

**Changes**

- Added device control methods (turn_on, turn_off, set_brightness)
- Added effect/scene control
- Fixed control port (6767 instead of 9090)

Version 0.1.0
-------------

*Released: 2024-12*

**Initial Release**

- UDP listener with broadcast support
- Device discovery via broadcast
- FlatBuffer state parsing
- Packet fragment reassembly
- ARP table utilities for MAC resolution
