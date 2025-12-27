"""
ARP table utilities for MAC address resolution.

This module provides utilities for looking up IP addresses from MAC addresses
and vice versa using the system's ARP table. This allows Home Assistant to
store only MAC addresses for persistent device identification, resolving
the current IP at runtime.

Note: ARP table lookups only work for devices that have recently communicated
on the network. A device must be "warmed up" by sending it a packet first.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
from dataclasses import dataclass

_logger = logging.getLogger("pixelair.arp")


@dataclass
class ArpEntry:
    """
    Represents an entry in the ARP table.

    Attributes:
        ip_address: The IP address.
        mac_address: The MAC address (normalized to lowercase with colons).
        interface: The network interface name (if available).
        is_permanent: Whether this is a permanent/static entry.
    """
    ip_address: str
    mac_address: str
    interface: str | None = None
    is_permanent: bool = False


def normalize_mac(mac: str) -> str:
    """
    Normalize a MAC address to lowercase with colons.

    Handles various formats:
    - AA:BB:CC:DD:EE:FF
    - AA-BB-CC-DD-EE-FF
    - AABBCCDDEEFF
    - aa:bb:cc:dd:ee:ff

    Args:
        mac: The MAC address in any common format.

    Returns:
        Normalized MAC address (lowercase, colon-separated).

    Raises:
        ValueError: If the MAC address format is invalid.
    """
    # Remove common separators and convert to lowercase
    clean = mac.lower().replace(":", "").replace("-", "").replace(".", "")

    if len(clean) != 12:
        raise ValueError(f"Invalid MAC address: {mac}")

    # Validate hex characters
    if not all(c in "0123456789abcdef" for c in clean):
        raise ValueError(f"Invalid MAC address: {mac}")

    # Format with colons
    return ":".join(clean[i:i+2] for i in range(0, 12, 2))


def _parse_arp_output_darwin(output: str) -> list[ArpEntry]:
    """
    Parse macOS `arp -a` output.

    Example format:
    ? (192.168.0.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]
    ? (192.168.0.110) at 12:34:56:78:9a:bc on en0 ifscope [ethernet]
    """
    entries = []
    # Pattern: hostname (ip) at mac on interface ...
    pattern = re.compile(
        r'\S+\s+\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-fA-F:]+)\s+on\s+(\S+)'
    )

    for line in output.splitlines():
        match = pattern.search(line)
        if match:
            ip_addr = match.group(1)
            mac_addr = match.group(2)
            interface = match.group(3)

            # Skip incomplete entries
            if mac_addr == "(incomplete)":
                continue

            try:
                entries.append(ArpEntry(
                    ip_address=ip_addr,
                    mac_address=normalize_mac(mac_addr),
                    interface=interface,
                    is_permanent="permanent" in line.lower()
                ))
            except ValueError:
                continue

    return entries


def _parse_arp_output_linux(output: str) -> list[ArpEntry]:
    """
    Parse Linux `arp -a` or `ip neigh` output.

    arp -a format:
    hostname (192.168.0.1) at aa:bb:cc:dd:ee:ff [ether] on eth0

    ip neigh format:
    192.168.0.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
    """
    entries = []

    # Try arp -a format first
    arp_pattern = re.compile(
        r'\S+\s+\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-fA-F:]+)\s+.*on\s+(\S+)'
    )

    # ip neigh format
    neigh_pattern = re.compile(
        r'(\d+\.\d+\.\d+\.\d+)\s+dev\s+(\S+)\s+lladdr\s+([0-9a-fA-F:]+)'
    )

    for line in output.splitlines():
        # Try arp -a format
        match = arp_pattern.search(line)
        if match:
            try:
                entries.append(ArpEntry(
                    ip_address=match.group(1),
                    mac_address=normalize_mac(match.group(2)),
                    interface=match.group(3),
                    is_permanent=False
                ))
            except ValueError:
                continue
            continue

        # Try ip neigh format
        match = neigh_pattern.search(line)
        if match:
            try:
                entries.append(ArpEntry(
                    ip_address=match.group(1),
                    mac_address=normalize_mac(match.group(3)),
                    interface=match.group(2),
                    is_permanent=False
                ))
            except ValueError:
                continue

    return entries


async def get_arp_table() -> list[ArpEntry]:
    """
    Get the current system ARP table.

    This function reads the ARP table from the operating system and returns
    a list of entries. Works on macOS and Linux.

    Returns:
        List of ARP table entries.

    Note:
        Only returns entries for devices that have recently communicated.
        To populate an entry, you may need to send a packet to the device first.
    """
    loop = asyncio.get_running_loop()

    def _get_arp_sync() -> list[ArpEntry]:
        try:
            if sys.platform == "darwin":
                result = subprocess.run(
                    ["arp", "-a"],
                    capture_output=True,
                    text=True,
                    timeout=5.0
                )
                if result.returncode == 0:
                    return _parse_arp_output_darwin(result.stdout)
            else:
                # Try ip neigh first (modern Linux)
                try:
                    result = subprocess.run(
                        ["ip", "neigh"],
                        capture_output=True,
                        text=True,
                        timeout=5.0
                    )
                    if result.returncode == 0:
                        return _parse_arp_output_linux(result.stdout)
                except FileNotFoundError:
                    pass

                # Fall back to arp -a
                result = subprocess.run(
                    ["arp", "-a"],
                    capture_output=True,
                    text=True,
                    timeout=5.0
                )
                if result.returncode == 0:
                    return _parse_arp_output_linux(result.stdout)

        except subprocess.TimeoutExpired:
            _logger.warning("ARP command timed out")
        except FileNotFoundError:
            _logger.warning("ARP command not found")
        except Exception as e:
            _logger.exception("Error reading ARP table: %s", e)

        return []

    return await loop.run_in_executor(None, _get_arp_sync)


async def lookup_ip_by_mac(mac_address: str) -> str | None:
    """
    Look up an IP address by MAC address in the ARP table.

    Args:
        mac_address: The MAC address to look up (any common format).

    Returns:
        The IP address if found, None otherwise.

    Note:
        The device must have recently communicated on the network for its
        ARP entry to exist. You may need to send a broadcast first.
    """
    try:
        normalized = normalize_mac(mac_address)
    except ValueError:
        _logger.warning("Invalid MAC address: %s", mac_address)
        return None

    entries = await get_arp_table()
    for entry in entries:
        if entry.mac_address == normalized:
            return entry.ip_address

    return None


async def lookup_mac_by_ip(ip_address: str) -> str | None:
    """
    Look up a MAC address by IP address in the ARP table.

    Args:
        ip_address: The IP address to look up.

    Returns:
        The MAC address if found (normalized format), None otherwise.

    Note:
        The device must have recently communicated on the network for its
        ARP entry to exist. You may need to ping or send a packet first.
    """
    entries = await get_arp_table()
    for entry in entries:
        if entry.ip_address == ip_address:
            return entry.mac_address

    return None


async def warm_arp_cache(ip_address: str) -> bool:
    """
    Attempt to warm the ARP cache for a given IP address.

    Sends a UDP packet to the device to trigger ARP resolution.
    This is useful before looking up a MAC address.

    Args:
        ip_address: The IP address to warm.

    Returns:
        True if the packet was sent (doesn't guarantee ARP entry exists).
    """
    import socket

    loop = asyncio.get_running_loop()

    def _send_packet() -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.1)
            # Send a small packet to an unlikely port
            sock.sendto(b"\x00", (ip_address, 9))
            sock.close()
            return True
        except Exception:
            return False

    return await loop.run_in_executor(None, _send_packet)
