"""
File management utilities for reading music directories.

This module provides the FileManager class for efficient file system operations
on music directories. It includes:
- Caching to reduce filesystem operations
- Automatic cache invalidation on directory changes
- Permission and validation checks
- Error handling and recovery

The cache system tracks directory modification times to automatically detect
when new files are added or removed, ensuring the cache stays fresh without
manual invalidation.

Example:
    ```python
    from radio.file_manager import FileManager
    
    manager = FileManager(cache_ttl=5.0)
    files = manager.get_mp3_files("/path/to/songs")
    # Returns: ["song1.mp3", "song2.mp3", ...]
    ```
"""

import logging
import os
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

class FileManager:
    """
    Manages file operations for music directories.
    
    This class provides efficient file listing with intelligent caching. The cache
    is automatically invalidated when:
    - More than cache_ttl seconds have passed, OR
    - Directory modification time has changed (new files added/removed)
    
    This ensures that new files are detected quickly while minimizing filesystem
    operations for better performance.
    
    Attributes:
        cache_ttl (float): Time to live for directory cache in seconds
        _cache (dict[str, tuple[List[str], float, float]]): Cache mapping directory
            paths to (files, timestamp, dir_mtime) tuples
            
    Example:
        ```python
        manager = FileManager(cache_ttl=5.0)
        
        # First call: scans directory and caches result
        files1 = manager.get_mp3_files("/songs")
        
        # Second call (within 5 seconds): returns cached result
        files2 = manager.get_mp3_files("/songs")
        
        # Force refresh
        manager.invalidate_cache("/songs")
        files3 = manager.get_mp3_files("/songs", force_refresh=True)
        ```
    """
    
    def __init__(self, cache_ttl: float = 5.0):
        """
        Initialize the file manager.
        
        Args:
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
        self.cache_ttl = cache_ttl
        # Cache: path -> (files, timestamp, dir_mtime)
        # dir_mtime tracks directory modification time to detect new files
        self._cache: dict[str, tuple[List[str], float, float]] = {}
    
    def _get_directory_mtime(self, directory: str) -> float:
        """
        Get directory modification time.
        
        This method is used to detect when directories change (new files added/removed)
        for automatic cache invalidation.
        
        Args:
            directory: Path to directory
            
        Returns:
            Modification time as float (seconds since epoch), or 0.0 if directory
            doesn't exist or is unreadable.
        """
        try:
            return os.path.getmtime(directory)
        except OSError:
            return 0.0
    
    def get_mp3_files(self, directory: str, use_cache: bool = True, force_refresh: bool = False) -> List[str]:
        """
        Get all MP3 files from a directory.
        
        This method returns a list of MP3 filenames from the specified directory.
        It uses intelligent caching to minimize filesystem operations while
        ensuring new files are detected promptly.
        
        The cache is automatically invalidated when:
        - More than cache_ttl seconds have passed, OR
        - Directory modification time has changed
        
        Args:
            directory: Path to directory to scan
            use_cache: Whether to use cached results (default: True)
                      Set to False to always scan directory
            force_refresh: Force refresh even if cache is valid (default: False)
                          Overrides cache validity check
            
        Returns:
            List of MP3 filenames (not full paths) found in the directory.
            Returns empty list if:
            - Directory doesn't exist
            - Directory is not readable
            - Directory is not actually a directory
            - Error occurs during scanning
            
        Note:
            - Only returns files with .mp3 extension
            - Validates files are actually files (not directories)
            - Validates directory is readable before scanning
            - Logs errors for debugging
            - Logs debug message when cache is refreshed
            
        Example:
            ```python
            files = manager.get_mp3_files("/music/songs")
            # Returns: ["song1.mp3", "song2.mp3", ...]
            
            # Force refresh
            files = manager.get_mp3_files("/music/songs", force_refresh=True)
            ```
        """
        if not os.path.exists(directory):
            logger.debug(f"Directory does not exist: {directory}")
            return []
        
        current_time = time.time()
        current_mtime = self._get_directory_mtime(directory)
        
        # Check cache
        if use_cache and not force_refresh and directory in self._cache:
            files, timestamp, cached_mtime = self._cache[directory]
            
            # Check if cache is still valid:
            # 1. Time hasn't expired
            # 2. Directory hasn't been modified (new files would change mtime)
            if (current_time - timestamp < self.cache_ttl and 
                cached_mtime == current_mtime):
                return files
        
        # Cache expired or directory modified - refresh
        try:
            # Validate directory is actually a directory
            if not os.path.isdir(directory):
                logger.error(f"Path is not a directory: {directory}")
                return []
            
            # Check read permissions
            if not os.access(directory, os.R_OK):
                logger.error(f"Directory is not readable: {directory}")
                return []
            
            files = [f for f in os.listdir(directory) 
                    if f.endswith('.mp3') and os.path.isfile(os.path.join(directory, f))]
            
            # Update cache with current mtime
            self._cache[directory] = (files, current_time, current_mtime)
            
            logger.debug(f"Refreshed file list for {directory}: {len(files)} MP3 files")
            return files
        except PermissionError as e:
            logger.error(f"Permission denied reading directory {directory}: {e}")
            return []
        except OSError as e:
            logger.error(f"Error reading directory {directory}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error reading directory {directory}: {e}", exc_info=True)
            return []
    
    def clear_cache(self) -> None:
        """
        Clear the entire directory cache.
        
        This method removes all cached directory listings, forcing a fresh
        scan on the next get_mp3_files() call for any directory.
        
        Note:
            - Clears cache for all directories
            - Use invalidate_cache() to clear a specific directory
        """
        self._cache.clear()
    
    def invalidate_cache(self, directory: str) -> None:
        """
        Invalidate cache for a specific directory.
        
        This method removes the cached listing for the specified directory,
        forcing a fresh scan on the next get_mp3_files() call for that directory.
        
        Args:
            directory: Path to directory whose cache should be invalidated
            
        Note:
            - Safe to call even if directory is not in cache
            - Does not affect cache for other directories
            - Use clear_cache() to clear all directories
        """
        self._cache.pop(directory, None)
    
    def refresh_all(self) -> None:
        """
        Force refresh all cached directories on next access.
        
        This method clears the entire cache, causing all directories to be
        re-scanned on their next access. This is equivalent to clear_cache().
        
        Note:
            - Alias for clear_cache()
            - Use invalidate_cache() to refresh a specific directory
        """
        self.clear_cache()

