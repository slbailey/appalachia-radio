"""
Cadence manager for DJ segment timing.

Phase 7: Implements spacing rules and probability ramp for DJ segments.
"""

import logging
import threading

logger = logging.getLogger(__name__)


class CadenceManager:
    """
    Manages spacing and probability for DJ segments.
    
    Rules:
    - Minimum spacing: Never allow DJ segment if fewer than 3 songs since last segment
    - Probability ramp: Base 20% at 3 songs, linearly increases to 85% at 8+ songs
    """
    
    def __init__(
        self,
        min_songs_between_segments: int = 3,
        base_probability: float = 0.20,
        max_probability: float = 0.85,
        max_probability_at_songs: int = 8,
    ) -> None:
        """
        Initialize the cadence manager.
        
        Args:
            min_songs_between_segments: Minimum songs required between segments (default: 3)
            base_probability: Base probability when minimum is met (default: 0.20 = 20%)
            max_probability: Maximum probability after ramp (default: 0.85 = 85%)
            max_probability_at_songs: Songs at which max probability is reached (default: 8)
        """
        self._songs_since_last_segment = 0
        self._min_songs = min_songs_between_segments
        self._base_p = base_probability
        self._max_p = max_probability
        self._max_at = max_probability_at_songs
        self._lock = threading.Lock()
    
    def register_song_played(self) -> None:
        """
        Register that a song was played.
        
        Call this once per song, regardless of whether a DJ segment was played.
        """
        with self._lock:
            self._songs_since_last_segment += 1
            logger.debug(f"[Cadence] Songs since last segment: {self._songs_since_last_segment}")
    
    def register_segment_played(self) -> None:
        """
        Register that a DJ segment was played.
        
        Call this only when a DJ segment is actually added to the event queue.
        """
        with self._lock:
            if self._songs_since_last_segment > 0:
                logger.debug(f"[Cadence] Segment played, resetting counter (was {self._songs_since_last_segment})")
            self._songs_since_last_segment = 0
    
    def can_play_segment(self) -> bool:
        """
        Check if enough songs have passed to allow a DJ segment.
        
        Returns:
            True if minimum spacing requirement is met, False otherwise
        """
        with self._lock:
            n = self._songs_since_last_segment
            can_play = n >= self._min_songs
            if not can_play:
                logger.debug(f"[Cadence] Blocked: only {n} songs since last segment (need {self._min_songs})")
            return can_play
    
    def speaking_probability(self) -> float:
        """
        Calculate the probability of speaking based on songs since last segment.
        
        Probability ramp:
        - 0-2 songs: 0.0 (blocked by minimum spacing)
        - 3 songs: base_probability (20%)
        - 4-7 songs: Linear increase from base to max
        - 8+ songs: max_probability (85%)
        
        Returns:
            Probability (0.0 to 1.0)
        """
        with self._lock:
            n = self._songs_since_last_segment
        
        if n < self._min_songs:
            return 0.0
        
        if n >= self._max_at:
            return self._max_p
        
        # Linear ramp between base and max
        span = self._max_at - self._min_songs
        factor = (n - self._min_songs) / span
        probability = self._base_p + factor * (self._max_p - self._base_p)
        
        return probability
    
    def get_songs_since_last_segment(self) -> int:
        """
        Get current count of songs since last segment (for debugging/logging).
        
        Returns:
            Number of songs since last DJ segment
        """
        with self._lock:
            return self._songs_since_last_segment
