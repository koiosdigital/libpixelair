"""Tests for ARP utilities."""

import pytest
from libpixelair import normalize_mac


class TestNormalizeMac:
    """Tests for MAC address normalization."""

    def test_colon_format(self) -> None:
        """Test normalizing colon-separated MAC."""
        assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"

    def test_hyphen_format(self) -> None:
        """Test normalizing hyphen-separated MAC."""
        assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"

    def test_no_separator_format(self) -> None:
        """Test normalizing MAC without separators."""
        assert normalize_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"

    def test_lowercase_input(self) -> None:
        """Test normalizing already lowercase MAC."""
        assert normalize_mac("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"

    def test_mixed_case(self) -> None:
        """Test normalizing mixed case MAC."""
        assert normalize_mac("Aa:Bb:Cc:Dd:Ee:Ff") == "aa:bb:cc:dd:ee:ff"

    def test_invalid_length(self) -> None:
        """Test that invalid length raises ValueError."""
        with pytest.raises(ValueError, match="Invalid MAC address"):
            normalize_mac("AA:BB:CC")

    def test_invalid_characters(self) -> None:
        """Test that invalid characters raise ValueError."""
        with pytest.raises(ValueError, match="Invalid MAC address"):
            normalize_mac("GG:HH:II:JJ:KK:LL")

    def test_empty_string(self) -> None:
        """Test that empty string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid MAC address"):
            normalize_mac("")
