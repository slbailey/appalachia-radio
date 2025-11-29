"""
Audio playback module using pygame mixer.

This module provides a simple interface for playing MP3 files using pygame's
mixer module. It includes:
- Headless environment support (WSL, servers, Raspberry Pi)
- File validation before playback
- Safety limits to prevent infinite loops
- Error handling and recovery

Example:
    ```python
    from radio.audio_player import AudioPlayer
    
    player = AudioPlayer()
    if player.play("song.mp3"):
        print("Song played successfully")
    ```
"""

import logging
import os
import time
import pygame
from pygame import mixer
from typing import Optional

logger = logging.getLogger(__name__)

class AudioPlayer:
    """
    Handles audio playback using pygame mixer.
    
    This class provides a simple, robust interface for playing MP3 files.
    It automatically handles headless environments by setting up a dummy video
    driver, and includes comprehensive file validation and error handling.
    
    Attributes:
        TICK_RATE (int): Frames per second for the playback loop (default: 10)
        
    Example:
        ```python
        player = AudioPlayer(frequency=48000, buffer_size=2048)
        
        if player.play("/path/to/song.mp3"):
            # Song is playing
            while player.is_playing():
                time.sleep(0.1)
        ```
    """
    
    TICK_RATE = 10  # Frames per second for the playback loop
    
    def __init__(self, frequency: int = 48000, buffer_size: int = 2048) -> None:
        """
        Initialize the audio player.
        
        This method sets up pygame mixer for audio playback. It automatically
        configures a dummy video driver for headless environments (WSL, servers,
        Raspberry Pi without display).
        
        Args:
            frequency: Audio frequency in Hz (default: 48000)
                       Common values: 44100 (CD quality), 48000 (professional)
            buffer_size: Audio buffer size in samples (default: 2048)
                        Larger buffers = less CPU, more latency
                        Smaller buffers = more CPU, less latency
            
        Raises:
            pygame.error: If pygame mixer initialization fails (e.g., no audio device)
            
        Note:
            - Automatically detects headless environments (no DISPLAY variable)
            - Creates a minimal dummy display surface (required by pygame)
            - Logs success/failure messages
        """
        try:
            # Set dummy video driver for headless environments (WSL, servers, etc.)
            if 'DISPLAY' not in os.environ:
                os.environ['SDL_VIDEODRIVER'] = 'dummy'
            
            pygame.mixer.pre_init(frequency=frequency, buffer=buffer_size)
            pygame.mixer.init()
            
            # Initialize display for headless environments (required by pygame)
            if not pygame.display.get_init():
                pygame.display.init()
                # Create a minimal dummy surface (required but not used)
                pygame.display.set_mode((1, 1), flags=pygame.HIDDEN)
            
            logger.info("Audio player initialized successfully")
        except pygame.error as e:
            logger.error(f"Failed to initialize audio player: {e}")
            raise
    
    def play(self, mp3_file: str) -> bool:
        """
        Play an MP3 file using pygame mixer.
        
        This method performs comprehensive validation before playback:
        - Checks file exists and is readable
        - Validates file is not empty or suspiciously small
        - Verifies file is actually a file (not directory)
        
        Playback blocks until the song finishes or is interrupted. The method
        includes a safety limit (1 hour) to prevent infinite loops from corrupted
        files or playback issues.
        
        Args:
            mp3_file: Path to the MP3 file to play (absolute or relative)
            
        Returns:
            True if playback completed successfully, False otherwise.
            
        Note:
            - Blocks until song finishes playing
            - Maximum playback time: 1 hour (safety limit)
            - Logs detailed error messages for debugging
            - Handles pygame errors gracefully
            
        Raises:
            No exceptions are raised. All errors are logged and False is returned.
            
        Example:
            ```python
            if player.play("song.mp3"):
                print("Song finished playing")
            else:
                print("Failed to play song")
            ```
        """
        if not mp3_file:
            logger.error("Audio file path is empty")
            return False
        
        if not os.path.exists(mp3_file):
            logger.error(f"Audio file not found: {mp3_file}")
            return False
        
        if not os.path.isfile(mp3_file):
            logger.error(f"Audio path is not a file: {mp3_file}")
            return False
        
        # Check file is readable
        if not os.access(mp3_file, os.R_OK):
            logger.error(f"Audio file is not readable: {mp3_file}")
            return False
        
        # Check file size (avoid empty or corrupted files)
        try:
            file_size = os.path.getsize(mp3_file)
            if file_size == 0:
                logger.error(f"Audio file is empty: {mp3_file}")
                return False
            if file_size < 1024:  # Less than 1KB is suspicious
                logger.warning(f"Audio file is very small ({file_size} bytes): {mp3_file}")
        except OSError as e:
            logger.error(f"Cannot access file size for {mp3_file}: {e}")
            return False
        
        try:
            pygame.mixer.music.load(mp3_file)
            pygame.mixer.music.play()
            logger.info(f"Playing: {os.path.basename(mp3_file)}")
            
            clock = pygame.time.Clock()
            max_wait_time = 3600  # Maximum 1 hour per song (safety limit)
            start_time = time.time()
            
            while pygame.mixer.music.get_busy():
                # Safety check: prevent infinite loops
                if time.time() - start_time > max_wait_time:
                    logger.warning(f"Song playback exceeded maximum time ({max_wait_time}s), stopping")
                    pygame.mixer.music.stop()
                    return False
                
                clock.tick(self.TICK_RATE)
                # Allow for interruption by checking events
                pygame.event.pump()
            
            logger.debug(f"Finished playing: {os.path.basename(mp3_file)}")
            return True
        except pygame.error as e:
            logger.error(f"Error playing {mp3_file}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error playing {mp3_file}: {e}", exc_info=True)
            return False
    
    def stop(self) -> None:
        """
        Stop the currently playing music.
        
        This method immediately stops playback of any currently playing audio.
        It's safe to call even if nothing is playing.
        
        Note:
            - Logs success/failure messages
            - Handles pygame errors gracefully
            - Safe to call multiple times
            
        Raises:
            No exceptions are raised. Errors are logged but don't propagate.
        """
        try:
            pygame.mixer.music.stop()
            logger.info("Playback stopped")
        except pygame.error as e:
            logger.error(f"Error stopping playback: {e}")
    
    def is_playing(self) -> bool:
        """
        Check if music is currently playing.
        
        Returns:
            True if music is currently playing, False otherwise.
            
        Note:
            - Handles pygame errors gracefully (returns False on error)
            - Safe to call at any time
            - Non-blocking check
        """
        try:
            return pygame.mixer.music.get_busy()
        except pygame.error:
            return False
