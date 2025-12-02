"""
YouTube Live streaming audio sink.

This module provides the YouTubeSink class, which streams PCM frames to
YouTube Live via RTMP using FFmpeg. Uses internal worker thread for pacing.
"""

import logging
import subprocess
import threading
import time
from collections import deque
from typing import Optional
from outputs.sink_base import SinkBase

logger = logging.getLogger(__name__)


class YouTubeSink(SinkBase):
    """
    YouTube Live streaming audio sink.
    
    Simple design: write_frame() enqueues frames, background worker thread
    paces writes to FFmpeg at real-time rate. No MasterClock integration.
    """
    
    def __init__(
        self,
        rtmp_url: str,
        reconnect_delay: float = 5.0,
        sample_rate: int = 48000,
        channels: int = 2,
        frame_size: int = 4096,
        video_source: str = "color",
        video_file: str | None = None,
        video_size: str = "1280x720",
        video_fps: int = 2,
        video_bitrate: str = "4000k",
        debug: bool = False
    ) -> None:
        """
        Initialize the YouTube sink.
        
        Args:
            rtmp_url: Full RTMP URL including stream key
            reconnect_delay: Delay in seconds before reconnecting after failure
            sample_rate: Audio sample rate in Hz (default: 48000)
            channels: Number of audio channels (default: 2 = stereo)
            frame_size: Frame size in bytes (default: 4096)
            video_source: Video source type - "color", "image", or "video"
            video_file: Path to video/image file (required if video_source is "image" or "video")
            video_size: Video resolution in format "WIDTHxHEIGHT" (default: "1280x720")
            video_fps: Video frame rate (default: 2)
            video_bitrate: Video bitrate with unit, e.g. "4000k" (default: "4000k")
        """
        super().__init__()
        self.rtmp_url = rtmp_url
        self.reconnect_delay = reconnect_delay
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_size = frame_size
        self.video_source = video_source
        self.video_file = video_file
        self.video_size = video_size
        self.video_fps = video_fps
        self.video_bitrate = video_bitrate
        self.debug = debug
        
        # Calculate tick interval for pacing
        samples_per_frame = frame_size // (2 * channels)  # 2 bytes per sample
        self.tick_interval = samples_per_frame / sample_rate  # ~0.021333s for 4096 bytes at 48kHz
        
        # Internal queue for frames (max ~4 seconds of audio)
        self._queue: deque[bytes] = deque()
        self._max_queue_size = 200
        self._queue_lock = threading.Lock()
        
        # FFmpeg process management
        self._process: Optional[subprocess.Popen] = None
        self._process_lock = threading.Lock()
        self._is_connected = False
        self._disconnected = False  # Phase 4: Track if YouTube is disconnected (non-fatal)
        
        # Frame counter
        self._frames_written = 0
        
        # Background worker thread for pacing and I/O
        self._worker: Optional[threading.Thread] = None
        
        # Periodic logging
        self._last_log_time = time.time()
    
    def start(self) -> bool:
        """
        Start the YouTube sink by spawning background worker thread.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self._running:
            logger.warning("YouTubeSink is already running")
            return True
        
        try:
            # Set running flag BEFORE starting thread
            self._running = True
            
            # Spawn background worker thread for pacing and I/O
            self._worker = threading.Thread(
                target=self._drain_loop,
                name="YouTubeSinkWorker",
                daemon=True
            )
            self._worker.start()
            
            # Attempt initial FFmpeg connection
            self._ensure_ffmpeg_running()
            
            if self.debug:
                logger.info("YouTubeSink started (worker thread pacing)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start YouTubeSink: {e}", exc_info=True)
            self._running = False
            return False
    
    def write_frame(self, pcm_frame: bytes) -> None:
        """
        Write a PCM frame to the YouTube sink (O(1), non-blocking).
        
        Phase 4: Non-fatal - if this throws, it's logged as WARNING and YouTube is marked disconnected.
        FM continues playing uninterrupted.
        
        Enqueues frame into buffer. Worker thread writes frames to FFmpeg
        at real-time rate. No I/O performed here.
        
        Args:
            pcm_frame: Raw PCM frame bytes
        """
        if not self._running:
            return
        
        # Phase 4: If disconnected, silently drop frames (non-fatal)
        if self._disconnected:
            return
        
        try:
            # Enqueue frame (drop oldest if queue full)
            with self._queue_lock:
                if len(self._queue) >= self._max_queue_size:
                    # Drop oldest frame to avoid unbounded growth
                    self._queue.popleft()
                self._queue.append(pcm_frame)
        except Exception as e:
            # Phase 4: Non-fatal error - log as WARNING, mark disconnected
            logger.warning(f"[YouTubeSink] write_frame error (non-critical): {e}")
            self._disconnected = True
            # Do not raise - allow FM to continue
    
    def _drain_loop(self) -> None:
        """
        Worker thread that paces writes to FFmpeg at real-time rate.

        Writes exactly one frame per tick_interval to maintain continuous
        audio stream. Handles reconnection if FFmpeg dies.
        """
        if self.debug:
            logger.debug("[YouTubeSink] Worker thread started")

        flush_counter = 0
        next_write_time = None

        while self._running:
            try:
                # Ensure FFmpeg is running
                if not self._ensure_ffmpeg_running():
                    if self._running:
                        time.sleep(self.reconnect_delay)
                    next_write_time = None  # Reset timing on reconnect
                    continue

                # Initialize timing on first iteration
                now = time.monotonic()
                if next_write_time is None:
                    next_write_time = now
                
                # Sleep until it's time to write the next frame
                sleep_time = next_write_time - now
                if sleep_time > 0:
                    time.sleep(sleep_time)
                
                # Update next write time (maintains steady pace)
                next_write_time += self.tick_interval
                
                # Pull one frame (or silence)
                frame = None
                with self._queue_lock:
                    if self._queue:
                        frame = self._queue.popleft()
                    else:
                        frame = bytes(self.frame_size)  # silence
                
                # Write frame to FFmpeg
                try:
                    with self._process_lock:
                        if self._process is None or self._process.poll() is not None:
                            continue

                        if self._process.stdin and not self._process.stdin.closed:
                                self._process.stdin.write(frame)
                                self._frames_written += 1
                                flush_counter += 1

                                if flush_counter >= 16:
                                    self._process.stdin.flush()
                                    flush_counter = 0

                                if not self._is_connected:
                                    if self.debug:
                                        logger.info("YouTube stream connected")
                                    logger.info(
                                        "[YouTubeSink] First frame written to FFmpeg: %d bytes",
                                        len(frame)
                                    )
                                    self._is_connected = True

                except BrokenPipeError:
                    # Phase 4: Non-fatal - mark disconnected, attempt reconnection
                    logger.warning("[YouTubeSink] Broken pipe (non-critical), marking disconnected")
                    self._is_connected = False
                    self._disconnected = True
                    next_write_time = None  # Reset timing on error
                    # Reconnection will be attempted in _ensure_ffmpeg_running()
                except BlockingIOError:
                    # FFmpeg backpressure - don't advance timing, retry next iteration
                    # This prevents buffer growth when FFmpeg is slow
                    next_write_time = time.monotonic()  # Reset to now to retry immediately
                except Exception as e:
                    # Phase 4: Non-fatal - log as WARNING, mark disconnected
                    logger.warning(f"[YouTubeSink] worker write error (non-critical): {e}")
                    self._is_connected = False
                    self._disconnected = True
                    next_write_time = None  # Reset timing on error
                
                # Periodic logging (every ~5 seconds, only if debug enabled)
                if self.debug:
                    log_now = time.time()
                    if log_now - self._last_log_time >= 5.0:
                        with self._queue_lock:
                            queue_size = len(self._queue)
                        logger.info(
                            "[YT] buffer=%d frames_written=%d",
                            queue_size,
                            self._frames_written
                        )
                        self._last_log_time = log_now

            except Exception as e:
                # Phase 4: Non-fatal - log as WARNING, mark disconnected
                logger.warning(f"[YouTubeSink] drain_loop error (non-critical): {e}")
                self._disconnected = True
                self._is_connected = False
                next_write_time = None  # Reset timing on exception
                time.sleep(0.1)
        
        if self.debug:
            logger.debug("[YouTubeSink] Worker thread stopped")

    def _ensure_ffmpeg_running(self) -> bool:
        """
        Ensure FFmpeg process is running. Start if needed.
        
        Phase 4: Attempts reconnection if disconnected. Non-fatal - returns False on failure.
        
        Returns:
            True if FFmpeg is running, False otherwise
        """
        with self._process_lock:
            # Check if process exists and is alive
            if self._process is not None:
                if self._process.poll() is None:
                    # Process is running - if we were disconnected, mark as reconnected
                    if self._disconnected:
                        logger.info("[YouTubeSink] Reconnected to YouTube stream")
                        self._disconnected = False
                    return True
                # Process died - close it
                self._close_ffmpeg_process()
            
            # Process doesn't exist or died - attempt to start it
            if self._start_ffmpeg_process():
                # Successfully started - mark as reconnected
                if self._disconnected:
                    logger.info("[YouTubeSink] Reconnected to YouTube stream")
                    self._disconnected = False
                return True
            else:
                # Failed to start - mark as disconnected (non-fatal)
                self._disconnected = True
                return False
    
    def _start_ffmpeg_process(self) -> bool:
        """
        Start FFmpeg process for RTMP streaming.
        
        Returns:
            True if started successfully, False otherwise
        """
        try:
            # Build video input based on video_source
            if self.video_source == "video":
                if not self.video_file:
                    logger.error("video_source is 'video' but no video_file provided")
                    return False
                import os
                if not os.path.exists(self.video_file):
                    logger.error(f"Video file not found: {self.video_file}")
                    return False
                # Simple video input matching working CLI test
                video_input = [
                    "-re",
                    "-stream_loop", "-1",
                    "-i", self.video_file,
                ]
                if self.debug:
                    logger.info(f"Using video file: {self.video_file}")
            elif self.video_source == "image":
                if not self.video_file:
                    logger.error("video_source is 'image' but no video_file provided")
                    return False
                import os
                if not os.path.exists(self.video_file):
                    logger.error(f"Image file not found: {self.video_file}")
                    return False
                video_input = [
                    "-loop", "1",
                    "-framerate", str(self.video_fps),
                    "-i", self.video_file
                ]
                if self.debug:
                    logger.info(f"Using image file: {self.video_file}")
            elif self.video_source == "color":
                video_input = [
                    "-f", "lavfi",
                    "-i", f"color=black:s={self.video_size}:r={self.video_fps}"
                ]
                logger.debug("Using solid color background")
            else:
                logger.error(f"Invalid video_source: {self.video_source}")
                return False
            
            # Build FFmpeg command
            # Match working CLI test order: video input first, then audio input
            cmd = ["ffmpeg"]
            
            # Add video input first (matches working CLI test)
            cmd.extend(video_input)
            
            # Add audio input (simple PCM pipe, no -re, no -use_wallclock_as_timestamps)
            cmd.extend([
                "-f", "s16le",
                "-ac", str(self.channels),
                "-ar", str(self.sample_rate),
                "-i", "pipe:0",
            ])
            
            # Output encoding settings
            if self.video_source == "video":
                # Video passthrough (copy) - no re-encoding
                # Pi cannot handle live video encoding, must use copy
                # Note: Keyframe frequency is determined by source video file
                # If YouTube complains about keyframes, the source file needs to be
                # pre-encoded with smaller GOP size (4 seconds or less)
                # Note: YouTube may warn about low bitrate when using -c:v copy
                # This is expected for static/looping video and is acceptable
                cmd.extend([
                    "-c:v", "copy",  # Copy video codec (no re-encoding)
                    "-c:a", "aac",   # Encode audio to AAC
                    "-b:a", "160k",  # Audio bitrate
                ])
            else:
                # Image/color sources still need video encoding
                # Calculate GOP for <= 4 second keyframe interval (YouTube requirement)
                max_keyframe_seconds = 4
                gop_size = self.video_fps * max_keyframe_seconds
            
            # Calculate buffer size
            try:
                bitrate_str = self.video_bitrate.rstrip('kKmM')
                bitrate_num = int(bitrate_str)
                bitrate_unit = self.video_bitrate[-1].lower() if self.video_bitrate[-1] in 'kKmM' else 'k'
                buffer_num = bitrate_num * 2
                buffer_size = f"{buffer_num}{bitrate_unit}"
            except (ValueError, IndexError):
                buffer_size = "8000k"
            
            cmd.extend([
                "-c:a", "aac",
                    "-b:a", "160k",
                "-c:v", "libx264",
                    "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-b:v", self.video_bitrate,
                "-maxrate", self.video_bitrate,
                "-bufsize", buffer_size,
                "-g", str(gop_size),
                "-keyint_min", str(self.video_fps),
                "-sc_threshold", "0",
                    "-fflags", "nobuffer",
                    "-flags", "low_delay",
            ])
            
            if self.video_source == "image":
                cmd.extend(["-vf", f"scale={self.video_size}"])
            
            # Output format
            cmd.extend([
                "-f", "flv",
                "-loglevel", "error",
                self.rtmp_url
            ])
            
            if self.debug:
                logger.info(f"Starting FFmpeg for YouTube stream: {self.rtmp_url[:50]}...")
            
            # Spawn FFmpeg process
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0  # Unbuffered for real-time
            )
            
            if self._process is None:
                logger.error("Failed to start FFmpeg: subprocess.Popen returned None")
                return False
            
            # Give it a moment to start
            time.sleep(0.5)
            
            if self._process is None:
                return False
            
            if self._process.poll() is not None:
                stderr = ""
                if self._process.stderr:
                    try:
                        stderr = self._process.stderr.read().decode('utf-8', errors='ignore')
                    except Exception:
                        pass
                logger.error(f"FFmpeg process exited immediately: {stderr[:500]}")
                self._process = None
                return False
            
            if self.debug:
                logger.info("FFmpeg process started successfully")
            # Reset frame counter for new process
            self._frames_written = 0
            return True
            
        except FileNotFoundError:
            logger.error("FFmpeg not found. Please install: sudo apt-get install ffmpeg")
            self._process = None
            return False
        except Exception as e:
            logger.error(f"Failed to start FFmpeg: {e}", exc_info=True)
            if self._process is not None:
                try:
                    self._process.terminate()
                except Exception:
                    pass
            self._process = None
            return False
    
    def _close_ffmpeg_process(self) -> None:
        """Close FFmpeg process gracefully."""
        if self._process is not None:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.terminate()
                try:
                    self._process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    logger.warning("FFmpeg process didn't terminate, killing...")
                    self._process.kill()
                    self._process.wait()
            except Exception as e:
                logger.error(f"Error closing FFmpeg process: {e}")
            finally:
                self._process = None
    
    def stop(self) -> None:
        """Stop the YouTube sink."""
        self._running = False
        
        # Close FFmpeg process
        with self._process_lock:
            self._close_ffmpeg_process()
        
        # Wait for worker thread to finish
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=2.0)
            if self._worker.is_alive():
                logger.warning("YouTubeSink worker thread did not stop in time")
        
        self._worker = None
        if self._is_connected and self.debug:
            logger.info("YouTube stream disconnected (shutdown)")
        self._is_connected = False
        if self.debug:
            logger.info("YouTubeSink stopped")
    
    def is_connected(self) -> bool:
        """Check if YouTube stream is currently connected."""
        return self._is_connected and self._running
    
    def try_reconnect(self) -> bool:
        """
        Phase 4: Attempt to reconnect YouTube stream if disconnected.
        
        Non-fatal - returns False on failure, but does not raise.
        Can be called periodically from main loop or timer.
        
        Returns:
            True if reconnected, False otherwise
        """
        if not self._disconnected:
            # Already connected
            return True
        
        logger.info("[YouTubeSink] Attempting to reconnect...")
        return self._ensure_ffmpeg_running()
    
    def is_disconnected(self) -> bool:
        """
        Phase 4: Check if YouTube is currently disconnected.
        
        Returns:
            True if disconnected, False if connected
        """
        return self._disconnected
    
    def try_reconnect(self) -> bool:
        """
        Phase 4: Attempt to reconnect YouTube stream if disconnected.
        
        Non-fatal - returns False on failure, but does not raise.
        Can be called periodically from main loop or timer.
        
        Returns:
            True if reconnected, False otherwise
        """
        if not self._disconnected:
            # Already connected
            return True
        
        logger.info("[YouTubeSink] Attempting to reconnect...")
        return self._ensure_ffmpeg_running()
    
    def is_disconnected(self) -> bool:
        """
        Phase 4: Check if YouTube is currently disconnected.
        
        Returns:
            True if disconnected, False if connected
        """
        return self._disconnected
