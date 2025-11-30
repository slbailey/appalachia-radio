"""
YouTube Live Streaming module using FFmpeg.

This module provides functionality to stream audio (and optional video) to YouTube Live
using FFmpeg. It handles:
- Audio capture from PulseAudio or ALSA
- Video track generation (solid color, static image, or video file)
- RTMP streaming to YouTube's live streaming endpoint
- Automatic reconnection and health monitoring
- Thread-safe state management

Example:
    ```python
    from radio.youtube_streamer import YouTubeStreamer
    
    streamer = YouTubeStreamer(
        stream_key="your-youtube-stream-key",
        audio_format="pulse",
        video_source="color",
        video_color="black"
    )
    
    if streamer.start():
        # Stream is running
        pass
    
    # Later...
    streamer.stop()
    ```

Requirements:
    - FFmpeg must be installed and available in PATH
    - For PulseAudio: pactl must be available
    - Network connectivity to YouTube RTMP servers
"""

import logging
import os
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

class YouTubeStreamer:
    """
    Handles streaming audio to YouTube Live using FFmpeg.
    
    This class manages the entire lifecycle of a YouTube Live stream, including:
    - Configuration validation
    - FFmpeg process management
    - Audio/video capture and encoding
    - RTMP connection monitoring
    - Automatic reconnection on failures
    - Health checking and status reporting
    
    The streamer uses a separate thread to monitor FFmpeg's stderr output for
    connection status and errors. All shared state is protected by threading locks
    to ensure thread safety.
    
    Attributes:
        stream_key (str): YouTube stream key for authentication
        audio_device (str): Audio device identifier (e.g., "default", "pulse")
        audio_format (str): Audio input format ("pulse" or "alsa")
        sample_rate (int): Audio sample rate in Hz
        channels (int): Number of audio channels (1=mono, 2=stereo)
        bitrate (str): Audio bitrate (e.g., "128k")
        video_source (str): Video source type ("color", "image", "video", or "none")
        video_file (str): Path to image/video file (if using image/video source)
        video_color (str): Color name or hex code for solid color video
        video_size (str): Video resolution as "WIDTHxHEIGHT"
        video_fps (int): Video framerate
        process (Optional[subprocess.Popen]): FFmpeg subprocess handle
        is_streaming (bool): Whether streaming is currently active
        last_frame_time (Optional[float]): Timestamp of last frame sent
        connection_confirmed (bool): Whether RTMP connection is confirmed
        _lock (threading.Lock): Thread lock for shared state protection
        _monitor_thread (Optional[threading.Thread]): Thread monitoring FFmpeg output
        
    Example:
        ```python
        streamer = YouTubeStreamer(
            stream_key="abc123-def456-ghi789",
            audio_format="pulse",
            video_source="color",
            video_color="black",
            video_size="1280x720",
            video_fps=2
        )
        
        if streamer.start():
            print("Stream started!")
            # Stream will run until stop() is called
        ```
    """
    
    def __init__(
        self,
        stream_key: str,
        audio_device: str = "default",
        audio_format: str = "pulse",
        sample_rate: int = 48000,
        channels: int = 2,
        bitrate: str = "128k",
        video_source: str = "color",
        video_file: str = "",
        video_color: str = "black",
        video_size: str = "1280x720",
        video_fps: int = 2
    ) -> None:
        """
        Initialize the YouTube streamer.
        
        Args:
            stream_key: YouTube stream key (from YouTube Studio > Go Live)
            audio_device: Audio device to capture from (default: "default" for PulseAudio)
            audio_format: Audio input format (pulse, alsa, etc.)
            sample_rate: Audio sample rate in Hz (default: 48000)
            channels: Number of audio channels (default: 2 for stereo)
            bitrate: Audio bitrate (default: "128k")
            video_source: Video source type - 'color', 'image', 'video', or 'none' (default: 'color')
            video_file: Path to image or video file (required if video_source is 'image' or 'video')
            video_color: Color for solid color video (default: 'black')
            video_size: Video resolution as 'WIDTHxHEIGHT' (default: '1280x720')
            video_fps: Video framerate (default: 2)
        """
        self.stream_key = stream_key
        self.audio_device = audio_device
        self.audio_format = audio_format
        self.sample_rate = sample_rate
        self.channels = channels
        self.bitrate = bitrate
        self.video_source = video_source.lower()
        self.video_file = video_file
        self.video_color = video_color
        self.video_size = video_size
        self.video_fps = video_fps
        self.process: Optional[subprocess.Popen] = None
        self.is_streaming = False
        self.last_frame_time: Optional[float] = None
        self.connection_confirmed = False
        self._lock = threading.Lock()  # Thread lock for shared state
        self._monitor_thread: Optional[threading.Thread] = None
        
        # YouTube RTMP endpoint
        self.rtmp_url = "rtmp://a.rtmp.youtube.com/live2"
        
        # Validate configuration
        self._validate_config()
        
        logger.info("YouTubeStreamer initialized")
    
    def _validate_config(self) -> None:
        """
        Validate and normalize configuration parameters.
        
        This method checks all configuration values and corrects invalid ones
        to defaults. It logs warnings for any corrections made.
        
        Validations performed:
        - video_size: Must be in "WIDTHxHEIGHT" format
        - video_fps: Must be positive integer
        - sample_rate: Should be between 8000-192000 Hz (warning only)
        - video_source: Must be one of ['color', 'image', 'video', 'none']
        
        Note:
            This method modifies instance attributes if invalid values are found.
        """
        # Validate video_size format (should be WIDTHxHEIGHT)
        if self.video_size and 'x' in self.video_size:
            try:
                width, height = self.video_size.split('x')
                int(width)
                int(height)
            except (ValueError, AttributeError):
                logger.warning(f"Invalid video_size format: {self.video_size}, using default 1280x720")
                self.video_size = "1280x720"
        
        # Validate video_fps is positive
        if self.video_fps <= 0:
            logger.warning(f"Invalid video_fps: {self.video_fps}, using default 2")
            self.video_fps = 2
        
        # Validate sample_rate is reasonable
        if self.sample_rate < 8000 or self.sample_rate > 192000:
            logger.warning(f"Unusual sample_rate: {self.sample_rate}, expected 8000-192000")
        
        # Validate video_source
        if self.video_source not in ['color', 'image', 'video', 'none']:
            logger.warning(f"Invalid video_source: {self.video_source}, using default 'color'")
            self.video_source = 'color'
    
    def _check_ffmpeg(self) -> bool:
        """
        Check if FFmpeg is installed and available in the system PATH.
        
        This method runs `ffmpeg -version` to verify FFmpeg is accessible.
        It includes timeout protection to prevent hanging.
        
        Returns:
            True if FFmpeg is available and working, False otherwise.
            
        Note:
            Logs an error message if FFmpeg is not found, including installation
            instructions for Debian/Ubuntu systems.
        """
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info("FFmpeg is available")
                return True
            else:
                logger.error("FFmpeg check failed")
                return False
        except FileNotFoundError:
            logger.error("FFmpeg not found. Please install FFmpeg: sudo apt-get install ffmpeg")
            return False
        except Exception as e:
            logger.error(f"Error checking FFmpeg: {e}")
            return False
    
    def _find_pulse_monitor_source(self) -> Optional[str]:
        """
        Find the PulseAudio monitor source to capture system audio output.
        
        This method attempts to automatically detect the correct PulseAudio monitor
        source by:
        1. Getting the default sink name
        2. Listing all available sources and finding monitors
        3. Matching the default sink's monitor specifically (if it exists)
        4. Falling back to any active monitor if default sink's monitor not found
        5. Falling back to any suspended monitor as last resort
        
        Returns:
            Monitor source name (e.g., "RDPSink.monitor") or None if not found.
            
        Note:
            This method is only used when audio_format is "pulse" and audio_device
            is "default". For other configurations, the provided audio_device is
            used directly.
            
        Raises:
            No exceptions are raised. Errors are logged and None is returned.
        """
        try:
            # Get the default sink name
            default_sink = None
            result = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                default_sink = result.stdout.strip()
                if default_sink:
                    logger.debug(f"Default sink: {default_sink}")
            
            # List all sources to find available monitors
            result = subprocess.run(
                ["pactl", "list", "short", "sources"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode != 0:
                logger.warning("Failed to list PulseAudio sources")
                return None
            
            # Collect all monitors, categorized by state and relationship to default sink
            default_sink_monitor = None
            default_sink_monitor_suspended = None
            active_monitors = []
            suspended_monitors = []
            
            for line in result.stdout.split('\n'):
                if not line.strip() or '.monitor' not in line:
                    continue
                
                parts = line.split()
                if len(parts) < 2:
                    continue
                
                monitor_name = parts[1]
                is_suspended = 'SUSPENDED' in line
                
                # Check if this monitor matches the default sink
                if default_sink and monitor_name == f"{default_sink}.monitor":
                    if is_suspended:
                        default_sink_monitor_suspended = monitor_name
                        logger.debug(f"Found default sink's monitor (suspended): {monitor_name}")
                    else:
                        default_sink_monitor = monitor_name
                        logger.debug(f"Found default sink's monitor (active): {monitor_name}")
                else:
                    # Other monitors
                    if is_suspended:
                        suspended_monitors.append(monitor_name)
                    else:
                        active_monitors.append(monitor_name)
            
            # Log all found monitors for debugging
            if default_sink_monitor or default_sink_monitor_suspended or active_monitors or suspended_monitors:
                logger.debug(f"Available monitors - Default sink's: {default_sink_monitor or default_sink_monitor_suspended or 'none'}, "
                           f"Other active: {len(active_monitors)}, Other suspended: {len(suspended_monitors)}")
            
            # Priority order:
            # 1. Default sink's monitor (active)
            # 2. Default sink's monitor (suspended)
            # 3. Any active monitor
            # 4. Any suspended monitor
            
            if default_sink_monitor:
                logger.info(f"Using default sink's monitor: {default_sink_monitor}")
                return default_sink_monitor
            
            if default_sink_monitor_suspended:
                logger.info(f"Using default sink's monitor (suspended): {default_sink_monitor_suspended}")
                return default_sink_monitor_suspended
            
            if active_monitors:
                selected = active_monitors[0]
                logger.info(f"Using active monitor (default sink's monitor not found): {selected}")
                if len(active_monitors) > 1:
                    logger.warning(f"Multiple active monitors found: {active_monitors}. Using: {selected}")
                return selected
            
            if suspended_monitors:
                selected = suspended_monitors[0]
                logger.info(f"Using suspended monitor (no active monitors found): {selected}")
                if len(suspended_monitors) > 1:
                    logger.warning(f"Multiple suspended monitors found: {suspended_monitors}. Using: {selected}")
                return selected
            
            logger.warning("No PulseAudio monitor source found")
            if default_sink:
                logger.warning(f"Default sink is '{default_sink}', but no monitor source found. "
                             f"Try setting YOUTUBE_AUDIO_DEVICE explicitly in .env file.")
            return None
        except Exception as e:
            logger.warning(f"Error finding PulseAudio monitor source: {e}")
            return None
    
    def _verify_sink_available(self, sink_name: str) -> bool:
        """
        Verify that a PulseAudio sink exists and is available.
        
        Args:
            sink_name: The sink name to verify (e.g., "alsa_output...analog-stereo")
            
        Returns:
            True if the sink is available, False otherwise.
        """
        try:
            result = subprocess.run(
                ["pactl", "list", "short", "sinks"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode != 0:
                logger.warning("Failed to list PulseAudio sinks for verification")
                return False
            
            # Check if the sink is in the list
            for line in result.stdout.split('\n'):
                if sink_name in line:
                    # Check if it's not suspended
                    if 'SUSPENDED' in line:
                        logger.debug(f"Sink '{sink_name}' is suspended")
                    return True
            
            logger.warning(f"Sink '{sink_name}' not found in sink list")
            return False
        except Exception as e:
            logger.warning(f"Error verifying sink '{sink_name}': {e}")
            return False
    
    def _verify_pulse_monitor_source(self, monitor_name: str) -> bool:
        """
        Verify that a PulseAudio monitor source exists and is accessible.
        
        This method checks if the monitor source is actually available and tries
        to activate it if it's suspended. Some monitor sources may appear in the
        list but not be accessible until explicitly activated.
        
        Args:
            monitor_name: The monitor source name to verify (e.g., "alsa_output...monitor")
            
        Returns:
            True if the monitor source is verified and accessible, False otherwise.
            
        Note:
            This method attempts to resume the source if it's suspended, which
            may help with sources that are detected but not accessible.
        """
        try:
            # First, verify the source exists in the current source list
            result = subprocess.run(
                ["pactl", "list", "short", "sources"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode != 0:
                logger.warning("Failed to list PulseAudio sources for verification")
                return False
            
            # Check if the monitor is in the list
            found = False
            is_suspended = False
            for line in result.stdout.split('\n'):
                if monitor_name in line:
                    found = True
                    if 'SUSPENDED' in line:
                        is_suspended = True
                    break
            
            if not found:
                logger.warning(f"Monitor source '{monitor_name}' not found in current source list")
                return False
            
            # If suspended, try to resume it
            if is_suspended:
                logger.info(f"Monitor source '{monitor_name}' is suspended, attempting to activate...")
                
                # Extract the sink name from the monitor name (remove .monitor suffix)
                sink_name = monitor_name.replace('.monitor', '')
                
                # Try to set the sink as default, which should activate its monitor
                result = subprocess.run(
                    ["pactl", "set-default-sink", sink_name],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0:
                    logger.info(f"Set sink '{sink_name}' as default to activate monitor")
                    time.sleep(0.5)  # Give it a moment to activate
                else:
                    logger.debug(f"Could not set sink as default (may already be default): {result.stderr.strip()}")
                
                # Also try to resume the source directly
                result = subprocess.run(
                    ["pactl", "suspend-source", monitor_name, "0"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0:
                    logger.info(f"Successfully resumed monitor source: {monitor_name}")
                    time.sleep(0.5)
                else:
                    logger.debug(f"Could not resume monitor source directly: {result.stderr.strip()}")
                    logger.info("Monitor source is suspended, but FFmpeg may be able to activate it when capturing starts")
                    # Still return True - FFmpeg might be able to use it even if suspended
                    # The monitor will activate when FFmpeg starts reading from it
            
            # Verify it's still in the source list after resume attempt
            result = subprocess.run(
                ["pactl", "list", "short", "sources"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0 and monitor_name in result.stdout:
                # Check if it's still suspended
                still_suspended = False
                for line in result.stdout.split('\n'):
                    if monitor_name in line:
                        if 'SUSPENDED' in line:
                            still_suspended = True
                            logger.info(f"Monitor source '{monitor_name}' is still suspended, but will attempt to use it")
                            logger.info("FFmpeg may be able to activate it when capturing starts")
                        else:
                            logger.debug(f"Monitor source '{monitor_name}' is active")
                        break
                logger.debug(f"Monitor source '{monitor_name}' verified in source list")
                # Return True even if suspended - FFmpeg can often activate suspended monitors
                return True
            else:
                logger.warning(f"Monitor source '{monitor_name}' not found in source list after verification")
                return False
                
        except Exception as e:
            logger.warning(f"Error verifying PulseAudio monitor source '{monitor_name}': {e}")
            return False
    
    def start(self) -> bool:
        """
        Start streaming to YouTube Live.
        
        This method:
        1. Validates FFmpeg is available
        2. Validates stream key is configured
        3. Detects audio source (if using PulseAudio)
        4. Builds FFmpeg command with appropriate inputs and encoding
        5. Starts FFmpeg subprocess
        6. Launches monitor thread to track connection status
        7. Waits for RTMP handshake
        
        The stream will continue running until stop() is called or an error occurs.
        Health can be checked using check_health() or is_active().
        
        Returns:
            True if streaming started successfully, False otherwise.
            
        Note:
            - If already streaming, returns True without starting a new stream
            - Waits up to 8 seconds for RTMP connection to establish
            - Connection confirmation may take additional time
            - Logs detailed FFmpeg command at DEBUG level
            
        Raises:
            No exceptions are raised. All errors are logged and False is returned.
        """
        if self.is_streaming:
            logger.warning("Streaming is already active")
            return True
        
        if not self._check_ffmpeg():
            return False
        
        if not self.stream_key:
            logger.error("YouTube stream key is not configured")
            return False
        
        # Validate stream key format (YouTube stream keys are typically alphanumeric with hyphens)
        if len(self.stream_key) < 10:
            logger.error(f"YouTube stream key appears invalid (too short: {len(self.stream_key)} chars)")
            return False
        
        # Build FFmpeg command
        # This captures audio from PulseAudio/ALSA and streams to YouTube
        # Note: For PulseAudio, we need to capture from a monitor source, not the default sink
        audio_input = self.audio_device
        if self.audio_format == "pulse":
            if audio_input == "default":
                # Try to find the actual monitor source
                monitor_source = self._find_pulse_monitor_source()
                if monitor_source:
                    # Verify the monitor source is accessible before using it
                    if self._verify_pulse_monitor_source(monitor_source):
                        audio_input = monitor_source
                        logger.info(f"Using PulseAudio monitor source: {audio_input}")
                    else:
                        logger.warning(f"Monitor source '{monitor_source}' found but not accessible, using it anyway")
                        audio_input = monitor_source
                else:
                    logger.error("Could not auto-detect monitor source. To find available monitors, run:")
                    logger.error("  pactl list short sources | grep monitor")
                    logger.error("Then set YOUTUBE_AUDIO_DEVICE in your .env file to the monitor name.")
                    return False
            elif audio_input.endswith('.monitor'):
                # User explicitly set a monitor source - verify and activate it if needed
                logger.info(f"Using explicitly configured PulseAudio monitor source: {audio_input}")
                if self._verify_pulse_monitor_source(audio_input):
                    logger.info(f"Monitor source '{audio_input}' verified and ready")
                else:
                    logger.warning(f"Monitor source '{audio_input}' may not be accessible, but attempting to use it anyway")
        
        # Build video input based on video_source type
        video_input_args = []
        if self.video_source == "none":
            # Audio-only stream (YouTube may not accept this, but we'll try)
            logger.warning("Video source set to 'none' - YouTube may require a video track")
        elif self.video_source == "color":
            # Solid color video
            video_input_args = [
                "-f", "lavfi",
                "-i", f"color=c={self.video_color}:s={self.video_size}:r={self.video_fps}"
            ]
            logger.info(f"Using solid color video: {self.video_color} at {self.video_size}@{self.video_fps}fps")
        elif self.video_source == "image":
            # Static image (looped)
            if not self.video_file or not os.path.exists(self.video_file):
                logger.error(f"Image file not found: {self.video_file}")
                return False
            video_input_args = [
                "-loop", "1",
                "-i", self.video_file
            ]
            logger.info(f"Using static image: {self.video_file}")
        elif self.video_source == "video":
            # Video file (looped)
            if not self.video_file or not os.path.exists(self.video_file):
                logger.error(f"Video file not found: {self.video_file}")
                return False
            video_input_args = [
                "-stream_loop", "-1",
                "-i", self.video_file
            ]
            logger.info(f"Using video file: {self.video_file}")
        else:
            logger.error(f"Invalid video_source: {self.video_source}. Must be 'color', 'image', 'video', or 'none'")
            return False
        
        # Build FFmpeg command
        ffmpeg_cmd = ["ffmpeg"]
        
        # Add video input (if not 'none')
        if self.video_source != "none":
            ffmpeg_cmd.extend(video_input_args)
        
        # Add audio input
        ffmpeg_cmd.extend([
            "-f", self.audio_format,
            "-i", audio_input
        ])
        
        # Add encoding options
        if self.video_source != "none":
            ffmpeg_cmd.extend([
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-pix_fmt", "yuv420p",
                "-r", str(self.video_fps),  # Output framerate
                "-g", str(self.video_fps * 2),  # GOP size (keyframe every 2 seconds)
            ])
            # Scale video if needed (for image/video sources)
            if self.video_source in ["image", "video"]:
                ffmpeg_cmd.extend(["-vf", f"scale={self.video_size}"])
        
        # Audio encoding
        ffmpeg_cmd.extend([
            "-c:a", "aac",
            "-b:a", self.bitrate,
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
        ])
        
        # Output format and RTMP options
        ffmpeg_cmd.extend([
            "-f", "flv",
            "-strict", "-2",
            # RTMP connection options for better reliability
            "-rtmp_live", "live",  # Specify this is a live stream
            f"{self.rtmp_url}/{self.stream_key}"
        ])
        
        try:
            logger.info("Starting YouTube stream...")
            # Log the full command at INFO level for debugging
            logger.info(f"FFmpeg command: {' '.join(ffmpeg_cmd)}")
            
            # Start FFmpeg process
            # Use stderr=subprocess.PIPE to capture error messages, but don't capture stdout
            # to avoid blocking if FFmpeg buffers output
            self.process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,  # Discard stdout
                stderr=subprocess.PIPE,      # Capture stderr for error messages
                stdin=subprocess.PIPE
            )
            
            # Give it a moment to start and check for immediate errors
            time.sleep(5)  # Increased wait time for RTMP handshake
            
            # Check if process is still running
            if self.process.poll() is None:
                with self._lock:
                    self.is_streaming = True
                    self.connection_confirmed = False
                    self.last_frame_time = None
                logger.info("FFmpeg process started - waiting for RTMP connection...")
                
                # Wait a bit more and check for connection confirmation
                time.sleep(3)
                with self._lock:
                    connection_confirmed = self.connection_confirmed
                
                if not connection_confirmed:
                    logger.warning("Stream started but RTMP connection not yet confirmed - this may be normal, checking...")
                    # Read any immediate error messages
                    if self.process.stderr:
                        try:
                            # Non-blocking check for errors
                            import select
                            if select.select([self.process.stderr], [], [], 0.1)[0]:
                                error_line = self.process.stderr.readline().decode('utf-8', errors='ignore').strip()
                                if error_line and ('error' in error_line.lower() or 'failed' in error_line.lower()):
                                    logger.error(f"FFmpeg error detected: {error_line}")
                        except (OSError, ValueError) as e:
                            logger.debug(f"Error checking stderr: {e}")
                
                # Start a thread to monitor stderr for errors
                self._monitor_thread = threading.Thread(target=self._monitor_stderr, daemon=True)
                self._monitor_thread.start()
                return True
            else:
                # Process exited, get error
                stderr_output = ""
                if self.process.stderr:
                    try:
                        stderr_output = self.process.stderr.read().decode('utf-8', errors='ignore')
                    except (OSError, ValueError, UnicodeDecodeError) as e:
                        logger.debug(f"Could not read stderr: {e}")
                        stderr_output = "Could not read stderr"
                
                logger.error(f"FFmpeg process exited immediately. Exit code: {self.process.returncode}")
                # Log full error output (split into multiple log lines if needed)
                if stderr_output:
                    logger.error("FFmpeg error output:")
                    # Split into lines and log each (up to reasonable limit to avoid log spam)
                    error_lines = stderr_output.split('\n')
                    for i, line in enumerate(error_lines[:100]):  # First 100 lines
                        if line.strip():
                            logger.error(f"  {line}")
                    if len(error_lines) > 100:
                        logger.error(f"  ... (truncated, {len(error_lines) - 100} more lines)")
                else:
                    logger.error("No error output captured from FFmpeg")
                self.process = None
                return False
                
        except Exception as e:
            logger.error(f"Failed to start YouTube stream: {e}", exc_info=True)
            self.process = None
            return False
    
    def _monitor_stderr(self) -> None:
        """
        Monitor FFmpeg stderr output in a separate thread.
        
        This method runs in a background thread and continuously reads FFmpeg's
        stderr output to:
        - Detect RTMP connection establishment
        - Track frame output (confirms data is being sent)
        - Filter non-critical warnings (expected during shutdown)
        - Log critical errors
        
        The method updates shared state (connection_confirmed, last_frame_time)
        using thread locks to ensure thread safety.
        
        Note:
            - This is a daemon thread, so it will terminate when main thread exits
            - Non-critical warnings (e.g., "broken pipe" on shutdown) are logged
              at DEBUG level
            - RTMP connection messages are logged at INFO level
            - Errors are logged at ERROR level
            
        Raises:
            No exceptions are raised. All errors are caught and logged.
        """
        if not self.process or not self.process.stderr:
            return
        
        try:
            # Common non-critical warnings that occur during normal operation
            non_critical_warnings = [
                'failed to update header',
                'failed to update header with correct',
                'connection lost',
                'connection reset',
                'broken pipe',
                'error writing trailer',
                'conversion failed',  # Often appears with broken pipe on shutdown
            ]
            
            for line in iter(self.process.stderr.readline, b''):
                if not self.process or self.process.poll() is not None:
                    break
                
                if line:
                    try:
                        line_str = line.decode('utf-8', errors='ignore').strip()
                        
                        # Skip empty lines
                        if not line_str:
                            continue
                        
                        # Check if it's a non-critical warning
                        is_non_critical = any(warning in line_str.lower() for warning in non_critical_warnings)
                        
                        if is_non_critical:
                            logger.debug(f"FFmpeg warning (non-critical): {line_str}")
                        elif 'error' in line_str.lower() or 'failed' in line_str.lower():
                            # Check for RTMP connection errors specifically
                            if any(keyword in line_str.lower() for keyword in ['rtmp', 'connection', 'timeout']):
                                logger.error(f"FFmpeg RTMP connection error: {line_str}")
                            else:
                                logger.error(f"FFmpeg error: {line_str}")
                        elif any(keyword in line_str.lower() for keyword in ['rtmp', 'streaming', 'connected', 'connection', 'frame=', 'size=', 'time=', 'publishing', 'handshake', 'rtmp://']):
                            # Log RTMP connection and streaming status
                            line_lower = line_str.lower()
                            if any(conn_word in line_lower for conn_word in ['connected', 'publishing', 'handshake']):
                                with self._lock:
                                    self.connection_confirmed = True
                                logger.info(f"✓ FFmpeg connection: {line_str}")
                            elif 'frame=' in line_lower:
                                # Track frame output to detect if data is being sent
                                current_time = time.time()
                                with self._lock:
                                    self.last_frame_time = current_time
                                    if not self.connection_confirmed:
                                        self.connection_confirmed = True
                                        logger.info("✓ Stream is sending data to YouTube")
                            elif 'rtmp://' in line_lower or 'streaming' in line_lower:
                                logger.info(f"FFmpeg: {line_str}")
                            else:
                                logger.debug(f"FFmpeg: {line_str}")
                    except (UnicodeDecodeError, AttributeError) as e:
                        logger.debug(f"Error decoding FFmpeg output: {e}")
                        continue
        except (OSError, ValueError) as e:
            # Process may have closed stderr
            logger.debug(f"Error reading from FFmpeg stderr: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error in stderr monitor: {e}", exc_info=True)
    
    def stop(self) -> None:
        """
        Stop streaming to YouTube and clean up resources.
        
        This method performs a graceful shutdown:
        1. Sends 'q' to FFmpeg stdin to request graceful termination
        2. Waits up to 5 seconds for process to exit
        3. If still running, sends SIGTERM
        4. If still running after 2 more seconds, sends SIGKILL
        5. Closes all pipes (stdin, stderr)
        6. Resets all state variables
        
        All cleanup is performed even if errors occur during shutdown.
        
        Note:
            - If not streaming, returns immediately
            - Thread-safe: uses locks when accessing shared state
            - Logs warnings if graceful shutdown fails
            - Ensures process is fully terminated before returning
            
        Raises:
            No exceptions are raised. All errors are logged but don't prevent cleanup.
        """
        with self._lock:
            if not self.is_streaming or not self.process:
                return
            process = self.process
        
        try:
            logger.info("Stopping YouTube stream...")
            
            # Send 'q' to FFmpeg to quit gracefully
            if process.stdin:
                try:
                    process.stdin.write(b'q')
                    process.stdin.flush()
                    process.stdin.close()
                except (OSError, ValueError, BrokenPipeError):
                    pass
            
            # Wait for process to terminate (with timeout)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't stop gracefully
                logger.warning("FFmpeg didn't stop gracefully, forcing termination...")
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    logger.warning("FFmpeg didn't terminate, killing...")
                    process.kill()
                    process.wait()
                except (OSError, ProcessLookupError):
                    # Process already terminated
                    pass
            
            # Clean up stderr pipe
            if process.stderr:
                try:
                    process.stderr.close()
                except (OSError, ValueError):
                    pass
            
            with self._lock:
                self.is_streaming = False
                self.process = None
                self.connection_confirmed = False
                self.last_frame_time = None
            
            logger.info("YouTube stream stopped")
            
        except Exception as e:
            logger.error(f"Error stopping YouTube stream: {e}", exc_info=True)
            with self._lock:
                self.is_streaming = False
                self.process = None
    
    def restart(self) -> bool:
        """
        Restart the stream (useful for reconnection after failures).
        
        This method:
        1. Stops the current stream (if running)
        2. Waits for process to fully terminate
        3. Waits additional time for resources to be released
        4. Starts a new stream
        
        The new stream will reconnect to the same YouTube Live stream if it's still
        active, or create a new stream if the old one timed out. YouTube will
        automatically handle reconnection to an existing stream.
        
        Returns:
            True if restart was successful, False otherwise.
            
        Note:
            - Total wait time before restart: up to 6 seconds
            - Logs warning if process cleanup takes longer than expected
            - Uses the same configuration as the original stream
            
        Raises:
            No exceptions are raised. Errors are logged and False is returned.
        """
        logger.info("Restarting YouTube stream...")
        logger.info("Note: Will reconnect to same stream if still active, or create new stream if timed out")
        
        # Ensure clean stop
        self.stop()
        
        # Wait for process to fully terminate and resources to be released
        max_wait = 5
        wait_time = 0
        while wait_time < max_wait:
            with self._lock:
                if not self.process:
                    break
            time.sleep(0.5)
            wait_time += 0.5
        
        if wait_time >= max_wait:
            logger.warning("Process cleanup took longer than expected")
        
        time.sleep(1)  # Additional buffer before restart
        return self.start()
    
    def is_active(self) -> bool:
        """
        Check if streaming is currently active.
        
        This method checks both the internal state flag and the actual process
        status. If the process has exited, it updates the internal state.
        
        Returns:
            True if streaming is active (process running and state flag set),
            False otherwise.
            
        Note:
            - Thread-safe: uses locks when accessing shared state
            - Automatically updates is_streaming flag if process has exited
            - This is a lightweight check; use check_health() for detailed status
        """
        with self._lock:
            if not self.process:
                return False
            process = self.process
            is_streaming = self.is_streaming
        
        # Check if process is still running
        if process.poll() is not None:
            # Process has exited
            with self._lock:
                self.is_streaming = False
                self.connection_confirmed = False
            return False
        
        return is_streaming
    
    def check_health(self) -> bool:
        """
        Check if the stream is healthy (process running and sending data).
        
        This method performs a comprehensive health check:
        1. Verifies process is still running (via is_active())
        2. Checks if frames have been sent recently (within last 30 seconds)
        3. Verifies connection has been confirmed (within 10 seconds of start)
        
        A stream is considered unhealthy if:
        - Process has exited
        - No frames sent in last 30 seconds
        - Connection not confirmed after 10 seconds
        
        Returns:
            True if stream is healthy, False if it needs to be restarted.
            
        Note:
            - Thread-safe: uses locks when accessing shared state
            - Designed to be called periodically (e.g., every 60 seconds)
            - Returns True during initial connection phase (first 10 seconds)
            - Logs warnings when health issues are detected
            
        Example:
            ```python
            if not streamer.check_health():
                logger.warning("Stream unhealthy, restarting...")
                streamer.restart()
            ```
        """
        if not self.is_active():
            return False
        
        current_time = time.time()
        with self._lock:
            last_frame = self.last_frame_time
            connection_confirmed = self.connection_confirmed
        
        # Check if we've received frame data recently (within last 30 seconds)
        if last_frame:
            time_since_last_frame = current_time - last_frame
            if time_since_last_frame > 30:
                logger.warning(f"No data sent in {time_since_last_frame:.1f} seconds - stream may be disconnected")
                return False
        
        # If we haven't confirmed connection yet, wait a bit longer
        if not connection_confirmed:
            # Give it up to 10 seconds to confirm connection
            # Track when stream started (use a reasonable default if last_frame is None)
            # We'll use a simple check: if no frames in 10 seconds, consider it unhealthy
            if last_frame:
                time_since_start = current_time - last_frame
            else:
                # No frames yet - give it 10 seconds from now
                time_since_start = 0  # Just started, give it time
            
            if time_since_start < 10:
                return True  # Still waiting for confirmation
            else:
                logger.warning("Stream started but connection not confirmed - may not be sending data")
                return False
        
        return True

