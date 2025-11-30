"""
Audio mixer for frame-based audio processing.

This module provides the AudioMixer class, which processes PCM frames
and outputs them to registered audio sinks.
"""

import logging
import sys
import threading
from typing import Optional
from broadcast_core.event_queue import AudioEvent
from mixer.audio_decoder import AudioDecoder

logger = logging.getLogger(__name__)


class AudioMixer:
    """
    Frame-based audio mixer.
    
    Processes PCM frames and outputs them to registered audio sinks.
    Handles decoding, mixing, and frame delivery.
    """
    
    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        frame_size: int = 4096
    ) -> None:
        """
        Initialize the audio mixer.
        
        Args:
            sample_rate: Audio sample rate in Hz (default: 48000)
            channels: Number of audio channels (default: 2 = stereo)
            frame_size: Frame size in bytes (default: 4096)
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_size = frame_size
        self.sinks = []
        self.fm_sink = None  # Primary sink
        self.decoder = AudioDecoder(sample_rate, channels, frame_size)
        self._current_event: Optional[AudioEvent] = None
        self._current_frame_generator = None
        self._generator_lock = threading.Lock()  # Protect generator access
    
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
    
    def play_event(self, event: AudioEvent) -> None:
        """
        Start playing an audio event.
        
        Args:
            event: AudioEvent to play
        """
        with self._generator_lock:
            # If there's already an event playing, wait for it to finish
            # This prevents "generator already executing" errors
            if self._current_frame_generator is not None:
                logger.warning("Attempted to play new event while one is already playing. Waiting for current event to finish...")
                # Consume remaining frames from current generator
                try:
                    while True:
                        next(self._current_frame_generator)
                except StopIteration:
                    pass
                self._current_event = None
                self._current_frame_generator = None
            
            self._current_event = event
            try:
                self._current_frame_generator = self.decoder.stream_frames(event)
            except Exception as e:
                logger.error(f"Error starting playback of {event.path}: {e}")
                self._current_event = None
                self._current_frame_generator = None
                raise
    
    def tick(self) -> None:
        """
        Process one frame from current event and output to sinks.
        
        This should be called repeatedly in a loop to process frames.
        """
        with self._generator_lock:
            if not self._current_frame_generator:
                return
            
            try:
                # Get next frame from decoder
                frame = next(self._current_frame_generator)
                
                # Apply gain if needed
                if self._current_event and self._current_event.gain != 1.0:
                    # Simple gain application (would need proper PCM manipulation in production)
                    pass  # TODO: Apply gain to frame
            
            except StopIteration:
                # Event finished
                self._current_event = None
                self._current_frame_generator = None
                return
            except Exception as e:
                logger.error(f"Error processing frame: {e}")
                self._current_event = None
                self._current_frame_generator = None
                return
        
        # Output frame outside the lock to avoid blocking
        # (frame was captured before releasing the lock)
        if frame:
            self.push_frame(frame)
    
    def push_frame(self, pcm_frame: bytes) -> None:
        """
        Process and output a single PCM frame chunk to all sinks.
        
        Args:
            pcm_frame: Raw PCM frame bytes (typically 4096-8192 bytes)
        """
        # Always write to FM sink first (critical path)
        if self.fm_sink:
            try:
                self.fm_sink.write_frame(pcm_frame)
                logger.debug(f"[Mixer] → FM {len(pcm_frame)} bytes")
            except Exception as e:
                # FM sink failure is critical
                if not sys.is_finalizing():
                    logger.critical(f"FM sink error: {e}")
                raise
        
        # Write to other sinks (non-blocking)
        for sink in self.sinks:
            if sink is not self.fm_sink:
                try:
                    sink.write_frame(pcm_frame)
                except Exception as e:
                    # Non-FM sink failures are non-critical
                    if not sys.is_finalizing():
                        logger.warning(f"Sink {type(sink).__name__} error: {e}")
                    # Continue to other sinks
    
    def is_playing(self) -> bool:
        """
        Check if currently playing an event.
        
        Returns:
            True if playing, False otherwise
        """
        return self._current_event is not None and self._current_frame_generator is not None
    
    def stop(self) -> None:
        """Stop the mixer and cleanup resources."""
        self._current_event = None
        self._current_frame_generator = None
        self.decoder.close()

