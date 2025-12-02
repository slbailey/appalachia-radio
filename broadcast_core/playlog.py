"""
Playlog system for tracking audio event playback history.

Phase 5: Monitoring and visibility only. No control decisions.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

logger = logging.getLogger(__name__)


@dataclass
class PlaylogEntry:
    """
    Represents a single playlog entry for an audio event.
    
    Attributes:
        started_at: When the event started playing
        ended_at: When the event finished (None if still playing)
        path: File path to the audio file
        type: Event type (song, intro, outro, talk)
        deck: Which turntable deck played this (A or B)
    """
    started_at: datetime
    ended_at: Optional[datetime]
    path: str
    type: Literal["song", "intro", "outro", "talk"]
    deck: Literal["A", "B"]


class Playlog:
    """
    Thread-safe playlog for tracking audio event playback history.
    
    Stores entries in memory only (no disk persistence in Phase 5).
    Automatically drops oldest entries when max_entries is exceeded.
    """
    
    def __init__(self, max_entries: int = 500) -> None:
        """
        Initialize the playlog.
        
        Args:
            max_entries: Maximum number of entries to keep (default: 500)
        """
        self._entries: list[PlaylogEntry] = []
        self._lock = threading.Lock()
        self._max_entries = max_entries
    
    def add_start(self, path: str, type: str, deck: str) -> PlaylogEntry:
        """
        Create a new entry with started_at set and ended_at=None.
        
        Args:
            path: File path to the audio file
            type: Event type (song, intro, outro, talk)
            deck: Which turntable deck (A or B)
            
        Returns:
            The newly created PlaylogEntry
        """
        entry = PlaylogEntry(
            started_at=datetime.now(),
            ended_at=None,
            path=path,
            type=type,  # type: ignore
            deck=deck  # type: ignore
        )
        
        with self._lock:
            self._entries.append(entry)
            
            # Drop oldest if max exceeded
            if len(self._entries) > self._max_entries:
                dropped = self._entries.pop(0)
                logger.debug(f"[Playlog] Dropped oldest entry: {dropped.path}")
        
        return entry
    
    def mark_end(self, entry: PlaylogEntry) -> None:
        """
        Set ended_at for a previously started entry.
        
        Args:
            entry: The PlaylogEntry to mark as ended
        """
        with self._lock:
            # Find the entry in our list and update it
            # (entry is a reference, so we can update it directly)
            entry.ended_at = datetime.now()
    
    def recent(self, limit: int = 50) -> list[PlaylogEntry]:
        """
        Return the most recent N entries (newest last).
        
        Args:
            limit: Maximum number of entries to return (default: 50)
            
        Returns:
            List of PlaylogEntry objects, ordered oldest to newest
        """
        with self._lock:
            # Return the last N entries (newest last)
            return self._entries[-limit:] if len(self._entries) > limit else self._entries.copy()

