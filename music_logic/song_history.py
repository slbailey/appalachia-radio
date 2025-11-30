"""
Song history tracking module.

This module provides the SongHistory class for managing play history queues
and providing time-based queries for song selection algorithms.
"""

import logging
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


class SongHistory:
    """
    Manages play history queue and provides time-based queries.
    
    This class maintains a queue of recently played songs with timestamps,
    allowing efficient queries for recency penalties and time-based bonuses.
    
    Attributes:
        history: List of (song_name, timestamp, is_holiday) tuples
        max_size: Maximum number of entries to keep in history
    """
    
    def __init__(self, max_size: int = 48) -> None:
        """
        Initialize the song history tracker.
        
        Args:
            max_size: Maximum number of history entries to maintain
        """
        self.history: List[Tuple[str, float, bool]] = []  # (song_name, timestamp, is_holiday)
        self.max_size = max_size
    
    def add(self, song_name: str, timestamp: float, is_holiday: bool) -> None:
        """
        Add a song to the history queue.
        
        Args:
            song_name: Name of the song file
            timestamp: Unix timestamp when song was played
            is_holiday: Whether the song is a holiday song
        """
        raise NotImplementedError("Phase 3 will implement this")
    
    def get_most_recent_position(self, song_name: str, is_holiday: bool) -> Optional[int]:
        """
        Get the position of the most recent occurrence of a song in history.
        
        Position 0 = most recent, position 1 = second most recent, etc.
        Returns None if song not found in history.
        
        Args:
            song_name: Name of the song to search for
            is_holiday: Whether the song is a holiday song
            
        Returns:
            Position in history (0 = most recent) or None if not found
        """
        raise NotImplementedError("Phase 3 will implement this")
    
    def get_last_played_time(self, song_name: str, is_holiday: bool) -> Optional[float]:
        """
        Get the timestamp when a song was last played.
        
        Args:
            song_name: Name of the song to search for
            is_holiday: Whether the song is a holiday song
            
        Returns:
            Unix timestamp of last play, or None if never played
        """
        raise NotImplementedError("Phase 3 will implement this")
    
    def get_songs_played_in_last_hour(self) -> List[Tuple[str, bool]]:
        """
        Get list of songs played in the last hour.
        
        Returns:
            List of (song_name, is_holiday) tuples
        """
        raise NotImplementedError("Phase 3 will implement this")
    
    def clear(self) -> None:
        """
        Clear all history entries.
        """
        raise NotImplementedError("Phase 3 will implement this")

