"""
Audio mixer for frame-based audio processing.

This module provides the AudioMixer class, which processes PCM frames
and outputs them to registered audio sinks. Uses MasterClock for
synchronized, clock-driven frame delivery with real-time decoding.
"""

import logging
import sys
import threading
import time
from collections import deque
from typing import Optional
from broadcast_core.event_queue import AudioEvent
from mixer.audio_decoder import AudioDecoder

logger = logging.getLogger(__name__)


class AudioMixer:
    """
    Frame-based audio mixer with clock-driven decoding.
    
    Processes PCM frames and outputs them to registered audio sinks.
    Decoding happens in real-time, one frame per clock tick.
    """
    
    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        frame_size: int = 4096,
        master_clock = None
    ) -> None:
        """
        Initialize the audio mixer.
        
        Args:
            sample_rate: Audio sample rate in Hz (default: 48000)
            channels: Number of audio channels (default: 2 = stereo)
            frame_size: Frame size in bytes (default: 4096)
            master_clock: MasterClock instance (required for clock-driven decoding)
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_size = frame_size
        self.sinks = []
        self.fm_sink = None  # Primary sink
        
        # Clock-driven decoder (one frame per tick)
        self.decoder = AudioDecoder(sample_rate, channels, frame_size)
        self._decoder_lock = threading.Lock()
        
        # Small buffer (max 10 frames) for smoothing
        self._frame_buffer: deque[bytes] = deque(maxlen=10)
        self._buffer_lock = threading.Lock()
        
        # Warm-start buffer threshold
        self._min_buffer_frames = 10
        
        # Master clock
        self._master_clock = master_clock
        self._last_underrun_log_index = -1
        
        # Ready flag - prevents writing silence to sinks before real audio arrives
        self.ready = False
        
        # Buffer warming state
        self._buffer_ready = False  # True when buffer has >= min_buffer_frames
        self._warming_ticks = 0  # Counter for warming period
        self._last_data_time = None  # Track when we last had data
        
        # Event completion callback
        self._event_complete_callback = None
        
        # Register with master clock if provided
        if master_clock:
            master_clock.register_callback(self._on_clock_tick)
            logger.info("AudioMixer subscribed to MasterClock (clock-driven decoding)")
    
    def add_sink(self, sink) -> None:
        """
        Add an audio sink to the mixer.
        
        Args:
            sink: SinkBase instance to add
        """
        from outputs.sink_base import SinkBase
        
        if isinstance(sink, SinkBase):
            # Check if it's an FMSink (primary sink)
            from outputs.fm_sink import FMSink
            if isinstance(sink, FMSink):
                self.fm_sink = sink
            self.sinks.append(sink)
            logger.info(f"Added sink: {type(sink).__name__}")
    
    def set_event_complete_callback(self, callback) -> None:
        """
        Set callback to be called when an event finishes (EOF).
        
        Args:
            callback: Function(event: AudioEvent) to call on completion
        """
        self._event_complete_callback = callback
    
    def start_event(self, event: AudioEvent) -> bool:
        """
        Start decoding an audio event.
        
        This sets up the decoder but does NOT start decoding.
        Decoding happens one frame per clock tick.
        
        Args:
            event: AudioEvent to start decoding
            
        Returns:
            True if started successfully, False otherwise
        """
        with self._decoder_lock:
            # Close any existing decoder
            if self.decoder.is_active():
                logger.warning("[Mixer] Starting new event while decoder is active - closing previous")
                self.decoder.close()
            
            # Reset ready flag - wait for first PCM frame from new event
            self.ready = False
            self._buffer_ready = False
            self._warming_ticks = 0
            self._last_data_time = None
            
            # Clear buffer for new event
            with self._buffer_lock:
                self._frame_buffer.clear()
            
            # Start decoder for new event
            if not self.decoder.start(event):
                return False
            
            logger.info(f"[Mixer] Started event: {event.path} ({event.type})")
            return True
    
    def _on_clock_tick(self, frame_index: int) -> None:
        """
        Called by MasterClock on each tick.
        
        This is where real-time decoding happens:
        1. Call decoder.next_frame() to get one frame
        2. If frame, add to small buffer and write to sinks
        3. If None (EOF), trigger event completion
        
        Args:
            frame_index: Current frame index from MasterClock
        """
        frame = None
        completed_event = None
        
        # Get next frame from decoder (clock-driven)
        with self._decoder_lock:
            if self.decoder.is_active():
                frame = self.decoder.next_frame()
                
                # None = actual EOF, empty bytes = data not ready yet
                if frame is None:
                    # Actual EOF - event completed
                    completed_event = self.decoder.get_current_event()
                    self.decoder.close()
                elif frame == b'':
                    # Data not ready yet - use buffer or silence
                    frame = None
        
        if completed_event:
            # Event completed - notify callback
            if self._event_complete_callback:
                try:
                    self._event_complete_callback(completed_event)
                except Exception as e:
                    logger.error(f"[Mixer] Event complete callback error: {e}")
        
        # Get frame from buffer or use decoded frame
        with self._buffer_lock:
            if frame:
                # Add to buffer (max 10 frames)
                if len(self._frame_buffer) >= 10:
                    # Buffer full - drop oldest
                    self._frame_buffer.popleft()
                self._frame_buffer.append(frame)
                self._last_data_time = time.monotonic()
                
                # First valid PCM frame received - mixer is now ready
                if not self.ready:
                    self.ready = True
                    logger.info("[Mixer] First PCM frame received - mixer ready")
                
                # Check if buffer has reached warm-start threshold
                if not self._buffer_ready and len(self._frame_buffer) >= self._min_buffer_frames:
                    self._buffer_ready = True
                    logger.info("[Mixer] Buffer warmed — playback live")
            
            # Check if we should deliver frames (only if buffer is warmed)
            buffer_size = len(self._frame_buffer)
            
            if self._buffer_ready:
                # Buffer is warmed - deliver frames normally
                if buffer_size > 0:
                    deliver_frame = self._frame_buffer.popleft()
                elif frame:
                    deliver_frame = frame
                else:
                    # No frame available - buffer underrun
                    if frame_index != self._last_underrun_log_index + 1:
                        logger.warning(f"[Mixer] Buffer underrun at frame_index={frame_index} (no frames available)")
                        self._last_underrun_log_index = frame_index
                    else:
                        self._last_underrun_log_index = frame_index
                    
                    # Generate silence frame only if we've had no data for >500ms
                    if self._last_data_time is None or (time.monotonic() - self._last_data_time) > 0.5:
                        deliver_frame = bytes(self.frame_size)
                    else:
                        # Still warming or recent data - don't output silence
                        deliver_frame = None
            else:
                # Buffer not warmed yet - don't deliver frames, just accumulate
                self._warming_ticks += 1
                deliver_frame = None
        
        # Deliver frame to all sinks (synchronized by clock)
        # Only write to sinks if mixer is ready AND buffer is warmed
        if deliver_frame and self.ready and self._buffer_ready:
            # Always write to FM sink first (critical path) - SYNCHRONOUS
            # FM sink gets exactly 1 frame per tick
            if self.fm_sink:
                try:
                    self.fm_sink.write_frame(deliver_frame)
                    logger.debug(f"[Mixer] → FM {len(deliver_frame)} bytes (tick {frame_index})")
                except Exception as e:
                    # FM sink failure is critical
                    if not sys.is_finalizing():
                        logger.critical(f"[Mixer] FM sink error: {e}")
                    raise
            
            # Write to other sinks (non-blocking, errors ignored)
            # All sinks get the same frame per tick to maintain synchronization
            for sink in self.sinks:
                if sink is not self.fm_sink:
                    try:
                        sink.write_frame(deliver_frame)
                        # Log YouTube sink writes for debugging
                        from outputs.youtube_sink import YouTubeSink
                        if isinstance(sink, YouTubeSink):
                            yt_buffer_size = sink.get_buffer_size()
                            logger.debug(
                                f"[Mixer] → YouTube {len(deliver_frame)} bytes (buffer={yt_buffer_size}) (tick {frame_index})"
                            )
                    except Exception as e:
                        # Non-FM sink failures are non-critical
                        if not sys.is_finalizing():
                            logger.warning(f"[Mixer] Sink {type(sink).__name__} error (non-critical): {e}")
    
    def is_playing(self) -> bool:
        """
        Check if currently playing an event.
        
        Returns True if:
        - Decoder is active (decoding in progress), OR
        - Buffer has frames (waiting to be consumed)
        
        Returns:
            True if playing, False otherwise
        """
        with self._decoder_lock:
            has_active_decoder = self.decoder.is_active()
        
        with self._buffer_lock:
            has_buffered_frames = len(self._frame_buffer) > 0
        
        return has_active_decoder or has_buffered_frames
    
    def get_buffer_size(self) -> int:
        """
        Get current buffer size (number of frames waiting to be consumed).
        
        Returns:
            Number of frames in buffer
        """
        with self._buffer_lock:
            return len(self._frame_buffer)
    
    def stop(self) -> None:
        """Stop the mixer and cleanup resources."""
        # Unregister from master clock
        if self._master_clock:
            self._master_clock.unregister_callback(self._on_clock_tick)
        
        # Close decoder
        with self._decoder_lock:
            self.decoder.close()
        
        # Clear buffer
        with self._buffer_lock:
            self._frame_buffer.clear()
