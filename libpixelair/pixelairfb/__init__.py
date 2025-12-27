"""
FlatBuffer generated classes for PixelAir protocol.

This module sets up the import path so that the generated FlatBuffer
classes can find each other using their absolute imports (e.g.,
'from PixelAir.StringParameter import StringParameter').
"""

import sys
from pathlib import Path

# Add this directory to sys.path so FlatBuffer's absolute imports work
_this_dir = Path(__file__).parent
if str(_this_dir) not in sys.path:
    sys.path.insert(0, str(_this_dir))
