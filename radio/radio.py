"""
Main music player module that orchestrates audio playback.

This module provides the MusicPlayer class, which coordinates all components of
the radio system:
- Audio playback (AudioPlayer)
- Playlist management (PlaylistManager)
- DJ intro/outro management (DJManager)
- File management (FileManager)
- YouTube streaming (YouTubeStreamer, optional)

The player implements intelligent song selection with:
- Weighted random selection based on play history
- Holiday song probability based on date
- Dynamic DJ talk probability (increases over time)
- Automatic YouTube streaming (if enabled)

Example:
    ```python
    from radio.radio import MusicPlayer
    
    player = MusicPlayer(
        regular_music_path="/path/to/songs",
        holiday_music_path="/path/to/holiday_songs",
        dj_path="/path/to/dj_files"
    )
    
    # Play a random song with intro/outro
    player.play_random_mp3()
    ```
"""

import logging
import os
import random
import signal
import sys
import time
from typing import List, Optional, Tuple
from .audio_player import AudioPlayer
from .playlist_manager import PlaylistManager
from .dj_manager import DJManager
from .file_manager import FileManager
from .constants import (
    DJ_BASE_PROBABILITY, DJ_MAX_PROBABILITY, DJ_SONGS_BEFORE_INCREASE, DJ_MAX_SONGS_FOR_MAX_PROB,
    YOUTUBE_ENABLED, YOUTUBE_STREAM_KEY, YOUTUBE_AUDIO_DEVICE, YOUTUBE_AUDIO_FORMAT,
    YOUTUBE_SAMPLE_RATE, YOUTUBE_BITRATE,
    YOUTUBE_VIDEO_SOURCE, YOUTUBE_VIDEO_FILE, YOUTUBE_VIDEO_COLOR, YOUTUBE_VIDEO_SIZE, YOUTUBE_VIDEO_FPS
)

# Logging is configured in main.py
logger = logging.getLogger(__name__)

