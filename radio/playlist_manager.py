"""
Playlist management module for intelligent song selection.

This module provides the PlaylistManager class, which implements sophisticated
algorithms for:
- Tracking play history and counts
- Calculating weighted probabilities for fair song selection
- Holiday season detection and probability calculation
- Preventing immediate repeats and ensuring variety

The selection algorithm uses multiple factors:
- Recent play history (queue-like system)
- Time since last play (bonus for old songs)
- Play count balance (fair distribution)
- Never-played bonus (ensures all songs get played)

Example:
    ```python
    from radio.playlist_manager import PlaylistManager
    
    manager = PlaylistManager()
    manager.initialize_play_counts("/path/to/songs", "/path/to/holiday")
    
    # Calculate probabilities for song selection
    probs, files, is_holiday = manager.calculate_probabilities(
        regular_files=["song1.mp3", "song2.mp3"],
        holiday_files=["holiday1.mp3"]
    )
    ```
"""

import logging
import os
import time
from datetime import datetime
from typing import List, Tuple
from .constants import (
    HISTORY_SIZE, IMMEDIATE_REPEAT_PENALTY, NEVER_PLAYED_BONUS,
    MAX_TIME_BONUS, RECENT_PLAY_WINDOW, RECENT_PLAY_BASE_PENALTY, RECENT_PLAY_DECAY
)

logger = logging.getLogger(__name__)

