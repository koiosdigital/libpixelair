"""
PixelAirDevice - Individual device management and OSC control.

This module provides the PixelAirDevice class that handles individual device state,
packet decoding, and OSC command sending.
"""

import asyncio
import socket
import time
import logging

from typing import Dict, Optional, List, Union, Any
from pythonosc.osc_message_builder import OscMessageBuilder

from .pixelair_generated import PixelAirDevice as PixelAirDeviceFB
from .fragmented_state_manager import FragmentedStateManager
from .pixelair_state import PixelAirState

class PixelAirDevice:
    """
    A PixelAir device.
    """
    
    def __init__(self, ip_address: str):
        """
        Initialize a PixelAir device.
        
        Args:
            ip_address: IP address of the device (used as the device identifier)
        """
        self.ip_address = ip_address
        self.last_seen = 0.0
        self.name: Optional[str] = None
        self.fragment_manager = FragmentedStateManager(self._complete_packet_handler)
        
        # Device state tracking
        self.last_decoded_state: Optional[PixelAirDeviceFB] = None
        self.state = PixelAirState()
        
        # State packet waiting infrastructure
        self._state_waiters: List[asyncio.Event] = []
        self._state_waiters_lock: Optional[asyncio.Lock] = None
        
        # Logger for this device
        self.logger = logging.getLogger(f"PixelAirDevice:{self.ip_address}")
    
    def _ensure_async_lock(self):
        """Ensure the async lock exists (create it lazily)."""
        if self._state_waiters_lock is None:
            self._state_waiters_lock = asyncio.Lock()
    
    async def set_power(self, on: bool) -> bool:
        """
        Set the power state of the device.
        
        Args:
            on: True to turn on, False to turn off
        
        Returns:
            bool: True if command was sent successfully, False otherwise
        """
        route = "/SyYOTiXjQBjW"
        params = [1 if on else 0]
        await self._send_osc_message(route, params)
        await self.get_state()  # Request updated state after changing power
        return True
    
    async def set_brightness(self, brightness: int) -> bool:
        """
        Set the brightness of the device.
        
        Args:
            brightness: Brightness level (0-100)
        
        Returns:
            bool: True if command was sent successfully, False otherwise
        """
        if brightness < 0 or brightness > 100:
            self.logger.error("Brightness must be between 0 and 100")
            return False
        
        route = "/Uv7aMFw5P2lX"
        params = [float(brightness) / 100.0]
        await self._send_osc_message(route, params)
        await self.get_state()  # Request updated state after changing brightness
        return True

    async def get_state(self, timeout: float = 5.0) -> bool:
        """
        Request the current state from the device.
        
        Returns:
            bool: True if command was sent successfully, False otherwise
        """
        route = "/fluoraDiscovery"
        ip = self._get_recv_ip()
        #convert to list of ascii chars
        ip_chars = [ord(c) for c in ip]
        
        return await self._send_osc_and_wait_for_state(route, ip_chars, port=9090)
    
    def is_stale(self, timeout: float = 300.0) -> bool:
        """
        Check if this device hasn't been seen recently.
        
        Args:
            timeout: Timeout in seconds (default 5 minutes)
            
        Returns:
            True if device hasn't been seen within timeout period
        """
        return time.time() - self.last_seen > timeout

    def get_device_info(self) -> Optional[Dict[str, Any]]:
        """
        Get device information dictionary.
        
        Returns:
            Dictionary with device information or None if no state available
        """
        if self.last_decoded_state is None:
            return None
        
        return {
            "ip_address": self.ip_address,
            "model": self.state.model,
            "serial_number": self.state.serial_number,
            "nickname": self.state.nickname,
            "is_on": self.state.is_on,
            "brightness": self.state.brightness,
            "last_seen": self.last_seen
        }
    
    def handle_packet(self, payload: bytes):
        """
        Handle a complete defragmented packet from this device.
        
        This method decodes the payload as a PixelAir FlatBuffer and calls
        the appropriate handler method.
        
        Args:
            payload: Complete defragmented payload
            is_response: True if this is a response to a request, False if proactive
        """
        self._update_last_seen()
        self.fragment_manager.process_buffer(payload)
            
    async def _send_osc_message(self, route: str, params: List[Union[int, float, str, bool]] = None, port: int = 6767) -> bool:
        """
        Send a binary OSC message to the device on port 6767.
        
        This is a private helper method for sending OSC commands to the device.
        
        Args:
            route: OSC route/address (e.g., "/device/brightness")
            params: Optional list of parameters to include in the message
        
        Returns:
            bool: True if message was sent successfully, False otherwise
        """
        if params is None:
            params = []
        
        try:
            # Build the OSC message
            msg_builder = OscMessageBuilder(route)
            
            # Add parameters to the message
            for param in params:
                if isinstance(param, int):
                    msg_builder.add_arg(param, 'i')  # integer
                elif isinstance(param, float):
                    msg_builder.add_arg(param, 'f')  # float
                elif isinstance(param, str):
                    msg_builder.add_arg(param, 's')  # string
                elif isinstance(param, bool):
                    msg_builder.add_arg(param, 'T' if param else 'F')  # true/false
                else:
                    # Try to convert to string as fallback
                    msg_builder.add_arg(str(param), 's')
            
            # Build the binary message
            osc_message = msg_builder.build()
            
            # Send the message asynchronously
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._send_udp_message, osc_message.dgram, port)
                
            self.logger.debug(f"Sent OSC message to {self.ip_address}:6767 - Route: {route}, Params: {params}")
            return True
            
        except Exception as e:
            return False
        
    def _get_recv_ip(self) -> str:
        """
        Fallback method to get local IP by attempting to connect to the device.
        
        Returns:
            Local IP address that would be used to connect to the device
        """
        try:
            # Create a socket and connect to the device to see which local IP is used
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                # Connect to the device IP (doesn't actually send data for UDP)
                sock.connect((self.ip_address, 6767))
                local_ip = sock.getsockname()[0]
                return local_ip
        except Exception as e:
            self.logger.warning("Could not determine local IP for device %s: %s", self.ip_address, e)
            # Ultimate fallback - return a common default
            return "0.0.0.0"
        
    def _complete_packet_handler(self, payload: bytes):
        try:
            device_state = PixelAirDeviceFB.GetRootAs(payload)
            self.last_decoded_state = device_state
            
            # Call the state packet handler
            self._handle_state_packet(device_state)
        except Exception as e:
            self.logger.warning(f"Failed to decode packet from {self.ip_address}: {e}")
    
    def _handle_state_packet(self, device_state: PixelAirDeviceFB):
        """
        Handle a decoded PixelAir device state packet.
        
        This method can be overridden by subclasses to implement
        device-specific state handling logic.
        
        Args:
            device_state: Decoded PixelAir device state FlatBuffer
            is_response: True if this is a response to a request, False if proactive
        """
        # Default implementation logs basic device information
        model = device_state.Model() or "Unknown"
        serial = device_state.SerialNumber() or "Unknown"
        
        self.state.model = model
        self.state.serial_number = serial
        
        if device_state.Nickname():
            self.state.nickname = device_state.Nickname().Value()
        
        if device_state.Engine():
            self.state.is_on = device_state.Engine().IsDisplaying().Value()
            if device_state.Engine().Brightness():
                self.state.brightness = device_state.Engine().Brightness().Value() * 100.0

        # Log state update with meaningful information
        power_state = "ON" if self.state.is_on else "OFF"
        self.logger.info(f"State update - Model: {self.state.model}, Power: {power_state}, Brightness: {self.state.brightness:.1f}%")
        
        # Notify any waiting coroutines - try to get running loop or skip if none
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._notify_state_waiters())
        except RuntimeError:
            # No running event loop, which is fine for synchronous usage
            pass
        
    async def _notify_state_waiters(self):
        """Notify all waiting coroutines that a state packet was received."""
        self._ensure_async_lock()
        async with self._state_waiters_lock:
            for event in self._state_waiters:
                event.set()
    
    async def _send_osc_and_wait_for_state(self, route: str, params: List[Union[int, float, str, bool]] = None, 
                                         timeout: float = 5.0, max_responses: int = 1, port: int = 6767) -> bool:
        """
        Send an OSC packet and wait for state packet responses.
        
        Args:
            route: OSC route/address (e.g., "/device/brightness")
            params: Optional list of parameters to include in the message
            timeout: Maximum time to wait for responses in seconds
            max_responses: Maximum number of state responses to wait for (1 or more)
        
        Returns:
            bool: True if the OSC message was sent and at least one state response was received, False otherwise
        """
        if params is None:
            params = []
        
        # Create an event to wait for state packet responses
        response_event = asyncio.Event()
        responses_received = 0
        
        # Add our event to the waiters list
        self._ensure_async_lock()
        async with self._state_waiters_lock:
            self._state_waiters.append(response_event)
        
        try:
            # Send the OSC message
            send_success = await self._send_osc_message(route, params, port)
            if not send_success:
                return False
            
            # Wait for state packet responses
            while responses_received < max_responses:
                try:
                    # Wait for a state packet with timeout
                    await asyncio.wait_for(response_event.wait(), timeout=timeout)
                    responses_received += 1
                    
                    # If we're expecting more responses, clear the event and continue waiting
                    if responses_received < max_responses:
                        response_event.clear()
                    
                except asyncio.TimeoutError:
                    self.logger.warning("Timeout waiting for state response %d/%d from %s", 
                                      responses_received + 1, max_responses, self.ip_address)
                    break
            
            return responses_received > 0
            
        finally:
            # Clean up: remove our event from the waiters list
            self._ensure_async_lock()
            async with self._state_waiters_lock:
                if response_event in self._state_waiters:
                    self._state_waiters.remove(response_event)
        
    def _send_udp_message(self, dgram: bytes) -> None:
        """
        Send a UDP datagram synchronously.
        
        This method is called from an executor to avoid blocking the event loop.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(5.0)  # 5 second timeout
            sock.sendto(dgram, (self.ip_address, 6767))
    
    def _update_last_seen(self):
        """Update the last seen timestamp."""
        self.last_seen = time.time()
    
    def __str__(self) -> str:
        return f"PixelAirDevice({self.ip_address})"
    
    def __repr__(self) -> str:
        return self.__str__()
