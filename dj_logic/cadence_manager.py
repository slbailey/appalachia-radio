"""
Cadence manager for DJ segment timing.

This module provides the CadenceManager class, which manages the counter
for songs since last DJ talk and handles reset behavior.
"""

import logging

logger = logging.getLogger(__name__)


class CadenceManager:
    """
    Manages timing and cadence for DJ segments.
    
    Tracks songs_since_last_dj_talk counter:
    - Increments once per song
    - Resets to 0 when DJ plays
    - Used by RulesEngine for probability calculation
    """
    
    def __init__(self) -> None:
        """Initialize the cadence manager."""
        self.songs_since_last_dj_talk: int = 0
    
    def increment(self) -> None:
        """
        Increment the counter (call once per song).
        
        This should be called after each song, regardless of whether
        DJ segments were played or not.
        """
        self.songs_since_last_dj_talk += 1
        logger.debug(f"Songs since last DJ talk: {self.songs_since_last_dj_talk}")
    
    def reset(self) -> None:
        """
        Reset the counter to 0 (call when DJ segment is played).
        
        This should be called when a DJ intro or outro is actually played.
        """
        if self.songs_since_last_dj_talk > 0:
            logger.debug(f"Resetting DJ counter (was {self.songs_since_last_dj_talk})")
        self.songs_since_last_dj_talk = 0
    
    def get_count(self) -> int:
        """
        Get current count of songs since last DJ talk.
        
        Returns:
            Number of songs since last DJ segment
        """
        return self.songs_since_last_dj_talk
    
    def can_play_segment(self, songs_since_dj: int, min_songs_between: int = 3) -> bool:
        """
        Check if enough songs have passed to allow a DJ segment.
        
        Args:
            songs_since_dj: Number of songs since last DJ segment
            min_songs_between: Minimum songs required between segments
            
        Returns:
            True if segment can play, False otherwise
        """
        return songs_since_dj >= min_songs_between
    
    def should_prefer_intro_over_outro(self, song_path: str) -> bool:
        """
        Determine if intro should be preferred over outro for a song.
        
        Phase 6: Always prefers intro (matches legacy behavior).
        Future phases can add logic based on song attributes.
        
        Args:
            song_path: Path to the song file
            
        Returns:
            True if intro should be preferred, False if outro preferred
        """
        # Legacy behavior: intro always has priority
        return True
    
    def get_time_since_last_dj(self) -> None:
        """
        Get time (in seconds) since last DJ segment.
        
        Phase 6: Not implemented (uses song count instead).
        Future phases can track timestamps.
        
        Returns:
            None (not implemented in Phase 6)
        """
        return None
