"""
Thread-safe queue for audio events.

Provides EventQueue class for managing AudioEvent objects in a thread-safe manner.
"""

import logging
import queue
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class AudioEvent:
    """
    Represents an audio playback event.
    
    Attributes:
        path: File path to audio file
        type: Event type (song, intro, outro, or talk)
        gain: Volume gain multiplier (0.0-1.0, default: 1.0)
    """
    path: str
    type: Literal["song", "intro", "outro", "talk"]
    gain: float = 1.0


class EventQueue:
    """
    Thread-safe queue for AudioEvent objects.
    
    Wraps queue.Queue with convenience methods for audio events.
    """
    
    def __init__(self) -> None:
        """Initialize the event queue."""
        self._queue: queue.Queue[AudioEvent] = queue.Queue()
    
    def put(self, event: AudioEvent) -> None:
        """
        Add an audio event to the queue.
        
        Args:
            event: AudioEvent to add
        """
        self._queue.put(event)
        logger.debug(f"[EVENT] Pushed: {event.type} - {event.path}")
    
    def get(self, block: bool = True, timeout: float | None = None) -> AudioEvent:
        """
        Get an audio event from the queue.
        
        Args:
            block: If True, block until an event is available
            timeout: Maximum time to wait (if block=True)
            
        Returns:
            AudioEvent from the queue
            
        Raises:
            queue.Empty: If block=False and queue is empty
        """
        event = self._queue.get(block=block, timeout=timeout)
        logger.debug(f"[EVENT] Pulled: {event.type} - {event.path}")
        return event
    
    def empty(self) -> bool:
        """
        Check if the queue is empty.
        
        Returns:
            True if queue is empty, False otherwise
        """
        return self._queue.empty()
    
    def qsize(self) -> int:
        """
        Get the approximate size of the queue.
        
        Returns:
            Approximate number of items in queue
        """
        return self._queue.qsize()
    
    def task_done(self) -> None:
        """
        Indicate that a previously enqueued task is complete.
        
        Should be called after get() to indicate the task is done.
        """
        self._queue.task_done()

