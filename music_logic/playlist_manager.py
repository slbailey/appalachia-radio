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
"""

import json
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Constants matching legacy implementation
HISTORY_SIZE: int = 48
IMMEDIATE_REPEAT_PENALTY: float = 0.01
RECENT_PLAY_WINDOW: int = 20
RECENT_PLAY_BASE_PENALTY: float = 0.1
RECENT_PLAY_DECAY: float = 0.15
NEVER_PLAYED_BONUS: float = 3.0
MAX_TIME_BONUS: float = 2.0


class PlaylistManager:
    """
    Manages playlist logic, play counts, and song selection probabilities.
    
    This class implements an intelligent song selection system that ensures:
    - Fair distribution of songs (no song is ignored)
    - Variety (recently played songs are less likely)
    - Balance (songs with fewer plays get higher weight)
    - Time awareness (old songs get bonus weight)
    
    Attributes:
        history: List of (song_name, timestamp, is_holiday) tuples
        play_counts: Map of regular song names to play counts
        holiday_play_counts: Map of holiday song names to play counts
    """
    
    def __init__(self, state_file: Optional[str] = None) -> None:
        """
        Initialize the playlist manager.
        
        Creates empty data structures for tracking play history and counts.
        Call initialize_play_counts() to populate play counts from directories.
        
        Args:
            state_file: Optional path to JSON file for saving/loading state
        """
        self.history: List[Tuple[str, float, bool]] = []  # (song_name, timestamp, is_holiday)
        self.play_counts: dict[str, int] = {}  # Tracks how many times each song has been played
        self.holiday_play_counts: dict[str, int] = {}  # Separate tracking for holiday songs
        self.state_file: Optional[str] = state_file
    
    def initialize_play_counts(self, regular_path: str, holiday_path: str) -> None:
        """
        Initialize play count tracking for all songs in both directories.
        
        This method scans both the regular and holiday music directories and
        initializes play count tracking for all MP3 files found. This should
        be called once during player initialization.
        
        Args:
            regular_path: Path to regular music directory
            holiday_path: Path to holiday music directory
        """
        # Initialize regular play counts
        if os.path.exists(regular_path):
            try:
                files = [f for f in os.listdir(regular_path) if f.endswith('.mp3')]
                self.play_counts = {f: 0 for f in files}
                logger.debug(f"[LIBRARY] Initialized {len(self.play_counts)} regular songs")
            except OSError as e:
                logger.error(f"[LIBRARY] Error reading regular directory: {e}")
                self.play_counts = {}
        else:
            logger.warning(f"[LIBRARY] Regular path does not exist: {regular_path}")
            self.play_counts = {}
        
        # Initialize holiday play counts
        if os.path.exists(holiday_path):
            try:
                files = [f for f in os.listdir(holiday_path) if f.endswith('.mp3')]
                self.holiday_play_counts = {f: 0 for f in files}
                logger.debug(f"[LIBRARY] Initialized {len(self.holiday_play_counts)} holiday songs")
            except OSError as e:
                logger.error(f"[LIBRARY] Error reading holiday directory: {e}")
                self.holiday_play_counts = {}
        else:
            logger.warning(f"[LIBRARY] Holiday path does not exist: {holiday_path}")
            self.holiday_play_counts = {}
    
    def is_holiday_season(self) -> bool:
        """
        Check if current date is within holiday season.
        
        The holiday season is defined as November 1 through December 31.
        This method is used to determine whether holiday songs should be
        considered for selection.
        
        Returns:
            True if current date is in November or December, False otherwise.
        """
        current_date = datetime.now()
        return current_date.month in (11, 12)
    
    def get_holiday_selection_probability(self) -> float:
        """
        Calculate the probability of selecting a holiday song based on date.
        
        This method implements a date-based probability curve that increases
        throughout the holiday season:
        - November 1: 1% chance
        - Linear progression to December 25: 33% chance
        - December 26-31: 33% chance (stays at maximum)
        - Outside holiday season: 0% chance
        
        Returns:
            Probability between 0.0 and 0.33 (0% to 33%).
        """
        if not self.is_holiday_season():
            return 0.0
        
        current_date = datetime.now()
        
        # Calculate days from Nov 1
        if current_date.month == 11:
            days_from_nov1 = current_date.day - 1  # Nov 1 = 0, Nov 30 = 29
        elif current_date.month == 12:
            days_from_nov1 = 30 + (current_date.day - 1)  # Dec 1 = 30, Dec 25 = 54
        else:
            return 0.0
        
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
            return max_probability
    
    def calculate_probabilities(
        self, regular_files: List[str], holiday_files: List[str]
    ) -> Tuple[List[float], List[str], List[bool]]:
        """
        Calculate weighted probabilities for song selection.
        
        This method implements a sophisticated weighting algorithm that considers
        multiple factors to ensure fair and varied song selection:
        
        1. Recent Play Penalty: Songs played recently get reduced weight
        2. Time-Based Bonus: Songs not played in a while get bonus weight
        3. Never-Played Bonus: Songs never played get bonus multiplier
        4. Play Count Balance: Songs with fewer plays get higher weight
        
        Args:
            regular_files: List of regular song filenames
            holiday_files: List of holiday song filenames
            
        Returns:
            Tuple of:
            - probabilities: List of float weights (normalized to sum to 1.0)
            - all_files: List of all song filenames (regular + holiday)
            - is_holiday_list: List of booleans indicating holiday status
        """
        current_time = time.time()
        probabilities = []
        
        all_files = [(f, False) for f in regular_files] + [(f, True) for f in holiday_files]
        
        for mp3_file, is_holiday in all_files:
            weight = 1.0
            play_counts = self.holiday_play_counts if is_holiday else self.play_counts
            
            # Queue-like system: Check if song was recently played
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
                    recovery = most_recent_position / RECENT_PLAY_WINDOW
                    penalty_factor = RECENT_PLAY_BASE_PENALTY + (1.0 - RECENT_PLAY_BASE_PENALTY) * recovery
                    penalty_factor = max(0.05, min(1.0, penalty_factor))  # Clamp between 5% and 100%
                    weight *= penalty_factor
                
                # Time-based bonus for songs not played in a while
                if last_played_time:
                    hours_since_played = (current_time - last_played_time) / 3600
                    if hours_since_played > 1:
                        time_factor = min(MAX_TIME_BONUS, (hours_since_played / 24) ** 0.5)
                        weight *= time_factor
            else:
                # Song never played - give it a bonus
                weight *= NEVER_PLAYED_BONUS
            
            # Play count balance - ensure all songs get fair play
            total_plays = sum(play_counts.values())
            if total_plays > 0 and len(play_counts) > 0:
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
            probabilities = [1.0 / len(all_files)] * len(all_files) if all_files else []
            
        return probabilities, [f for f, _ in all_files], [h for _, h in all_files]
    
    def select_next_song(self, available_tracks: List[str]) -> str:
        """
        Select the next song from available tracks using weighted probabilities.
        
        Uses existing probability calculation logic to select one song.
        Handles holiday season detection and probability calculation.
        Returns full path to selected song.
        
        Args:
            available_tracks: List of full paths to available MP3 files
            
        Returns:
            Full path to selected song
            
        Raises:
            ValueError: If no tracks available
        """
        if not available_tracks:
            raise ValueError("No tracks available for selection")
        
        # Separate regular and holiday tracks
        # Determine if track is holiday based on path (simple heuristic)
        # In Phase 7, we assume tracks are already separated or we check path
        # For now, we'll need to know which tracks are holiday
        # This is a simplified version - full implementation would check paths
        
        # For Phase 7, we'll use a simple approach:
        # If holiday season and holiday files exist, use holiday probability
        # Otherwise, treat all as regular
        
        regular_files = []
        holiday_files = []
        
        # Simple heuristic: check if path contains "holiday" (case-insensitive)
        # This matches legacy behavior where holiday_path is separate
        for track in available_tracks:
            track_lower = track.lower()
            if 'holiday' in track_lower:
                holiday_files.append(track)
            else:
                regular_files.append(track)
        
        # Check holiday season and calculate holiday probability
        holiday_prob = 0.0
        if self.is_holiday_season():
            holiday_prob = self.get_holiday_selection_probability()
        
        # Decide if we should select from holiday files
        import random
        use_holiday = False
        if holiday_files and random.random() < holiday_prob:
            use_holiday = True
        
        # Select from appropriate pool
        if use_holiday:
            candidate_files = holiday_files
            is_holiday = True
        else:
            candidate_files = regular_files
            is_holiday = False
        
        # If no candidates in selected pool, fall back to other pool
        if not candidate_files:
            candidate_files = holiday_files if not use_holiday else regular_files
            is_holiday = not is_holiday
        
        # Extract just filenames for probability calculation
        # (PlaylistManager works with filenames internally)
        candidate_filenames = [os.path.basename(f) for f in candidate_files]
        other_filenames = [os.path.basename(f) for f in (holiday_files if not is_holiday else regular_files)]
        
        # Calculate probabilities
        if is_holiday:
            probabilities, all_files, is_holiday_list = self.calculate_probabilities(
                regular_files=other_filenames,
                holiday_files=candidate_filenames
            )
        else:
            probabilities, all_files, is_holiday_list = self.calculate_probabilities(
                regular_files=candidate_filenames,
                holiday_files=other_filenames
            )
        
        # Select using weighted random
        if not all_files:
            # Fallback: random selection
            selected_filename = random.choice(candidate_filenames)
        else:
            selected_index = random.choices(range(len(all_files)), weights=probabilities)[0]
            selected_filename = all_files[selected_index]
            selected_is_holiday = is_holiday_list[selected_index]
            
            # Find full path for selected filename
            search_pool = holiday_files if selected_is_holiday else regular_files
            for track in search_pool:
                if os.path.basename(track) == selected_filename:
                    return track
        
        # Fallback: find full path
        for track in candidate_files:
            if os.path.basename(track) == selected_filename:
                return track
        
        # Last resort: return first candidate
        return candidate_files[0]
    
    def update_history(self, mp3_file: str, is_holiday: bool) -> None:
        """
        Update play history and counts for a played song.
        
        This method should be called after each song is played to:
        1. Add the song to play history with current timestamp
        2. Increment the play count for the song
        3. Maintain history size limit (removes oldest entries)
        
        Args:
            mp3_file: Name of the song file that was played (e.g., "song.mp3")
            is_holiday: Whether the song is a holiday song
        """
        current_time = time.time()
        self.history.append((mp3_file, current_time, is_holiday))
        if len(self.history) > HISTORY_SIZE:
            self.history.pop(0)
        
        if is_holiday:
            self.holiday_play_counts[mp3_file] = self.holiday_play_counts.get(mp3_file, 0) + 1
        else:
            self.play_counts[mp3_file] = self.play_counts.get(mp3_file, 0) + 1
        
        # Auto-save state after each update
        if self.state_file:
            self.save_state()
    
    def save_state(self) -> None:
        """
        Save playlist state (history and play counts) to JSON file.
        
        This preserves the weighted playlist state across restarts to prevent
        immediate repeats after graceful restart.
        """
        if not self.state_file:
            return
        
        try:
            state = {
                "history": self.history,
                "play_counts": self.play_counts,
                "holiday_play_counts": self.holiday_play_counts,
            }
            
            # Write to temp file first, then rename (atomic operation)
            temp_file = self.state_file + ".tmp"
            with open(temp_file, "w") as f:
                json.dump(state, f, indent=2)
            
            # Atomic rename
            os.replace(temp_file, self.state_file)
            
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Saved playlist state to {self.state_file}")
        except Exception as e:
            logger.warning(f"[LIBRARY] Failed to save state: {e}")
    
    def load_state(self) -> bool:
        """
        Load playlist state (history and play counts) from JSON file.
        
        Returns:
            True if state was loaded successfully, False otherwise
        """
        if not self.state_file or not os.path.exists(self.state_file):
            return False
        
        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)
            
            # Restore state
            self.history = [
                (item[0], float(item[1]), bool(item[2]))
                for item in state.get("history", [])
            ]
            self.play_counts = state.get("play_counts", {})
            self.holiday_play_counts = state.get("holiday_play_counts", {})
            
            logger.debug(f"[LIBRARY] Loaded state: {len(self.history)} history, {len(self.play_counts)} regular, {len(self.holiday_play_counts)} holiday")
            return True
        except Exception as e:
            logger.warning(f"[LIBRARY] Failed to load state: {e}")
            return False

