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
        frame_size: int = 4096,
        debug: bool = False
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
        self.debug = debug
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
            logger.error(f"[DECODER] File not found: {os.path.basename(event.path)}")
            return False
        
        # Close any existing process
        self.close()
        
        # Build FFmpeg command
        cmd = [
            "ffmpeg",
            "-fflags", "+bitexact",
            "-i", event.path,
            "-af", "aresample=async=1:min_comp=0.001:first_pts=0",
            "-f", "s16le",
            "-ac", str(self.channels),
            "-ar", str(self.sample_rate),
            "-loglevel", "error",
            "pipe:1"
        ]
        
        try:
            logger.debug(f"[DECODER] Starting: {os.path.basename(event.path)}")
            
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
                    logger.warning(f"[DECODER] Failed to set non-blocking: {e}")
            
            # Give FFmpeg a moment to initialize
            import time
            time.sleep(0.1)  # 100ms initialization
            
            # Check if process started successfully
            if self._process.poll() is not None:
                stderr_output = self._process.stderr.read()
                if stderr_output:
                    error_msg = stderr_output.decode('utf-8', errors='ignore')
                    logger.error(f"[DECODER] FFmpeg failed: {error_msg}")
                else:
                    logger.error(f"[DECODER] FFmpeg exited (code: {self._process.returncode})")
                return False
            
            self._current_event = event
            self._first_frame = True
            self._frame_count = 0
            
            logger.debug(f"[DECODER] Started: {os.path.basename(event.path)}")
            return True
            
        except Exception as e:
            logger.error(f"[DECODER] Error starting: {os.path.basename(event.path)}: {e}")
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
            # Store event path before close() clears it
            event_path = self._current_event.path if self._current_event else "unknown"
            frame_count = self._frame_count
            
            stderr_output = self._process.stderr.read()
            if stderr_output:
                error_msg = stderr_output.decode('utf-8', errors='ignore')
                logger.error(f"[DECODER] FFmpeg stderr: {error_msg}")
            
            logger.debug(f"[DECODER] EOF: {os.path.basename(event_path)} ({frame_count} frames)")
            # Don't close here - let mixer handle it so it can get the event first
            # Just mark process as done
            self._process = None
            # Keep _current_event so mixer can retrieve it before closing
            return None
        
        # Read frame using stdout.read() - simpler and more reliable for MP3/TTS
        try:
            frame = self._process.stdout.read(self.frame_size)
        except Exception:
            frame = b''
        
        if not frame:
            # If the process ended AND we've drained stdout → true EOF
            if self._process.poll() is not None:
                self._process = None
                return None
            # No data yet → not EOF
            return b''
        
        if not frame:
            # Empty read - check if process finished
            if self._process.poll() is not None:
                # Store event path before clearing
                event_path = self._current_event.path if self._current_event else "unknown"
                frame_count = self._frame_count
                logger.debug(f"[DECODER] EOF: {os.path.basename(event_path)} ({frame_count} frames)")
                # Don't close here - let mixer handle it so it can get the event first
                self._process = None
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
            if self.debug:
                logger.debug(f"[DECODER] First frame: {len(frame)} bytes")
            self._first_frame = False
        
        return frame
    
    def set_event(self, event: AudioEvent) -> None:
        """
        Set the event for this decoder WITHOUT starting FFmpeg.
        
        This is used for preloading - the event is stored but FFmpeg
        doesn't start until start() is called. This allows decision-making
        and preparation (e.g., API calls for speech generation) to happen
        while the active deck is playing, without starting audio decoding.
        
        FIX A: This method MUST store the event in a way that get_current_event()
        returns it reliably, even when FFmpeg is NOT started. The event is stored
        in _current_event, which is NOT cleared unless close() is explicitly called.
        
        Args:
            event: AudioEvent to set
        """
        # Close any existing FFmpeg process (but preserve event if already set)
        # Only close the process, don't clear the event yet
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
        
        # Store the event - this is the source of truth for get_current_event()
        # This MUST be set even if FFmpeg hasn't started
        self._current_event = event
        self._first_frame = True
        self._frame_count = 0
        logger.debug(f"[DECODER] Preloaded: {os.path.basename(event.path)}")
    
    def is_active(self) -> bool:
        """
        Check if decoder is actively decoding (FFmpeg process running).
        
        Returns:
            True if decoder has an active process, False otherwise
        """
        return self._process is not None and self._current_event is not None
    
    def has_event(self) -> bool:
        """
        Check if decoder has an event set (preloaded or active).
        
        Returns:
            True if decoder has an event (even if not started), False otherwise
        """
        return self._current_event is not None
    
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
