"""
FragmentedStateManager - A helper class for managing fragmented byte buffers.

This class handles incoming fragmented packets with 4-byte headers and reconstructs
complete payloads, calling a callback when all fragments are received.

Header format:
- Byte 1: Flag indicating if response is to a request (1) or proactive state change (0)
- Byte 2: High-level counter to help group fragments (wraps at 255)
- Byte 3: Total number of fragments in the response
- Byte 4: Index of this fragment (0-based)

Fragments that don't complete within 1 second are automatically discarded.
"""

import time
import threading
from typing import Callable, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class FragmentInfo:
    """Information about a fragment."""
    counter: int
    total_fragments: int
    fragment_index: int
    payload: bytes
    timestamp: float


class FragmentedResponse:
    """Represents a fragmented response being assembled."""
    
    def __init__(self, counter: int, total_fragments: int):
        self.counter = counter
        self.total_fragments = total_fragments
        self.fragments: Dict[int, bytes] = {}
        self.timestamp = time.time()
        self.lock = threading.Lock()
    
    def add_fragment(self, fragment_index: int, payload: bytes) -> bool:
        """
        Add a fragment to this response.
        
        Args:
            fragment_index: Index of the fragment (0-based)
            payload: Payload data for this fragment
            
        Returns:
            True if this completes the response, False otherwise
        """
        with self.lock:
            if fragment_index >= self.total_fragments:
                return False
                
            self.fragments[fragment_index] = payload
            return len(self.fragments) == self.total_fragments
    
    def get_complete_payload(self) -> Optional[bytes]:
        """
        Get the complete assembled payload if all fragments are present.
        
        Returns:
            Complete payload bytes or None if not all fragments received
        """
        with self.lock:
            if len(self.fragments) != self.total_fragments:
                return None
                
            # Assemble fragments in order
            payload = b''
            for i in range(self.total_fragments):
                if i not in self.fragments:
                    return None
                payload += self.fragments[i]
            
            return payload
    
    def is_expired(self, timeout: float = 1.0) -> bool:
        """Check if this response has expired."""
        return time.time() - self.timestamp > timeout


class FragmentedStateManager:
    """
    Manages fragmented byte buffers and reconstructs complete payloads.
    
    This class handles incoming fragmented packets, tracks their assembly,
    and calls a callback when complete payloads are ready.
    """
    
    def __init__(self, callback: Callable[[bytes], None]):
        """
        Initialize the FragmentedStateManager.
        
        Args:
            callback: Function to call with (payload: bytes)
                     when a complete payload is assembled
        """
        self.callback = callback
        self.responses: Dict[int, FragmentedResponse] = {}
        self.lock = threading.Lock()
        self.cleanup_timer: Optional[threading.Timer] = None
        
        # Start periodic cleanup
        self._schedule_cleanup()
    
    def process_buffer(self, buffer: bytes) -> bool:
        """
        Process an incoming byte buffer.
        
        Args:
            buffer: Raw byte buffer with 4-byte header + payload
            
        Returns:
            True if a complete payload was assembled and callback called
            
        Raises:
            ValueError: If buffer is too small or has invalid header
        """
        if len(buffer) < 4:
            raise ValueError("Buffer too small - must be at least 4 bytes for header")
        
        # Parse header
        counter = buffer[1]
        total_fragments = buffer[2]
        fragment_index = buffer[3]
        payload = buffer[4:]
        
        if total_fragments == 0:
            raise ValueError("Total fragments cannot be zero")
        
        if fragment_index >= total_fragments:
            raise ValueError(f"Fragment index {fragment_index} >= total fragments {total_fragments}")
        
        with self.lock:
            # Clean up expired responses
            self._cleanup_expired_responses()
            
            # Handle single fragment case
            if total_fragments == 1:
                self.callback(payload)
                return True
            
            # Get or create fragmented response
            if counter not in self.responses:
                self.responses[counter] = FragmentedResponse(counter, total_fragments)
            
            response = self.responses[counter]
            
            # Verify this fragment belongs to the same response
            if (response.total_fragments != total_fragments):
                # Mismatched response, start over
                self.responses[counter] = FragmentedResponse(counter, total_fragments)
                response = self.responses[counter]
            
            # Add fragment
            is_complete = response.add_fragment(fragment_index, payload)
            
            if is_complete:
                complete_payload = response.get_complete_payload()
                if complete_payload is not None:
                    # Remove completed response
                    del self.responses[counter]
                    self.callback(complete_payload)
                    return True
        
        return False
    
    def _cleanup_expired_responses(self):
        """Remove expired fragmented responses (called with lock held)."""
        expired_counters = [
            counter for counter, response in self.responses.items()
            if response.is_expired()
        ]
        
        for counter in expired_counters:
            del self.responses[counter]
    
    def _schedule_cleanup(self):
        """Schedule periodic cleanup of expired responses."""
        def cleanup():
            with self.lock:
                self._cleanup_expired_responses()
            self._schedule_cleanup()
        
        # Schedule cleanup every 0.5 seconds
        self.cleanup_timer = threading.Timer(1, cleanup)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()
    
    def reset(self):
        """Reset the manager, clearing all pending fragments."""
        with self.lock:
            self.responses.clear()
    
    def get_pending_count(self) -> int:
        """Get the number of pending fragmented responses."""
        with self.lock:
            return len(self.responses)
    
    def get_pending_info(self) -> Dict[int, Tuple[int, int, float]]:
        """
        Get information about pending responses.
        
        Returns:
            Dict mapping counter to (received_fragments, total_fragments, age_seconds)
        """
        with self.lock:
            current_time = time.time()
            return {
                counter: (
                    len(response.fragments),
                    response.total_fragments,
                    current_time - response.timestamp
                )
                for counter, response in self.responses.items()
            }
    
    def shutdown(self):
        """Shutdown the manager and cancel cleanup timer."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
        self.reset()
