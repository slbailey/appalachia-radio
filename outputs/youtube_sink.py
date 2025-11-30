"""
YouTube Live streaming audio sink.

This module provides the YouTubeSink class, which streams PCM frames to
YouTube Live via RTMP using FFmpeg. This is an optional secondary sink
with background reconnection.
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
    
    Optional secondary output. Consumes same PCM stream as FMSink.
    Uses FFmpeg to send PCM → RTMP. Runs background worker thread that
    reads from internal queue and writes to FFmpeg stdin.
    
    Connects/reconnects independently. Handles network failures gracefully.
    Must not block FMSink if offline. All errors are handled internally.
    """
    
    def __init__(
        self,
        rtmp_url: str,
        reconnect_delay: float = 5.0,
        max_buffer_frames: int = 2500,  # YouTube needs 3-5 seconds of buffered PCM (~2500 frames at 48kHz)
        sample_rate: int = 48000,
        channels: int = 2,
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
                     (e.g., "rtmp://a.rtmp.youtube.com/live2/STREAM_KEY")
            reconnect_delay: Delay in seconds before reconnecting after failure
            max_buffer_frames: Maximum frames to buffer (drops oldest if exceeded)
            sample_rate: Audio sample rate in Hz (default: 48000)
            channels: Number of audio channels (default: 2 = stereo)
            video_source: Video source type - "color", "image", or "video" (default: "color")
            video_file: Path to video/image file (required if video_source is "image" or "video")
            video_size: Video resolution in format "WIDTHxHEIGHT" (default: "1280x720")
            video_fps: Video frame rate (default: 2)
            video_bitrate: Video bitrate with unit, e.g. "4000k" (default: "4000k")
        """
        super().__init__()
        self.rtmp_url = rtmp_url
        self.reconnect_delay = reconnect_delay
        self.max_buffer_frames = max_buffer_frames
        self.sample_rate = sample_rate
        self.channels = channels
        self.video_source = video_source
        self.video_file = video_file
        self.video_size = video_size
        self.video_fps = video_fps
        self.video_bitrate = video_bitrate
        
        # Internal structures
        self._queue: deque[bytes] = deque(maxlen=max_buffer_frames)
        self._queue_lock = threading.Lock()
        self._queue_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._is_connected = False
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
            # Spawn background worker thread
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="YouTubeSinkWorker",
                daemon=True
            )
            self._thread.start()
            
            self._running = True
            logger.info("YouTubeSink started (worker thread spawned)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start YouTubeSink: {e}", exc_info=True)
            return False
    
    def write_frame(self, pcm_frame: bytes) -> None:
        """
        Enqueue a PCM frame for streaming.
        
        Non-blocking. If queue is full, drops oldest frames.
        Never raises exceptions - all errors handled internally.
        
        Args:
            pcm_frame: Raw PCM frame bytes
        """
        if not self._running:
            return
        
        with self._queue_lock:
            # If queue is full, drop oldest frame
            if len(self._queue) >= self.max_buffer_frames:
                try:
                    self._queue.popleft()
                    logger.debug("YouTubeSink queue full, dropped oldest frame")
                except IndexError:
                    pass  # Queue was empty (shouldn't happen)
            
            # Append new frame
            self._queue.append(pcm_frame)
            
            # Signal worker thread
            self._queue_event.set()
    
    def _worker_loop(self) -> None:
        """
        Background worker loop that manages FFmpeg process and writes frames.
        
        Ensures FFmpeg process is running, reconnects on failure, and
        writes frames from queue to FFmpeg stdin with proper pacing.
        """
        logger.info("YouTubeSink worker thread started")
        
        while self._running:
            try:
                # Ensure FFmpeg process is running
                if self._process is None or self._process.poll() is not None:
                    # Process not running - attempt to start
                    if not self._start_ffmpeg_process():
                        # Failed to start - wait before retry
                        logger.warning(f"FFmpeg start failed, retrying in {self.reconnect_delay}s...")
                        time.sleep(self.reconnect_delay)
                        continue
                
                # Process is running - write frames with pacing
                frames_written = 0
                batch_size = 0
                while self._running and self._process is not None and self._process.poll() is None:
                    # Periodic health logging (every 5 seconds)
                    current_time = time.time()
                    if current_time - self._last_log_time >= 5.0:
                        queue_len = 0
                        with self._queue_lock:
                            queue_len = len(self._queue)
                        logger.info(f"[YT] queue={queue_len} connected={self._is_connected}")
                        self._last_log_time = current_time
                    
                    # Get frame from queue
                    frame = None
                    queue_len = 0
                    with self._queue_lock:
                        queue_len = len(self._queue)
                        if queue_len > 0:
                            frame = self._queue.popleft()
                    
                    # Queue underrun safety check
                    if queue_len < 10 and queue_len > 0:
                        logger.warning(f"[YT] Queue underrun: only {queue_len} frames remaining")
                    
                    if frame is None:
                        # Queue empty - flush any pending writes and wait
                        if batch_size > 0:
                            try:
                                self._process.stdin.flush()
                            except (BrokenPipeError, OSError):
                                pass
                            batch_size = 0
                        
                        self._queue_event.clear()
                        if not self._queue_event.wait(timeout=0.1):
                            # Timeout - continue loop to check process
                            continue
                        # Event set - try again
                        continue
                    
                    # Calculate frame interval for pacing
                    # frame_bytes / (sample_rate * channels * bytes_per_sample)
                    # For s16le: 2 bytes per sample
                    frame_interval = len(frame) / (self.sample_rate * self.channels * 2)
                    
                    # Adjust pacing based on queue level
                    # If queue is low, slow down slightly to prevent starvation
                    if queue_len < 10:
                        # Slow down by 10% when queue is low
                        sleep_time = frame_interval * 1.1
                    else:
                        # Normal pacing with slight reduction to prevent drift
                        sleep_time = frame_interval * 0.9
                    
                    # Write frame to FFmpeg stdin
                    try:
                        self._process.stdin.write(frame)
                        batch_size += 1
                        frames_written += 1
                        if not self._is_connected:
                            logger.info("YouTube stream connected")
                        self._is_connected = True
                        
                        # Flush every 10 frames to balance performance and latency
                        if batch_size >= 10:
                            self._process.stdin.flush()
                            batch_size = 0
                        
                        # Pace writes to match real-time audio flow
                        time.sleep(sleep_time)
                    except BrokenPipeError:
                        logger.warning("YouTubeSink: Broken pipe - FFmpeg process may have died")
                        if self._is_connected:
                            logger.warning("YouTube stream disconnected (broken pipe)")
                        self._is_connected = False
                        # Break inner loop to reconnect
                        break
                    except Exception as e:
                        logger.error(f"YouTubeSink write error: {e}")
                        if self._is_connected:
                            logger.warning("YouTube stream disconnected (broken pipe)")
                        self._is_connected = False
                        break
                
                # Final flush before reconnecting
                if batch_size > 0 and self._process is not None:
                    try:
                        self._process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        pass
                
                # Process died or error - close and reconnect
                if self._process is not None:
                    self._close_ffmpeg_process()
                    if self._is_connected:
                        logger.warning("YouTube stream disconnected (process died)")
                    self._is_connected = False
                
            except Exception as e:
                logger.error(f"YouTubeSink worker error: {e}", exc_info=True)
                if self._is_connected:
                    logger.warning("YouTube stream disconnected (worker error)")
                self._is_connected = False
                # Wait before retry
                if self._running:
                    time.sleep(self.reconnect_delay)
        
        # Cleanup on exit
        self._close_ffmpeg_process()
        logger.info("YouTubeSink worker thread stopped")
    
    def _start_ffmpeg_process(self) -> bool:
        """
        Start FFmpeg process for RTMP streaming.
        
        Returns:
            True if started successfully, False otherwise
        """
        try:
            # Build video input based on video_source
            if self.video_source == "video":
                # Video file (looped)
                if not self.video_file:
                    logger.error("video_source is 'video' but no video_file provided")
                    return False
                import os
                if not os.path.exists(self.video_file):
                    logger.error(f"Video file not found: {self.video_file}")
                    return False
                video_input = [
                    "-re",  # Read input at native frame rate (ONLY for video file input)
                    "-stream_loop", "-1",  # Loop video indefinitely
                    "-i", self.video_file
                ]
                logger.info(f"Using video file: {self.video_file}")
            elif self.video_source == "image":
                # Static image (looped)
                if not self.video_file:
                    logger.error("video_source is 'image' but no video_file provided")
                    return False
                import os
                if not os.path.exists(self.video_file):
                    logger.error(f"Image file not found: {self.video_file}")
                    return False
                video_input = [
                    "-re",  # Read input at native frame rate (ONLY for image file input)
                    "-loop", "1",  # Loop image
                    "-framerate", str(self.video_fps),  # Set framerate
                    "-i", self.video_file
                ]
                logger.info(f"Using image file: {self.video_file}")
            elif self.video_source == "color":
                # Solid color background
                video_input = [
                    "-f", "lavfi",  # Video input format (libavfilter)
                    "-i", f"color=black:s={self.video_size}:r={self.video_fps}"  # Black background
                ]
                logger.debug("Using solid color background")
            else:
                logger.error(f"Invalid video_source: {self.video_source}. Must be 'color', 'image', or 'video'")
                return False
            
            # Build FFmpeg command
            # Input: raw PCM from stdin (s16le, stereo, 48kHz) + video
            # Output: AAC audio + H.264 video to RTMP
            # YouTube requires both audio and video streams
            # NOTE: DO NOT use -re on audio input (pipe:0) - we want real-time data flow
            cmd = [
                "ffmpeg",
                "-f", "s16le",  # Audio input format
                "-ac", str(self.channels),  # Audio channels
                "-ar", str(self.sample_rate),  # Audio sample rate
                "-i", "pipe:0",  # Read audio from stdin (real-time, no throttling)
            ]
            # Add video input (with -re ONLY for file-based inputs)
            cmd.extend(video_input)
            
            # Calculate GOP size (keyframes every 2 seconds for smooth playback)
            # YouTube recommends keyframes every 2 seconds
            gop_size = self.video_fps * 2  # Keyframe every 2 seconds
            if gop_size < 30:
                gop_size = 30  # Minimum GOP size
            
            # Calculate buffer size (2x video bitrate for smoother streaming)
            try:
                # Parse bitrate (e.g., "4000k" -> 4000)
                bitrate_str = self.video_bitrate.rstrip('kKmM')
                bitrate_num = int(bitrate_str)
                bitrate_unit = self.video_bitrate[-1].lower() if self.video_bitrate[-1] in 'kKmM' else 'k'
                buffer_num = bitrate_num * 2
                buffer_size = f"{buffer_num}{bitrate_unit}"
            except (ValueError, IndexError):
                # Fallback if parsing fails
                buffer_size = "8000k"
            
            # Add encoding options optimized for YouTube streaming
            cmd.extend([
                "-c:a", "aac",  # Audio codec
                "-b:a", "160k",  # Audio bitrate
                "-af", "aresample=async=1:min_hard_comp=0.1:first_pts=0,asetpts=N/SR/TB",  # Generates proper PTS timestamps for real-time audio flow
                "-c:v", "libx264",  # Video codec
                "-preset", "veryfast",  # Better quality than ultrafast, still fast enough
                "-tune", "zerolatency",  # Low latency
                "-pix_fmt", "yuv420p",  # Pixel format (required for compatibility)
                "-b:v", self.video_bitrate,  # Video bitrate (configurable)
                "-maxrate", self.video_bitrate,  # Maximum bitrate (same as target)
                "-bufsize", buffer_size,  # Buffer size (2x bitrate) - helps prevent buffering
                "-g", str(gop_size),  # GOP size (keyframe every 2 seconds)
                "-keyint_min", str(self.video_fps),  # Minimum keyframe interval (1 second)
                "-sc_threshold", "0",  # Disable scene change detection for consistent keyframes
            ])
            
            # Scale video if needed (for image/video sources)
            if self.video_source in ["image", "video"]:
                cmd.extend(["-vf", f"scale={self.video_size}"])
            
            # Add output framerate and format
            cmd.extend([
                "-r", str(self.video_fps),  # Force output framerate
                "-f", "flv",  # Output format
                "-loglevel", "error",  # Suppress FFmpeg output
                self.rtmp_url  # RTMP URL
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
            
            # Give it a moment to start
            time.sleep(0.5)
            
            # Check if process is still running
            if self._process.poll() is not None:
                # Process died immediately
                stderr = self._process.stderr.read().decode('utf-8', errors='ignore') if self._process.stderr else "No error output"
                logger.error(f"FFmpeg process exited immediately: {stderr[:500]}")
                self._process = None
                return False
            
            logger.info("FFmpeg process started successfully")
            return True
            
        except FileNotFoundError:
            logger.error("FFmpeg not found. Please install: sudo apt-get install ffmpeg")
            return False
        except Exception as e:
            logger.error(f"Failed to start FFmpeg: {e}", exc_info=True)
            return False
    
    def _close_ffmpeg_process(self) -> None:
        """
        Close FFmpeg process gracefully.
        """
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
                    logger.warning("FFmpeg process didn't terminate, killing...")
                    self._process.kill()
                    self._process.wait()
                
            except Exception as e:
                logger.error(f"Error closing FFmpeg process: {e}")
            finally:
                self._process = None
    
    def stop(self) -> None:
        """
        Stop the YouTube sink by stopping worker thread and closing FFmpeg.
        """
        self._running = False
        
        # Signal thread to exit
        self._queue_event.set()
        
        # Close FFmpeg process
        self._close_ffmpeg_process()
        
        # Wait for thread to finish
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning("YouTubeSink worker thread did not stop in time")
        
        self._thread = None
        if self._is_connected:
            logger.info("YouTube stream disconnected (shutdown)")
        self._is_connected = False
        logger.info("YouTubeSink stopped")
    
    def is_connected(self) -> bool:
        """
        Check if YouTube stream is currently connected.
        
        Returns:
            True if connected, False otherwise
        """
        return self._is_connected and self._running

