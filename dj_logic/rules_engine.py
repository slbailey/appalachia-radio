"""
Rules engine for DJ segment selection.

This module provides the RulesEngine class, which implements rules for
when DJ segments should be played. Extracted from legacy _calculate_dj_probability.
"""

import logging

logger = logging.getLogger(__name__)

# Constants matching legacy implementation
DJ_BASE_PROBABILITY: float = 0.2  # Base chance to play intro/outro (20%)
DJ_MAX_PROBABILITY: float = 0.85  # Maximum chance after long silence (85%)
DJ_SONGS_BEFORE_INCREASE: int = 3  # Number of songs before probability starts increasing
DJ_MAX_SONGS_FOR_MAX_PROB: int = 8  # Songs without DJ talk to reach max probability


class RulesEngine:
    """
    Implements rules for DJ segment selection.
    
    Extracted from legacy _calculate_dj_probability method.
    Probability increases linearly based on songs since last DJ talk.
    """
    
    def __init__(
        self,
        base_probability: float = DJ_BASE_PROBABILITY,
        max_probability: float = DJ_MAX_PROBABILITY,
        songs_before_increase: int = DJ_SONGS_BEFORE_INCREASE,
        songs_for_max_prob: int = DJ_MAX_SONGS_FOR_MAX_PROB
    ) -> None:
        """
        Initialize the rules engine.
        
        Args:
            base_probability: Base probability (default: 0.2 = 20%)
            max_probability: Maximum probability (default: 0.85 = 85%)
            songs_before_increase: Songs before probability starts increasing (default: 3)
            songs_for_max_prob: Songs without DJ to reach max probability (default: 8)
        """
        self.base_probability = base_probability
        self.max_probability = max_probability
        self.songs_before_increase = songs_before_increase
        self.songs_for_max_prob = songs_for_max_prob
    
    def calculate_dynamic_probability(self, songs_since_dj: int) -> float:
        """
        Calculate dynamic probability that increases over time.
        
        This matches the legacy _calculate_dj_probability exactly:
        - First DJ_SONGS_BEFORE_INCREASE songs: base probability
        - After that: Linear increase to max probability
        - Reaches max after DJ_MAX_SONGS_FOR_MAX_PROB songs
        
        Args:
            songs_since_dj: Number of songs since last DJ segment
            
        Returns:
            Current probability (base_probability to max_probability)
        """
        if songs_since_dj < self.songs_before_increase:
            # Use base probability for first few songs (music-friendly)
            return self.base_probability
        
        # Calculate how much to increase probability
        songs_over_base = songs_since_dj - self.songs_before_increase
        max_increase_songs = self.songs_for_max_prob - self.songs_before_increase
        
        # Linear increase from base to max
        if songs_over_base >= max_increase_songs:
            return self.max_probability
        
        increase_factor = songs_over_base / max_increase_songs
        probability = self.base_probability + (self.max_probability - self.base_probability) * increase_factor
        
        return min(probability, self.max_probability)
    
    def evaluate_intro_rule(
        self, song_path: str, songs_since_dj: int, base_probability: float
    ) -> float:
        """
        Evaluate if an intro should play based on rules.
        
        For Phase 6, this simply uses the dynamic probability.
        Future phases can add track-specific rules.
        
        Args:
            song_path: Path to the song file
            songs_since_dj: Number of songs since last DJ segment
            base_probability: Base probability (unused, kept for interface compatibility)
            
        Returns:
            Adjusted probability (0.0 to 1.0)
        """
        return self.calculate_dynamic_probability(songs_since_dj)
    
    def evaluate_outro_rule(
        self, song_path: str, songs_since_dj: int, intro_played: bool, base_probability: float
    ) -> float:
        """
        Evaluate if an outro should play based on rules.
        
        If intro was played, outro probability is 0 (never both).
        Otherwise uses dynamic probability.
        
        Args:
            song_path: Path to the song file
            songs_since_dj: Number of songs since last DJ segment
            intro_played: Whether an intro was played for this song
            base_probability: Base probability (unused, kept for interface compatibility)
            
        Returns:
            Adjusted probability (0.0 to 1.0)
        """
        # Never play outro if intro was played
        if intro_played:
            return 0.0
        
        return self.calculate_dynamic_probability(songs_since_dj)
