"""Packet Assembler for PixelAir fragmented UDP packets.

PixelAir devices send large state payloads as fragmented UDP packets.
Each fragment has a 4-byte header:
    - Byte 0: Header marker (0x46 = 'F')
    - Byte 1: Counter (groups fragments of the same response, wraps at 255)
    - Byte 2: Total number of fragments in this response
    - Byte 3: Fragment index (0-based)
    - Bytes 4+: Payload data

This module provides the PacketAssembler class that reassembles these
fragments into complete payloads and invokes a callback when ready.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

# Header byte that marks a fragmented packet
FRAGMENT_HEADER_MARKER = 0x46  # 'F'

# Minimum packet size (4 byte header)
MIN_PACKET_SIZE = 4

# Default timeout for incomplete fragment reassembly
DEFAULT_FRAGMENT_TIMEOUT = 2.0


def _get_monotonic_time() -> float:
    """Get monotonic time from the event loop or fallback."""
    try:
        loop = asyncio.get_running_loop()
        return loop.time()
    except RuntimeError:
        # No running loop, use monotonic clock directly
        import time
        return time.monotonic()


@dataclass
class FragmentBuffer:
    """Buffer for collecting fragments of a single response.

    Attributes:
        counter: The counter value grouping these fragments.
        total_fragments: Total number of fragments expected.
        fragments: Dictionary mapping fragment index to payload bytes.
        created_at: Monotonic timestamp when this buffer was created.
    """
    counter: int
    total_fragments: int
    fragments: dict[int, bytes] = field(default_factory=dict)
    created_at: float = field(default_factory=_get_monotonic_time)

    def add_fragment(self, index: int, payload: bytes) -> bool:
        """Add a fragment to this buffer.

        Args:
            index: The fragment index (0-based).
            payload: The fragment payload data.

        Returns:
            True if all fragments have been received after adding this one.
        """
        if index >= self.total_fragments:
            return False
        self.fragments[index] = payload
        return len(self.fragments) == self.total_fragments

    def is_complete(self) -> bool:
        """Check if all fragments have been received.

        Returns:
            True if all fragments are present.
        """
        return len(self.fragments) == self.total_fragments

    def is_expired(self, timeout: float = DEFAULT_FRAGMENT_TIMEOUT) -> bool:
        """Check if this buffer has expired.

        Args:
            timeout: Maximum age in seconds before expiration.

        Returns:
            True if the buffer is older than the timeout.
        """
        return _get_monotonic_time() - self.created_at > timeout

    def assemble(self) -> bytes | None:
        """Assemble all fragments into a complete payload.

        Returns:
            The complete payload bytes if all fragments are present,
            None if any fragments are missing.
        """
        if not self.is_complete():
            return None

        # Use list join for O(n) assembly instead of O(n^2) concatenation
        try:
            parts = [self.fragments[i] for i in range(self.total_fragments)]
            return b"".join(parts)
        except KeyError:
            return None

    @property
    def received_count(self) -> int:
        """Get the number of fragments received so far.

        Returns:
            Count of received fragments.
        """
        return len(self.fragments)


# Type alias for the completion callback
type CompletionCallback = Callable[[bytes], None] | Callable[[bytes], Awaitable[None]]


class PacketAssembler:
    """Assembles fragmented PixelAir packets into complete payloads.

    This class handles the reassembly of fragmented UDP packets from PixelAir
    devices. It maintains buffers for in-progress reassembly and invokes a
    callback when a complete payload is ready.

    The assembler automatically cleans up expired fragment buffers to prevent
    memory leaks from incomplete transmissions.

    Example::

        async def on_complete(payload: bytes):
            print(f"Received complete payload: {len(payload)} bytes")

        assembler = PacketAssembler(on_complete)
        await assembler.start()

        # Process incoming packets
        await assembler.process_packet(data, ("192.168.1.100", 12345))

        # Clean up
        await assembler.stop()
    """

    def __init__(
        self,
        callback: CompletionCallback,
        fragment_timeout: float = DEFAULT_FRAGMENT_TIMEOUT,
        cleanup_interval: float = 1.0
    ) -> None:
        """Initialize the packet assembler.

        Args:
            callback: Function to call when a complete payload is assembled.
                Can be sync or async. Called with the complete payload bytes.
            fragment_timeout: Time in seconds before incomplete fragments expire.
            cleanup_interval: Interval in seconds between cleanup runs.
        """
        self._callback = callback
        self._fragment_timeout = fragment_timeout
        self._cleanup_interval = cleanup_interval

        # Buffers keyed by (source_ip, counter)
        self._buffers: dict[tuple[str, int], FragmentBuffer] = {}
        self._lock = asyncio.Lock()

        self._cleanup_task: asyncio.Task[None] | None = None
        self._running = False

        self._logger = logging.getLogger("pixelair.packet_assembler")

        # Statistics
        self._complete_count = 0
        self._expired_count = 0
        self._invalid_count = 0

    @property
    def pending_count(self) -> int:
        """Get the number of pending fragment buffers.

        Returns:
            Count of buffers awaiting completion.
        """
        return len(self._buffers)

    @property
    def stats(self) -> dict[str, int]:
        """Get assembler statistics.

        Returns:
            Dictionary with counts of complete, expired, and invalid packets.
        """
        return {
            "complete": self._complete_count,
            "expired": self._expired_count,
            "invalid": self._invalid_count,
            "pending": self.pending_count
        }

    async def start(self) -> None:
        """Start the packet assembler.

        This starts the background cleanup task for expired fragments.

        Raises:
            RuntimeError: If the assembler is already running.
        """
        if self._running:
            raise RuntimeError("PacketAssembler is already running")

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._logger.debug("PacketAssembler started")

    async def stop(self) -> None:
        """Stop the packet assembler.

        This stops the cleanup task and clears all pending buffers.
        """
        if not self._running:
            return

        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

        async with self._lock:
            self._buffers.clear()

        self._logger.debug("PacketAssembler stopped")

    async def process_packet(
        self,
        data: bytes,
        source_address: tuple[str, int]
    ) -> bool:
        """Process an incoming UDP packet.

        This method handles both single-fragment and multi-fragment packets.
        When a complete payload is assembled, the callback is invoked.

        Args:
            data: The raw packet data.
            source_address: Tuple of (ip_address, port) of the sender.

        Returns:
            True if a complete payload was assembled from this packet.
        """
        source_ip = source_address[0]

        # Validate packet structure
        if not self._validate_packet(data):
            return False

        # Parse header
        data[0]
        counter = data[1]
        total_fragments = data[2]
        fragment_index = data[3]
        payload = data[4:]

        self._logger.debug(
            "Received fragment from %s: counter=%d, %d/%d, payload=%d bytes",
            source_ip,
            counter,
            fragment_index + 1,
            total_fragments,
            len(payload)
        )

        # Handle single-fragment packet (fast path)
        if total_fragments == 1:
            await self._invoke_callback(payload)
            self._complete_count += 1
            return True

        # Multi-fragment packet - add to buffer
        buffer_key = (source_ip, counter)

        async with self._lock:
            # Get or create buffer
            if buffer_key not in self._buffers:
                self._buffers[buffer_key] = FragmentBuffer(
                    counter=counter,
                    total_fragments=total_fragments
                )

            buffer = self._buffers[buffer_key]

            # Validate consistency
            if buffer.total_fragments != total_fragments:
                # Mismatched fragment count - start fresh
                self._logger.warning(
                    "Fragment count mismatch from %s (counter=%d): expected %d, got %d",
                    source_ip,
                    counter,
                    buffer.total_fragments,
                    total_fragments
                )
                self._buffers[buffer_key] = FragmentBuffer(
                    counter=counter,
                    total_fragments=total_fragments
                )
                buffer = self._buffers[buffer_key]

            # Add fragment
            is_complete = buffer.add_fragment(fragment_index, payload)

            if is_complete:
                complete_payload = buffer.assemble()
                del self._buffers[buffer_key]

                if complete_payload is not None:
                    self._logger.debug(
                        "Assembled complete payload from %s: %d bytes from %d fragments",
                        source_ip,
                        len(complete_payload),
                        total_fragments
                    )
                    # Release lock before callback
                    self._complete_count += 1

        # Invoke callback outside of lock if complete
        if is_complete and complete_payload is not None:
            await self._invoke_callback(complete_payload)
            return True

        return False

    def _validate_packet(self, data: bytes) -> bool:
        """Validate packet structure.

        Args:
            data: The raw packet data.

        Returns:
            True if the packet has a valid header structure.
        """
        if len(data) < MIN_PACKET_SIZE:
            self._logger.debug("Packet too small: %d bytes", len(data))
            self._invalid_count += 1
            return False

        header = data[0]
        if header != FRAGMENT_HEADER_MARKER:
            self._logger.debug("Invalid header marker: 0x%02x", header)
            self._invalid_count += 1
            return False

        total_fragments = data[2]
        fragment_index = data[3]

        if total_fragments == 0:
            self._logger.debug("Total fragments is zero")
            self._invalid_count += 1
            return False

        if fragment_index >= total_fragments:
            self._logger.debug(
                "Fragment index %d >= total %d",
                fragment_index,
                total_fragments
            )
            self._invalid_count += 1
            return False

        return True

    async def _invoke_callback(self, payload: bytes) -> None:
        """Invoke the completion callback.

        Args:
            payload: The complete assembled payload.
        """
        try:
            result = self._callback(payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            self._logger.exception("Callback raised exception: %s", e)

    async def _cleanup_loop(self) -> None:
        """Background task that periodically cleans up expired fragments."""
        while self._running:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.exception("Cleanup loop error: %s", e)

    async def _cleanup_expired(self) -> None:
        """Remove expired fragment buffers."""
        async with self._lock:
            expired_keys = [
                key for key, buffer in self._buffers.items()
                if buffer.is_expired(self._fragment_timeout)
            ]

            for key in expired_keys:
                buffer = self._buffers.pop(key)
                self._expired_count += 1
                self._logger.debug(
                    "Expired incomplete buffer: source=%s, counter=%d, "
                    "received=%d/%d fragments",
                    key[0],
                    key[1],
                    buffer.received_count,
                    buffer.total_fragments
                )

    def reset_stats(self) -> None:
        """Reset the assembler statistics to zero."""
        self._complete_count = 0
        self._expired_count = 0
        self._invalid_count = 0

    async def __aenter__(self) -> PacketAssembler:
        """Async context manager entry.

        Returns:
            The PacketAssembler instance after starting.
        """
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Async context manager exit.

        Stops the assembler on exit.
        """
        await self.stop()
