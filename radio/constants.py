"""Configuration constants for the radio player."""

import os
from typing import Final

# Directory paths (expands ~ to full home directory path)
# These can be configured via environment variables, with defaults shown below
REGULAR_MUSIC_PATH: Final[str] = os.path.expanduser(
    os.environ.get('REGULAR_MUSIC_PATH', '~/source/appalachia-radio/songs')
)
HOLIDAY_MUSIC_PATH: Final[str] = os.path.expanduser(
    os.environ.get('HOLIDAY_MUSIC_PATH', '~/source/appalachia-radio/holiday_songs')
)
DJ_PATH: Final[str] = os.path.expanduser(
    os.environ.get('DJ_PATH', '~/source/appalachia-radio/julie')
)

# DJ file limits
MAX_INTRO_FILES: Final[int] = 5
MAX_OUTRO_FILES: Final[int] = 5

# Playlist management
HISTORY_SIZE: Final[int] = 48  # Number of recent songs to track
IMMEDIATE_REPEAT_PENALTY: Final[float] = 0.01  # Weight multiplier for the very last song (almost eliminated)
RECENT_PLAY_WINDOW: Final[int] = 20  # Number of recent songs to apply penalties to
RECENT_PLAY_BASE_PENALTY: Final[float] = 0.1  # Base penalty for songs in recent window
RECENT_PLAY_DECAY: Final[float] = 0.15  # How quickly penalty decreases (higher = faster recovery)
NEVER_PLAYED_BONUS: Final[float] = 3.0  # Weight multiplier for unplayed songs
MAX_TIME_BONUS: Final[float] = 2.0  # Maximum time-based weight bonus

# DJ intro/outro probability (dynamic - increases over time)
DJ_BASE_PROBABILITY: Final[float] = 0.2  # Base chance to play intro/outro (music-friendly)
DJ_MAX_PROBABILITY: Final[float] = 0.85  # Maximum chance after long silence
DJ_SONGS_BEFORE_INCREASE: Final[int] = 3  # Number of songs before probability starts increasing
DJ_MAX_SONGS_FOR_MAX_PROB: Final[int] = 8  # Songs without DJ talk to reach max probability

# Holiday season settings
HOLIDAY_BOOST: Final[float] = 1.5  # Weight multiplier for holiday songs during season (reduced from 3.0)

# YouTube Live Streaming settings
# All YouTube settings must be configured via environment variables
# Get your stream key from: YouTube Studio > Go Live > Stream Settings
YOUTUBE_STREAM_KEY: Final[str] = os.environ.get('YOUTUBE_STREAM_KEY', '').strip()
# Check if YOUTUBE_ENABLED is explicitly set
_youtube_enabled_env = os.environ.get('YOUTUBE_ENABLED', '').strip().lower()
if _youtube_enabled_env:
    # Explicitly set - respect the setting
    _youtube_enabled = _youtube_enabled_env == 'true'
else:
    # Not explicitly set - auto-enable if stream key is present
    _youtube_enabled = bool(YOUTUBE_STREAM_KEY)
YOUTUBE_ENABLED: Final[bool] = _youtube_enabled
YOUTUBE_AUDIO_DEVICE: Final[str] = os.environ.get('YOUTUBE_AUDIO_DEVICE', 'default').strip()
YOUTUBE_AUDIO_FORMAT: Final[str] = os.environ.get('YOUTUBE_AUDIO_FORMAT', 'pulse').strip().lower()  # 'pulse' for PulseAudio, 'alsa' for ALSA
# Validate and parse sample rate
try:
    _sample_rate = int(os.environ.get('YOUTUBE_SAMPLE_RATE', '48000'))
    if _sample_rate < 8000 or _sample_rate > 192000:
        import logging
        logging.getLogger(__name__).warning(f"Unusual sample rate: {_sample_rate}, using 48000")
        _sample_rate = 48000
except (ValueError, TypeError):
    _sample_rate = 48000
YOUTUBE_SAMPLE_RATE: Final[int] = _sample_rate
YOUTUBE_BITRATE: Final[str] = os.environ.get('YOUTUBE_BITRATE', '128k').strip()
# Video track settings
# Video source type: 'color' (solid color), 'image' (static image), 'video' (video file), or 'none' (no video, audio-only)
YOUTUBE_VIDEO_SOURCE: Final[str] = os.environ.get('YOUTUBE_VIDEO_SOURCE', 'color').strip().lower()
YOUTUBE_VIDEO_FILE: Final[str] = os.environ.get('YOUTUBE_VIDEO_FILE', '').strip()  # Path to image or video file (if using 'image' or 'video')
YOUTUBE_VIDEO_COLOR: Final[str] = os.environ.get('YOUTUBE_VIDEO_COLOR', 'black').strip()  # Color name or hex (e.g., 'black', '#000000')
YOUTUBE_VIDEO_SIZE: Final[str] = os.environ.get('YOUTUBE_VIDEO_SIZE', '1280x720').strip()  # Video resolution (width x height)
# Validate and parse video FPS
try:
    _video_fps = int(os.environ.get('YOUTUBE_VIDEO_FPS', '2'))
    if _video_fps <= 0 or _video_fps > 120:
        import logging
        logging.getLogger(__name__).warning(f"Invalid video FPS: {_video_fps}, using 2")
        _video_fps = 2
except (ValueError, TypeError):
    _video_fps = 2
YOUTUBE_VIDEO_FPS: Final[int] = _video_fps