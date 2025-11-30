"""
Centralized configuration management for the radio station.

This module provides RadioConfig dataclass and configuration loading
from environment variables and CLI arguments.
"""

import os
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

logger = None  # Will be set after logging is configured


@dataclass
class RadioConfig:
    """
    Centralized configuration for the radio station.
    
    All configuration values can be loaded from environment variables
    or CLI arguments, with sensible defaults.
    """
    regular_music_path: Path  # Path to regular music directory
    holiday_music_path: Path  # Path to holiday music directory
    dj_path: Path  # Directory for DJ intros/outros
    fm_device: str  # ALSA device name, e.g. "default" or "hw:1,0"
    enable_youtube: bool  # Toggle YouTube sink
    rtmp_url: Optional[str]  # Full RTMP URL including stream key, if enabled
    youtube_stream_key: Optional[str]  # YouTube stream key (alternative to rtmp_url)
    video_source: str  # Video source type: "color", "image", or "video"
    video_file: Optional[str]  # Path to video/image file
    video_size: str  # Video resolution, e.g. "1280x720"
    video_fps: int  # Video frame rate
    video_bitrate: str  # Video bitrate, e.g. "4000k"
    refresh_interval_seconds: int  # Library refresh interval
    log_level: str  # Logging level: "INFO", "DEBUG", etc.
    now_playing_path: Path  # File to write current track metadata to


def load_config_from_env_and_args(args: Optional[argparse.Namespace] = None) -> RadioConfig:
    """
    Load configuration from environment variables and CLI arguments.
    
    Environment variables are loaded first, then CLI arguments override them.
    Paths with ~ are expanded to user home directory.
    
    Args:
        args: Optional argparse.Namespace from parsed CLI arguments
        
    Returns:
        RadioConfig instance with all configuration values
    """
    # Load .env file if it exists
    load_dotenv()
    
    # Helper to expand ~ in paths
    def expand_path(path_str: str) -> Path:
        """Expand ~ in path and convert to Path."""
        return Path(os.path.expanduser(path_str))
    
    # Helper to get value from args or env with default
    def get_value(arg_name: str, env_name: str, default: str) -> str:
        """Get value from args (if provided) or env, with default fallback."""
        if args and hasattr(args, arg_name):
            value = getattr(args, arg_name)
            if value is not None:
                return value
        return os.getenv(env_name, default)
    
    # Helper to get bool value
    def get_bool(arg_name: str, env_name: str, default: bool) -> bool:
        """Get bool value from args or env."""
        if args and hasattr(args, arg_name):
            value = getattr(args, arg_name)
            if value is not None:
                return bool(value)
        env_val = os.getenv(env_name)
        if env_val:
            return env_val.lower() in ('true', '1', 'yes', 'on')
        return default
    
    # Helper to get int value
    def get_int(arg_name: str, env_name: str, default: int) -> int:
        """Get int value from args or env."""
        if args and hasattr(args, arg_name):
            value = getattr(args, arg_name)
            if value is not None:
                return int(value)
        env_val = os.getenv(env_name)
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
        return default
    
    # Load configuration values
    regular_music_path = expand_path(
        get_value("regular_music_path", "REGULAR_MUSIC_PATH", "/home/pi/Music")
    )
    holiday_music_path = expand_path(
        get_value("holiday_music_path", "HOLIDAY_MUSIC_PATH", "/home/pi/HolidayMusic")
    )
    dj_path = expand_path(
        get_value("dj_path", "DJ_PATH", "/home/pi/DJ")
    )
    # FM device: Use SDL_AUDIODEVICE from .env, with CLI override and default fallback
    fm_device = get_value("fm_device", "SDL_AUDIODEVICE", "hw:1,0")
    enable_youtube = get_bool("youtube_enabled", "YOUTUBE_ENABLED", False)
    rtmp_url = get_value("youtube_rtmp_url", "YOUTUBE_RTMP_URL", None)
    youtube_stream_key = get_value("youtube_stream_key", "YOUTUBE_STREAM_KEY", None)
    video_source = get_value("video_source", "YOUTUBE_VIDEO_SOURCE", "color")
    video_file = get_value("video_file", "YOUTUBE_VIDEO_FILE", None)
    video_size = get_value("video_size", "YOUTUBE_VIDEO_SIZE", "1280x720")
    video_fps = get_int("video_fps", "YOUTUBE_VIDEO_FPS", 2)
    video_bitrate = get_value("video_bitrate", "YOUTUBE_VIDEO_BITRATE", "4000k")
    refresh_interval_seconds = get_int("refresh_interval", "REFRESH_INTERVAL_SECONDS", 300)
    log_level = get_value("log_level", "LOG_LEVEL", "INFO")
    now_playing_path = expand_path(
        get_value("now_playing_path", "NOW_PLAYING_PATH", "/tmp/now_playing.json")
    )
    
    return RadioConfig(
        regular_music_path=regular_music_path,
        holiday_music_path=holiday_music_path,
        dj_path=dj_path,
        fm_device=fm_device,
        enable_youtube=enable_youtube,
        rtmp_url=rtmp_url,
        youtube_stream_key=youtube_stream_key,
        video_source=video_source,
        video_file=video_file,
        video_size=video_size,
        video_fps=video_fps,
        video_bitrate=video_bitrate,
        refresh_interval_seconds=refresh_interval_seconds,
        log_level=log_level,
        now_playing_path=now_playing_path
    )

