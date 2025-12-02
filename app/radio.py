"""
Main entry point for the radio broadcast system.

This module provides the main() function that initializes all components,
wires them together, and starts the station.
"""

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from music_logic.library_manager import LibraryManager
from music_logic.playlist_manager import PlaylistManager
from dj_logic.dj_engine import DJEngine
from mixer.audio_mixer import AudioMixer
from outputs.fm_sink import FMSink
from outputs.youtube_sink import YouTubeSink
from broadcast_core.playout_engine import PlayoutEngine
from broadcast_core.event_queue import AudioEvent
from app.station import Station
from app.config import RadioConfig, load_config_from_env_and_args
from app.now_playing import NowPlayingWriter
from clock.master_clock import MasterClock

logger = logging.getLogger(__name__)


def build_engine(
    regular_music_path: str,
    holiday_music_path: str,
    dj_path: str,
    fm_device: str,
    youtube_enabled: bool = False,
    youtube_rtmp_url: str | None = None,
    youtube_stream_key: str | None = None,
    video_source: str = "color",
    video_file: str | None = None,
    video_size: str = "1280x720",
    video_fps: int = 2,
    video_bitrate: str = "4000k",
    debug: bool = False
) -> tuple[AudioMixer, PlayoutEngine, DJEngine, MasterClock]:
    """
    Build and wire up the audio engine components.
    
    Args:
        regular_music_path: Path to regular music files directory
        holiday_music_path: Path to holiday music files directory
        dj_path: Path to DJ files directory
        fm_device: ALSA device for FM transmitter
        youtube_enabled: Whether YouTube streaming is enabled
        youtube_rtmp_url: Full RTMP URL for YouTube
        youtube_stream_key: YouTube stream key (used if rtmp_url not provided)
        video_source: Video source type ("color", "image", or "video")
        video_file: Path to video/image file (if video_source is "image" or "video")
        video_size: Video resolution (e.g., "1280x720")
        video_fps: Video frame rate
        video_bitrate: Video bitrate (e.g., "4000k")
    
    Returns:
        Tuple of (mixer, playout_engine, dj_engine, master_clock)
    """
    # Phase 9: Create MasterClock first
    sample_rate = 48000
    frame_size = 4096
    master_clock = MasterClock(
        sample_rate=sample_rate,
        frame_size=frame_size,
        dev_mode=False
    )
    
    # Create mixer with MasterClock
    mixer = AudioMixer(
        sample_rate=sample_rate,
        channels=2,
        frame_size=frame_size,
        master_clock=master_clock,
        debug=debug
    )
    
    # Create FM sink (always active)
    fm_sink = FMSink(device=fm_device, sample_rate=sample_rate, channels=2, frame_size=frame_size)
    mixer.add_sink(fm_sink)
    
    # Create YouTube sink (optional)
    if youtube_enabled:
        if not youtube_rtmp_url and youtube_stream_key:
            # Construct RTMP URL from stream key
            youtube_rtmp_url = f"rtmp://a.rtmp.youtube.com/live2/{youtube_stream_key}"
        
        if youtube_rtmp_url:
            youtube_sink = YouTubeSink(
                rtmp_url=youtube_rtmp_url,
                sample_rate=sample_rate,
                channels=2,
                frame_size=frame_size,
                video_source=video_source,
                video_file=video_file,
                video_size=video_size,
                video_fps=video_fps,
                video_bitrate=video_bitrate,
                debug=debug
            )
            mixer.add_sink(youtube_sink)
    
    # Create playout engine
    playout_engine = PlayoutEngine(mixer, debug=debug)
    
    # DJ engine will be created later with full dependencies (library_manager, playlist_manager, playlog)
    # Return None for now - will be set in start_station
    dj_engine = None
    
    return mixer, playout_engine, dj_engine, master_clock


