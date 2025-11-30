"""
FM transmitter audio sink.

This module provides the FMSink class, which outputs PCM frames to an ALSA device
(FM transmitter) using aplay. This is the primary, always-active output sink.
"""

import logging
import subprocess
import sys
from typing import Optional
from outputs.sink_base import SinkBase

logger = logging.getLogger(__name__)


class FMSink(SinkBase):
    """
    FM transmitter audio sink.
    
    Primary output sink. Always active and critical path.
    Consumes PCM frames from mixer and outputs to ALSA device (FM transmitter).
    Uses aplay subprocess for reliable ALSA output.
    
    Handles device errors gracefully and reconnects automatically.
    """
    
    def __init__(
        self,
        device: str = "hw:1,0",
        sample_rate: int = 48000,
        channels: int = 2
    ) -> None:
        """
        Initialize the FM sink.
        
        Args:
            device: ALSA device name (e.g., "hw:1,0")
            sample_rate: Audio sample rate in Hz (default: 48000)
            channels: Number of audio channels (default: 2 = stereo)
        """
        super().__init__()
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self._process: Optional[subprocess.Popen] = None
    
    def start(self) -> bool:
        """
        Start the FM sink by spawning aplay process.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self._running:
            logger.warning("FMSink is already running")
            return True
        
        try:
            # Build aplay command
            # -f S16_LE: 16-bit signed little-endian PCM
            # -r: sample rate
            # -c: channels
            # -D: ALSA device
            cmd = [
                "aplay",
                "-f", "S16_LE",
                "-r", str(self.sample_rate),
                "-c", str(self.channels),
                "-D", self.device,
                "-"  # Read from stdin
            ]
            
            logger.info(f"Starting FMSink: aplay -D {self.device}")
            
            # Spawn aplay process
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0  # Unbuffered for real-time
            )
            
            # Give it a moment to start
            import time
            time.sleep(0.1)
            
            # Check if process is still running
            if self._process.poll() is not None:
                # Process died immediately
                stderr = self._process.stderr.read().decode('utf-8', errors='ignore') if self._process.stderr else "No error output"
                logger.error(f"aplay process exited immediately: {stderr[:500]}")
                self._process = None
                return False
            
            self._running = True
            logger.info("FMSink started successfully")
            return True
            
        except FileNotFoundError:
            logger.error("aplay not found. Please install: sudo apt-get install alsa-utils")
            return False
        except Exception as e:
            logger.error(f"Failed to start FMSink: {e}", exc_info=True)
            return False
    
    def write_frame(self, pcm_frame: bytes) -> None:
        """
        Write a PCM frame to the FM sink.
        
        Blocks until frame is written. Raises exception on error.
        
        Args:
            pcm_frame: Raw PCM frame bytes
        """
        if not self._running or self._process is None:
            return
        
        try:
            # Check if process is still running
            if self._process.poll() is not None:
                logger.error("aplay process died, attempting restart...")
                self._running = False
                if not self.start():
                    raise RuntimeError("Failed to restart FMSink")
                # Retry write after restart
                if self._process:
                    self._process.stdin.write(pcm_frame)
                    self._process.stdin.flush()
                return
            
            # Write frame to aplay stdin
            self._process.stdin.write(pcm_frame)
            self._process.stdin.flush()  # Ensure immediate transmission
            
        except BrokenPipeError:
            logger.error("FMSink: Broken pipe - aplay process may have died")
            self._running = False
            # Attempt restart
            if not self.start():
                raise RuntimeError("FMSink failed and could not restart")
        except Exception as e:
            # Check if we're shutting down
            if sys.is_finalizing():
                return
            logger.error(f"FMSink write error: {e}")
            self._running = False
            raise
    
    def stop(self) -> None:
        """
        Stop the FM sink by closing aplay process.
        """
        self._running = False
        
        if self._process is not None:
            try:
                # Close stdin
                if self._process.stdin:
                    self._process.stdin.close()
                
                # Terminate process
                self._process.terminate()
                
                # Wait for termination
                try:
                    self._process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't terminate
                    logger.warning("aplay process didn't terminate, killing...")
                    self._process.kill()
                    self._process.wait()
                
            except Exception as e:
                # Check if we're shutting down
                if not sys.is_finalizing():
                    logger.error(f"Error closing FMSink: {e}")
            finally:
                self._process = None
        
        logger.info("FMSink stopped")

