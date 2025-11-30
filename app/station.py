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
        now_playing_writer: Optional[NowPlayingWriter] = None
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
        """
        self.library_manager = library_manager
        self.playlist_manager = playlist_manager
        self.dj_engine = dj_engine
        self.playout_engine = playout_engine
        self.shutdown_event = shutdown_event
        self.now_playing_writer = now_playing_writer
        self._running = False
    
    def run(self) -> None:
        """
        Run the main station loop.
        
        Continuously selects songs, queues events, and processes playback.
        Matches Phase 7 spec: wait until playout is idle, then select next song.
        """
        self._running = True
        logger.info("Station started")
        
        try:
            while self._running and not self.shutdown_event.is_set():
                # Wait until playout is idle (Phase 7: poll every 250-500ms)
                while self._running and not self.shutdown_event.is_set() and not self.playout_engine.is_idle():
                    # Poll playout engine to process events
                    self.playout_engine.run()
                    # Wait 250-500ms before checking again (Phase 7 spec)
                    time.sleep(0.375)  # 375ms = middle of 250-500ms range
                
                if not self._running or self.shutdown_event.is_set():
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
                
                # Log song start with DJ usage
                logger.info(f"Now playing: {filename} (intro={intro_used}, outro={outro_used})")
                
                # Queue all events (Phase 7 spec: for ev in events: playout.queue_event(ev))
                for ev in events:
                    self.playout_engine.queue_event(ev)
                
                # Playout engine runs in its own thread, so events will be processed automatically
                
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")
        except Exception as e:
            logger.error(f"Station error: {e}", exc_info=True)
        finally:
            self.stop()
    
    def stop(self) -> None:
        """Stop the station."""
        self._running = False
        try:
            logger.info("Stopping station...")
        except (OSError, ValueError):
            # Ignore logging errors during shutdown
            pass
        logger.info("Station stopped")

