"""
YouTube Live streaming audio sink.

This module provides the YouTubeSink class, which streams PCM frames to
YouTube Live via RTMP using FFmpeg. Clock-driven - no internal timing.
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
    
    MasterClock-driven sink - writes exactly one frame per MasterClock tick.
    No internal pacing or timing loops. MasterClock is the sole timing source.
    
    Small buffer (max 5 frames) for jitter tolerance only.
    """
    
    def __init__(
        self,
        rtmp_url: str,
        master_clock=None,  # MasterClock instance (required for clock-driven operation)
        reconnect_delay: float = 5.0,
        sample_rate: int = 48000,
        channels: int = 2,
        frame_size: int = 4096,
        video_source: str = "color",
        video_file: str | None = None,
        video_size: str = "1280x720",
        video_fps: int = 2,
        video_bitrate: str = "4000k"
    ) -> None:
        """
        Initialize the YouTube sink.
        
        Args:
            rtmp_url: Full RTMP URL including stream key
            master_clock: MasterClock instance (required for clock-driven operation)
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
        self.master_clock = master_clock
        self.reconnect_delay = reconnect_delay
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_size = frame_size
        self.video_source = video_source
        self.video_file = video_file
        self.video_size = video_size
        self.video_fps = video_fps
        self.video_bitrate = video_bitrate
        
        # Calculate tick interval to match MasterClock
        samples_per_frame = frame_size // (2 * 2)  # 2 bytes per sample, 2 channels
        self.tick_interval = samples_per_frame / sample_rate
        
        # Internal queue for frames (maintain 3-8 frames for healthy buffer)
        # No maxlen - we manually drop newest to prevent time jump
        self._queue: deque[bytes] = deque()
        self._queue_lock = threading.Lock()
        
        # FFmpeg process management
        self._process: Optional[subprocess.Popen] = None
        self._process_lock = threading.Lock()
        self._is_connected = False
        
        # Frame counter and index tracking
        self._frames_written = 0
        self._frame_index = 0
        self._frame_index_lock = threading.Lock()
        
        # Background worker thread (only for FFmpeg monitoring/unblocking, NOT timing)
        self._worker: Optional[threading.Thread] = None
        
        # Periodic logging (every 1 second)
        self._last_log_time = time.time()
        
        # Register with MasterClock if provided
        if master_clock:
            master_clock.register_callback(self.on_clock_tick)
            logger.info("YouTubeSink registered with MasterClock (clock-driven, no internal timing)")
    
    def start(self) -> bool:
        """
        Start the YouTube sink by spawning background monitoring thread.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self._running:
            logger.warning("YouTubeSink is already running")
            return True
        
        try:
            # Set running flag BEFORE starting thread
            self._running = True
            
            # Spawn background worker thread for all I/O
            self._worker = threading.Thread(
                target=self._drain_loop,
                name="YouTubeSinkWorker",
                daemon=True
            )
            self._worker.start()
            
            # Attempt initial FFmpeg connection
            self._ensure_ffmpeg_running()
            
            logger.info("YouTubeSink started (MasterClock-driven, no internal timing)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start YouTubeSink: {e}", exc_info=True)
            self._running = False
            return False
    
    def write_frame(self, pcm_frame: bytes) -> None:
        """
        Write a PCM frame to the YouTube sink (O(1), non-blocking).
        
        Enqueues frame into buffer (maintains 3-8 frames). MasterClock's on_clock_tick
        will write frames to FFmpeg at the correct rate. No unbounded growth.
        
        Args:
            pcm_frame: Raw PCM frame bytes
        """
        if not self._running:
            return
        
        # Enqueue frame with size limit (max 8 frames)
        with self._queue_lock:
            if len(self._queue) >= 8:
                # Queue full - drop newest to prevent time jump (oldest would cause gap)
                # Dropping newest maintains continuity
                self._queue.pop()
            self._queue.append(pcm_frame)
    
    def get_buffer_size(self) -> int:
        """
        Get current buffer size (thread-safe).
        
        Returns:
            Number of frames currently in buffer
        """
        with self._queue_lock:
            return len(self._queue)
    
    def on_clock_tick(self, frame_index: int) -> None:
        """
        Called by MasterClock on each tick. Writes exactly ONE frame to FFmpeg.
        
        This is the primary timing mechanism - MasterClock drives all audio output.
        No internal pacing loops or timing logic.
        
        Always writes one frame per tick to maintain real-time playback. Uses silence
        if buffer is empty to avoid underrun. Mixer pushes multiple frames per tick to
        maintain healthy buffer level (3-8 frames).
        
        Args:
            frame_index: Current frame index from MasterClock
        """
        if not self._running:
            return
        
        # Ensure FFmpeg is running
        if not self._ensure_ffmpeg_running():
            return
        
        # Get one frame from queue (or use silence if empty)
        frame = None
        buffer_size = 0
        with self._queue_lock:
            buffer_size = len(self._queue)
            if len(self._queue) > 0:
                frame = self._queue.popleft()
        
        # If queue empty, use silence to avoid underrun (maintains real-time)
        if frame is None:
            frame = b'\x00' * self.frame_size
            # Log warning if buffer is consistently low (indicates starvation)
            if buffer_size == 0:
                logger.warning(f"[YouTubeSink] Buffer empty at tick {frame_index} - using silence")
        
        # Write frame to FFmpeg (non-blocking, handles backpressure)
        try:
            with self._process_lock:
                if self._process is None or self._process.poll() is not None:
                    # FFmpeg died - frame will be dropped, will retry next tick
                    return
                
                if self._process.stdin and not self._process.stdin.closed:
                    try:
                        # Write exactly ONE frame per tick
                        self._process.stdin.write(frame)
                        
                        # Update counters
                        with self._frame_index_lock:
                            self._frame_index = frame_index
                        self._frames_written += 1
                        
                        # Flush periodically (every 16 frames) for consistent timestamps
                        if self._frames_written % 16 == 0:
                            self._process.stdin.flush()
                        
                        if not self._is_connected:
                            logger.info("YouTube stream connected")
                            logger.info(
                                "[YouTubeSink] First frame written to FFmpeg: %d bytes (frame_index=%d)",
                                len(frame),
                                frame_index,
                            )
                        self._is_connected = True
                        
                        # Log first few frames for debugging
                        if self._frames_written <= 5:
                            logger.debug(
                                "[YouTubeSink] Wrote frame %d (index=%d) to FFmpeg: %d bytes",
                                self._frames_written,
                                frame_index,
                                len(frame),
                            )
                    
                    except BlockingIOError:
                        # Pipe would block - requeue frame (if not silence) and let drain_loop handle it
                        if frame != b'\x00' * self.frame_size:
                            with self._queue_lock:
                                if len(self._queue) < 5:
                                    self._queue.appendleft(frame)
                        # Don't log - this is normal backpressure
                        return
                    except BrokenPipeError:
                        # FFmpeg died
                        logger.warning("[YouTubeSink] Broken pipe, will restart on next tick")
                        self._is_connected = False
                        return
        
        except Exception as e:
            logger.error(f"[YouTubeSink] on_clock_tick write error: {e}", exc_info=True)
        
        # Periodic logging every 1 second
        current_time = time.time()
        if current_time - self._last_log_time >= 1.0:
            with self._queue_lock:
                queue_size = len(self._queue)
            with self._frame_index_lock:
                current_frame_index = self._frame_index
            logger.info(
                "[YT] buffer=%d frames_written=%d frame_index=%d",
                queue_size,
                self._frames_written,
                current_frame_index,
            )
            self._last_log_time = current_time
    
    def _drain_loop(self) -> None:
        """
        Worker thread that monitors FFmpeg and handles unblocking.
        
        NOT used for primary timing - MasterClock's on_clock_tick handles that.
        This thread only:
        - Monitors FFmpeg process health
        - Handles reconnection if FFmpeg dies
        - Optionally drains buffered frames if FFmpeg unblocks after backpressure
        """
        logger.debug("[YouTubeSink] Worker thread started (FFmpeg monitoring only)")

        while self._running:
            try:
                # Ensure FFmpeg is running (monitoring only)
                if not self._ensure_ffmpeg_running():
                    # Failed to start - wait before retry
                    if self._running:
                        time.sleep(self.reconnect_delay)
                    continue
                
                # Brief sleep to avoid busy loop (we're not doing primary timing here)
                time.sleep(0.1)  # Check every 100ms

            except Exception as e:
                logger.error(f"[YouTubeSink] drain_loop error: {e}", exc_info=True)
                time.sleep(0.1)

        logger.debug("[YouTubeSink] Worker thread stopped")


    def _ensure_ffmpeg_running(self) -> bool:
        """
        Ensure FFmpeg process is running. Start if needed.
        
        Returns:
            True if FFmpeg is running, False otherwise
        """
        with self._process_lock:
            # Check if process exists and is alive
            if self._process is not None:
                if self._process.poll() is None:
                    # Process is running
                    return True
                # Process died - close it
                self._close_ffmpeg_process()
            
            # Process doesn't exist or died - start it
            return self._start_ffmpeg_process()
    
    
    def _start_ffmpeg_process(self) -> bool:
        """
        Start FFmpeg process for RTMP streaming.
        
        Returns:
            True if started successfully, False otherwise
        """
        try:
            # Build video input based on video_source
            # For video files, use -re flag to read at native frame rate (matches working command style)
            if self.video_source == "video":
                if not self.video_file:
                    logger.error("video_source is 'video' but no video_file provided")
                    return False
                import os
                if not os.path.exists(self.video_file):
                    logger.error(f"Video file not found: {self.video_file}")
                    return False
                video_input = [
                    "-re",  # Read input at native frame rate (matches working command)
                    "-stream_loop", "-1",
                    "-i", self.video_file
                ]
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
            # Audio input: Use -use_wallclock_as_timestamps so FFmpeg uses wallclock
            # for PTS. We pace writes to exactly one frame per tick_interval (21.333ms)
            # so timestamps are continuous and correct.
            cmd = [
                "ffmpeg",
            ]
            
            # Add video input first (matches working command style: video input before audio)
            cmd.extend(video_input)
            
            # Add audio input (raw PCM from pipe)
            cmd.extend([
                "-f", "s16le",
                "-ac", str(self.channels),
                "-ar", str(self.sample_rate),
                "-use_wallclock_as_timestamps", "1",  # Use wallclock for PTS (we pace writes correctly)
                "-i", "pipe:0",
            ])
            
            # Output encoding settings
            # For video files: copy video codec (no re-encoding) - matches working command
            # For image/color: still need encoding
            if self.video_source == "video":
                # Copy video directly (no encoding) - matches working command style
                cmd.extend([
                    "-vcodec", "copy",  # Copy video codec (matches working command)
                    "-acodec", "aac",   # Encode audio to AAC
                    "-b:a", "160k",     # Audio bitrate (matches working command: 160k)
                ])
            else:
                # Image/color sources still need video encoding
                # Calculate GOP size for encoding
                gop_size = self.video_fps * 2
                if gop_size < 30:
                    gop_size = 30
                
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
                    "-b:a", "160k",     # Match audio bitrate
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
                "-f", "flv",  # FLV format (matches working command)
                "-loglevel", "error",
                self.rtmp_url
            ])
            
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
            
            logger.info("FFmpeg process started successfully")
            # Reset frame counter and index for new process
            self._frames_written = 0
            with self._frame_index_lock:
                self._frame_index = 0
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
        
        # Unregister from MasterClock
        if self.master_clock:
            try:
                self.master_clock.unregister_callback(self.on_clock_tick)
            except Exception as e:
                logger.warning(f"Error unregistering from MasterClock: {e}")
        
        # Close FFmpeg process
        with self._process_lock:
            self._close_ffmpeg_process()
        
        # Wait for worker thread to finish
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=2.0)
            if self._worker.is_alive():
                logger.warning("YouTubeSink worker thread did not stop in time")
        
        self._worker = None
        if self._is_connected:
            logger.info("YouTube stream disconnected (shutdown)")
        self._is_connected = False
        logger.info("YouTubeSink stopped")
    
    def is_connected(self) -> bool:
        """Check if YouTube stream is currently connected."""
        return self._is_connected and self._running
