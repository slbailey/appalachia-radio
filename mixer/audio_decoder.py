"""
Audio decoder using FFmpeg for MP3 to PCM conversion.

This module provides the AudioDecoder class, which uses FFmpeg subprocess
with pipe output to decode audio files to raw PCM frames.
"""

import logging
import subprocess
from typing import Generator, Optional
from broadcast_core.event_queue import AudioEvent

logger = logging.getLogger(__name__)


class AudioDecoder:
    """
    Audio decoder using FFmpeg subprocess with pipe output.
    
    Decodes audio files (MP3, etc.) to raw PCM frames using FFmpeg.
    Uses streaming generator interface for frame-by-frame processing.
    """
    
    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        frame_size: int = 4096
    ) -> None:
        """
        Initialize the audio decoder.
        
        Args:
            sample_rate: Output sample rate in Hz (default: 48000)
            channels: Number of output channels (default: 2 = stereo)
            frame_size: Frame size in bytes (default: 4096)
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_size = frame_size
        self._process: Optional[subprocess.Popen] = None
    
    def stream_frames(self, event: AudioEvent) -> Generator[bytes, None, None]:
        """
        Stream PCM frames from an audio file.
        
        Uses FFmpeg to decode the file and yields PCM frame chunks.
        
        Args:
            event: AudioEvent containing path to audio file
            
        Yields:
            Raw PCM frame bytes (frame_size bytes per frame)
            
        Raises:
            FileNotFoundError: If audio file doesn't exist
            subprocess.CalledProcessError: If FFmpeg fails
        """
        import os
        
        if not os.path.exists(event.path):
            raise FileNotFoundError(f"Audio file not found: {event.path}")
        
        # Build FFmpeg command
        # -f s16le: 16-bit signed little-endian PCM
        # -ac: channels
        # -ar: sample rate
        # pipe:1: Output to stdout
        cmd = [
            "ffmpeg",
            "-i", event.path,
            "-f", "s16le",
            "-ac", str(self.channels),
            "-ar", str(self.sample_rate),
            "-loglevel", "error",  # Suppress FFmpeg output
            "pipe:1"
        ]
        
        try:
            # Spawn FFmpeg process
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0  # Unbuffered for real-time
            )
            
            # Read frames from stdout
            while True:
                frame = self._process.stdout.read(self.frame_size)
                if not frame:
                    break
                if len(frame) < self.frame_size:
                    # Pad last frame if needed
                    frame += b'\x00' * (self.frame_size - len(frame))
                yield frame
            
            # Wait for process to finish
            self._process.wait()
            
        except Exception as e:
            logger.error(f"Error decoding {event.path}: {e}")
            if self._process:
                self._process.terminate()
            raise
        finally:
            if self._process:
                self._process = None
    
    def close(self) -> None:
        """Close the decoder and cleanup resources."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=1.0)
            except Exception:
                pass
            self._process = None

