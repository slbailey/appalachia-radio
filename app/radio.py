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
    video_bitrate: str = "4000k"
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
        master_clock=master_clock
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
                master_clock=master_clock,  # Pass MasterClock for clock-driven operation
                sample_rate=sample_rate,
                channels=2,
                frame_size=frame_size,
                video_source=video_source,
                video_file=video_file,
                video_size=video_size,
                video_fps=video_fps,
                video_bitrate=video_bitrate
            )
            mixer.add_sink(youtube_sink)
    
    # Create playout engine
    playout_engine = PlayoutEngine(mixer)
    
    # Create DJ engine (music_path parameter kept for compatibility but not used)
    dj_engine = DJEngine(dj_path, regular_music_path)
    
    return mixer, playout_engine, dj_engine, master_clock


def build_events_for_song(
    song_file: str,
    full_path: str,
    dj_engine: DJEngine,
    dj_path: str
) -> list[AudioEvent]:
    """
    Build a list of AudioEvents for a song, including optional intro/outro.
    
    Returns: [ intro? ] → AudioEvent(song) → [ outro? ]
    Never returns both intro and outro together.
    
    Args:
        song_file: Song filename (e.g., "MySong.mp3")
        full_path: Full path to the song file
        dj_engine: DJEngine instance
        dj_path: Path to DJ files directory
        
    Returns:
        List of AudioEvent objects in play order
    """
    import os
    
    events: list[AudioEvent] = []
    
    # Get DJ segments for this song
    segments = dj_engine.get_segments_for_song(full_path)
    
    # Add intro if present
    for segment in segments:
        if segment.segment_type == "intro":
            intro_path = os.path.join(dj_path, segment.file_name)
            events.append(AudioEvent(path=intro_path, type="intro", gain=1.0))
            break  # Only one intro
    
    # Add the song itself
    events.append(AudioEvent(path=full_path, type="song", gain=1.0))
    
    # Add outro if present (only if no intro was added)
    if not any(e.type == "intro" for e in events):
        for segment in segments:
            if segment.segment_type == "outro":
                outro_path = os.path.join(dj_path, segment.file_name)
                events.append(AudioEvent(path=outro_path, type="outro", gain=1.0))
                break  # Only one outro
    
    return events


def start_station(config: RadioConfig) -> None:
    """
    Start the radio station.
    
    Args:
        config: RadioConfig instance with all configuration values
    """
    # Ensure logs directory exists
    os.makedirs("logs", exist_ok=True)
    
    # Log startup parameters (Phase 8)
    logger.info("Starting Appalachia Radio Station")
    logger.info(f"Music root (regular): {config.regular_music_path}")
    logger.info(f"Music root (holiday): {config.holiday_music_path}")
    logger.info(f"DJ path: {config.dj_path}")
    logger.info(f"FM device: {config.fm_device}")
    logger.info(f"YouTube enabled: {config.enable_youtube}")
    
    # Initialize components
    library_manager = LibraryManager(
        regular_music_path=str(config.regular_music_path),
        holiday_music_path=str(config.holiday_music_path)
    )
    playlist_manager = PlaylistManager()
    
    # Build RTMP URL if needed
    rtmp_url = config.rtmp_url
    if config.enable_youtube and not rtmp_url and config.youtube_stream_key:
        rtmp_url = f"rtmp://a.rtmp.youtube.com/live2/{config.youtube_stream_key}"
    
    # Build engine (now returns master_clock)
    mixer, playout_engine, dj_engine, master_clock = build_engine(
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
        video_bitrate=config.video_bitrate
    )
    
    # Create shutdown event (Phase 8)
    shutdown_event = threading.Event()
    
    # Update playout engine with stop_event
    playout_engine._stop_event = shutdown_event
    
    # Create now-playing writer (Phase 8)
    now_playing_writer = NowPlayingWriter(config.now_playing_path)
    
    # Phase 9: Start MasterClock first (must be running before sinks)
    master_clock.start()
    logger.info("MasterClock started")
    
    # Start sinks
    if mixer.fm_sink:
        if not mixer.fm_sink.start():
            logger.error("Failed to start FM sink")
            master_clock.stop()
            return
    
    for sink in mixer.sinks:
        if sink is not mixer.fm_sink:
            try:
                sink.start()
                if isinstance(sink, YouTubeSink):
                    logger.info("YouTube sink started")
            except Exception as e:
                logger.warning(f"Failed to start sink {type(sink).__name__}: {e}")
    
    # Create and start station
    station = Station(
        library_manager=library_manager,
        playlist_manager=playlist_manager,
        dj_engine=dj_engine,
        playout_engine=playout_engine,
        shutdown_event=shutdown_event,
        now_playing_writer=now_playing_writer
    )
    
    # Handle signals for graceful shutdown (Phase 8)
    def signal_handler(sig, frame):
        logger.info("Shutdown requested")
        shutdown_event.set()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run playout engine in separate thread
    playout_thread = threading.Thread(target=playout_engine.run, daemon=True)
    playout_thread.start()
    
    try:
        # Run station in main thread
        station.run()
    finally:
        # Cleanup (Phase 8 + Phase 9)
        logger.info("Shutting down...")
        shutdown_event.set()
        playout_engine.stop()
        
        # Phase 9: Stop MasterClock first (stops frame delivery)
        master_clock.stop()
        
        # Stop all sinks
        for sink in mixer.sinks:
            try:
                sink.stop()
            except Exception as e:
                logger.error(f"Error stopping sink: {e}")
        
        # Wait for playout thread to finish
        playout_thread.join(timeout=2.0)


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

