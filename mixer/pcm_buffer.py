"""
PCM frame buffer management.

This module provides utilities for managing PCM frame buffers.
"""

from collections import deque
from typing import Optional


class PCMBuffer:
    """
    Manages PCM frame buffers.
    
    Handles buffer underrun/overrun and provides thread-safe operations.
    """
    
    def __init__(self, max_size: int = 100) -> None:
        """
        Initialize the PCM buffer.
        
        Args:
            max_size: Maximum number of frames to buffer
        """
        self._buffer: deque[bytes] = deque(maxlen=max_size)
        self.max_size = max_size
    
    def push(self, frame: bytes) -> None:
        """
        Push a frame into the buffer.
        
        Args:
            frame: PCM frame bytes
        """
        self._buffer.append(frame)
    
    def pop(self) -> Optional[bytes]:
        """
        Pop a frame from the buffer.
        
        Returns:
            PCM frame bytes, or None if buffer is empty
        """
        try:
            return self._buffer.popleft()
        except IndexError:
            return None
    
    def empty(self) -> bool:
        """
        Check if buffer is empty.
        
        Returns:
            True if empty, False otherwise
        """
        return len(self._buffer) == 0
    
    def size(self) -> int:
        """
        Get current buffer size.
        
        Returns:
            Number of frames in buffer
        """
        return len(self._buffer)
    
    def clear(self) -> None:
        """Clear the buffer."""
        self._buffer.clear()