class PlaylistManager:
    """
    Manages playlist logic, play counts, and song selection probabilities.
    
    This class implements an intelligent song selection system that ensures:
    - Fair distribution of songs (no song is ignored)
    - Variety (recently played songs are less likely)
    - Balance (songs with fewer plays get higher weight)
    - Time awareness (old songs get bonus weight)
    
    The class maintains:
    - Play history (recent songs with timestamps)
    - Play counts (how many times each song has been played)
    - Separate tracking for regular and holiday songs
    
    Attributes:
        history (List[Tuple[str, float, bool]]): List of (song_name, timestamp, is_holiday)
        play_counts (dict[str, int]): Map of regular song names to play counts
        holiday_play_counts (dict[str, int]): Map of holiday song names to play counts
        
    Example:
        ```python
        manager = PlaylistManager()
        manager.initialize_play_counts("/songs", "/holiday")
        
        # Update after playing a song
        manager.update_history("song.mp3", is_holiday=False)
        
        # Get selection probabilities
        probs, files, is_holiday = manager.calculate_probabilities(
            regular_files=["song1.mp3", "song2.mp3"],
            holiday_files=[]
        )
        ```
    """
    
    def __init__(self) -> None:
        """
        Initialize the playlist manager.
        
        Creates empty data structures for tracking play history and counts.
        Call initialize_play_counts() to populate play counts from directories.
        """
        self.history: List[Tuple[str, float, bool]] = []  # (song_name, timestamp, is_holiday)
        self.play_counts: dict[str, int] = {}  # Tracks how many times each song has been played
        self.holiday_play_counts: dict[str, int] = {}  # Separate tracking for holiday songs

    def _initialize_play_counts_for_path(self, path: str, is_holiday: bool) -> dict[str, int]:
        """
        Initialize play counts for a single directory.
        
        This method scans a directory for MP3 files and creates a dictionary
        mapping each filename to a play count of 0. This establishes the baseline
        for tracking which songs have been played.
        
        Args:
            path: Path to music directory
            is_holiday: Whether this is a holiday music directory (for logging)
            
        Returns:
            Dictionary mapping filenames to play counts (all initialized to 0).
            Empty dictionary if directory doesn't exist or is unreadable.
            
        Note:
            - Only includes files with .mp3 extension
            - Logs warning if directory doesn't exist
            - Logs error if directory is unreadable
            - Logs info message with count of initialized songs
        """
        if not os.path.exists(path):
            logger.warning(f"{'Holiday' if is_holiday else 'Regular'} music path does not exist: {path}")
            return {}
        
        try:
            files = [f for f in os.listdir(path) if f.endswith('.mp3')]
            counts = {f: 0 for f in files}
            logger.info(f"Initialized {len(counts)} {'holiday' if is_holiday else 'regular'} songs")
            return counts
        except OSError as e:
            logger.error(f"Error reading {'holiday' if is_holiday else 'regular'} music directory {path}: {e}")
            return {}
    
    def initialize_play_counts(self, regular_path: str, holiday_path: str) -> None:
        """
        Initialize play count tracking for all songs in both directories.
        
        This method scans both the regular and holiday music directories and
        initializes play count tracking for all MP3 files found. This should
        be called once during player initialization.
        
        Args:
            regular_path: Path to regular music directory
            holiday_path: Path to holiday music directory
            
        Note:
            - Creates separate tracking dictionaries for regular and holiday songs
            - All play counts start at 0
            - Safe to call multiple times (will re-scan directories)
            - Logs information about how many songs were found in each directory
        """
        self.play_counts = self._initialize_play_counts_for_path(regular_path, is_holiday=False)
        self.holiday_play_counts = self._initialize_play_counts_for_path(holiday_path, is_holiday=True)

    def is_holiday_season(self) -> bool:
        """
        Check if current date is within holiday season.
        
        The holiday season is defined as November 1 through December 31.
        This method is used to determine whether holiday songs should be
        considered for selection.
        
        Returns:
            True if current date is in November or December, False otherwise.
            
        Example:
            - November 1: True
            - December 25: True
            - January 1: False
            - October 31: False
        """
        current_date = datetime.now()
        if current_date.month == 11:
            return True
        if current_date.month == 12:
            return True
        return False

    def get_holiday_selection_probability(self) -> float:
        """
        Calculate the probability of selecting a holiday song based on date.
        
        This method implements a date-based probability curve that increases
        throughout the holiday season:
        - November 1: 1% chance
        - Linear progression to December 25: 33% chance
        - December 26-31: 33% chance (stays at maximum)
        - Outside holiday season: 0% chance
        
        The probability is calculated based on days from November 1, creating
        a smooth progression that makes holiday songs more likely as Christmas
        approaches.
        
        Returns:
            Probability between 0.0 and 0.33 (0% to 33%).
            
        Note:
            - Returns 0.0 outside of November-December
            - Uses linear interpolation between Nov 1 and Dec 25
            - Maximum probability is 0.33 (33%)
            
        Example:
            ```python
            prob = manager.get_holiday_selection_probability()
            if random.random() < prob:
                # Select holiday song
                pass
            ```
        """
        if not self.is_holiday_season():
            return 0.0
        
        current_date = datetime.now()
        
        # Calculate days from Nov 1
        # Nov 1 = day 0, Nov 30 = day 29, Dec 1 = day 30, Dec 25 = day 54
        if current_date.month == 11:
            days_from_nov1 = current_date.day - 1  # Nov 1 = 0, Nov 30 = 29
        elif current_date.month == 12:
            days_from_nov1 = 30 + (current_date.day - 1)  # Dec 1 = 30, Dec 25 = 54
        else:
            return 0.0
        
        # Total days from Nov 1 to Dec 25 = 55 days (Nov 1 to Dec 25 inclusive)
        # Actually: Nov has 30 days, so Nov 1 to Nov 30 = 30 days (days 0-29)
        # Dec 1 to Dec 25 = 25 days (days 30-54)
        # Total = 55 days, but day 0 to day 54 = 55 days total
        total_days_to_dec25 = 54  # Day 0 (Nov 1) to day 54 (Dec 25)
        max_probability = 0.33  # Maximum 33% chance
        
        if current_date.month == 12 and current_date.day > 25:
            # Dec 26-31: 33% chance
            return max_probability
        elif days_from_nov1 <= total_days_to_dec25:
            # Nov 1 to Dec 25: Linear progression from 1% to 33%
            progress = days_from_nov1 / total_days_to_dec25
            return 0.01 + progress * (max_probability - 0.01)  # Linear from 1% to 33%
        else:
            # Shouldn't reach here, but just in case
            return max_probability

    def calculate_probabilities(
        self, regular_files: List[str], holiday_files: List[str]
    ) -> Tuple[List[float], List[str], List[bool]]:
        """
        Calculate weighted probabilities for song selection.
        
        This method implements a sophisticated weighting algorithm that considers
        multiple factors to ensure fair and varied song selection:
        
        1. **Recent Play Penalty**: Songs played recently get reduced weight
           - Last song: IMMEDIATE_REPEAT_PENALTY (almost eliminated)
           - Recent window (last 20 songs): Decreasing penalty based on position
           - Beyond recent window: No penalty
        
        2. **Time-Based Bonus**: Songs not played in a while get bonus weight
           - More than 1 hour: Increasing bonus up to MAX_TIME_BONUS
           - Formula: (hours_since_play / 24) ^ 0.5
        
        3. **Never-Played Bonus**: Songs never played get NEVER_PLAYED_BONUS multiplier
        
        4. **Play Count Balance**: Songs with fewer plays get higher weight
           - Formula: (expected_plays + 1) / (actual_plays + 1)
           - Ensures all songs get fair play over time
        
        The final probabilities are normalized so they sum to 1.0, making them
        suitable for use with random.choices().
        
        Args:
            regular_files: List of regular song filenames
            holiday_files: List of holiday song filenames
            
        Returns:
            Tuple of:
            - probabilities: List of float weights (normalized to sum to 1.0)
            - all_files: List of all song filenames (regular + holiday)
            - is_holiday_list: List of booleans indicating holiday status
            
        Note:
            - Probabilities are normalized to sum to 1.0
            - If all probabilities are 0, equal weights are assigned
            - Separate play counts are maintained for regular and holiday songs
            - History is searched from most recent to oldest for efficiency
            
        Example:
            ```python
            probs, files, is_holiday = manager.calculate_probabilities(
                regular_files=["song1.mp3", "song2.mp3"],
                holiday_files=["holiday1.mp3"]
            )
            
            # Select song using weighted random
            selected_index = random.choices(range(len(files)), weights=probs)[0]
            selected_song = files[selected_index]
            ```
        """
        current_time = time.time()
        probabilities = []
        
        # Note: Holiday selection is now handled separately in play_random_mp3()
        # This method only calculates weights for the provided files
        all_files = [(f, False) for f in regular_files] + [(f, True) for f in holiday_files]
        
        for mp3_file, is_holiday in all_files:
            weight = 1.0
            play_counts = self.holiday_play_counts if is_holiday else self.play_counts
            
            # Queue-like system: Check if song was recently played
            # Find the most recent occurrence of this song in history
            most_recent_position = None
            last_played_time = None
            
            # Search history from most recent to oldest
            for idx in range(len(self.history) - 1, -1, -1):
                song, timestamp, h = self.history[idx]
                if song == mp3_file and h == is_holiday:
                    most_recent_position = len(self.history) - 1 - idx  # 0 = most recent
                    last_played_time = timestamp
                    break
            
            if most_recent_position is not None:
                # Song was played recently - apply queue penalty
                if most_recent_position == 0:
                    # Very last song - almost eliminate it
                    weight *= IMMEDIATE_REPEAT_PENALTY
                elif most_recent_position < RECENT_PLAY_WINDOW:
                    # In recent play window - apply decreasing penalty
                    # Penalty decreases as position increases (more songs ago = less penalty)
                    # Formula: base_penalty + (1 - base_penalty) * (position / window)
                    # This creates a sliding scale where songs gradually recover
                    recovery = most_recent_position / RECENT_PLAY_WINDOW
                    penalty_factor = RECENT_PLAY_BASE_PENALTY + (1.0 - RECENT_PLAY_BASE_PENALTY) * recovery
                    penalty_factor = max(0.05, min(1.0, penalty_factor))  # Clamp between 5% and 100%
                    weight *= penalty_factor
                
                # Also apply time-based bonus for songs that haven't played in a while
                if last_played_time:
                    hours_since_played = (current_time - last_played_time) / 3600
                    if hours_since_played > 1:  # Only apply if more than 1 hour
                        time_factor = min(MAX_TIME_BONUS, (hours_since_played / 24) ** 0.5)
                        weight *= time_factor
            else:
                # Song never played - give it a bonus
                weight *= NEVER_PLAYED_BONUS

            # Play count balance - ensure all songs get fair play
            total_plays = sum(play_counts.values())
            if total_plays > 0:
                expected_plays = total_plays / len(play_counts)
                actual_plays = play_counts.get(mp3_file, 0)
                play_count_factor = (expected_plays + 1) / (actual_plays + 1)
                weight *= play_count_factor
            
            probabilities.append(weight)
        
        # Normalize probabilities
        total = sum(probabilities)
        if total > 0:
            probabilities = [p / total for p in probabilities]
        else:
            probabilities = [1.0 / len(all_files)] * len(all_files)
            
        return probabilities, [f for f, _ in all_files], [h for _, h in all_files]

    def update_history(self, mp3_file: str, is_holiday: bool) -> None:
        """
        Update play history and counts for a played song.
        
        This method should be called after each song is played to:
        1. Add the song to play history with current timestamp
        2. Increment the play count for the song
        3. Maintain history size limit (removes oldest entries)
        
        The history is used by calculate_probabilities() to determine recent
        play penalties, and play counts are used for balance calculations.
        
        Args:
            mp3_file: Name of the song file that was played (e.g., "song.mp3")
            is_holiday: Whether the song is a holiday song (determines which
                       play_counts dictionary to update)
            
        Note:
            - History is limited to HISTORY_SIZE entries (oldest removed)
            - Play counts are incremented (initialized to 0 if first play)
            - Timestamp is current time in seconds since epoch
            - Separate tracking for regular and holiday songs
        """
        current_time = time.time()
        self.history.append((mp3_file, current_time, is_holiday))
        if len(self.history) > HISTORY_SIZE:
            self.history.pop(0)
            
        if is_holiday:
            self.holiday_play_counts[mp3_file] = self.holiday_play_counts.get(mp3_file, 0) + 1
        else:
            self.play_counts[mp3_file] = self.play_counts.get(mp3_file, 0) + 1