def start_station(config: RadioConfig) -> None:
    """
    Start the radio station.
    
    Args:
        config: RadioConfig instance with all configuration values
    """
    # Initialize PID file path early (needed for cleanup in finally block)
    # Use /var/run if writable, otherwise fall back to project directory
    # (systemd uses private /tmp directories which cause path issues)
    default_pid_file = "/var/run/appalachia-radio.pid"
    if not os.access(os.path.dirname(default_pid_file), os.W_OK):
        # Fall back to project directory if /var/run is not writable
        # Get project root (parent of app/ directory)
        project_root = Path(__file__).parent.parent
        default_pid_file = str(project_root / "appalachia-radio.pid")
    pid_file = os.environ.get("PID_FILE", default_pid_file)
    
    # Ensure logs directory exists
    os.makedirs("logs", exist_ok=True)
    
    # Log startup parameters
    youtube_status = "on" if config.enable_youtube else "off"
    logger.info(f"[STATION] Startup (FM {config.fm_device}, YouTube: {youtube_status})")
    
    # Initialize components
    library_manager = LibraryManager(
        regular_music_path=str(config.regular_music_path),
        holiday_music_path=str(config.holiday_music_path)
    )
    playlist_manager = PlaylistManager(state_file=str(config.playlist_state_path))
    # Load saved state if available
    playlist_manager.load_state()
    
    # Build RTMP URL if needed
    rtmp_url = config.rtmp_url
    if config.enable_youtube and not rtmp_url and config.youtube_stream_key:
        rtmp_url = f"rtmp://a.rtmp.youtube.com/live2/{config.youtube_stream_key}"
    
    # Build engine (now returns master_clock)
    mixer, playout_engine, _, master_clock = build_engine(
        regular_music_path=str(config.regular_music_path),
        holiday_music_path=str(config.holiday_music_path),
        dj_path=str(config.dj_path),
        fm_device=config.fm_device,
        youtube_enabled=config.enable_youtube,
        youtube_rtmp_url=rtmp_url,
        youtube_stream_key=config.youtube_stream_key,
        video_source=config.video_source,
        video_file=config.video_file,
        video_size=config.video_size,
        video_fps=config.video_fps,
        video_bitrate=config.video_bitrate,
        debug=config.debug
    )
    
    # Create DJ engine with full dependencies
    from dj_logic.dj_engine import DJEngine
    dj_engine = DJEngine(
        dj_path=str(config.dj_path),
        music_path=str(config.regular_music_path),
        library_manager=library_manager,
        playlist_manager=playlist_manager,
        playlog=playout_engine.playlog
    )
    
    # Create shutdown event (Phase 8)
    shutdown_event = threading.Event()
    
    # Update playout engine with stop_event
    playout_engine._stop_event = shutdown_event
    
    # Create now-playing writer (Phase 8)
    now_playing_writer = NowPlayingWriter(config.now_playing_path)
    
    # Phase 9: Start MasterClock first (must be running before sinks)
    master_clock.start()
    logger.debug("[STATION] MasterClock started")
    
    # Start sinks
    if mixer.fm_sink:
        if not mixer.fm_sink.start():
            logger.error("[STATION] Failed to start FM sink")
            master_clock.stop()
            return
    
    for sink in mixer.sinks:
        if sink is not mixer.fm_sink:
            try:
                sink.start()
                logger.debug(f"[STATION] {type(sink).__name__} started")
            except Exception as e:
                logger.warning(f"[STATION] Failed to start {type(sink).__name__}: {e}")
    
    # Create and start station
    station = Station(
        library_manager=library_manager,
        playlist_manager=playlist_manager,
        dj_engine=dj_engine,
        playout_engine=playout_engine,
        debug=config.debug,
        shutdown_event=shutdown_event,
        now_playing_writer=now_playing_writer
    )
    
    # Phase 4: Handle SIGUSR1 for graceful restart (wait for current event to finish)
    # Must be defined after playout_engine is created
    def restart_handler(sig, frame):
        logger.info("[STATION] Restart signal received")
        playout_engine.request_restart()
        # Also set shutdown to stop accepting new events
        shutdown_event.set()
    
    signal.signal(signal.SIGUSR1, restart_handler)
    
    # Phase 6: Start HTTP status server in background thread
    from app import dashboard
    dashboard_server = None
    dashboard_thread = None
    try:
        dashboard_server = dashboard.make_server(
            host="0.0.0.0",
            port=8080,
            get_health=playout_engine.health,
            get_now_playing=playout_engine.now_playing,
            get_next_up=playout_engine.next_up,
            get_recent_playlog=lambda limit: playout_engine.playlog.recent(limit),
        )
        dashboard_thread = dashboard.run_in_background(dashboard_server)
        logger.debug("[DASHBOARD] Started on port 8080")
    except Exception as e:
        logger.warning(f"[DASHBOARD] Failed to start: {e}")
    
    # Run playout engine in separate thread
    playout_thread = threading.Thread(target=playout_engine.run, daemon=True)
    playout_thread.start()
    
    # Write PID file for restart scripts
    try:
        pid_value = os.getpid()
        with open(pid_file, "w") as f:
            f.write(str(pid_value))
            f.flush()
            os.fsync(f.fileno())  # Force write to disk
        # Verify file exists and contains correct PID
        if os.path.exists(pid_file):
            with open(pid_file, "r") as f:
                stored_pid = f.read().strip()
            if stored_pid == str(pid_value):
                logger.debug(f"[STATION] PID file: {pid_file} (PID: {pid_value})")
            else:
                logger.warning(f"[STATION] PID file mismatch: expected {pid_value}, got {stored_pid}")
        else:
            logger.warning(f"[STATION] PID file was not created: {pid_file}")
    except Exception as e:
        logger.warning(f"[STATION] Could not write PID file: {e}")
    
    try:
        # Run station in main thread
        station.run()
    finally:
        # Cleanup
        logger.info("[STATION] Shutting down")
        shutdown_event.set()
        playout_engine.stop()
        
        # Phase 6: Stop HTTP status server
        if dashboard_server is not None:
            try:
                dashboard_server.shutdown()
                if dashboard_thread:
                    dashboard_thread.join(timeout=1.0)
            except Exception as e:
                logger.warning(f"[DASHBOARD] Error stopping: {e}")
        
        # Phase 9: Stop MasterClock first (stops frame delivery)
        master_clock.stop()
        
        # Stop all sinks
        for sink in mixer.sinks:
            try:
                sink.stop()
            except Exception as e:
                logger.error(f"[STATION] Error stopping sink: {e}")
        
        # Wait for playout thread to finish
        playout_thread.join(timeout=2.0)
        
        # Save playlist state before shutdown (via station, which has playlist_manager)
        try:
            station.playlist_manager.save_state()
        except Exception as e:
            if config.debug:
                logger.warning(f"[STATION] Failed to save playlist state: {e}")
        
        # Remove PID file (only if it contains our PID)
        try:
            if os.path.exists(pid_file):
                try:
                    with open(pid_file, "r") as f:
                        stored_pid = int(f.read().strip())
                    # Only remove if it's our PID (prevents removing PID from restarted process)
                    if stored_pid == os.getpid():
                        os.remove(pid_file)
                except (ValueError, OSError):
                    # File is corrupted or unreadable, remove it anyway
                    os.remove(pid_file)
        except Exception:
            pass


