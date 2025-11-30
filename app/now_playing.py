"""
Now playing metadata writer.

This module provides NowPlaying dataclass and NowPlayingWriter for
writing current track metadata to a JSON file.
"""

import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class NowPlaying:
    """
    Metadata for currently playing track.
    
    Attributes:
        title: Song title (typically filename)
        path: Full path to the song file
        started_at: UNIX timestamp when playback started
        intro_used: Whether an intro segment was played
        outro_used: Whether an outro segment was played
    """
    title: str
    path: str
    started_at: float
    intro_used: bool
    outro_used: bool


class NowPlayingWriter:
    """
    Thread-safe writer for now-playing metadata.
    
    Writes NowPlaying data to a JSON file atomically using
    a temporary file and rename operation.
    """
    
    def __init__(self, path: Path) -> None:
        """
        Initialize the now-playing writer.
        
        Args:
            path: Path to the JSON file to write
        """
        self._path = Path(path)
        self._lock = threading.Lock()
        
        # Ensure parent directory exists
        self._path.parent.mkdir(parents=True, exist_ok=True)
    
    def write(self, now_playing: NowPlaying) -> None:
        """
        Write now-playing metadata to file atomically.
        
        Uses a temporary file and rename to avoid partial writes.
        
        Args:
            now_playing: NowPlaying instance to write
        """
        with self._lock:
            # Create temporary file path
            tmp_path = self._path.with_suffix(".tmp")
            
            try:
                # Serialize to JSON
                data = asdict(now_playing)
                json_str = json.dumps(data, indent=2)
                
                # Write to temporary file
                with open(tmp_path, 'w') as f:
                    f.write(json_str)
                    f.write('\n')  # Trailing newline
                
                # Atomic rename
                os.replace(tmp_path, self._path)
                
            except Exception as e:
                # Clean up temp file on error
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                # Re-raise to allow caller to handle
                raise

