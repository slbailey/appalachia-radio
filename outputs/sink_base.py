"""
Base class for audio output sinks.

All audio sinks must inherit from SinkBase and implement the required methods.
"""

from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class SinkBase(ABC):
    """
    Abstract base class for all audio output sinks.
    
    All sinks must implement:
    - write_frame(): Write a PCM frame chunk to the sink
    - start(): Start the sink (open device, connect stream, etc.)
    - stop(): Stop the sink (close device, disconnect stream, etc.)
    """
    
    def __init__(self) -> None:
        """Initialize the sink."""
        self._running = False
    
    @abstractmethod
    def write_frame(self, pcm_frame: bytes) -> None:
        """
        Write a single PCM frame chunk to the sink.
        
        Args:
            pcm_frame: Raw PCM frame bytes (typically 4096-8192 bytes)
        """
        pass
    
    @abstractmethod
    def start(self) -> bool:
        """
        Start the sink (e.g., open device, connect stream).
        
        Returns:
            True if started successfully, False otherwise
        """
        pass
    
    @abstractmethod
    def stop(self) -> None:
        """
        Stop the sink (e.g., close device, disconnect stream).
        """
        pass
    
    def is_running(self) -> bool:
        """
        Check if the sink is currently running.
        
        Returns:
            True if running, False otherwise
        """
        return self._running

