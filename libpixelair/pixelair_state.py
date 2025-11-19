"""
PixelAirState - Data class for PixelAir device state information.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PixelAirState:
    """State information for a PixelAir device."""
    nickname: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    brightness: int = 0  # 0-100
    is_on: bool = False  # True if the device is on
