"""UDP Listener Manager for PixelAir devices.

This module provides a reusable async UDP listener that binds to port 12345
on all available network interfaces. It handles incoming packets and routes
them to registered handlers.

The listener is designed to be shared across multiple components (discovery,
device state updates, etc.) and uses asyncio for non-blocking operation.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass

# Default port for receiving PixelAir device responses
PIXELAIR_LISTEN_PORT = 12345


@dataclass(frozen=True)
class NetworkInterface:
    """Represents a network interface with its IP and broadcast addresses.

    Attributes:
        name: The interface name (e.g., 'en0', 'eth0').
        ip_address: The IP address assigned to this interface.
        broadcast_address: The broadcast address for this interface's subnet.
        netmask: The network mask for this interface.
    """
    name: str
    ip_address: str
    broadcast_address: str
    netmask: str


class PacketHandler(ABC):
    """Abstract base class for UDP packet handlers.

    Implementations of this class can be registered with the UDPListener
    to receive incoming packets. Each handler can filter packets based on
    content and source address.
    """

    @abstractmethod
    async def handle_packet(self, data: bytes, source_address: tuple[str, int]) -> bool:
        """Handle an incoming UDP packet.

        Args:
            data: The raw packet data received.
            source_address: Tuple of (ip_address, port) of the sender.

        Returns:
            True if the packet was handled and should not be passed to other
            handlers, False if other handlers should also receive this packet.
        """
        pass


class UDPProtocol(asyncio.DatagramProtocol):
    """Asyncio datagram protocol for handling UDP packets.

    This protocol receives UDP datagrams and dispatches them to
    registered packet handlers.
    """

    def __init__(
        self,
        handlers: list[PacketHandler],
        logger: logging.Logger
    ) -> None:
        """Initialize the UDP protocol.

        Args:
            handlers: List of packet handlers to dispatch received packets to.
            logger: Logger instance for logging events.
        """
        self._handlers = handlers
        self._logger = logger
        self._transport: asyncio.DatagramTransport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def connection_made(self, transport: asyncio.transports.BaseTransport) -> None:
        """Called when the UDP socket is ready.

        Args:
            transport: The transport representing the UDP socket.
        """
        self._transport = transport  # type: ignore[assignment]
        self._loop = asyncio.get_running_loop()
        self._logger.debug("UDP protocol connection established")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Called when a UDP datagram is received.

        Args:
            data: The raw packet data.
            addr: Tuple of (ip_address, port) of the sender.
        """
        if self._loop is not None:
            self._loop.create_task(self._dispatch_packet(data, addr))

    async def _dispatch_packet(self, data: bytes, addr: tuple[str, int]) -> None:
        """Dispatch a packet to all registered handlers.

        Args:
            data: The raw packet data.
            addr: Tuple of (ip_address, port) of the sender.
        """
        for handler in self._handlers:
            try:
                handled = await handler.handle_packet(data, addr)
                if handled:
                    break
            except Exception as e:
                self._logger.exception(
                    "Handler %s raised exception processing packet from %s: %s",
                    handler.__class__.__name__,
                    addr,
                    e
                )

    def error_received(self, exc: Exception) -> None:
        """Called when a send or receive operation raises an OSError.

        Args:
            exc: The exception that was raised.
        """
        self._logger.warning("UDP protocol error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        """Called when the connection is lost or closed.

        Args:
            exc: The exception that caused the connection loss, or None.
        """
        if exc:
            self._logger.warning("UDP protocol connection lost: %s", exc)
        else:
            self._logger.debug("UDP protocol connection closed")


class UDPListener:
    """Async UDP listener for receiving PixelAir device packets.

    This class manages a UDP socket bound to port 12345 on all available
    network interfaces. It provides a clean async interface for registering
    packet handlers and sending broadcast/unicast messages.

    The listener is designed to be reusable and can be shared across multiple
    components that need to receive UDP packets from PixelAir devices.

    Example::

        async def main():
            listener = UDPListener()
            await listener.start()

            # Register a handler
            listener.add_handler(my_handler)

            # Send a broadcast
            await listener.send_broadcast(data, port=9090)

            # Clean up
            await listener.stop()
    """

    def __init__(
        self,
        port: int = PIXELAIR_LISTEN_PORT,
        buffer_size: int = 65535
    ) -> None:
        """Initialize the UDP listener.

        Args:
            port: The UDP port to listen on. Defaults to 12345.
            buffer_size: The receive buffer size. Defaults to max UDP size (65535).
        """
        self._port = port
        self._buffer_size = buffer_size
        self._handlers: list[PacketHandler] = []
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: UDPProtocol | None = None
        self._interfaces: list[NetworkInterface] = []
        self._running = False
        self._logger = logging.getLogger("pixelair.udp_listener")

    @property
    def is_running(self) -> bool:
        """Check if the listener is currently running.

        Returns:
            True if the listener is active and receiving packets.
        """
        return self._running

    @property
    def interfaces(self) -> list[NetworkInterface]:
        """Get the list of discovered network interfaces.

        Returns:
            List of NetworkInterface objects representing available interfaces.
        """
        return self._interfaces.copy()

    @property
    def port(self) -> int:
        """Get the port this listener is bound to.

        Returns:
            The UDP port number.
        """
        return self._port

    def add_handler(self, handler: PacketHandler) -> None:
        """Register a packet handler.

        Handlers are called in the order they are registered. If a handler
        returns True from handle_packet(), subsequent handlers are not called.

        Args:
            handler: The PacketHandler implementation to register.
        """
        if handler not in self._handlers:
            self._handlers.append(handler)
            self._logger.debug("Added packet handler: %s", handler.__class__.__name__)

    def remove_handler(self, handler: PacketHandler) -> bool:
        """Unregister a packet handler.

        Args:
            handler: The PacketHandler to remove.

        Returns:
            True if the handler was removed, False if it wasn't registered.
        """
        if handler in self._handlers:
            self._handlers.remove(handler)
            self._logger.debug("Removed packet handler: %s", handler.__class__.__name__)
            return True
        return False

    async def start(self) -> None:
        """Start the UDP listener.

        This method discovers network interfaces, creates a UDP socket bound
        to port 12345 on all interfaces, and begins receiving packets.

        Raises:
            RuntimeError: If the listener is already running.
            OSError: If the socket cannot be bound (e.g., port in use).
        """
        if self._running:
            raise RuntimeError("UDP listener is already running")

        # Discover network interfaces
        self._interfaces = self._discover_interfaces()
        self._logger.info(
            "Discovered %d network interface(s): %s",
            len(self._interfaces),
            ", ".join(f"{iface.name}={iface.ip_address}" for iface in self._interfaces)
        )

        # Create and bind the UDP socket
        loop = asyncio.get_running_loop()

        # Create a socket with broadcast capability
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # Set receive buffer size to maximum MTU
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self._buffer_size)

        # Bind to all interfaces
        sock.bind(("0.0.0.0", self._port))
        sock.setblocking(False)

        # Create the asyncio transport and protocol
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: UDPProtocol(self._handlers, self._logger),
            sock=sock
        )

        self._running = True
        self._logger.info("UDP listener started on port %d", self._port)

    async def stop(self) -> None:
        """Stop the UDP listener.

        This method closes the UDP socket and stops receiving packets.
        It is safe to call this method multiple times.
        """
        if not self._running:
            return

        self._running = False

        if self._transport:
            self._transport.close()
            self._transport = None

        self._protocol = None
        self._logger.info("UDP listener stopped")

    async def send_to(self, data: bytes, address: str, port: int) -> None:
        """Send a UDP packet to a specific address.

        Args:
            data: The packet data to send.
            address: The destination IP address.
            port: The destination port.

        Raises:
            RuntimeError: If the listener is not running.
        """
        if not self._running or not self._transport:
            raise RuntimeError("UDP listener is not running")

        self._transport.sendto(data, (address, port))
        self._logger.debug("Sent %d bytes to %s:%d", len(data), address, port)

    async def send_broadcast(self, data: bytes, port: int) -> int:
        """Send a UDP broadcast packet on all interfaces.

        This method sends the packet to the broadcast address of each
        discovered network interface.

        Args:
            data: The packet data to send.
            port: The destination port.

        Returns:
            The number of interfaces the broadcast was sent on.

        Raises:
            RuntimeError: If the listener is not running.
        """
        if not self._running or not self._transport:
            raise RuntimeError("UDP listener is not running")

        sent_count = 0
        for iface in self._interfaces:
            try:
                self._transport.sendto(data, (iface.broadcast_address, port))
                self._logger.debug(
                    "Sent broadcast (%d bytes) to %s:%d via %s",
                    len(data),
                    iface.broadcast_address,
                    port,
                    iface.name
                )
                sent_count += 1
            except Exception as e:
                self._logger.warning(
                    "Failed to send broadcast on %s: %s",
                    iface.name,
                    e
                )

        return sent_count

    def _discover_interfaces(self) -> list[NetworkInterface]:
        """Discover available network interfaces with broadcast capability.

        Returns:
            List of NetworkInterface objects for interfaces that support
            broadcast (i.e., not loopback).
        """
        interfaces: list[NetworkInterface] = []

        try:
            import netifaces

            for iface_name in netifaces.interfaces():
                addrs = netifaces.ifaddresses(iface_name)

                # Get IPv4 addresses
                if netifaces.AF_INET in addrs:
                    for addr_info in addrs[netifaces.AF_INET]:
                        ip_addr = addr_info.get("addr")
                        broadcast = addr_info.get("broadcast")
                        netmask = addr_info.get("netmask")

                        # Skip loopback and addresses without broadcast
                        if ip_addr and broadcast and not ip_addr.startswith("127."):
                            interfaces.append(NetworkInterface(
                                name=iface_name,
                                ip_address=ip_addr,
                                broadcast_address=broadcast,
                                netmask=netmask or "255.255.255.0"
                            ))
        except ImportError:
            self._logger.warning(
                "netifaces not available, falling back to socket-based discovery"
            )
            interfaces = self._discover_interfaces_fallback()
        except Exception as e:
            self._logger.warning(
                "Failed to discover interfaces with netifaces: %s, using fallback",
                e
            )
            interfaces = self._discover_interfaces_fallback()

        return interfaces

    def _discover_interfaces_fallback(self) -> list[NetworkInterface]:
        """Fallback interface discovery using socket.

        This method attempts to discover the primary network interface by
        connecting to a well-known address and checking the local endpoint.

        Returns:
            List containing at most one NetworkInterface for the primary interface.
        """
        interfaces: list[NetworkInterface] = []

        try:
            # Try to find primary interface by connecting to Google DNS
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                ip_addr = sock.getsockname()[0]

            # Calculate broadcast address (assume /24 subnet)
            ip_parts = ip_addr.split(".")
            broadcast = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.255"

            interfaces.append(NetworkInterface(
                name="primary",
                ip_address=ip_addr,
                broadcast_address=broadcast,
                netmask="255.255.255.0"
            ))
        except Exception as e:
            self._logger.error("Failed to discover primary interface: %s", e)

        return interfaces

    async def __aenter__(self) -> UDPListener:
        """Async context manager entry.

        Returns:
            The UDPListener instance after starting.
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

        Stops the listener on exit.
        """
        await self.stop()
