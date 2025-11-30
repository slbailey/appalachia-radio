"""
Broadcast core module.

Provides PlayoutEngine, EventQueue, and state management for audio playback.
"""

from broadcast_core.event_queue import AudioEvent, EventQueue
from broadcast_core.state_machine import PlaybackState, PlaybackContext, StateMachine
from broadcast_core.playout_engine import PlayoutEngine

__all__ = [
    "AudioEvent",
    "EventQueue",
    "PlaybackState",
    "PlaybackContext",
    "StateMachine",
    "PlayoutEngine",
]
