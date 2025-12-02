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
from dj_logic.dj_engine import DJEngine
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
        self._current_song_path = None  # Full path to currently playing song
        self._current_song_is_holiday = False
        self._current_song_finished = False  # Flag to track if current song finished during restart
        
        # Wire DJEngine to PlayoutEngine for event-driven decisions
        self.playout_engine.set_dj_engine(self.dj_engine)
        
        # DJ no longer uses queue_events_callback - decisions are returned directly
        # DJ no longer receives song_finished callbacks - only song_started
        
        # Register callback to update playlist history when songs complete
        self.playout_engine.add_event_complete_callback(self._on_song_complete)
        
        # Register metadata/history tracking callback
        self.playout_engine.add_event_start_callback(self._on_song_start)
    
    
    def run(self) -> None:
        """
        Run the main station loop.
        
        Pure event-driven model:
        - Station startup event → DJ decision → preload deck A
        - Song started event → DJ decision → preload opposite deck
        - Song finished event → DJ records completion
        - Loop only waits for shutdown/restart
        """
        self._running = True
        if self.debug:
            logger.info("Station started (DJ-driven event model)")
        
        try:
            while self._running:
                # Check if normal shutdown was requested (not a restart)
                if not self._running or (self.shutdown_event.is_set() and not self._restart_requested):
                    # Normal shutdown - break immediately
                    break
                
                # Wait until playout is idle before checking for restart
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
                
                # If we get here, playout is idle and we're not shutting down
                # This shouldn't normally happen in the event-driven model, but
                # we keep the loop for restart/shutdown handling
                time.sleep(0.5)  # Small sleep to prevent busy-waiting
                
        except KeyboardInterrupt:
            if self.debug:
                logger.info("Received interrupt signal, shutting down...")
        except Exception as e:
            logger.error(f"Station error: {e}", exc_info=True)
        finally:
            self._running = False
            
            # Save playlist state
            if hasattr(self, 'playlist_manager') and self.playlist_manager:
                self.playlist_manager.save_state()
            
            if self.debug:
                logger.info("Station stopped")
    
    def _on_song_start(self, event: AudioEvent) -> None:
        """
        Callback when an event starts playing - handles metadata and history tracking.
        
        DJ decisions are handled by DJEngine callbacks (on_song_started).
        
        Args:
            event: Started AudioEvent
        """
        # Only handle metadata for song events (not intro/outro)
        if event.type == "song":
            # Store current song path for history tracking
            self._current_song_path = event.path
            self._current_song_filename = os.path.basename(event.path)
            self._current_song_is_holiday = 'holiday' in event.path.lower()
            
            # Write now-playing metadata
            if self.now_playing_writer:
                now_playing = NowPlaying(
                    title=self._current_song_filename,
                    path=event.path,
                    started_at=time.time(),
                    intro_used=False,  # TODO: Track this from DJ decision
                    outro_used=False   # TODO: Track this from DJ decision
                )
                try:
                    self.now_playing_writer.write(now_playing)
                except Exception as e:
                    logger.error(f"Failed to write now-playing metadata: {e}")
    
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
