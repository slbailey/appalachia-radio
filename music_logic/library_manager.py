"""
Library manager for scanning and maintaining music track lists.

This module provides the LibraryManager class, which scans music directories
recursively for MP3 files and maintains a track list with periodic refresh.
"""

import logging
import os
import time
from pathlib import Path
from typing import List, Optional
import random

logger = logging.getLogger(__name__)


class LibraryManager:
    """
    Manages music library by scanning directories and maintaining track lists.
    
    Scans directories recursively for MP3 files and maintains a cached list
    with automatic periodic refresh. Provides methods to get all tracks or
    a random track.
    """
    
    def __init__(
        self,
        regular_music_path: str,
        holiday_music_path: str,
        refresh_interval: float = 300.0  # 5 minutes default
    ) -> None:
        """
        Initialize the library manager.
        
        Args:
            regular_music_path: Path to regular music directory
            holiday_music_path: Path to holiday music directory
            refresh_interval: Interval in seconds between automatic refreshes (default: 300.0 = 5 minutes)
        """
        self.regular_music_path = regular_music_path
        self.holiday_music_path = holiday_music_path
        self.refresh_interval = refresh_interval
        
        # Track lists (full paths)
        self._regular_tracks: List[str] = []
        self._holiday_tracks: List[str] = []
        
        # Cache timestamps
        self._last_refresh_time: float = 0.0
    
    def _scan_directory_recursive(self, directory: str) -> List[str]:
        """
        Recursively scan a directory for MP3 files.
        
        Args:
            directory: Path to directory to scan
            
        Returns:
            List of full paths to MP3 files found
        """
        mp3_files = []
        
        if not os.path.exists(directory):
            logger.debug(f"Directory does not exist: {directory}")
            return mp3_files
        
        if not os.path.isdir(directory):
            logger.warning(f"Path is not a directory: {directory}")
            return mp3_files
        
        try:
            # Walk directory tree recursively
            for root, dirs, files in os.walk(directory):
                for file in files:
                    if file.lower().endswith('.mp3'):
                        full_path = os.path.join(root, file)
                        # Verify it's actually a file (not a symlink to directory, etc.)
                        if os.path.isfile(full_path):
                            mp3_files.append(full_path)
            
            logger.debug(f"Scanned {directory}: found {len(mp3_files)} MP3 files")
        except OSError as e:
            logger.error(f"Error scanning directory {directory}: {e}")
        
        return mp3_files
    
    def refresh(self, force: bool = False) -> None:
        """
        Refresh the track lists by scanning directories.
        
        Args:
            force: Force refresh even if cache is still valid
        """
        current_time = time.time()
        
        # Check if refresh is needed
        if not force and (current_time - self._last_refresh_time) < self.refresh_interval:
            return  # Cache still valid
        
        logger.info("Refreshing music library...")
        
        # Scan both directories
        self._regular_tracks = self._scan_directory_recursive(self.regular_music_path)
        self._holiday_tracks = self._scan_directory_recursive(self.holiday_music_path)
        
        self._last_refresh_time = current_time
        
        logger.info(f"Library refreshed: {len(self._regular_tracks)} regular tracks, {len(self._holiday_tracks)} holiday tracks")
    
    def get_all_tracks(self, include_holiday: bool = True) -> List[str]:
        """
        Get all tracks (full paths) from the library.
        
        Automatically refreshes if cache is expired.
        
        Args:
            include_holiday: Whether to include holiday tracks (default: True)
            
        Returns:
            List of full paths to all MP3 files
        """
        # Auto-refresh if needed
        self.refresh()
        
        tracks = list(self._regular_tracks)
        if include_holiday:
            tracks.extend(self._holiday_tracks)
        
        return tracks
    
    def get_regular_tracks(self) -> List[str]:
        """
        Get only regular tracks (full paths).
        
        Returns:
            List of full paths to regular MP3 files
        """
        self.refresh()
        return list(self._regular_tracks)
    
    def get_holiday_tracks(self) -> List[str]:
        """
        Get only holiday tracks (full paths).
        
        Returns:
            List of full paths to holiday MP3 files
        """
        self.refresh()
        return list(self._holiday_tracks)
    
    def get_random_track(self, include_holiday: bool = True) -> Optional[str]:
        """
        Get a random track from the library.
        
        Args:
            include_holiday: Whether to include holiday tracks in selection
            
        Returns:
            Full path to a random MP3 file, or None if library is empty
        """
        tracks = self.get_all_tracks(include_holiday=include_holiday)
        if not tracks:
            return None
        return random.choice(tracks)
    
    def get_track_count(self) -> tuple[int, int]:
        """
        Get count of tracks in library.
        
        Returns:
            Tuple of (regular_count, holiday_count)
        """
        self.refresh()
        return (len(self._regular_tracks), len(self._holiday_tracks))