class MusicPlayer:
    """
    Main music player that orchestrates audio playback with smart playlist management.
    
    This class coordinates all components of the radio system to provide intelligent
    music playback with DJ segments, holiday songs, and optional YouTube streaming.
    
    The player implements several intelligent features:
    - **Weighted song selection**: Songs are selected based on play history, ensuring
      variety and fair distribution
    - **Holiday song integration**: Automatically includes holiday songs during
      November-December with date-based probability
    - **Dynamic DJ probability**: DJ intro/outro probability increases the longer
      it's been since the DJ last talked
    - **YouTube streaming**: Optional simultaneous streaming to YouTube Live
    
    Attributes:
        regular_music_path (str): Path to regular music directory
        holiday_music_path (str): Path to holiday music directory
        dj_path (str): Path to DJ intro/outro files directory
        audio_player (AudioPlayer): Audio playback handler
        playlist_manager (PlaylistManager): Playlist and history manager
        dj_manager (DJManager): DJ intro/outro file manager
        file_manager (FileManager): File system operations manager
        youtube_streamer (Optional[YouTubeStreamer]): YouTube streaming handler (if enabled)
        songs_since_last_dj_talk (int): Counter for dynamic DJ probability
        
    Example:
        ```python
        player = MusicPlayer(
            regular_music_path="/music/songs",
            holiday_music_path="/music/holiday",
            dj_path="/music/dj"
        )
        
        # Play songs in a loop
        while True:
            player.play_random_mp3()
        ```
    """
    
    def __init__(
        self, 
        regular_music_path: str, 
        holiday_music_path: str, 
        dj_path: str
    ) -> None:
        """
        Initialize the music player.
        
        Args:
            regular_music_path: Path to regular music directory
            holiday_music_path: Path to holiday music directory
            dj_path: Path to DJ intro/outro files directory
        """
        self.regular_music_path = regular_music_path
        self.holiday_music_path = holiday_music_path
        self.dj_path = dj_path
        self.audio_player = AudioPlayer()
        self.playlist_manager = PlaylistManager()
        self.dj_manager = DJManager(dj_path)  # Pass path to constructor
        self.file_manager = FileManager()
        
        # Track DJ talk timing for dynamic probability
        self.songs_since_last_dj_talk = 0
        
        # Initialize YouTube streamer if enabled
        # Note: YouTube streaming failure should not prevent radio from running
        self.youtube_streamer = None
        if YOUTUBE_ENABLED and YOUTUBE_STREAM_KEY:
            try:
                from .youtube_streamer import YouTubeStreamer
                self.youtube_streamer = YouTubeStreamer(
                    stream_key=YOUTUBE_STREAM_KEY,
                    audio_device=YOUTUBE_AUDIO_DEVICE,
                    audio_format=YOUTUBE_AUDIO_FORMAT,
                    sample_rate=YOUTUBE_SAMPLE_RATE,
                    bitrate=YOUTUBE_BITRATE,
                    video_source=YOUTUBE_VIDEO_SOURCE,
                    video_file=YOUTUBE_VIDEO_FILE,
                    video_color=YOUTUBE_VIDEO_COLOR,
                    video_size=YOUTUBE_VIDEO_SIZE,
                    video_fps=YOUTUBE_VIDEO_FPS
                )
                logger.info("YouTube streaming enabled")
            except Exception as e:
                logger.warning(f"Failed to initialize YouTube streamer: {e}", exc_info=True)
                logger.info("Radio will continue without YouTube streaming")
                self.youtube_streamer = None
        
        # Validate paths exist
        self._validate_paths()
        
        self.playlist_manager.initialize_play_counts(regular_music_path, holiday_music_path)
        logger.info("MusicPlayer initialized")
    
    def _validate_paths(self) -> None:
        """
        Validate that all required paths exist and are accessible.
        
        This method checks each configured path (regular music, holiday music, DJ)
        and validates:
        - Path is not empty
        - Path exists
        - Path is a directory (not a file)
        - Path is readable
        
        Warnings are logged for any validation failures, but the player will
        continue to operate (it will simply have no files to play from invalid paths).
        
        Note:
            This method is called during initialization and logs warnings only.
            It does not raise exceptions or prevent player initialization.
        """
        for path_name, path in [
            ('Regular music', self.regular_music_path),
            ('Holiday music', self.holiday_music_path),
            ('DJ', self.dj_path)
        ]:
            if not path:
                logger.warning(f"{path_name} path is empty")
                continue
            
            if not os.path.exists(path):
                logger.warning(f"{path_name} path does not exist: {path}")
            elif not os.path.isdir(path):
                logger.warning(f"{path_name} path is not a directory: {path}")
            elif not os.access(path, os.R_OK):
                logger.warning(f"{path_name} path is not readable: {path}")
    
    def _calculate_dj_probability(self) -> float:
        """
        Calculate dynamic DJ talk probability based on time since last talk.
        
        The probability increases linearly the longer it's been since the DJ
        last talked, creating a natural progression from music-heavy to DJ-heavy
        as time passes.
        
        Probability progression:
        - First DJ_SONGS_BEFORE_INCREASE songs: DJ_BASE_PROBABILITY (e.g., 20%)
        - After that: Linear increase to DJ_MAX_PROBABILITY (e.g., 85%)
        - Reaches max after DJ_MAX_SONGS_FOR_MAX_PROB songs (e.g., 8 songs)
        
        Returns:
            Probability value between DJ_BASE_PROBABILITY and DJ_MAX_PROBABILITY.
            This is a float between 0.0 and 1.0 representing the chance that
            a DJ intro or outro will be played.
            
        Example:
            If DJ_BASE_PROBABILITY=0.2, DJ_MAX_PROBABILITY=0.85, and it's been
            5 songs since last DJ talk:
            - First 3 songs: 0.2 (20%)
            - Song 4: ~0.33 (33%)
            - Song 5: ~0.46 (46%)
            - Song 8+: 0.85 (85%)
        """
        if self.songs_since_last_dj_talk < DJ_SONGS_BEFORE_INCREASE:
            # Use base probability for first few songs (music-friendly)
            return DJ_BASE_PROBABILITY
        
        # Calculate how much to increase probability
        songs_over_base = self.songs_since_last_dj_talk - DJ_SONGS_BEFORE_INCREASE
        max_increase_songs = DJ_MAX_SONGS_FOR_MAX_PROB - DJ_SONGS_BEFORE_INCREASE
        
        # Linear increase from base to max
        if songs_over_base >= max_increase_songs:
            return DJ_MAX_PROBABILITY
        
        increase_factor = songs_over_base / max_increase_songs
        probability = DJ_BASE_PROBABILITY + (DJ_MAX_PROBABILITY - DJ_BASE_PROBABILITY) * increase_factor
        
        return min(probability, DJ_MAX_PROBABILITY)

    def _get_song_files(self) -> tuple[List[str], List[str]]:
        """
        Get MP3 files from both regular and holiday directories.
        
        This method uses the FileManager to retrieve cached file lists from
        both music directories. The cache is automatically refreshed when
        directories are modified.
        
        Returns:
            Tuple of (regular_files, holiday_files) where each is a list of
            MP3 filenames (not full paths) from the respective directory.
            
        Note:
            - Uses cached results for performance
            - Returns empty lists if directories don't exist or are unreadable
            - Only returns files with .mp3 extension
        """
        regular_files = self.file_manager.get_mp3_files(self.regular_music_path)
        holiday_files = self.file_manager.get_mp3_files(self.holiday_music_path)
        return regular_files, holiday_files
    
    def _select_song(self, regular_files: List[str], holiday_files: List[str]) -> tuple[str, bool]:
        """
        Select a song based on holiday probability and weighted selection.
        
        This method implements a two-stage selection process:
        1. First, decide whether to select a holiday song based on date-based
           probability (get_holiday_selection_probability())
        2. Then, select a specific song using weighted random selection based
           on play history and fairness
        
        The weighted selection ensures:
        - Songs played recently are less likely to be selected
        - Songs never played get a bonus
        - Play counts are balanced over time
        
        Args:
            regular_files: List of regular song filenames
            holiday_files: List of holiday song filenames
            
        Returns:
            Tuple of (selected_song_filename, is_holiday) where:
            - selected_song_filename: Name of the selected MP3 file
            - is_holiday: True if holiday song, False if regular song
            
        Raises:
            ValueError: If no regular songs are available (holiday songs are optional)
            
        Note:
            - Holiday selection probability is date-based (Nov-Dec)
            - Regular song selection uses weighted probabilities from PlaylistManager
            - Logs selection details at INFO level
        """
        holiday_probability = self.playlist_manager.get_holiday_selection_probability()
        holiday_roll = random.random()
        
        logger.info(f"🎄 Holiday prob: {holiday_probability:.1%}, rolled: {holiday_roll:.3f}", extra={'simple': True})
        
        if holiday_files and holiday_roll < holiday_probability:
            # Pick a random holiday song
            selected = random.choice(holiday_files)
            logger.info(f"🎄 Selected HOLIDAY song: {selected}", extra={'simple': True})
            return selected, True
        else:
            # Use weighted selection from regular songs
            if not regular_files:
                raise ValueError("No regular songs available")
            
            probabilities, all_files, _ = self.playlist_manager.calculate_probabilities(
                regular_files, [])  # Empty holiday list for regular-only selection
            
            selected_index = random.choices(range(len(all_files)), weights=probabilities)[0]
            selected = all_files[selected_index]
            logger.info(f"🎵 Selected REGULAR song: {selected}", extra={'simple': True})
            return selected, False
    
    def _play_dj_segment(self, mp3_file: str, segment_type: str, dj_probability: float) -> bool:
        """
        Attempt to play a DJ intro or outro segment.
        
        This method:
        1. Checks for available DJ files matching the song name
        2. Rolls a random number against dj_probability
        3. If successful, randomly selects one of the available files
        4. Plays the selected file
        
        DJ file naming convention:
        - song_intro.mp3 or song_intro1.mp3, song_intro2.mp3, etc.
        - song_outro.mp3 or song_outro1.mp3, song_outro2.mp3, etc.
        
        Args:
            mp3_file: Name of the song file (e.g., "song.mp3")
            segment_type: Either 'intro' or 'outro'
            dj_probability: Current DJ talk probability (0.0 to 1.0)
            
        Returns:
            True if segment was played successfully, False otherwise.
            False can mean:
            - No DJ files found for this song
            - Random roll failed (probability check)
            - File playback failed
            
        Note:
            - Logs detailed information about the selection process
            - Uses DJManager to find matching files
            - Only one intro OR outro is played per song (not both)
        """
        check_method = self.dj_manager.check_intro_files if segment_type == 'intro' else self.dj_manager.check_outro_files
        files = check_method(mp3_file)
        
        if not files:
            logger.info(f"  📢 No {segment_type} files found", extra={'simple': True})
            return False
        
        roll = random.random()
        logger.info(f"  📢 {segment_type.capitalize()} files: {len(files)}, rolled: {roll:.3f}", extra={'simple': True})
        
        if roll < dj_probability:
            selected_file = random.choice(files)
            logger.info(f"  ✅ Playing {segment_type.upper()}: {selected_file}", extra={'simple': True})
            file_path = os.path.join(self.dj_path, selected_file)
            if self.audio_player.play(file_path):
                return True
            else:
                logger.warning(f"Failed to play {segment_type}: {selected_file}")
                return False
        else:
            logger.info(f"  ❌ {segment_type.capitalize()} skipped (roll {roll:.3f} >= prob {dj_probability:.3f})", extra={'simple': True})
            return False
    
    def play_random_mp3(self) -> bool:
        """
        Play a random MP3 file with optional intro and outro.
        
        This is the main playback method that orchestrates the entire song
        playback process:
        1. Get available songs from both directories
        2. Select a song using intelligent weighted selection
        3. Calculate dynamic DJ probability
        4. Attempt to play intro (if probability check passes)
        5. Play the selected song
        6. Attempt to play outro (only if intro didn't play)
        7. Update play history and counts
        
        The method includes comprehensive error handling to ensure the player
        continues operating even if individual components fail.
        
        Returns:
            True if a song was played successfully, False otherwise.
            False can occur if:
            - No songs are available
            - Song selection fails
            - File doesn't exist (cache may be stale)
            - Audio playback fails
            
        Note:
            - Updates DJ talk counter after each song
            - Resets DJ counter if intro or outro is played
            - Invalidates file cache if selected song doesn't exist
            - Updates playlist history for intelligent future selection
            - All errors are logged with full context
            
        Example:
            ```python
            while True:
                if player.play_random_mp3():
                    # Song played successfully
                    pass
                else:
                    # Handle error (e.g., wait before retry)
                    time.sleep(5)
            ```
        """
        # Get available files
        try:
            regular_files, holiday_files = self._get_song_files()
        except Exception as e:
            logger.error(f"Error getting song files: {e}", exc_info=True)
            return False
        
        if not regular_files and not holiday_files:
            logger.warning("No MP3 files found in the folders.")
            return False
        
        # Select song
        try:
            random_mp3, is_holiday_song = self._select_song(regular_files, holiday_files)
        except ValueError as e:
            logger.error(f"Song selection error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error selecting song: {e}", exc_info=True)
            return False
        
        # Determine music path
        music_path = self.holiday_music_path if is_holiday_song else self.regular_music_path
        
        # Validate song file exists before attempting to play
        song_path = os.path.join(music_path, random_mp3)
        if not os.path.exists(song_path):
            logger.error(f"Selected song file does not exist: {song_path}")
            # Invalidate cache to force refresh
            self.file_manager.invalidate_cache(music_path)
            return False
        
        # Calculate DJ probability
        dj_probability = self._calculate_dj_probability()
        self.songs_since_last_dj_talk += 1
        
        logger.info(f"🎤 DJ prob: {dj_probability:.1%}, songs since last talk: {self.songs_since_last_dj_talk}", extra={'simple': True})
        
        # Play intro
        try:
            play_intro = self._play_dj_segment(random_mp3, 'intro', dj_probability)
            dj_talked = play_intro
        except Exception as e:
            logger.error(f"Error playing intro: {e}", exc_info=True)
            play_intro = False
            dj_talked = False
        
        # Play song
        if not self.audio_player.play(song_path):
            logger.error(f"Failed to play song: {random_mp3}")
            return False
        
        # Play outro (only if intro didn't play)
        if not play_intro:
            try:
                play_outro = self._play_dj_segment(random_mp3, 'outro', dj_probability)
                if play_outro:
                    dj_talked = True
            except Exception as e:
                logger.error(f"Error playing outro: {e}", exc_info=True)
        else:
            logger.info(f"  📢 Outro skipped (intro already played)", extra={'simple': True})
        
        # Reset counter if DJ talked
        if dj_talked:
            self.songs_since_last_dj_talk = 0
            logger.info(f"  🎤 DJ talked - resetting counter", extra={'simple': True})
        
        # Update history
        try:
            self.playlist_manager.update_history(random_mp3, is_holiday_song)
        except Exception as e:
            logger.warning(f"Error updating history: {e}", exc_info=True)
            # Don't fail playback if history update fails
        
        return True

    def start_youtube_stream(self) -> bool:
        """
        Start YouTube streaming if enabled.
        
        This is a convenience method that delegates to the YouTubeStreamer
        if it's been initialized. If YouTube streaming is not enabled or
        not configured, this method returns True (not an error condition).
        
        Returns:
            True if:
            - Streaming started successfully, OR
            - YouTube streaming is not enabled
            False if streaming is enabled but failed to start
            
        Note:
            - Safe to call even if YouTube streaming is disabled
            - Logs errors if streaming fails to start
            - Does not block; streaming runs in background
        """
        if self.youtube_streamer:
            return self.youtube_streamer.start()
        return True

    def stop_youtube_stream(self) -> None:
        """
        Stop YouTube streaming if active.
        
        This is a convenience method that safely stops the YouTube stream
        if it's running. It's safe to call even if streaming is not enabled
        or not currently active.
        
        Note:
            - Safe to call multiple times
            - No-op if streaming is not enabled or not active
            - Performs graceful shutdown of FFmpeg process
        """
        if self.youtube_streamer:
            self.youtube_streamer.stop()
    
    def sigterm_handler(self, _signo: int, _stack_frame) -> None:
        """
        Handle termination signal for graceful shutdown.
        
        This method is registered as a signal handler for SIGTERM (and optionally
        SIGHUP) to allow the application to shut down gracefully when stopped by
        systemd or other process managers.
        
        The handler:
        1. Stops audio playback
        2. Stops YouTube streaming (if active)
        3. Exits the application
        
        Args:
            _signo: Signal number (e.g., signal.SIGTERM)
            _stack_frame: Stack frame (unused, required by signal handler signature)
            
        Note:
            - This method calls sys.exit(0) to terminate the application
            - Should be registered with signal.signal() during initialization
            - Logs shutdown message before exiting
        """
        logger.info("Received termination signal, shutting down gracefully...")
        self.audio_player.stop()
        self.stop_youtube_stream()
        sys.exit(0)

def main() -> None:
    """Main entry point for the radio player."""
    from .constants import REGULAR_MUSIC_PATH, HOLIDAY_MUSIC_PATH, DJ_PATH
    
    # Create an instance of MusicPlayer
    player = MusicPlayer(REGULAR_MUSIC_PATH, HOLIDAY_MUSIC_PATH, DJ_PATH)

    # Handle SIGTERM to allow for graceful shutdown
    signal.signal(signal.SIGTERM, player.sigterm_handler)

    # Loop indefinitely to play random MP3s
    logger.info("Starting radio player...")
    while True:
        try:
            player.play_random_mp3()
        except KeyboardInterrupt:
            logger.info("Interrupted by user, shutting down...")
            player.audio_player.stop()
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            # Continue playing despite errors

if __name__ == "__main__":
    main()