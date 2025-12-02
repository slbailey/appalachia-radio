"""
Audio mixer for frame-based audio processing.

This module provides the AudioMixer class, which processes PCM frames
and outputs them to registered audio sinks. Uses MasterClock for
synchronized, clock-driven frame delivery with real-time decoding.

===========================================================
AUDIO MIXER CONCURRENCY CONTRACT (MUST NOT BE BROKEN)
===========================================================

The AudioMixer runs entirely in the MasterClock thread and owns
all real-time audio behavior. It is the ONLY thread that:

    - Pulls PCM frames from decoders
    - Detects EOF events
    - Writes audio to sinks

THREADING & LOCKING RULES
-------------------------

1. Mixer internal locks:
       _decoder_lock
       _buffer_lock
       _inactive_buffer_lock

   These locks MUST NEVER be held while calling ANY callback.

2. Allowed operations while holding locks:
       - Switching decks
       - Starting FFmpeg
       - Closing decoders
       - Mutating buffers

3. Forbidden operations while holding locks:
       - Calling _event_complete_callback
       - Calling _song_started_callback
       - Calling _fm_failure_callback
       - Calling any function that may call back into Mixer

4. Callbacks MUST be invoked *after* releasing all locks.

CALLBACK CONTRACT
-----------------

Mixer emits the following callbacks:

    _event_complete_callback(event, deck)
    _song_started_callback(deck, event)
    _fm_failure_callback()

These callbacks run in the MasterClock thread after locks have been
released. They MUST execute quickly and never block or perform I/O.

SPECIAL RULES FOR start_event()
-------------------------------

start_event():
    - May acquire _decoder_lock to activate a deck.
    - MUST release _decoder_lock BEFORE firing _song_started_callback().
    - MUST NOT recursively re-enter any mixer method that requires locks.

THIS FILE MAY NOT BREAK THESE RULES.
If locks are held during callbacks, the system WILL deadlock
(especially since DJ logic calls preload_event()).
"""

