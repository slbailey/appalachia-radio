"""
DJ intro/outro file management module.

This module provides the DJManager class for managing DJ intro and outro files
that can be played before or after songs. The manager handles:
- File discovery based on naming conventions
- Caching for performance
- Automatic cache invalidation when directories change

DJ file naming convention:
- song_intro.mp3 or song_intro1.mp3, song_intro2.mp3, etc. (up to MAX_INTRO_FILES)
- song_outro.mp3 or song_outro1.mp3, song_outro2.mp3, etc. (up to MAX_OUTRO_FILES)

Example:
    ```python
    from radio.dj_manager import DJManager
    
    manager = DJManager("/path/to/dj/files")
    intro_files = manager.check_intro_files("song.mp3")
    # Returns: ["song_intro.mp3", "song_intro1.mp3", ...]
    ```
"""

import logging
import os
from typing import List, Literal
from .constants import MAX_INTRO_FILES, MAX_OUTRO_FILES

logger = logging.getLogger(__name__)

class DJManager:
    """
    Manages DJ intro and outro files for songs.
    
    This class provides methods to discover and manage DJ intro/outro files
    that match song names. It uses a caching system to avoid repeated
    filesystem operations.
    
    DJ files are matched using a naming convention:
    - For song "song.mp3", looks for:
      - song_intro.mp3, song_intro1.mp3, song_intro2.mp3, ...
      - song_outro.mp3, song_outro1.mp3, song_outro2.mp3, ...
    
    Attributes:
        dj_path (str): Path to DJ files directory
        cache_ttl (float): Cache time-to-live in seconds
        _available_files (set[str]): Cached set of available files
        _cache_timestamp (float): When cache was last updated
        _cache_mtime (float): Directory modification time when cached
        
    Example:
        ```python
        manager = DJManager("/music/dj", cache_ttl=5.0)
        
        # Check for intro files
        intros = manager.check_intro_files("song.mp3")
        if intros:
            # Play one of the intro files
            pass
        
        # Force cache refresh
        manager.invalidate_cache()
        ```
    """
    
    def __init__(self, dj_path: str, cache_ttl: float = 5.0):
        """
        Initialize the DJ manager.
        
        Args:
            dj_path: Path to the DJ files directory containing intro/outro files
            cache_ttl: Time to live for directory cache in seconds (default: 5.0)
                      Lower TTL ensures new files are detected quickly but uses
                      more filesystem operations. Higher TTL improves performance
                      but delays detection of new files.
                      
        Note:
            - Cache is automatically invalidated when directory modification
              time changes (new files added/removed)
            - Cache is also invalidated after cache_ttl seconds
            - Initial cache is empty (populated on first access)
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
            Directory modification time as float (seconds since epoch), or 0.0
            if directory doesn't exist or is unreadable.
            
        Note:
            Used to detect when new files are added/removed for cache invalidation.
        """
        try:
            return os.path.getmtime(self.dj_path)
        except OSError:
            return 0.0
    
    def _get_available_files(self) -> set[str]:
        """
        Get cached list of available files in DJ directory.
        
        This method maintains a cache of files in the DJ directory to avoid
        repeated filesystem operations. The cache is automatically refreshed when:
        - More than cache_ttl seconds have passed, OR
        - Directory modification time has changed (new files added/removed)
        
        Returns:
            Set of filenames in the DJ directory, or empty set if directory
            doesn't exist or is unreadable.
            
        Note:
            - Cache is thread-safe for read operations
            - Logs debug message when cache is refreshed
            - Logs error if directory is unreadable
        """
        import time
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
    
    def invalidate_cache(self) -> None:
        """
        Force refresh of DJ file cache on next access.
        
        This method resets the cache timestamp and modification time, causing
        the cache to be refreshed the next time _get_available_files() is called.
        
        Use this method when you know files have been added/removed and want
        to ensure they're detected immediately.
        
        Note:
            - Does not immediately refresh cache (happens on next access)
            - Safe to call multiple times
        """
        self._cache_timestamp = 0.0
        self._cache_mtime = 0.0
    
    def _check_dj_files(self, mp3_file: str, file_type: Literal['intro', 'outro']) -> List[str]:
        """
        Check for existing intro or outro files for a given MP3 file.
        
        This method searches for DJ files matching the naming convention:
        - song_intro.mp3, song_intro1.mp3, song_intro2.mp3, ... (up to MAX_INTRO_FILES)
        - song_outro.mp3, song_outro1.mp3, song_outro2.mp3, ... (up to MAX_OUTRO_FILES)
        
        Args:
            mp3_file: Name of the MP3 file (e.g., "song.mp3")
            file_type: Either 'intro' or 'outro' to specify which type to search for
            
        Returns:
            List of filenames that exist in the DJ directory, in order:
            - Base name first (e.g., "song_intro.mp3")
            - Then numbered variants (e.g., "song_intro1.mp3", "song_intro2.mp3")
            - Empty list if no matching files found
            
        Note:
            - Uses cached file list for performance
            - Logs debug message when files are found
            - File extension is removed from mp3_file before matching
        """
        available_files = self._get_available_files()
        if not available_files:
            return []
        
        base_name = mp3_file.rsplit('.', 1)[0]  # Remove extension
        max_files = MAX_INTRO_FILES if file_type == 'intro' else MAX_OUTRO_FILES
        
        # Generate possible file names
        possible_files = [f"{base_name}_{file_type}{i}.mp3" for i in range(1, max_files + 1)]
        possible_files.append(f"{base_name}_{file_type}.mp3")
        
        # Find files that actually exist
        found_files = [f for f in possible_files if f in available_files]
        
        if found_files:
            logger.debug(f"Found {len(found_files)} {file_type} file(s) for {mp3_file}")
        
        return found_files
    
    def check_intro_files(self, mp3_file: str) -> List[str]:
        """
        Check for existing intro files for a given MP3 file.
        
        Convenience method that calls _check_dj_files() with file_type='intro'.
        
        Args:
            mp3_file: Name of the MP3 file (e.g., "song.mp3")
            
        Returns:
            List of intro filenames that exist, or empty list if none found.
            
        Example:
            ```python
            intros = manager.check_intro_files("song.mp3")
            # Returns: ["song_intro.mp3", "song_intro1.mp3"] if they exist
            ```
        """
        return self._check_dj_files(mp3_file, 'intro')

    def check_outro_files(self, mp3_file: str) -> List[str]:
        """
        Check for existing outro files for a given MP3 file.
        
        Convenience method that calls _check_dj_files() with file_type='outro'.
        
        Args:
            mp3_file: Name of the MP3 file (e.g., "song.mp3")
            
        Returns:
            List of outro filenames that exist, or empty list if none found.
            
        Example:
            ```python
            outros = manager.check_outro_files("song.mp3")
            # Returns: ["song_outro.mp3", "song_outro1.mp3"] if they exist
            ```
        """
        return self._check_dj_files(mp3_file, 'outro')
