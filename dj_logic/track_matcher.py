"""
Track matcher for DJ file discovery.

Phase 7: Finds intro/outro files for songs based on filename patterns.
"""

import logging
import os
import random
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

# Constants
MAX_INTRO_FILES: int = 5
MAX_OUTRO_FILES: int = 5


class TrackMatcher:
    """
    Matches DJ files to songs based on naming conventions.
    
    For a song "inkspots_wethree.mp3", looks for:
    - Intros: inkspots_wethree_intro.mp3, inkspots_wethree_intro1.mp3, etc.
    - Outros: inkspots_wethree_outro.mp3, inkspots_wethree_outro1.mp3, etc.
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
                logger.debug(f"[TrackMatcher] Refreshed DJ file list: {len(self._available_files)} files")
            except OSError as e:
                logger.error(f"[TrackMatcher] Error reading DJ directory {self.dj_path}: {e}")
                return set()
        
        return self._available_files
    
    def _find_variants(self, base: str, kind: str) -> List[str]:
        """
        Find variant files for a base name and kind (intro/outro).
        
        Args:
            base: Base filename without extension (e.g., "inkspots_wethree")
            kind: Either "intro" or "outro"
            
        Returns:
            List of full paths to matching files (numbered variants first, then base)
        """
        candidates: List[str] = []
        available_files = self._get_available_files()
        
        if not available_files:
            return candidates
        
        max_files = MAX_INTRO_FILES if kind == "intro" else MAX_OUTRO_FILES
        
        # 1) Check numbered variants (intro1, intro2, ..., intro5)
        for i in range(1, max_files + 1):
            name = f"{base}_{kind}{i}.mp3"
            if name in available_files:
                candidate = os.path.join(self.dj_path, name)
                if os.path.isfile(candidate):
                    candidates.append(candidate)
        
        # 2) Check base name without number (intro.mp3, outro.mp3)
        name = f"{base}_{kind}.mp3"
        if name in available_files:
            candidate = os.path.join(self.dj_path, name)
            if os.path.isfile(candidate):
                candidates.append(candidate)
        
        return candidates
    
    def find_intro(self, song_path: str) -> Optional[str]:
        """
        Find an intro file for a song.
        
        For song "inkspots_wethree.mp3", looks for:
        - inkspots_wethree_intro1.mp3, inkspots_wethree_intro2.mp3, ...
        - inkspots_wethree_intro.mp3
        
        Returns a random choice from available variants, or None if none found.
        
        Args:
            song_path: Full path to the song file
            
        Returns:
            Full path to an intro file, or None if none found
        """
        base = os.path.splitext(os.path.basename(song_path))[0]
        variants = self._find_variants(base, "intro")
        
        if variants:
            selected = random.choice(variants)
            logger.debug(f"[TrackMatcher] Found intro: {os.path.basename(selected)} for {os.path.basename(song_path)}")
            return selected
        
        return None
    
    def find_outro(self, song_path: str) -> Optional[str]:
        """
        Find an outro file for a song.
        
        For song "inkspots_wethree.mp3", looks for:
        - inkspots_wethree_outro1.mp3, inkspots_wethree_outro2.mp3, ...
        - inkspots_wethree_outro.mp3
        
        Returns a random choice from available variants, or None if none found.
        
        Args:
            song_path: Full path to the song file
            
        Returns:
            Full path to an outro file, or None if none found
        """
        base = os.path.splitext(os.path.basename(song_path))[0]
        variants = self._find_variants(base, "outro")
        
        if variants:
            selected = random.choice(variants)
            logger.debug(f"[TrackMatcher] Found outro: {os.path.basename(selected)} for {os.path.basename(song_path)}")
            return selected
        
        return None
    
    def find_intro(self, song_path: str) -> Optional[str]:
        """
        Find an intro file for a song (returns full path).
        
        Args:
            song_path: Full path to the song file
            
        Returns:
            Full path to an intro file, or None if none found
        """
        base = os.path.splitext(os.path.basename(song_path))[0]
        variants = self._find_variants(base, "intro")
        
        if variants:
            selected = random.choice(variants)
            logger.debug(f"[TrackMatcher] Found intro: {os.path.basename(selected)} for {os.path.basename(song_path)}")
            return selected
        
        return None
    
    def find_outro(self, song_path: str) -> Optional[str]:
        """
        Find an outro file for a song (returns full path).
        
        Args:
            song_path: Full path to the song file
            
        Returns:
            Full path to an outro file, or None if none found
        """
        base = os.path.splitext(os.path.basename(song_path))[0]
        variants = self._find_variants(base, "outro")
        
        if variants:
            selected = random.choice(variants)
            logger.debug(f"[TrackMatcher] Found outro: {os.path.basename(selected)} for {os.path.basename(song_path)}")
            return selected
        
        return None
    
    def find_intro_files(self, song_path: str) -> List[str]:
        """
        Legacy method: Find all intro files for a song.
        
        Returns list of filenames (not full paths) for backward compatibility.
        
        Args:
            song_path: Path to the song file
            
        Returns:
            List of matching intro filenames (not full paths), empty if none found
        """
        base = os.path.splitext(os.path.basename(song_path))[0]
        variants = self._find_variants(base, "intro")
        # Return just filenames for backward compatibility
        return [os.path.basename(v) for v in variants]
    
    def find_outro_files(self, song_path: str) -> List[str]:
        """
        Legacy method: Find all outro files for a song.
        
        Returns list of filenames (not full paths) for backward compatibility.
        
        Args:
            song_path: Path to the song file
            
        Returns:
            List of matching outro filenames (not full paths), empty if none found
        """
        base = os.path.splitext(os.path.basename(song_path))[0]
        variants = self._find_variants(base, "outro")
        # Return just filenames for backward compatibility
        return [os.path.basename(v) for v in variants]
    
    def invalidate_cache(self) -> None:
        """
        Invalidate the file cache to force refresh on next access.
        """
        self._cache_timestamp = 0.0
        self._cache_mtime = 0.0
        logger.debug("[TrackMatcher] Cache invalidated")
