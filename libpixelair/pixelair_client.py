"""
PixelAirClient - UDP client for receiving fragmented packets from PixelAir devices.
"""

import socket
import threading
from typing import Dict, Optional
import logging

from .pixelair_device import PixelAirDevice

class PixelAirClient:
    """
    A UDP client for receiving fragmented packets from PixelAir devices.
    """
    
    def __init__(self):
        """
        Initialize the PixelAir client.
        """
        
        self.devices: Dict[str, PixelAirDevice] = {}
        self.devices_lock = threading.RLock()

        # Socket and threading
        self.socket: Optional[socket.socket] = None
        self.running = False
        
        self.listen_thread: Optional[threading.Thread] = None

        # Logging
        self.logger = logging.getLogger("PixelAirClient")
    
    def start(self) -> bool:
        """
        Start the UDP listener and background threads.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self.running:
            self.logger.warning("Client is already running")
            return False
        
        try:
            # Create and bind socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # Set receive buffer size based on MTU
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048 * 16)
            
            self.socket.bind(("0.0.0.0", 12345))
            self.socket.settimeout(1.0)  # 1 second timeout for clean shutdown
            
            self.running = True
            
            # Start listener thread
            self.listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.listen_thread.start()
            
            self.logger.info("PixelAirClient started.")
            return True
            
        except Exception as e:
            self.logger.error("Failed to start client (OSError): %s", e)
            self.running = False
            if self.socket:
                self.socket.close()
                self.socket = None
            return False
    
    def stop(self):
        """Stop the UDP listener and background threads."""
        if not self.running:
            return
        
        self.logger.info("Stopping PixelAirClient...")
        self.running = False
        
        # Close socket
        if self.socket:
            self.socket.close()
            self.socket = None
        
        # Wait for threads to finish
        if self.listen_thread and self.listen_thread.is_alive():
            self.listen_thread.join(timeout=2.0)
    
    def register_device(self, device: PixelAirDevice) -> bool:
        """
        Register a device in the client registry (creates PixelAirDevice instance).
        
        Args:
            ip_address: IP address of the device to register
            
        Returns:
            True if registered successfully, False if already exists
        """
        with self.devices_lock:
            if device.ip_address in self.devices:
                self.logger.warning("Device %s already registered", device.ip_address)
                return False
            self.devices[device.ip_address] = device
            self.logger.debug("Registered PixelAir device: %s", device.ip_address)
            return True
    
    def unregister_device(self, device: PixelAirDevice) -> bool:
        """
        Unregister a device from the client registry.
        
        Args:
            ip_address: IP address of device to unregister
            
        Returns:
            True if unregistered successfully, False if not found
        """
        with self.devices_lock:
            if device.ip_address not in self.devices:
                return False
            
            device = self.devices.pop(device)
            self.logger.debug("Unregistered device: %s", device.ip_address)
            return True

    def get_device(self, ip_address: str) -> Optional[PixelAirDevice]:
        """
        Get a registered device by IP address (synchronous version).
        
        Args:
            ip_address: IP address of the device
            
        Returns:
            PixelAirDevice instance or None if not found
        """
        with self.devices_lock:
            return self.devices.get(ip_address)

    def get_all_devices(self) -> Dict[str, PixelAirDevice]:
        """
        Get all registered devices (synchronous version).
        
        Returns:
            Dictionary of IP addresses to PixelAirDevice instances
        """
        with self.devices_lock:
            return self.devices.copy()

    def _listen_loop(self):
        """Main UDP listening loop."""
        self.logger.info("UDP listener started")
        
        while self.running and self.socket:
            try:
                data, address = self.socket.recvfrom(2048)
                ip_address = address[0]

                with self.devices_lock:
                    device = self.devices.get(ip_address)
                    if not device:
                        self.logger.debug(f"Received packet from unregistered device {ip_address}, ignoring")
                        continue
                    device.handle_packet(data)
            except Exception:
                self.logger.warning("Error in UDP listener loop", exc_info=True)
                continue

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()
