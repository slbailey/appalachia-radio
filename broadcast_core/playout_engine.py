"""
Playout engine for managing audio event playback.

This module provides the PlayoutEngine class, which manages the queue of audio events
and coordinates with the mixer for clock-driven, real-time frame delivery.
"""

import logging
import threading
import queue
from typing import Optional
from broadcast_core.event_queue import AudioEvent, EventQueue
from broadcast_core.state_machine import PlaybackState, StateMachine

logger = logging.getLogger(__name__)


class PlayoutEngine:
    """
    Non-blocking playout scheduler for audio events.
    
    Manages queue of audio events and sets up decoders in the mixer.
    Actual decoding happens one frame per clock tick in the mixer.
    """
    
    def __init__(self, mixer, stop_event: Optional[threading.Event] = None, debug: bool = False) -> None:
        """
        Initialize the playout engine.
        
        Args:
            mixer: AudioMixer instance for audio processing
            stop_event: Optional threading.Event for graceful shutdown
            debug: Enable debug logging
        """
        self.mixer = mixer
        self.event_queue = EventQueue()
        self.state_machine = StateMachine()
        self._running = False
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        self.debug = debug
        self._event_complete_callbacks: list = []  # List of callbacks for event completion
        
        # Set up event completion callback
        self.mixer.set_event_complete_callback(self._on_event_complete)
    
    def add_event_complete_callback(self, callback) -> None:
        """
        Add a callback to be called when an event completes.
        
        Args:
            callback: Function that takes (event: AudioEvent) as argument
        """
        self._event_complete_callbacks.append(callback)
    
    def queue_event(self, event: AudioEvent) -> None:
        """
        Add an audio event to the playout queue.
        
        Args:
            event: AudioEvent to add to queue
        """
        self.event_queue.put(event)
        if self.debug:
            logger.debug(f"Queued event: {event.path} ({event.type})")
    
    def current_state(self) -> PlaybackState:
        """
        Get current playback state.
        
        Returns:
            Current PlaybackState
        """
        return self.state_machine.get_state()
    
    def is_idle(self) -> bool:
        """
        Check if engine is idle (no events playing).
        
        Returns:
            True if idle (no events in queue and not currently playing), False otherwise
        """
        # Engine is idle if:
        # 1. State machine is IDLE
        # 2. Event queue is empty
        # 3. Mixer is not playing anything (no active decoder AND no buffered frames)
        is_state_idle = self.state_machine.get_state() == PlaybackState.IDLE
        is_queue_empty = self.event_queue.empty()
        is_mixer_idle = not self.mixer.is_playing() if self.mixer else True
        
        return is_state_idle and is_queue_empty and is_mixer_idle
    
    def run(self) -> None:
        """
        Main loop that processes events from queue.
        
        This method sets up decoders for events. Actual decoding happens
        one frame per clock tick in the mixer.
        """
        if not self._running:
            self._running = True
            if self.debug:
                logger.info("PlayoutEngine started (clock-driven decoding)")
        
        # Main loop - runs until stop_event is set
        while self._running and not self._stop_event.is_set():
            # Process events from queue
            try:
                # Get next event (non-blocking)
                event = self.event_queue.get(block=False)
                
                # Update state based on event type
                if event.type == "intro":
                    self.state_machine.transition_to(PlaybackState.PLAYING_INTRO)
                elif event.type == "song":
                    self.state_machine.transition_to(PlaybackState.PLAYING_SONG)
                elif event.type == "outro":
                    self.state_machine.transition_to(PlaybackState.PLAYING_OUTRO)
                elif event.type == "talk":
                    self.state_machine.transition_to(PlaybackState.PLAYING_INTRO)  # Treat talk like intro
                
                self.state_machine.set_current_event(event)
                
                # Log "Now playing" when event starts (always visible)
                logger.info(f"[ENGINE] Now playing {event.path} ({event.type})")
                
                # Start decoder for this event (decoding happens per clock tick)
                if not self.mixer.start_event(event):
                    logger.error(f"[ENGINE] Failed to start decoder for {event.path}")
                    self.event_queue.task_done()
                    # Transition to error state
                    self.state_machine.transition_to(PlaybackState.ERROR)
                    continue
                
                # Event is now being decoded by clock ticks
                # Completion will be handled by _on_event_complete callback
                
            except queue.Empty:
                # Queue empty - check if we should transition to IDLE
                if not self.mixer.is_playing():
                    if self.state_machine.get_state() != PlaybackState.IDLE:
                        self.state_machine.transition_to(PlaybackState.IDLE)
                        self.state_machine.set_current_event(None)
                pass
            except Exception as e:
                # Log other errors for debugging
                logger.error(f"Error processing event: {e}", exc_info=True)
                import traceback
                traceback.print_exc()
            
            # Small sleep to prevent busy-waiting when queue is empty
            import time
            time.sleep(0.01)  # 10ms sleep
        
        # Loop exited - mark as stopped
        self._running = False
        if self.debug:
            logger.info("PlayoutEngine stopped")
    
    def _on_event_complete(self, event: AudioEvent) -> None:
        """
        Callback when an event finishes (EOF from decoder).
        
        This is called by the mixer when decoder.next_frame() returns None.
        
        Args:
            event: Completed AudioEvent
        """
        logger.info(f"[ENGINE] Completed {event.path}")
        self.event_queue.task_done()
        
        # Call registered callbacks
        for callback in self._event_complete_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error in event complete callback: {e}", exc_info=True)
        
        # Update state to IDLE only if:
        # 1. Queue is empty (no more events)
        # 2. Mixer is idle (no active decoder AND no buffered frames)
        if self.event_queue.empty() and not self.mixer.is_playing():
            self.state_machine.transition_to(PlaybackState.IDLE)
            self.state_machine.set_current_event(None)
        else:
            # Still playing (buffer has frames) - state will transition when buffer empties
            if self.debug:
                logger.debug(f"[ENGINE] Event completed, but buffer still has {self.mixer.get_buffer_size()} frames")
    
    def stop(self) -> None:
        """
        Stop the playout engine.
        
        Sets the stop event and marks engine as not running.
        """
        self._stop_event.set()
        self._running = False
        if self.debug:
            logger.info("PlayoutEngine stopped")
