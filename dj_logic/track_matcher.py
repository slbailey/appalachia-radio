"""
Track matcher for DJ file discovery.

This module provides the TrackMatcher class, which matches DJ intro/outro
files to songs based on naming conventions. Extracted from legacy DJManager.
"""

import logging
import os
import time
from typing import List

logger = logging.getLogger(__name__)

# Constants matching legacy implementation
MAX_INTRO_FILES: int = 5
MAX_OUTRO_FILES: int = 5


class TrackMatcher:
    """
    Matches DJ files to songs.
    
    Handles:
    - Intro/outro file discovery with exact filename pattern matching
    - Multiple variant support (intro1, intro2, etc.)
    - File caching with TTL to avoid repeated directory scans
    """
    
    def __init__(self, dj_path: str, cache_ttl: float = 5.0) -> None:
        """
        Initialize the track matcher.
        
        Args:
            dj_path: Path to DJ files directory
            cache_ttl: Cache time-to-live in seconds (default: 5.0)
        """
        self.dj_path = dj_path
        self.cache_ttl = cache_ttl
        self._available_files: set[str] = set()
        self._cache_timestamp: float = 0.0
        self._cache_mtime: float = 0.0
    
    def _get_directory_mtime(self) -> float:
        """
        Get directory modification time.
        
        Returns:
            Directory modification time as float, or 0.0 if unreadable
        """
        try:
            return os.path.getmtime(self.dj_path)
        except OSError:
            return 0.0
    
    def _get_available_files(self) -> set[str]:
        """
        Get cached list of available files in DJ directory.
        
        Cache is automatically refreshed when:
        - More than cache_ttl seconds have passed, OR
        - Directory modification time has changed
        
        Returns:
            Set of filenames in the DJ directory
        """
        current_time = time.time()
        current_mtime = self._get_directory_mtime()
        
        if not os.path.exists(self.dj_path):
            return set()
        
        # Refresh cache if expired or directory modified
        if (current_time - self._cache_timestamp > self.cache_ttl or 
            current_mtime != self._cache_mtime):
            try:
                self._available_files = set(os.listdir(self.dj_path))
                self._cache_timestamp = current_time
                self._cache_mtime = current_mtime
                logger.debug(f"Refreshed DJ file list: {len(self._available_files)} files")
            except OSError as e:
                logger.error(f"Error reading DJ directory {self.dj_path}: {e}")
                return set()
        
        return self._available_files
    
    def find_intro_files(self, song_path: str) -> List[str]:
        """
        Find intro files matching a song.
        
        For song "MySong.mp3", looks for:
        - MySong_intro1.mp3, MySong_intro2.mp3, ..., MySong_intro5.mp3
        - MySong_intro.mp3
        
        Args:
            song_path: Path to the song file (e.g., "/path/to/MySong.mp3")
            
        Returns:
            List of matching intro filenames (not full paths), empty if none found
        """
        # Extract just the filename from path
        song_filename = os.path.basename(song_path)
        return self._check_dj_files(song_filename, 'intro')
    
    def find_outro_files(self, song_path: str) -> List[str]:
        """
        Find outro files matching a song.
        
        For song "MySong.mp3", looks for:
        - MySong_outro1.mp3, MySong_outro2.mp3, ..., MySong_outro5.mp3
        - MySong_outro.mp3
        
        Args:
            song_path: Path to the song file (e.g., "/path/to/MySong.mp3")
            
        Returns:
            List of matching outro filenames (not full paths), empty if none found
        """
        # Extract just the filename from path
        song_filename = os.path.basename(song_path)
        return self._check_dj_files(song_filename, 'outro')
    
    def _check_dj_files(self, mp3_file: str, file_type: str) -> List[str]:
        """
        Check for existing intro or outro files for a given MP3 file.
        
        This matches the legacy DJManager._check_dj_files logic exactly.
        
        Args:
            mp3_file: Name of the MP3 file (e.g., "song.mp3")
            file_type: Either 'intro' or 'outro'
            
        Returns:
            List of filenames that exist in the DJ directory, in order:
            - Base name first (e.g., "song_intro.mp3")
            - Then numbered variants (e.g., "song_intro1.mp3", "song_intro2.mp3")
        """
        available_files = self._get_available_files()
        if not available_files:
            return []
        
        base_name = mp3_file.rsplit('.', 1)[0]  # Remove extension
        max_files = MAX_INTRO_FILES if file_type == 'intro' else MAX_OUTRO_FILES
        
        # Generate possible file names
        # Note: Legacy checks numbered variants first, then base
        # But returns base first in the list
        possible_files = [f"{base_name}_{file_type}{i}.mp3" for i in range(1, max_files + 1)]
        possible_files.append(f"{base_name}_{file_type}.mp3")
        
        # Find files that actually exist
        found_files = [f for f in possible_files if f in available_files]
        
        # Reorder to match legacy: base first, then numbered
        # Legacy returns: base first, then numbered variants
        base_file = f"{base_name}_{file_type}.mp3"
        numbered_files = [f for f in found_files if f != base_file]
        
        result = []
        if base_file in found_files:
            result.append(base_file)
        result.extend(sorted(numbered_files))  # Sort numbered variants
        
        if found_files:
            logger.debug(f"Found {len(found_files)} {file_type} file(s) for {mp3_file}")
        
        return result
    
    def invalidate_cache(self) -> None:
        """
        Invalidate the file cache to force refresh on next access.
        """
        self._cache_timestamp = 0.0
        self._cache_mtime = 0.0
        logger.debug("DJ file cache invalidated")