import logging
import os
import sys
import threading
import time
from collections import deque
from typing import Optional, Literal
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
        master_clock = None,
        debug: bool = False
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
        self.debug = debug
        self.sinks = []
        self.fm_sink = None  # Primary sink
        
        # Two-turntable model: A and B decks
        self.turntable_a = AudioDecoder(sample_rate, channels, frame_size, debug=debug)
        self.turntable_b = AudioDecoder(sample_rate, channels, frame_size, debug=debug)
        self.active_deck: Literal["A", "B"] = "A"  # Which deck is currently feeding output
        self._decoder_lock = threading.Lock()
        
        # Legacy decoder reference for backward compatibility (points to active deck)
        # This allows existing code that references self.decoder to still work
        self.decoder = self.turntable_a
        
        # Small buffer (max 10 frames) for smoothing - feeds active deck output
        self._frame_buffer: deque[bytes] = deque(maxlen=10)
        self._buffer_lock = threading.Lock()
        
        # Pre-buffer for inactive deck (stores frames while pre-loading)
        # When we switch decks, these frames are available immediately
        self._inactive_deck_buffer: deque[bytes] = deque(maxlen=100)  # Larger buffer for pre-loading
        self._inactive_buffer_lock = threading.Lock()
        
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
        self._event_complete_callback_sig = None  # Cached signature
        # Song started callback (with deck info)
        self._song_started_callback = None
        
        # Register with master clock if provided
        if master_clock:
            master_clock.register_callback(self._on_clock_tick)
            logger.debug("[MIXER] Subscribed to MasterClock")
    
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
            logger.debug(f"[MIXER] Added sink: {type(sink).__name__}")
    
    def set_event_complete_callback(self, callback) -> None:
        """
        Set callback to be called when an event finishes (EOF).
        
        Args:
            callback: Function(event: AudioEvent, deck?: Literal["A","B"]) to call on completion
        """
        self._event_complete_callback = callback
        self._event_complete_callback_sig = None  # Reset cache when callback changes
    
    def set_song_started_callback(self, callback) -> None:
        """
        Set callback to be called when a song starts on a deck.
        
        Args:
            callback: Function(deck: Literal["A","B"], event: AudioEvent) to call on song start
        """
        self._song_started_callback = callback
    
    def switch_to(self, deck: Literal["A", "B"]) -> None:
        """
        Switch active deck (A or B).
        
        This is an instant binary switch - no mixing, no crossfade.
        The newly active deck immediately starts feeding PCM frames.
        
        Args:
            deck: "A" or "B" to switch to
        """
        if deck not in ("A", "B"):
            raise ValueError(f"Deck must be 'A' or 'B', got '{deck}'")
        
        with self._decoder_lock:
            old_deck = self.active_deck
            self.active_deck = deck
            
            # Update legacy decoder reference
            if deck == "A":
                self.decoder = self.turntable_a
            else:
                self.decoder = self.turntable_b
            
                logger.debug(f"[MIXER] Switch {old_deck} → {deck}")
    
    def _get_active_decoder(self) -> AudioDecoder:
        """Get the currently active decoder."""
        return self.turntable_a if self.active_deck == "A" else self.turntable_b
    
    def _get_inactive_decoder(self) -> AudioDecoder:
        """Get the currently inactive decoder."""
        return self.turntable_b if self.active_deck == "A" else self.turntable_a
    
    def preload_event(self, event: AudioEvent, deck: Literal["A", "B"]) -> bool:
        """
        Preload an event into the specified deck WITHOUT starting FFmpeg.
        
        Preloading is for preparation and decision-making that happens while
        the active deck is playing. This allows:
        - DJ decisions to be made (what to say, what song to play next)
        - Future: ChatGPT/ElevenLabs API calls to generate speech dynamically
        - Event queuing and preparation
        
        This method:
        - Stores the event reference (does NOT start FFmpeg)
        - Does NOT decode or buffer frames
        - Does NOT activate the deck
        - Does NOT start audio output
        - Does NOT switch decks
        
        FFmpeg only starts when start_event() is called (when deck becomes active).
        We don't "cross the start line" until the active song finishes.
        
        Args:
            event: AudioEvent to preload
            deck: Which deck to preload into ("A" or "B")
            
        Returns:
            True if preloaded successfully, False otherwise
        """
        with self._decoder_lock:
            if deck == "A":
                decoder = self.turntable_a
            else:
                decoder = self.turntable_b
            
            # Close decoder if it has an active process
            if decoder.is_active():
                decoder.close()
            
            # Clear the deck's pre-buffer
            with self._inactive_buffer_lock:
                self._inactive_deck_buffer.clear()
            
            # Set event (this just stores the reference, doesn't start FFmpeg)
            # This is the "get ready" phase - decision made, event queued, ready to go
            decoder.set_event(event)
            
            logger.debug(f"[MIXER] Preload deck {deck}: {os.path.basename(event.path)}")
            return True
    
    def start_event(self, event: AudioEvent, deck: Literal["A", "B"]) -> bool:
        """
        Activate a deck and start playback immediately.
        
        ALWAYS starts FFmpeg fresh for the given event, even if the decoder
        previously had a preloaded event assigned. Preloading only stores the
        event metadata; start_event() is the *only* place FFmpeg is launched.
        
        This method:
        - Switches to the specified deck (if not already active)
        - Always closes any existing decoder state
        - Always sets the event explicitly
        - Always starts FFmpeg fresh
        - Activates audio output (frames sent to sinks)
        - Makes this deck the active deck
        
        This is called:
        - At startup for Deck A
        - When Deck A finishes → start Deck B
        - When Deck B finishes → start Deck A
        
        Never during preload. Preload uses preload_event().
        
        Args:
            event: AudioEvent to start
            deck: Which deck to activate ("A" or "B")
            
        Returns:
            True if started successfully, False otherwise
        """
        with self._decoder_lock:
            # Inline deck switch to avoid nested _decoder_lock acquisition
            if self.active_deck != deck:
                old_deck = self.active_deck
                self.active_deck = deck
                
                # Update legacy decoder reference
                if deck == "A":
                    self.decoder = self.turntable_a
                else:
                    self.decoder = self.turntable_b
                
                logger.debug(f"[MIXER] Switch {old_deck} → {deck}")
            
            decoder = self._get_active_decoder()
            
            # Clear any stale decoder state (closes FFmpeg process if running)
            # Note: decoder.close() clears _current_event, but we set it again below
            decoder.close()
            
            # Set the event explicitly (preload may have set this, but we enforce it here)
            # This stores the event reference but does NOT start FFmpeg
            decoder.set_event(event)
            
            # Now start FFmpeg — this MUST succeed for audio to play
            if not decoder.start(event):
                logger.error(f"[MIXER] Failed to start deck {deck}: {os.path.basename(event.path)}")
                return False
            
            # Reset playback readiness flags
            self.ready = False
            self._buffer_ready = False
            self._warming_ticks = 0
            self._last_data_time = None
            
            # Clear output buffer
            with self._buffer_lock:
                self._frame_buffer.clear()
            
            # Clear inactive prebuffer (not used anymore)
            with self._inactive_buffer_lock:
                self._inactive_deck_buffer.clear()
            
            logger.debug(f"[MIXER] Start deck {deck}: {os.path.basename(event.path)}")
        
        # CRITICAL: Release lock before calling callbacks to avoid deadlock
        # Callbacks may call preload_event() which also needs the decoder lock
        # Notify listeners
        if event.type == "song" and hasattr(self, '_song_started_callback') and self._song_started_callback:
            logger.debug(f"[MIXER] Invoking song started callback for deck {deck}")
            try:
                self._song_started_callback(deck, event)
                logger.debug(f"[MIXER] Song started callback returned")
            except Exception as e:
                logger.error(f"[MIXER] Song started callback error: {e}", exc_info=True)
        
        return True
    
    def _on_clock_tick(self, frame_index: int) -> None:
        """
        Called by MasterClock on each tick.
        
        Two-turntable model: Only reads from active deck.
        When active deck finishes, PlayoutEngine switches to inactive deck (if pre-loaded).
        
        This is where real-time decoding happens:
        1. Call active_decoder.next_frame() to get one frame
        2. If frame, add to small buffer and write to sinks
        3. If None (EOF), trigger event completion callback
        
        Note: Inactive decoder does NOT decode during preload. Preload only stores
        event metadata. FFmpeg only starts when start_event() is called.
        
        Args:
            frame_index: Current frame index from MasterClock
        """
        frame = None
        completed_event = None
        completed_deck = None
        
        # Step 1: Detect EOF inside lock, store event/deck info, release lock
        with self._decoder_lock:
            active_decoder = self._get_active_decoder()
            inactive_decoder = self._get_inactive_decoder()
            
            # Check if active decoder has an event (either active or finished)
            if active_decoder.has_event():
                # Decoder has an event - try to get next frame
                # If decoder finished, next_frame() will return None and set _process = None
                frame = active_decoder.next_frame()
                
                # None = actual EOF, empty bytes = data not ready yet
                if frame is None:
                    # Check if decoder just finished (has event but process is None)
                    if not active_decoder.is_active() and active_decoder.has_event():
                        # Actual EOF - active deck finished
                        # Get event BEFORE closing (close() clears _current_event)
                        completed_event = active_decoder.get_current_event()
                        completed_deck = self.active_deck  # Store deck before switch
                        
                        # Only proceed if we have a valid event
                        if completed_event:
                            active_decoder.close()
                        else:
                            # No event - decoder was already closed or never had an event
                            logger.warning(f"[MIXER] Deck {self.active_deck} finished but no event found")
                            # Don't set completed_event - skip callback
                            completed_event = None
                            completed_deck = None
                        
                        # Active deck finished - mark for completion callback
                        # NOTE: We do NOT switch decks here - that's handled by PlayoutEngine
                        # calling start_event() after receiving the completion callback
                        # The inactive deck has event metadata pre-loaded but FFmpeg hasn't started yet
                        if inactive_decoder.has_event():
                            new_active = "B" if self.active_deck == "A" else "A"
                            logger.debug(f"[MIXER] Deck {completed_deck} finished, deck {new_active} preloaded")
                    # If decoder is still active but returned None, it's just not ready yet
                    # (shouldn't happen, but handle it)
                    elif active_decoder.is_active():
                        # Decoder is active but returned None - might be a transient issue
                        frame = None
                elif frame == b'':
                    # Data not ready yet - use buffer or silence
                    frame = None
            
            # NOTE: We do NOT read from inactive decoder here
            # Inactive decoder is preloaded (event metadata stored) but FFmpeg hasn't started yet
            # Preload is metadata-only - no decoding, no buffering, no FFmpeg process
            # FFmpeg only starts when start_event() is called (when deck becomes active)
        
        # Step 2: NOW outside all locks - invoke callback if event completed
        if completed_event:
            logger.debug(f"[MIXER] Invoking event complete callback for deck {completed_deck}")
            logger.info(f"[MIXER] EOF deck {completed_deck}: {os.path.basename(completed_event.path)}")
            
            if self._event_complete_callback:
                try:
                    # Try to call with deck info if callback accepts it
                    # Cache signature to avoid reflection overhead on every EOF
                    if self._event_complete_callback_sig is None:
                        import inspect
                        sig = inspect.signature(self._event_complete_callback)
                        self._event_complete_callback_sig = sig
                    else:
                        sig = self._event_complete_callback_sig
                    if len(sig.parameters) >= 2 and completed_deck is not None:
                        self._event_complete_callback(completed_event, completed_deck)
                    else:
                        self._event_complete_callback(completed_event)
                    logger.debug(f"[MIXER] Event complete callback returned")
                except Exception as e:
                    logger.error(f"[MIXER] Event complete callback error: {e}", exc_info=True)
            else:
                logger.warning(f"[MIXER] No completion callback registered")
        elif completed_deck is not None:
            # We detected EOF but couldn't get the event - this shouldn't happen
            logger.warning(f"[MIXER] Deck {completed_deck} finished but no event available")
        
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
                    logger.debug("[MIXER] First PCM frame received")
                
                # Check if buffer has reached warm-start threshold
                if not self._buffer_ready and len(self._frame_buffer) >= self._min_buffer_frames:
                    self._buffer_ready = True
                    logger.debug("[MIXER] Buffer warmed")
            
            # Check if we should deliver frames (only if buffer is warmed)
            buffer_size = len(self._frame_buffer)
            
            if self._buffer_ready:
                # Buffer is warmed - deliver frames normally
                if buffer_size > 0:
                    deliver_frame = self._frame_buffer.popleft()
                elif frame:
                    deliver_frame = frame
                else:
                    # No frame available – treat as underrun once buffer is warm
                    if frame_index != self._last_underrun_log_index + 1:
                        logger.warning(f"[MIXER] Buffer underrun (frame {frame_index})")
                    self._last_underrun_log_index = frame_index
                    
                    # Generate silence frame only if we've had no data for >500ms
                    if self._last_data_time is None or (time.monotonic() - self._last_data_time) > 0.5:
                        deliver_frame = bytes(self.frame_size)
                    else:
                        deliver_frame = None
            else:
                # Buffer not warmed yet - don't deliver frames, just accumulate
                self._warming_ticks += 1
                deliver_frame = None
        
        # Deliver frame to all sinks (synchronized by clock)
        # Only write to sinks if mixer is ready AND buffer is warmed
        if deliver_frame and self.ready and self._buffer_ready:
            # Phase 4: FM is primary (critical path) - write first, errors are fatal
            if self.fm_sink:
                try:
                    self.fm_sink.write_frame(deliver_frame)
                    if self.debug:
                        logger.debug(f"[MIXER] → FM {len(deliver_frame)} bytes")
                except Exception as e:
                    # FM sink failure is CRITICAL - propagate error (fatal)
                    logger.critical(f"[MIXER] FM sink error (CRITICAL): {e}", exc_info=True)
                    # Phase 5: Notify playout engine of FM failure (if callback exists)
                    if hasattr(self, '_fm_failure_callback') and self._fm_failure_callback:
                        logger.debug("[MIXER] Invoking FM failure callback")
                        try:
                            self._fm_failure_callback()
                            logger.debug("[MIXER] FM failure callback returned")
                        except Exception:
                            pass  # Don't let callback errors mask FM failure
                    # Re-raise to abort process or allow upper layers to handle
                    raise RuntimeError(f"FM sink write failed: {e}") from e
            
            # Phase 4: Other sinks (YouTube, etc.) are secondary - non-fatal
            # Write to all other sinks after FM (non-blocking, errors are warnings)
            for sink in self.sinks:
                if sink is not self.fm_sink:
                    try:
                        sink.write_frame(deliver_frame)
                        # Log YouTube sink writes for debugging (only if debug enabled)
                        if self.debug:
                            from outputs.youtube_sink import YouTubeSink
                            if isinstance(sink, YouTubeSink):
                                logger.debug(
                                    f"[MIXER] → YouTube {len(deliver_frame)} bytes (tick {frame_index})"
                                )
                    except Exception as e:
                        # Non-FM sink failures are non-critical (WARNING level)
                        logger.warning(f"[MIXER] Sink {type(sink).__name__} error: {e}")
                        # Do not raise - continue processing, FM keeps playing
    
    def is_playing(self) -> bool:
        """
        Check if currently playing an event.
        
        Returns True if:
        - Active decoder is active (decoding in progress), OR
        - Buffer has frames (waiting to be consumed)
        
        Returns:
            True if playing, False otherwise
        """
        with self._decoder_lock:
            active_decoder = self._get_active_decoder()
            has_active_decoder = active_decoder.is_active()
        
        with self._buffer_lock:
            has_buffered_frames = len(self._frame_buffer) > 0
        
        is_playing_result = has_active_decoder or has_buffered_frames
        
        if self.debug and not is_playing_result and has_active_decoder is False:
            # Log when we transition from playing to not playing
            logger.debug(f"[MIXER] is_playing() = False")
        
        return is_playing_result
    
    def is_inactive_preloaded(self) -> bool:
        """
        Check if inactive deck has a pre-loaded event ready.
        
        Returns:
            True if inactive decoder has an event set (pre-loaded), False otherwise
        """
        with self._decoder_lock:
            inactive_decoder = self._get_inactive_decoder()
            return inactive_decoder.has_event()
    
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
        
        # Close both decoders
        with self._decoder_lock:
            self.turntable_a.close()
            self.turntable_b.close()
        
        # Clear buffers
        with self._buffer_lock:
            self._frame_buffer.clear()
        with self._inactive_buffer_lock:
            self._inactive_deck_buffer.clear()
