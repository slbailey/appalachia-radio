"""
Station orchestrator for the radio broadcast system.

This module provides the Station class, which orchestrates all components
and runs the main scheduling loop.
"""

import logging
import os
import time
import sys
import threading
from typing import Optional
from music_logic.library_manager import LibraryManager
from music_logic.playlist_manager import PlaylistManager
from dj_logic.dj_engine import DJEngine, DJSegment
from broadcast_core.playout_engine import PlayoutEngine
from broadcast_core.event_queue import AudioEvent
from app.now_playing import NowPlaying, NowPlayingWriter

logger = logging.getLogger(__name__)


class Station:
    """
    Main station orchestrator.
    
    Coordinates LibraryManager, PlaylistManager, DJEngine, and PlayoutEngine
    to run the radio station continuously.
    """
    
    def __init__(
        self,
        library_manager: LibraryManager,
        playlist_manager: PlaylistManager,
        dj_engine: DJEngine,
        playout_engine: PlayoutEngine,
        shutdown_event: threading.Event,
        now_playing_writer: Optional[NowPlayingWriter] = None,
        debug: bool = False
    ) -> None:
        """
        Initialize the station.
        
        Args:
            library_manager: LibraryManager instance
            playlist_manager: PlaylistManager instance
            dj_engine: DJEngine instance
            playout_engine: PlayoutEngine instance
            shutdown_event: threading.Event for graceful shutdown
            now_playing_writer: Optional NowPlayingWriter for metadata
            debug: Enable debug logging
        """
        self.library_manager = library_manager
        self.playlist_manager = playlist_manager
        self.dj_engine = dj_engine
        self.playout_engine = playout_engine
        self.shutdown_event = shutdown_event
        self.now_playing_writer = now_playing_writer
        self._running = False
        self.debug = debug
        self._restart_requested = False
        self._current_song_filename = None
        self._current_song_is_holiday = False
        self._current_song_finished = False  # Flag to track if current song finished during restart
        
        # Register callback to update playlist history when songs complete
        self.playout_engine.add_event_complete_callback(self._on_song_complete)
    
    def run(self) -> None:
        """
        Run the main station loop.
        
        Continuously selects songs, queues events, and processes playback.
        Matches Phase 7 spec: wait until playout is idle, then select next song.
        """
        self._running = True
        if self.debug:
            logger.info("Station started")
        
        try:
            while self._running:
                # Check if normal shutdown was requested (not a restart)
                if not self._running or (self.shutdown_event.is_set() and not self._restart_requested):
                    # Normal shutdown - break immediately
                    break
                
                # Wait until playout is idle before queuing next song
                # For restart, continue waiting even if shutdown_event is set
                # Poll every 250-500ms until engine is truly idle
                while self._running and not self.playout_engine.is_idle():
                    # If shutdown is requested but not a restart, break immediately
                    if self.shutdown_event.is_set() and not self._restart_requested:
                        break
                    
                    # For restart, check if current song finished (via callback)
                    # This is more reliable than checking decoder state
                    if self._restart_requested and self._current_song_finished:
                        logger.info("Restart: Current song finished, ready to restart")
                        break
                    
                    # Wait before checking again (Phase 7 spec: 250-500ms)
                    time.sleep(0.375)  # 375ms = middle of 250-500ms range
                
                # After playout becomes idle, check for restart
                if self._restart_requested:
                    # Playout is idle, safe to restart
                    logger.info("Restart: Current song finished, ready to restart")
                    break
                
                # Check again for normal shutdown
                if not self._running or (self.shutdown_event.is_set() and not self._restart_requested):
                    break
                
                # Get all tracks from library (Phase 7 spec: library.get_all_tracks())
                available_tracks = self.library_manager.get_all_tracks()
                
                if not available_tracks:
                    logger.warning("No tracks available, waiting...")
                    time.sleep(5.0)
                    continue
                
                # Select next song (Phase 7 spec: playlist.select_next_song(available_tracks))
                track = self.playlist_manager.select_next_song(available_tracks)
                filename = os.path.basename(track)
                
                # Build events using Phase 6 helper pattern (Phase 7 spec: build_events_for_song)
                # Import here to avoid circular import
                from app.radio import build_events_for_song
                events = build_events_for_song(
                    song_file=filename,
                    full_path=track,
                    dj_engine=self.dj_engine,
                    dj_path=self.dj_engine.dj_path
                )
                
                # Determine intro/outro usage for now-playing metadata
                intro_used = any(event.type == "intro" for event in events)
                outro_used = any(event.type == "outro" for event in events)
                
                # Write now-playing metadata (Phase 8)
                if self.now_playing_writer:
                    now_playing = NowPlaying(
                        title=filename,
                        path=track,
                        started_at=time.time(),
                        intro_used=intro_used,
                        outro_used=outro_used
                    )
                    try:
                        self.now_playing_writer.write(now_playing)
                    except Exception as e:
                        logger.error(f"Failed to write now-playing metadata: {e}")
                
                # Log song start with DJ usage (only in debug mode, events are logged separately)
                if self.debug:
                    logger.info(f"Now playing: {filename} (intro={intro_used}, outro={outro_used})")
                
                # Queue all events (Phase 7 spec: for ev in events: playout.queue_event(ev))
                for ev in events:
                    self.playout_engine.queue_event(ev)
                
                # Store current song info for history update
                self._current_song_filename = filename
                self._current_song_is_holiday = 'holiday' in track.lower()
                self._current_song_finished = False  # Reset flag when new song starts
                
                # Playout engine runs in its own thread, so events will be processed automatically
                
        except KeyboardInterrupt:
            if self.debug:
                logger.info("Received interrupt signal, shutting down...")
        except Exception as e:
            logger.error(f"Station error: {e}", exc_info=True)
        finally:
            self.stop()
    
    def stop(self) -> None:
        """Stop the station."""
        self._running = False
        try:
            if self.debug:
                logger.info("Stopping station...")
        except (OSError, ValueError):
            # Ignore logging errors during shutdown
            pass
        # Save playlist state on shutdown
        if hasattr(self, 'playlist_manager') and self.playlist_manager:
            self.playlist_manager.save_state()
        
        if self.debug:
            logger.info("Station stopped")
    
    def _on_song_complete(self, event: AudioEvent) -> None:
        """
        Callback when an event completes - update playlist history for songs.
        
        Args:
            event: Completed AudioEvent
        """
        # If this is a song event (not intro/outro), mark it as finished
        # This is used for graceful restart detection
        if event.type == "song":
            self._current_song_finished = True
        # Only update history for song events (not intro/outro)
        if event.type == "song" and self._current_song_filename:
            filename = os.path.basename(event.path)
            # Use stored holiday status
            self.playlist_manager.update_history(
                filename,
                self._current_song_is_holiday
            )

