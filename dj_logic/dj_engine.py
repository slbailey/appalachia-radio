"""
DJ Engine for managing DJ intros, outros, and talk segments.

This module provides the DJEngine class, which manages DJ segment decision-making
and file matching.
"""

import logging
import os
from dataclasses import dataclass
from typing import List, Literal, Optional
from dj_logic.track_matcher import TrackMatcher
from dj_logic.rules_engine import RulesEngine
from dj_logic.cadence_manager import CadenceManager

logger = logging.getLogger(__name__)


@dataclass
class DJSegment:
    """
    Represents a DJ segment (intro or outro).
    
    Attributes:
        file_name: Name of the DJ file (e.g., "MySong_intro.mp3")
        segment_type: Type of segment ("intro" or "outro")
    """
    file_name: str
    segment_type: Literal["intro", "outro"]


class DJEngine:
    """
    Main orchestrator for DJ system.
    
    Decides when to play intros, outros, or talk segments and manages
    DJ file discovery and matching.
    """
    
    def __init__(
        self,
        dj_path: str,
        music_path: str
    ) -> None:
        """
        Initialize the DJ engine.
        
        Args:
            dj_path: Path to DJ files directory
            music_path: Path to music files directory (unused, kept for compatibility)
        """
        self.dj_path = dj_path
        self.music_path = music_path
        self.track_matcher = TrackMatcher(dj_path)
        self.rules_engine = RulesEngine()
        self.cadence_manager = CadenceManager()
    
    def get_segments_for_song(self, song_path: str) -> List[DJSegment]:
        """
        Get DJ segments for a song.
        
        Returns a list of DJSegment objects. Never returns both intro and outro
        for the same song - intro has priority.
        
        Args:
            song_path: Path to the song file
            
        Returns:
            List of DJSegment objects (empty, or one intro, or one outro)
        """
        import random
        
        segments: List[DJSegment] = []
        
        # Get songs since last DJ talk
        songs_since_dj = self.cadence_manager.get_count()
        
        # Check cadence (should we play DJ segments now?)
        if not self.cadence_manager.can_play_segment(songs_since_dj):
            return segments
        
        # Calculate probabilities
        intro_prob = self.rules_engine.evaluate_intro_rule(song_path, songs_since_dj, 0.2)
        outro_prob = self.rules_engine.evaluate_outro_rule(song_path, songs_since_dj, False, 0.2)
        
        # Decide if we should play intro
        should_intro = random.random() < intro_prob
        
        # Find intro file if needed
        if should_intro:
            intro_files = self.track_matcher.find_intro_files(song_path)
            if intro_files:
                # Select random intro file
                intro_filename = random.choice(intro_files)
                segments.append(DJSegment(file_name=intro_filename, segment_type="intro"))
        
        # Decide if we should play outro (only if intro wasn't played)
        if not should_intro:
            should_outro = random.random() < outro_prob
            if should_outro:
                outro_files = self.track_matcher.find_outro_files(song_path)
                if outro_files:
                    # Select random outro file
                    outro_filename = random.choice(outro_files)
                    segments.append(DJSegment(file_name=outro_filename, segment_type="outro"))
        
        # Update cadence after decision
        if segments:
            self.cadence_manager.reset()
        else:
            self.cadence_manager.increment()
        
        return segments
    
    def get_segments_for_song_legacy(self, song_path: str) -> tuple[Optional[str], Optional[str]]:
        """
        Legacy method that returns tuple for backward compatibility.
        
        Args:
            song_path: Path to the song file
            
        Returns:
            Tuple of (intro_path, outro_path), either may be None
        """
        segments = self.get_segments_for_song(song_path)
        intro_path = None
        outro_path = None
        
        for segment in segments:
            full_path = os.path.join(self.dj_path, segment.file_name)
            if segment.segment_type == "intro":
                intro_path = full_path
            elif segment.segment_type == "outro":
                outro_path = full_path
        
        return (intro_path, outro_path)