def main() -> None:
    """Main entry point."""
    # Parse CLI arguments
    parser = argparse.ArgumentParser(description="Appalachia Radio Station")
    
    # Music paths
    parser.add_argument(
        "--regular-music-path",
        type=str,
        help="Path to regular music files directory (overrides env)"
    )
    parser.add_argument(
        "--holiday-music-path",
        type=str,
        help="Path to holiday music files directory (overrides env)"
    )
    parser.add_argument(
        "--dj-path",
        type=str,
        help="Path to DJ files directory (overrides env)"
    )
    
    # FM device
    parser.add_argument(
        "--fm-device",
        type=str,
        help="ALSA device for FM transmitter (overrides env)"
    )
    
    # YouTube streaming
    parser.add_argument(
        "--youtube-enabled",
        action="store_true",
        help="Enable YouTube streaming (overrides env)"
    )
    parser.add_argument(
        "--youtube-rtmp-url",
        type=str,
        help="Full RTMP URL for YouTube streaming (overrides env)"
    )
    parser.add_argument(
        "--youtube-stream-key",
        type=str,
        help="YouTube stream key (overrides env)"
    )
    
    # Video settings
    parser.add_argument(
        "--video-source",
        type=str,
        choices=["color", "image", "video"],
        help="Video source type (overrides env)"
    )
    parser.add_argument(
        "--video-file",
        type=str,
        help="Path to video/image file (overrides env)"
    )
    parser.add_argument(
        "--video-size",
        type=str,
        help="Video resolution, e.g. '1280x720' (overrides env)"
    )
    parser.add_argument(
        "--video-fps",
        type=int,
        help="Video frame rate (overrides env)"
    )
    parser.add_argument(
        "--video-bitrate",
        type=str,
        help="Video bitrate, e.g. '4000k' (overrides env)"
    )
    
    # Logging
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (overrides env)"
    )
    
    # Now playing path
    parser.add_argument(
        "--now-playing-path",
        type=str,
        help="Path to now-playing JSON file (overrides env)"
    )
    
    args = parser.parse_args()
    
    # Load configuration from env and args
    config = load_config_from_env_and_args(args)
    
    # Configure logging (Phase 8)
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler('logs/radio.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Start station with config
    start_station(config)


if __name__ == "__main__":
    main()

