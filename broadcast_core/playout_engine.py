"""
Playout engine for managing audio event playback.

This module provides the PlayoutEngine class, which manages the queue of audio events
and coordinates with the mixer for frame-by-frame audio delivery.
"""

import logging
import threading
from typing import Optional
from broadcast_core.event_queue import AudioEvent, EventQueue
from broadcast_core.state_machine import PlaybackState, StateMachine

logger = logging.getLogger(__name__)


class PlayoutEngine:
    """
    Non-blocking playout scheduler for audio events.
    
    Manages queue of audio events (intro → song → outro) and coordinates
    with mixer for frame-by-frame audio delivery. This is a scheduler, not
    a player - it manages what should play when.
    """
    
    def __init__(self, mixer, stop_event: Optional[threading.Event] = None) -> None:
        """
        Initialize the playout engine.
        
        Args:
            mixer: AudioMixer instance for audio processing
            stop_event: Optional threading.Event for graceful shutdown
        """
        self.mixer = mixer
        self.event_queue = EventQueue()
        self.state_machine = StateMachine()
        self._running = False
        self._current_decoder = None
        self._stop_event = stop_event if stop_event is not None else threading.Event()
    
    def queue_event(self, event: AudioEvent) -> None:
        """
        Add an audio event to the playout queue.
        
        Args:
            event: AudioEvent to add to queue
        """
        self.event_queue.put(event)
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
            True if idle, False otherwise
        """
        return self.state_machine.get_state() == PlaybackState.IDLE
    
    def run(self) -> None:
        """
        Main blocking loop that processes events and ticks mixer.
        
        This method runs until stop_event is set. It processes events
        from the queue and coordinates with the mixer for audio delivery.
        """
        if not self._running:
            self._running = True
            logger.info("PlayoutEngine started")
        
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
                logger.info(f"[ENGINE] Now playing {event.path} ({event.type})")
                
                # Play the event through mixer
                self._play_event(event)
                
                # Process frames until event is complete
                # Keep calling tick() until the mixer finishes the event
                while self.mixer.is_playing() and not self._stop_event.is_set():
                    self.mixer.tick()
                    # Small sleep to prevent busy-waiting
                    import time
                    time.sleep(0.001)  # 1ms sleep between frames
                
                # Event completed
                logger.info(f"[ENGINE] Completed {event.path}")
                self.event_queue.task_done()
                
                # Update state to IDLE if no more events
                if self.event_queue.empty():
                    self.state_machine.transition_to(PlaybackState.IDLE)
                    self.state_machine.set_current_event(None)
                
            except Exception:
                # Queue empty or error - continue
                pass
            
            # Note: Mixer tick is now called within the event processing loop
            # to ensure events complete before moving to the next one
            # This prevents "generator already executing" errors
            
            # Small sleep to prevent busy-waiting when queue is empty
            import time
            time.sleep(0.01)  # 10ms sleep
        
        # Loop exited - mark as stopped
        self._running = False
        logger.info("PlayoutEngine stopped")
    
    def _play_event(self, event: AudioEvent) -> None:
        """
        Play an audio event through the mixer.
        
        Args:
            event: AudioEvent to play
        """
        if not self.mixer:
            logger.error("No mixer available")
            return
        
        try:
            # Use mixer to decode and play the event
            self.mixer.play_event(event)
        except Exception as e:
            logger.error(f"Error playing event {event.path}: {e}")
            raise
    
    def stop(self) -> None:
        """
        Stop the playout engine.
        
        Sets the stop event and marks engine as not running.
        """
        self._stop_event.set()
        self._running = False
        logger.info("PlayoutEngine stopped")

