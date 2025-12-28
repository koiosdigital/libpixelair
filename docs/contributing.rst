Contributing
============

Thank you for your interest in contributing to libpixelair! This guide will help
you get started.

Development Setup
-----------------

1. Clone the repository:

   .. code-block:: bash

      git clone https://github.com/Koios/libpixelair.git
      cd libpixelair

2. Install Poetry if you don't have it:

   .. code-block:: bash

      curl -sSL https://install.python-poetry.org | python3 -

3. Install dependencies:

   .. code-block:: bash

      poetry install --with dev,docs

4. Activate the virtual environment:

   .. code-block:: bash

      poetry shell

Code Quality
------------

We use strict type checking and linting. Run these before submitting:

Type Checking
^^^^^^^^^^^^^

.. code-block:: bash

   poetry run mypy libpixelair

Linting
^^^^^^^

.. code-block:: bash

   poetry run ruff check libpixelair
   poetry run ruff format libpixelair

Testing
^^^^^^^

.. code-block:: bash

   poetry run pytest

Documentation
-------------

Build the documentation locally:

.. code-block:: bash

   poetry run python scripts/docs.py build

Or build and serve with live preview:

.. code-block:: bash

   poetry run python scripts/docs.py serve

Type Annotations
----------------

All public APIs must have complete type annotations. We use Python 3.12+
features:

.. code-block:: python

   # Modern union syntax
   def get_value() -> str | None:
       ...

   # Type aliases with 'type' statement
   type Callback = Callable[[int], None] | Callable[[int], Awaitable[None]]

   # Generic collections
   def process(items: list[str]) -> dict[str, int]:
       ...

Docstrings
----------

All public classes, methods, and functions must have Google-style docstrings:

.. code-block:: python

   async def discover(
       self,
       timeout: float = 5.0,
   ) -> list[DiscoveredDevice]:
       """Perform a one-shot discovery scan.

       This method broadcasts discovery messages and waits for responses.

       Args:
           timeout: Time to wait for responses in seconds.

       Returns:
           List of discovered devices.

       Raises:
           RuntimeError: If the UDP listener is not running.

       Example:
           >>> devices = await discovery.discover(timeout=3.0)
           >>> for d in devices:
           ...     print(d.serial_number)
       """

Pull Request Guidelines
-----------------------

1. **Create a branch**: ``git checkout -b feature/my-feature``
2. **Write tests**: Add tests for new functionality
3. **Run checks**: Ensure mypy, ruff, and pytest pass
4. **Update docs**: Add documentation for new features
5. **Write a clear commit message**: Describe what and why
6. **Open a PR**: Describe the changes and link any issues

Commit Message Format
^^^^^^^^^^^^^^^^^^^^^

Use conventional commits:

- ``feat: Add new discovery method``
- ``fix: Handle timeout in state polling``
- ``docs: Update API documentation``
- ``refactor: Simplify packet assembly``
- ``test: Add tests for ARP lookup``
