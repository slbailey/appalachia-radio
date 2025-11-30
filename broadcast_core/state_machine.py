"""
State machine for playback state management.

This module provides the PlaybackState enum, PlaybackContext dataclass,
and helper functions for managing playback state.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class PlaybackState(Enum):
    """Playback state enumeration."""
    IDLE = "idle"
    PLAYING_INTRO = "playing_intro"
    PLAYING_SONG = "playing_song"
    PLAYING_OUTRO = "playing_outro"
    TRANSITIONING = "transitioning"
    ERROR = "error"


@dataclass
class PlaybackContext:
    """
    Context tracking current playback state and events.
    
    Attributes:
        current_event: Currently playing AudioEvent (if any)
        last_event: Last completed AudioEvent (if any)
        state: Current PlaybackState
    """
    current_event: Optional[object] = None  # AudioEvent (forward reference handled via type: ignore)
    last_event: Optional[object] = None
    state: PlaybackState = PlaybackState.IDLE
    
    def is_playing(self) -> bool:
        """
        Check if currently playing audio.
        
        Returns:
            True if playing (not IDLE or ERROR), False otherwise
        """
        return self.state not in (PlaybackState.IDLE, PlaybackState.ERROR)
    
    def is_idle(self) -> bool:
        """
        Check if idle (no playback).
        
        Returns:
            True if IDLE, False otherwise
        """
        return self.state == PlaybackState.IDLE


class StateMachine:
    """
    Manages playback state transitions.
    
    Ensures valid state sequences and handles error states.
    Provides state change callbacks for monitoring.
    """
    
    def __init__(self) -> None:
        """Initialize the state machine."""
        self._context = PlaybackContext()
        self._state_change_callbacks: list[Callable[[PlaybackState, PlaybackState], None]] = []
    
    def get_state(self) -> PlaybackState:
        """
        Get current playback state.
        
        Returns:
            Current PlaybackState
        """
        return self._context.state
    
    def get_context(self) -> PlaybackContext:
        """
        Get full playback context.
        
        Returns:
            Current PlaybackContext
        """
        return self._context
    
    def transition_to(self, new_state: PlaybackState) -> bool:
        """
        Transition to a new state if valid.
        
        Args:
            new_state: Target state
            
        Returns:
            True if transition was valid and executed, False otherwise
        """
        old_state = self._context.state
        
        # Allow any transition (simple model for Phase 4)
        # Phase 5+ can add validation rules if needed
        self._context.state = new_state
        self._notify_state_change(old_state, new_state)
        logger.debug(f"State transition: {old_state.value} → {new_state.value}")
        return True
    
    def can_transition_to(self, new_state: PlaybackState) -> bool:
        """
        Check if transition to new state is valid.
        
        For Phase 4, all transitions are allowed. Phase 5+ can add rules.
        
        Args:
            new_state: Target state
            
        Returns:
            True if transition is valid, False otherwise
        """
        # Phase 4: Allow all transitions
        # Phase 5+: Can add validation rules here
        return True
    
    def set_current_event(self, event: Optional[object]) -> None:
        """
        Set the current event being played.
        
        Args:
            event: AudioEvent or None
        """
        if self._context.current_event:
            self._context.last_event = self._context.current_event
        self._context.current_event = event
    
    def add_state_change_callback(
        self, callback: Callable[[PlaybackState, PlaybackState], None]
    ) -> None:
        """
        Add a callback for state changes.
        
        Args:
            callback: Function(old_state, new_state) called on state change
        """
        self._state_change_callbacks.append(callback)
    
    def _notify_state_change(self, old_state: PlaybackState, new_state: PlaybackState) -> None:
        """
        Notify all callbacks of state change.
        
        Args:
            old_state: Previous state
            new_state: New state
        """
        for callback in self._state_change_callbacks:
            try:
                callback(old_state, new_state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")


# Helper functions
def is_playing(context: PlaybackContext) -> bool:
    """
    Check if currently playing audio.
    
    Args:
        context: PlaybackContext to check
        
    Returns:
        True if playing (not IDLE or ERROR), False otherwise
    """
    return context.is_playing()


def is_idle(context: PlaybackContext) -> bool:
    """
    Check if idle (no playback).
    
    Args:
        context: PlaybackContext to check
        
    Returns:
        True if IDLE, False otherwise
    """
    return context.is_idle()

