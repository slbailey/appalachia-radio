"""
FM transmitter audio sink.

This module provides the FMSink class, which outputs PCM frames to an ALSA device
(FM transmitter) using aplay. This is the primary, always-active output sink.
"""

import logging
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Optional
from outputs.sink_base import SinkBase

logger = logging.getLogger(__name__)


class FMSink(SinkBase):
    """
    FM transmitter audio sink.
    
    Primary output sink. Always active and critical path.
    Consumes PCM frames from mixer and outputs to ALSA device (FM transmitter).
    Uses aplay subprocess for reliable ALSA output.
    
    Design: write_frame() is O(1) and never blocks. All I/O happens in a
    dedicated worker thread to ensure MasterClock tick handler never blocks.
    """
    
    def __init__(
        self,
        device: str = "hw:1,0",
        sample_rate: int = 48000,
        channels: int = 2,
        frame_size: int = 4096
    ) -> None:
        """
        Initialize the FM sink.
        
        Args:
            device: ALSA device name (e.g., "hw:1,0")
            sample_rate: Audio sample rate in Hz (default: 48000)
            channels: Number of audio channels (default: 2 = stereo)
            frame_size: Frame size in bytes (default: 4096)
        """
        super().__init__()
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_size = frame_size
        self._process: Optional[subprocess.Popen] = None
        
        # Internal queue for frames (~1 second of audio @ 48k/4096)
        self._queue: deque[bytes] = deque(maxlen=50)
        self._queue_lock = threading.Lock()
        self._queue_event = threading.Event()
        
        # Worker thread that performs all I/O
        self._worker: Optional[threading.Thread] = None
        
        # Frame counter for periodic flushing
        self._frames_written = 0
        
        # Logging state
        self._last_queue_log_time = 0.0
    
    def start(self) -> bool:
        """
        Start the FM sink by spawning aplay process and worker thread.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self._running:
            logger.warning("FMSink is already running")
            return True
        
        try:
            # Build aplay command
            cmd = [
                "aplay",
                "-f", "S16_LE",
                "-c", str(self.channels),
                "-r", str(self.sample_rate),
                "-D", self.device,
                "-"  # Read from stdin
            ]
            
            # Log the FULL command
            cmd_str = " ".join(cmd)
            logger.info(f"[FMSink] Starting FMSink: {cmd_str}")
            
            # Spawn aplay process
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0  # Unbuffered for real-time
            )
            
            # Give it a moment to start
            time.sleep(0.1)
            
            # Check if process is still running
            if self._process is None:
                return False
            if self._process.poll() is not None:
                # Process died immediately
                stderr = ""
                if self._process.stderr:
                    try:
                        stderr = self._process.stderr.read().decode('utf-8', errors='ignore')
                    except Exception:
                        pass
                logger.error(f"[FMSink] aplay process exited immediately (return code: {self._process.poll()}): {stderr[:500]}")
                self._process = None
                return False
            
            # Start worker thread (must be after _running is set)
            self._running = True
            self._frames_written = 0
            self._last_queue_log_time = time.time()
            
            self._worker = threading.Thread(
                target=self._drain_loop,
                name="FMSinkWorker",
                daemon=True,
            )
            self._worker.start()
            
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
        Write a PCM frame to the FM sink (O(1), non-blocking).
        
        PURE enqueue operation. Never writes to stdin or performs any I/O.
        All I/O happens in the worker thread.
        
        Args:
            pcm_frame: Raw PCM frame bytes
        """
        if not self._running:
            return
        
        # Enqueue frame (O(1) operation)
        with self._queue_lock:
            if len(self._queue) >= self._queue.maxlen:
                # Drop oldest to avoid unbounded growth
                self._queue.popleft()
            self._queue.append(pcm_frame)
        
        # Signal worker thread that frame is available
        self._queue_event.set()
        
        logger.debug(f"[FMSink] Enqueued frame, queue_size={len(self._queue)}")
    
    def _drain_loop(self) -> None:
        """
        Worker thread that drains the queue and writes to aplay.
        
        Runs in background thread. All I/O happens here.
        If aplay blocks because device/pipe is full, only this thread blocks,
        never the MasterClock.
        """
        logger.debug("[FMSink] Worker thread started")
        
        while self._running:
            try:
                # Wait until there is something to write
                if not self._queue_event.wait(timeout=0.1):
                    # Timeout - check if we should log queue status
                    current_time = time.time()
                    if current_time - self._last_queue_log_time >= 5.0:
                        with self._queue_lock:
                            queue_size = len(self._queue)
                        logger.info(f"[FMSink] Queue status: {queue_size} frames")
                        self._last_queue_log_time = current_time
                    continue
                
                # Drain queue continuously until empty
                while True:
                    # Pop one frame from queue
                    frame = None
                    with self._queue_lock:
                        if not self._queue:
                            # Nothing left; clear event and break
                            self._queue_event.clear()
                            break
                        frame = self._queue.popleft()
                    
                    logger.debug(f"[FMSink] Dequeued frame, queue_size={len(self._queue)}")
                    
                    # Write to aplay here
                    try:
                        # Check if process is running
                        if self._process is None or self._process.poll() is not None:
                            # Process died: try restart
                            if not self._ensure_process():
                                # If restart failed, requeue frame and break
                                with self._queue_lock:
                                    self._queue.appendleft(frame)
                                break
                        
                        # Write frame to stdin
                        if self._process.stdin and not self._process.stdin.closed:
                            self._process.stdin.write(frame)
                            self._frames_written += 1
                            
                            # Flush periodically (every 10 frames)
                            if self._frames_written % 10 == 0:
                                self._process.stdin.flush()
                        else:
                            # stdin unusable; try restart next iteration
                            with self._queue_lock:
                                self._queue.appendleft(frame)
                            break
                    
                    except BrokenPipeError:
                        # Process died; requeue frame and retry after restart
                        logger.warning("[FMSink] Broken pipe, attempting restart...")
                        with self._queue_lock:
                            self._queue.appendleft(frame)
                        self._restart_process()
                        break
                    
                    except Exception as e:
                        logger.error(f"[FMSink] worker write error: {e}", exc_info=True)
                        # On generic error, requeue and back off a bit
                        with self._queue_lock:
                            self._queue.appendleft(frame)
                        time.sleep(0.05)
                        break
                
            except Exception as e:
                logger.error(f"[FMSink] drain_loop error: {e}", exc_info=True)
                time.sleep(0.1)
        
        logger.debug("[FMSink] Worker thread stopped")
    
    def _ensure_process(self) -> bool:
        """
        Ensure aplay process is running. Attempts restart if needed.
        
        Called only by worker thread.
        
        Returns:
            True if process is running, False if restart failed
        """
        if self._process is not None and self._process.poll() is None:
            return True
        
        # Process is dead or doesn't exist - restart
        return self._restart_process()
    
    def _restart_process(self) -> bool:
        """
        Restart the aplay process.
        
        Called only by worker thread.
        
        Returns:
            True if restart successful, False otherwise
        """
        # Clean up old process
        if self._process is not None:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.terminate()
                try:
                    self._process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            except Exception as e:
                logger.debug(f"[FMSink] Error cleaning up old process: {e}")
            finally:
                self._process = None
        
        # Start new process
        try:
            cmd = [
                "aplay",
                "-f", "S16_LE",
                "-c", str(self.channels),
                "-r", str(self.sample_rate),
                "-D", self.device,
                "-"
            ]
            
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0
            )
            
            # Give it a moment to start
            time.sleep(0.1)
            
            if self._process.poll() is not None:
                # Process died immediately
                stderr = ""
                if self._process.stderr:
                    try:
                        stderr = self._process.stderr.read().decode('utf-8', errors='ignore')
                    except Exception:
                        pass
                logger.error(f"[FMSink] aplay restart failed (return code: {self._process.poll()}): {stderr[:500]}")
                self._process = None
                return False
            
            self._frames_written = 0
            logger.info("[FMSink] aplay process restarted successfully")
            return True
            
        except Exception as e:
            logger.error(f"[FMSink] Failed to restart aplay: {e}", exc_info=True)
            self._process = None
            return False
    
    def stop(self) -> None:
        """
        Stop the FM sink by closing aplay process.
        """
        self._running = False
        
        # Wake worker thread so it can exit
        self._queue_event.set()
        
        # Wait for worker thread to finish
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.0)
        
        # Terminate aplay process
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
