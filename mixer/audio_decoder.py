"""
Audio decoder using FFmpeg for MP3 to PCM conversion.

This module provides the AudioDecoder class, which uses FFmpeg subprocess
with pipe output to decode audio files to raw PCM frames one frame at a time.
"""

import logging
import fcntl
import os
import select
import subprocess
from typing import Optional
from broadcast_core.event_queue import AudioEvent

logger = logging.getLogger(__name__)


class AudioDecoder:
    """
    Audio decoder using FFmpeg subprocess with pipe output.
    
    Decodes audio files (MP3, etc.) to raw PCM frames using FFmpeg.
    Provides next_frame() method for clock-driven, one-frame-at-a-time decoding.
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
        self._current_event: Optional[AudioEvent] = None
        self._first_frame = True
        self._frame_count = 0
    
    def start(self, event: AudioEvent) -> bool:
        """
        Start decoding an audio event.
        
        Args:
            event: AudioEvent containing path to audio file
            
        Returns:
            True if started successfully, False otherwise
        """
        if not os.path.exists(event.path):
            logger.error(f"Audio file not found: {event.path}")
            return False
        
        # Close any existing process
        self.close()
        
        # Build FFmpeg command
        cmd = [
            "ffmpeg",
            "-i", event.path,
            "-f", "s16le",
            "-ac", str(self.channels),
            "-ar", str(self.sample_rate),
            "-loglevel", "error",
            "pipe:1"
        ]
        
        try:
            logger.info(f"[DECODER] Starting FFmpeg decoder for: {event.path}")
            
            # Spawn FFmpeg process
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0  # Unbuffered for real-time
            )
            
            # Set stdout to non-blocking mode
            if self._process.stdout:
                try:
                    import fcntl
                    fd = self._process.stdout.fileno()
                    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                except Exception as e:
                    logger.warning(f"Failed to set stdout non-blocking: {e}")
            
            # Give FFmpeg a moment to initialize
            import time
            time.sleep(0.1)  # 100ms initialization
            
            # Check if process started successfully
            if self._process.poll() is not None:
                stderr_output = self._process.stderr.read()
                if stderr_output:
                    error_msg = stderr_output.decode('utf-8', errors='ignore')
                    logger.error(f"[DECODER] FFmpeg failed to start: {error_msg}")
                return False
            
            self._current_event = event
            self._first_frame = True
            self._frame_count = 0
            
            logger.debug(f"[DECODER] Decoder started for: {event.path}")
            return True
            
        except Exception as e:
            logger.error(f"[DECODER] Error starting decoder for {event.path}: {e}")
            if self._process:
                self._process.terminate()
                self._process = None
            return False
    
    def next_frame(self) -> Optional[bytes]:
        """
        Get the next PCM frame from the current event.
        
        This method is called once per clock tick. Uses non-blocking read
        with select to avoid blocking the clock tick handler.
        
        Returns:
            bytes: Next PCM frame (frame_size bytes), or None if EOF or not ready
        """
        if not self._process or not self._current_event:
            return None
        
        # Check if process died
        if self._process.poll() is not None:
            # Process finished - check for errors
            stderr_output = self._process.stderr.read()
            if stderr_output:
                error_msg = stderr_output.decode('utf-8', errors='ignore')
                logger.error(f"[DECODER] FFmpeg stderr for {self._current_event.path}: {error_msg}")
            
            logger.info(f"[DECODER] EOF reached after {self._frame_count} frames: {self._current_event.path}")
            self.close()
            return None
        
        # Use select with zero timeout for truly non-blocking read
        # Clock tick must not block - if data not ready, return empty and try next tick
        
        # Zero timeout = non-blocking check only
        ready, _, _ = select.select([self._process.stdout], [], [], 0.0)
        
        if not ready:
            # No data available yet - return empty bytes to indicate "not ready"
            # Mixer will use buffer or silence, but won't treat as EOF
            return b''
        
        # Data available - read frame_size bytes (non-blocking)
        # Since stdout is non-blocking, read will return available data immediately
        try:
            frame = os.read(self._process.stdout.fileno(), self.frame_size)
        except BlockingIOError:
            # Shouldn't happen if select() said ready, but handle it
            return b''
        except OSError:
            # Process may have closed stdout
            if self._process.poll() is not None:
                logger.info(f"[DECODER] EOF reached after {self._frame_count} frames: {self._current_event.path}")
                self.close()
                return None
            return b''
        
        if not frame:
            # Empty read - check if process finished
            if self._process.poll() is not None:
                logger.info(f"[DECODER] EOF reached after {self._frame_count} frames: {self._current_event.path}")
                self.close()
                return None  # Actual EOF
            # Process still running but no data - return empty to try again
            return b''
        
        # Handle partial frames
        if len(frame) < self.frame_size:
            # Not a full frame yet - check if process finished (last frame of file)
            if self._process.poll() is not None:
                # Process finished - this is the last frame, pad it
                frame += b'\x00' * (self.frame_size - len(frame))
            else:
                # Process still running but partial frame - not ready yet
                # Put the partial data back? Actually, we can't with os.read()
                # For now, pad it to avoid blocking, but this is suboptimal
                # TODO: Accumulate partial frames in decoder state
                frame += b'\x00' * (self.frame_size - len(frame))
        
        self._frame_count += 1
        
        if self._first_frame:
            logger.info(f"[DECODER] First frame received: {len(frame)} bytes from {self._current_event.path}")
            self._first_frame = False
        
        return frame
    
    def is_active(self) -> bool:
        """
        Check if decoder is actively decoding.
        
        Returns:
            True if decoder has an active process, False otherwise
        """
        return self._process is not None and self._current_event is not None
    
    def get_current_event(self) -> Optional[AudioEvent]:
        """
        Get the current event being decoded.
        
        Returns:
            Current AudioEvent or None
        """
        return self._current_event
    
    def close(self) -> None:
        """Close the decoder and cleanup resources."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=1.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        
        self._current_event = None
        self._first_frame = True
        self._frame_count = 0
