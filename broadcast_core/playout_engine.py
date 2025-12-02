"""
Playout engine for managing audio event playback.

This module provides the PlayoutEngine class, which manages the queue of audio events
and coordinates with the mixer for clock-driven, real-time frame delivery.

===========================================================
PLAYOUT ENGINE CONCURRENCY CONTRACT (MUST NOT BE BROKEN)
===========================================================

The PlayoutEngine runs in its own thread and coordinates the
system-wide event flow. It does NOT decode audio and MUST NEVER
block or interfere with the real-time audio thread.

ROLE SUMMARY
------------

PlayoutEngine:
    - Handles station startup sequencing.
    - Receives event_complete and song_started callbacks from Mixer.
    - Chooses when to call mixer.start_event() (deck activation).
    - Calls DJEngine ONLY when safe (never under Mixer locks).
    - Manages playlog and state machine.

CALLBACK CONTRACT
-----------------

Mixer -> PlayoutEngine:
    _event_complete_callback(event, deck)
    _song_started_callback(deck, event)

These are always delivered AFTER Mixer releases its locks.
PlayoutEngine MUST NOT call Mixer functions that reacquire locks
from inside ANY callback that Mixer fired under its own lock.

ALLOWED:
    - In _on_event_complete(): call mixer.start_event()
    - In song_started handler: call DJEngine.on_song_started()
    - Queue events
    - Playlog writes
    - State machine transitions

FORBIDDEN:
    - Blocking calls (network, disk, sleeps)
    - Holding PlayoutEngine locks and then calling into Mixer
    - Re-entering Mixer methods in a way that could cause lock recursion

EVENT MODEL (PHASE 7+)
----------------------

station_started
    → DJEngine.on_station_started()
    → preload(A)
    → start(A)
    → song_started(A)

song_started(deck)
    → DJEngine.on_song_started()
    → preload(opposite deck)

song_finished(deck)
    → playout switches to opposite deck
    → start(opposite deck)
    → song_started(opposite deck)

This file MUST obey this flow and MUST NOT introduce blocking or
callback recursion. Violations will cause the radio to freeze.
"""


import logging
import os
import threading
import queue
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional, List
from broadcast_core.event_queue import AudioEvent, EventQueue
from broadcast_core.state_machine import PlaybackState, StateMachine
from broadcast_core.playlog import Playlog, PlaylogEntry

logger = logging.getLogger(__name__)


@dataclass
class PlaylistItem:
    """
    Represents a playlist entry with optional preroll and postroll.
    
    When DJ selects a playlist item, it contains:
    - preroll: Optional intro audio event (plays before main)
    - main: Required main audio event (song) - only this triggers DJ callbacks
    - postroll: Optional outro audio event (plays after main)
    
    Attributes:
        preroll: Optional intro event (type="intro")
        main: Required song event (type="song")
        postroll: Optional outro event (type="outro")
    """
    preroll: Optional[AudioEvent] = None
    main: AudioEvent = None
    postroll: Optional[AudioEvent] = None


@dataclass
class NowPlayingInfo:
    """
    Phase 5: Information about the currently playing event.
    
    Attributes:
        path: File path to the audio file
        type: Event type (song, intro, outro, talk)
        deck: Which turntable deck is playing (A or B)
        started_at: When playback started
    """
    path: str
    type: Literal["song", "intro", "outro", "talk"]
    deck: Literal["A", "B"]
    started_at: datetime


@dataclass
class NextUpInfo:
    """
    Phase 5: Information about the next queued event.
    
    Attributes:
        path: File path to the audio file
        type: Event type (song, intro, outro, talk)
    """
    path: str
    type: Literal["song", "intro", "outro", "talk"]


@dataclass
class EngineHealth:
    """
    Phase 5: Health snapshot of playout engine and sinks.
    
    Attributes:
        state: Current playback state (e.g., "IDLE", "PLAYING_SONG", "ERROR")
        queue_length: Number of events in queue
        restart_requested: Whether restart has been requested
        fm_ok: Whether FM sink is operational
        youtube_connected: Whether YouTube sink is connected
        active_deck: Currently active deck (A, B, or None)
    """
    state: str
    queue_length: int
    restart_requested: bool
    fm_ok: bool
    youtube_connected: bool
    active_deck: Optional[Literal["A", "B"]]


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
        self._event_start_callbacks: list = []  # List of callbacks for event start
        self._station_start_callbacks: list = []  # List of callbacks for station startup
        self._song_started_callbacks: list = []  # List of callbacks for song started (with deck info)
        self._song_finished_callbacks: list = []  # List of callbacks for song finished (with deck info)
        self._station_started = False  # Track if station start event has been fired
        
        # Phase 4: Restart request flag (thread-safe)
        self._restart_requested = False
        self._restart_lock = threading.Lock()
        
        # Phase 5: Playlog for tracking event history
        self.playlog = Playlog(max_entries=500)
        self._current_entry: Optional[PlaylogEntry] = None
        self._playlog_lock = threading.Lock()
        
        # Phase 5: FM failure tracking
        self._fm_failed = False
        self._fm_lock = threading.Lock()
        
        # FIX B: Store next events for each deck (source of truth for deck switching)
        # These are set when DJ returns events and cleared after start_event() succeeds
        self._next_event_A: Optional[AudioEvent] = None
        self._next_event_B: Optional[AudioEvent] = None
        
        # Track current playlist item sequence for each deck
        # When a playlist item is expanded, we track which part is currently playing
        # Sequence: preroll → main → postroll → next item
        self._current_sequence_A: Optional[List[AudioEvent]] = None  # [preroll?, main, postroll?]
        self._current_sequence_index_A: int = 0  # Index into sequence (0=preroll/main, 1=postroll)
        self._current_sequence_B: Optional[List[AudioEvent]] = None
        self._current_sequence_index_B: int = 0
        
        # Set up event completion callback
        self.mixer.set_event_complete_callback(self._on_event_complete)
        
        # Set up song started callback (mixer fires when decoder becomes active)
        def _on_mixer_song_started(deck: Literal["A", "B"], event: AudioEvent) -> None:
            """
            Forward mixer's song started event to DJ and other callbacks.
            
            IMPORTANT: Only "main" events (type="song") trigger DJ sequencing.
            Preroll and postroll events do NOT trigger DJ callbacks or cadence.
            
            This is the canonical song_started event that triggers:
            1. DJ decision for opposite deck (ONLY for main events)
            2. Preload of opposite deck with expanded playlist item
            """
            # Only process main events (type="song") - preroll/postroll are handled internally
            if event.type == "song":
                # DJ decides events for opposite deck
                if self._dj_engine:
                    try:
                        opposite_deck = "B" if deck == "A" else "A"
                        events = self._dj_engine.on_song_started(deck, event)
                        if events:
                            # Expand playlist item into preroll/main/postroll sequence
                            sequence = self._expand_playlist_item(events)
                            if not sequence:
                                logger.error("[PLAYOUT] Failed to expand playlist item")
                                return
                            
                            # Store sequence for opposite deck
                            if opposite_deck == "A":
                                self._current_sequence_A = sequence
                                self._current_sequence_index_A = 0
                            else:
                                self._current_sequence_B = sequence
                                self._current_sequence_index_B = 0
                            
                            # Preload first event in sequence (preroll or main)
                            first_event = sequence[0]
                            
                            # FIX B: Store next event for opposite deck (source of truth)
                            if opposite_deck == "A":
                                self._next_event_A = first_event
                            else:
                                self._next_event_B = first_event
                            
                            if self.mixer.preload_event(first_event, opposite_deck):
                                logger.info(f"[PLAYOUT] Queued for deck {opposite_deck}: {os.path.basename(first_event.path)}")
                            else:
                                logger.error(f"[PLAYOUT] Failed to preload deck {opposite_deck}: {os.path.basename(first_event.path)}")
                                # Clear stored event on failure
                                if opposite_deck == "A":
                                    self._next_event_A = None
                                    self._current_sequence_A = None
                                else:
                                    self._next_event_B = None
                                    self._current_sequence_B = None
                    except Exception as e:
                        logger.error(f"[PLAYOUT] DJ decision error: {e}", exc_info=True)
                else:
                    logger.warning(f"[PLAYOUT] DJ engine not available")
            
            # Fire song started callbacks (for other listeners) - ONLY for main events
            if event.type == "song":
                for callback in self._song_started_callbacks:
                    try:
                        callback(deck, event)
                    except Exception as e:
                        logger.error(f"[PLAYOUT] Song started callback error: {e}", exc_info=True)
        
        self.mixer.set_song_started_callback(_on_mixer_song_started)
        
        # Store DJ engine reference for event-driven decisions
        self._dj_engine = None
        
        # Phase 5: Set up FM failure callback
        self.mixer._fm_failure_callback = self._on_fm_failure
    
    def add_event_complete_callback(self, callback) -> None:
        """
        Add a callback to be called when an event completes.
        
        Args:
            callback: Function that takes (event: AudioEvent) as argument
        """
        self._event_complete_callbacks.append(callback)
    
    def add_event_start_callback(self, callback) -> None:
        """
        Add a callback to be called when an event starts playing.
        
        Args:
            callback: Function that takes (event: AudioEvent) as argument
        """
        self._event_start_callbacks.append(callback)
    
    def set_dj_engine(self, dj_engine) -> None:
        """
        Set the DJ engine for event-driven decision making.
        
        Args:
            dj_engine: DJEngine instance
        """
        self._dj_engine = dj_engine
    
    def add_station_start_callback(self, callback) -> None:
        """
        Add a callback to be called when station starts up.
        
        Args:
            callback: Function that takes no arguments
        """
        self._station_start_callbacks.append(callback)
    
    def add_song_started_callback(self, callback) -> None:
        """
        Add a callback to be called when a song starts on a deck.
        
        Args:
            callback: Function that takes (deck: Literal["A","B"], event: AudioEvent) as argument
        """
        self._song_started_callbacks.append(callback)
    
    def add_song_finished_callback(self, callback) -> None:
        """
        Add a callback to be called when a song finishes on a deck.
        
        Args:
            callback: Function that takes (deck: Literal["A","B"], event: AudioEvent) as argument
        """
        self._song_finished_callbacks.append(callback)
    
    def request_restart(self) -> None:
        """
        Phase 4: Request that the engine stop after the current event completes.
        
        Thread-safe. Restart will only happen when playout is idle (no events playing,
        queue empty, mixer idle). Never interrupts an active event.
        """
        with self._restart_lock:
            self._restart_requested = True
        logger.info("[PLAYOUT] Restart requested")
    
    def queue_event(self, event: AudioEvent) -> None:
        """
        Add an audio event to the playout queue.
        
        Args:
            event: AudioEvent to add to queue
        """
        self.event_queue.put(event)
        logger.debug(f"[PLAYOUT] Queue event: {os.path.basename(event.path)} ({event.type})")
    
    def queue_events(self, events: list[AudioEvent]) -> None:
        """
        Add multiple audio events to the playout queue.
        
        Events are queued in order and will play sequentially.
        
        Args:
            events: List of AudioEvent objects to add to queue
        """
        for event in events:
            self.queue_event(event)
    
    def _expand_playlist_item(self, events: List[AudioEvent]) -> List[AudioEvent]:
        """
        Expand a playlist item (list of events from DJ) into preroll/main/postroll sequence.
        
        DJ returns events in order: [intro?, song, outro?]
        We expand this into: [preroll?, main, postroll?]
        where:
        - preroll = first event if type="intro", else None
        - main = first event with type="song" (required)
        - postroll = first event with type="outro" after main, else None
        
        Args:
            events: List of AudioEvent from DJ (typically [intro?, song, outro?])
            
        Returns:
            List of AudioEvent in play order: [preroll?, main, postroll?]
        """
        preroll = None
        main = None
        postroll = None
        
        # Find main (required - must be type="song")
        for event in events:
            if event.type == "song":
                main = event
                break
        
        if main is None:
            logger.error("[PLAYOUT] No main song event found in playlist item")
            return []
        
        # Find preroll (optional - type="intro" before main)
        main_index = events.index(main)
        if main_index > 0:
            first_event = events[0]
            if first_event.type == "intro":
                preroll = first_event
        
        # Find postroll (optional - type="outro" after main)
        if main_index < len(events) - 1:
            remaining = events[main_index + 1:]
            for event in remaining:
                if event.type == "outro":
                    postroll = event
                    break
        
        # Build sequence: [preroll?, main, postroll?]
        sequence = []
        if preroll:
            sequence.append(preroll)
        sequence.append(main)
        if postroll:
            sequence.append(postroll)
        
        return sequence
    
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
        
        Event queue-driven playout: only starts a new event when no event is currently playing.
        When an event completes, immediately transitions to the next event in queue (hard switch).
        Actual decoding happens one frame per clock tick in the mixer.
        """
        if not self._running:
            self._running = True
            logger.debug("[PLAYOUT] Engine started")
            
            # Fire station start event on first iteration - event-driven flow
            if not self._station_started:
                self._station_started = True
                # Emit station_started event - DJ decides events for Deck A
                if self._dj_engine:
                    try:
                        events = self._dj_engine.on_station_started()
                        if events:
                            # Expand playlist item into preroll/main/postroll sequence
                            sequence = self._expand_playlist_item(events)
                            if not sequence:
                                logger.error("[PLAYOUT] Failed to expand playlist item for station start")
                            else:
                                # Store sequence for Deck A
                                self._current_sequence_A = sequence
                                self._current_sequence_index_A = 0
                                
                                # Get first event in sequence (preroll or main)
                                first_event = sequence[0]
                                
                                # FIX B: Store next event for Deck A (source of truth)
                                self._next_event_A = first_event
                                
                                # REQUIRED ORDER: preload → start → emit
                                # 1. Preload Deck A
                                if not self.mixer.preload_event(first_event, "A"):
                                    logger.error(f"[PLAYOUT] Failed to preload deck A: {os.path.basename(first_event.path)}")
                                    self._next_event_A = None  # Clear on failure
                                    self._current_sequence_A = None
                                else:
                                    # 2. Start Deck A (REQUIRED - must be called before emit)
                                    result = self.mixer.start_event(first_event, "A")
                                    if not result:
                                        logger.error(f"[PLAYOUT] Failed to start deck A: {os.path.basename(first_event.path)}")
                                        self._next_event_A = None  # Clear on failure
                                        self._current_sequence_A = None
                                        # Don't continue if start failed
                                    else:
                                        # FIX B: Clear stored event after successful start
                                        self._next_event_A = None
                                        # Update state
                                        if first_event.type == "intro":
                                            self.state_machine.transition_to(PlaybackState.PLAYING_INTRO)
                                        elif first_event.type == "song":
                                            self.state_machine.transition_to(PlaybackState.PLAYING_SONG)
                                        elif first_event.type == "outro":
                                            self.state_machine.transition_to(PlaybackState.PLAYING_OUTRO)
                                        
                                        self.state_machine.set_current_event(first_event)
                                        
                                        # Record in playlog
                                        with self._playlog_lock:
                                            self._current_entry = self.playlog.add_start(
                                                path=first_event.path,
                                                type=first_event.type,
                                                deck="A"
                                            )
                                        
                                        logger.info(f"[PLAYOUT] Start deck A: {os.path.basename(first_event.path)}")
                    
                                        # 3. Emit song_started event (after start_event)
                                        # Note: mixer.start_event() already fires song_started callback via _on_mixer_song_started
                                        # But we also call event start callbacks here for other listeners
                                        for callback in self._event_start_callbacks:
                                            try:
                                                callback(first_event)
                                            except Exception as e:
                                                logger.error(f"[PLAYOUT] Event start callback error: {e}", exc_info=True)
                                        
                                        # Only main events trigger song_started callbacks
                                        if first_event.type == "song":
                                            for callback in self._song_started_callbacks:
                                                try:
                                                    callback("A", first_event)
                                                except Exception as e:
                                                    logger.error(f"[PLAYOUT] Song started callback error: {e}", exc_info=True)
                        else:
                            logger.warning("[PLAYOUT] DJ returned no events for station start")
                    except Exception as e:
                        logger.error(f"[PLAYOUT] Station start error: {e}", exc_info=True)
                
                # Also fire legacy callbacks (for backward compatibility)
                for callback in self._station_start_callbacks:
                    try:
                        callback()
                    except Exception as e:
                        logger.error(f"[PLAYOUT] Station start callback error: {e}", exc_info=True)
        
        # Main loop - fully event-driven, just waits for shutdown/restart
        # All playback control happens via callbacks:
        # - station_started → DJ decides → preload(A) → start(A) → song_started(A)
        # - song_started(deck) → DJ decides → preload(opposite)
        # - song_finished(deck) → switch_to(opposite) → start(opposite) → song_started(opposite)
        while self._running and not self._stop_event.is_set():
            # Check if restart was requested when idle
            with self._restart_lock:
                restart_requested = self._restart_requested
            
            if restart_requested and self.is_idle():
                # Restart requested and playout is idle - exit run loop
                logger.info("[PLAYOUT] Restart: idle, exiting")
                break
            
            # Small sleep to prevent busy-waiting
            import time
            time.sleep(0.1)  # 100ms sleep - event-driven, no need to poll frequently
        
        # Loop exited - mark as stopped
        self._running = False
        logger.debug("[PLAYOUT] Engine stopped")
    
    def _on_event_complete(self, event: AudioEvent, deck: Optional[Literal["A", "B"]] = None) -> None:
        """
        Callback when an event finishes (EOF from decoder).
        
        Handles preroll/main/postroll sequencing:
        - If preroll finishes → automatically play main
        - If main finishes → play postroll (if exists) OR trigger song_finished and switch decks
        - If postroll finishes → trigger song_finished and switch decks
        
        Only "main" events trigger song_finished callbacks and DJ sequencing.
        Preroll and postroll are internal sequencing only.
        
        Args:
            event: Completed AudioEvent
            deck: Which deck finished (optional, will try to detect if not provided)
        """
        logger.debug(f"[PLAYOUT] Completed {os.path.basename(event.path) if event.path else 'unknown'} on deck {deck or 'unknown'}")
        
        # Detect deck if not provided
        if deck is None:
            deck = self.mixer.active_deck
        
        # Get current sequence for this deck
        if deck == "A":
            sequence = self._current_sequence_A
            sequence_index = self._current_sequence_index_A
        else:
            sequence = self._current_sequence_B
            sequence_index = self._current_sequence_index_B
        
        # Phase 5: Mark current entry as ended in playlog
        with self._playlog_lock:
            if self._current_entry is not None:
                self.playlog.mark_end(self._current_entry)
                self._current_entry = None
        
        # Call registered callbacks (for all event types)
        for callback in self._event_complete_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"[PLAYOUT] Event complete callback error: {e}", exc_info=True)
        
        # Handle sequencing: preroll → main → postroll
        if sequence and sequence_index < len(sequence):
            current_event_in_sequence = sequence[sequence_index]
            
            # Verify the completed event matches what we expect
            if current_event_in_sequence.path != event.path:
                logger.warning(f"[PLAYOUT] Event mismatch: expected {current_event_in_sequence.path}, got {event.path}")
            
            # Determine which part of sequence just finished
            # Sequence structure: [preroll?, main, postroll?]
            # Index 0 = preroll (if exists) OR main (if no preroll)
            # Index 1 = main (if preroll exists) OR postroll (if no preroll but postroll exists)
            # Index 2 = postroll (if preroll exists)
            is_preroll = current_event_in_sequence.type == "intro"
            is_main = current_event_in_sequence.type == "song"
            is_postroll = current_event_in_sequence.type == "outro"
            
            # Advance sequence index
            next_index = sequence_index + 1
            
            # If preroll finished → play main
            if is_preroll and next_index < len(sequence):
                next_event = sequence[next_index]  # This is main
                if deck == "A":
                    self._current_sequence_index_A = next_index
                    self._next_event_A = next_event
                else:
                    self._current_sequence_index_B = next_index
                    self._next_event_B = next_event
                
                # Start main immediately (no deck switch needed - same deck)
                result = self.mixer.start_event(next_event, deck)
                if not result:
                    logger.error(f"[PLAYOUT] Failed to start main on deck {deck}: {os.path.basename(next_event.path)}")
                    return
                
                # Update state and playlog
                self.state_machine.transition_to(PlaybackState.PLAYING_SONG)
                self.state_machine.set_current_event(next_event)
                with self._playlog_lock:
                    self._current_entry = self.playlog.add_start(
                        path=next_event.path,
                        type=next_event.type,
                        deck=deck
                    )
                
                logger.info(f"[PLAYOUT] Start deck {deck}: {os.path.basename(next_event.path)}")
                
                # Call event start callbacks
                for callback in self._event_start_callbacks:
                    try:
                        callback(next_event)
                    except Exception as e:
                        logger.error(f"[PLAYOUT] Event start callback error: {e}", exc_info=True)
                
                # Main event will trigger song_started callback (which triggers DJ for opposite deck)
                return
            
            # If main finished → play postroll (if exists) OR trigger song_finished and switch decks
            if is_main:
                # Fire song_finished callbacks (only for main events)
                if deck:
                    for callback in self._song_finished_callbacks:
                        try:
                            callback(deck, event)
                        except Exception as e:
                            logger.error(f"[PLAYOUT] Song finished callback error: {e}", exc_info=True)
                
                # Check if postroll exists
                if next_index < len(sequence):
                    # Postroll exists → play it
                    next_event = sequence[next_index]  # This is postroll
                    if deck == "A":
                        self._current_sequence_index_A = next_index
                        self._next_event_A = next_event
                    else:
                        self._current_sequence_index_B = next_index
                        self._next_event_B = next_event
                    
                    # Start postroll immediately (same deck)
                    result = self.mixer.start_event(next_event, deck)
                    if not result:
                        logger.error(f"[PLAYOUT] Failed to start postroll on deck {deck}: {os.path.basename(next_event.path)}")
                        return
                    
                    # Update state and playlog
                    self.state_machine.transition_to(PlaybackState.PLAYING_OUTRO)
                    self.state_machine.set_current_event(next_event)
                    with self._playlog_lock:
                        self._current_entry = self.playlog.add_start(
                            path=next_event.path,
                            type=next_event.type,
                            deck=deck
                        )
                    
                    logger.info(f"[PLAYOUT] Start deck {deck}: {os.path.basename(next_event.path)}")
                    
                    # Call event start callbacks
                    for callback in self._event_start_callbacks:
                        try:
                            callback(next_event)
                        except Exception as e:
                            logger.error(f"[PLAYOUT] Event start callback error: {e}", exc_info=True)
                    
                    return
                else:
                    # No postroll → sequence complete, switch decks
                    # Clear sequence
                    if deck == "A":
                        self._current_sequence_A = None
                        self._current_sequence_index_A = 0
                    else:
                        self._current_sequence_B = None
                        self._current_sequence_index_B = 0
                    
                    # Switch to opposite deck
                    current_active = deck
                    new_active = "B" if current_active == "A" else "A"
                    
                    # Get next event for opposite deck
                    if new_active == "A":
                        next_event = self._next_event_A
                    else:
                        next_event = self._next_event_B
                    
                    if next_event:
                        logger.info(f"[PLAYOUT] Switch {current_active} → {new_active}")
                        
                        result = self.mixer.start_event(next_event, new_active)
                        if not result:
                            logger.error(f"[PLAYOUT] Failed to start deck {new_active}: {os.path.basename(next_event.path)}")
                            if new_active == "A":
                                self._next_event_A = None
                            else:
                                self._next_event_B = None
                            return
                        
                        # Clear stored event after successful start
                        if new_active == "A":
                            self._next_event_A = None
                        else:
                            self._next_event_B = None
                        
                        # Update state based on event type
                        if next_event.type == "intro":
                            self.state_machine.transition_to(PlaybackState.PLAYING_INTRO)
                        elif next_event.type == "song":
                            self.state_machine.transition_to(PlaybackState.PLAYING_SONG)
                        elif next_event.type == "outro":
                            self.state_machine.transition_to(PlaybackState.PLAYING_OUTRO)
                        
                        self.state_machine.set_current_event(next_event)
                        
                        # Record event start in playlog
                        with self._playlog_lock:
                            self._current_entry = self.playlog.add_start(
                                path=next_event.path,
                                type=next_event.type,
                                deck=new_active
                            )
                        
                        logger.info(f"[PLAYOUT] Start deck {new_active}: {os.path.basename(next_event.path)}")
                        
                        # Call event start callbacks
                        for callback in self._event_start_callbacks:
                            try:
                                callback(next_event)
                            except Exception as e:
                                logger.error(f"[PLAYOUT] Event start callback error: {e}", exc_info=True)
                    else:
                        logger.debug(f"[PLAYOUT] No next event for deck {new_active}")
                    
                    return
            
            # If postroll finished → sequence complete, trigger song_finished and switch decks
            if is_postroll:
                # Clear sequence
                if deck == "A":
                    self._current_sequence_A = None
                    self._current_sequence_index_A = 0
                else:
                    self._current_sequence_B = None
                    self._current_sequence_index_B = 0
                
                # Switch to opposite deck
                current_active = deck
                new_active = "B" if current_active == "A" else "A"
                
                # Get next event for opposite deck
                if new_active == "A":
                    next_event = self._next_event_A
                else:
                    next_event = self._next_event_B
                
                if next_event:
                    logger.info(f"[PLAYOUT] Switch {current_active} → {new_active}")
                    
                    result = self.mixer.start_event(next_event, new_active)
                    if not result:
                        logger.error(f"[PLAYOUT] Failed to start deck {new_active}: {os.path.basename(next_event.path)}")
                        if new_active == "A":
                            self._next_event_A = None
                        else:
                            self._next_event_B = None
                        return
                    
                    # Clear stored event after successful start
                    if new_active == "A":
                        self._next_event_A = None
                    else:
                        self._next_event_B = None
                    
                    # Update state based on event type
                    if next_event.type == "intro":
                        self.state_machine.transition_to(PlaybackState.PLAYING_INTRO)
                    elif next_event.type == "song":
                        self.state_machine.transition_to(PlaybackState.PLAYING_SONG)
                    elif next_event.type == "outro":
                        self.state_machine.transition_to(PlaybackState.PLAYING_OUTRO)
                    
                    self.state_machine.set_current_event(next_event)
                    
                    # Record event start in playlog
                    with self._playlog_lock:
                        self._current_entry = self.playlog.add_start(
                            path=next_event.path,
                            type=next_event.type,
                            deck=new_active
                        )
                    
                    logger.info(f"[PLAYOUT] Start deck {new_active}: {os.path.basename(next_event.path)}")
                    
                    # Call event start callbacks
                    for callback in self._event_start_callbacks:
                        try:
                            callback(next_event)
                        except Exception as e:
                            logger.error(f"[PLAYOUT] Event start callback error: {e}", exc_info=True)
                else:
                    logger.debug(f"[PLAYOUT] No next event for deck {new_active}")
        
        # Fallback: If no sequence tracking, use old behavior (for backward compatibility)
        # This should not happen in normal operation
        if not sequence and event.type == "song" and deck:
            logger.warning("[PLAYOUT] Song finished but no sequence tracking - using fallback")
            current_active = deck
            new_active = "B" if current_active == "A" else "A"
            
            if new_active == "A":
                next_event = self._next_event_A
            else:
                next_event = self._next_event_B
            
            if next_event:
                logger.info(f"[PLAYOUT] Switch {current_active} → {new_active}")
                result = self.mixer.start_event(next_event, new_active)
                if not result:
                    logger.error(f"[PLAYOUT] Failed to start deck {new_active}: {os.path.basename(next_event.path)}")
                    return
                
                if new_active == "A":
                    self._next_event_A = None
                else:
                    self._next_event_B = None
                
                self.state_machine.transition_to(PlaybackState.PLAYING_SONG)
                self.state_machine.set_current_event(next_event)
                
                with self._playlog_lock:
                    self._current_entry = self.playlog.add_start(
                        path=next_event.path,
                        type=next_event.type,
                        deck=new_active
                    )
                
                logger.info(f"[PLAYOUT] Start deck {new_active}: {os.path.basename(next_event.path)}")
        
        # Update state to IDLE only if queue is empty and mixer is truly idle
        if self.event_queue.empty() and not self.mixer.is_playing():
            self.state_machine.transition_to(PlaybackState.IDLE)
            self.state_machine.set_current_event(None)
    
    def _on_fm_failure(self) -> None:
        """
        Phase 5: Callback when FM sink fails.
        
        Called by mixer when FM write fails (before exception is raised).
        """
        with self._fm_lock:
            self._fm_failed = True
    
    def stop(self) -> None:
        """
        Stop the playout engine.
        
        Sets the stop event and marks engine as not running.
        """
        self._stop_event.set()
        self._running = False
        logger.debug("[PLAYOUT] Engine stopped")
    
    def now_playing(self) -> Optional[NowPlayingInfo]:
        """
        Phase 5: Return info about the currently playing AudioEvent, or None if idle.
        
        Returns:
            NowPlayingInfo if an event is playing, None if idle
        """
        # Check state machine - if not playing, return None
        state = self.state_machine.get_state()
        if state == PlaybackState.IDLE:
            return None
        
        # Try to get current event from state machine first
        context = self.state_machine.get_context()
        current_event = context.current_event
        
        # If no event in state machine, try to get it from active decoder
        if current_event is None:
            # Get event from active deck decoder
            active_deck = self.mixer.active_deck
            if active_deck == "A":
                current_event = self.mixer.turntable_a.get_current_event()
            else:
                current_event = self.mixer.turntable_b.get_current_event()
        
        # If still no event, return None
        if current_event is None:
            return None
        
        # Get active deck
        active_deck = self.mixer.active_deck
        
        # Get playlog entry for timing
        with self._playlog_lock:
            entry = self._current_entry
        
        if entry is None:
            # No playlog entry yet - create info from current event
            return NowPlayingInfo(
                path=current_event.path,
                type=current_event.type,
                deck=active_deck,
                started_at=datetime.now()  # Approximate
            )
        
        return NowPlayingInfo(
            path=current_event.path,
            type=current_event.type,
            deck=active_deck,
            started_at=entry.started_at
        )
    
    def next_up(self) -> Optional[NextUpInfo]:
        """
        Phase 5: Return info about the next queued AudioEvent (if any).
        
        This reflects the head of EventQueue, not a long timeline.
        Only reports the first upcoming event.
        
        Returns:
            NextUpInfo if queue has events, None if queue is empty
        """
        # Check queue size
        if self.event_queue.empty():
            return None
        
        # Check if inactive deck is pre-loaded (that's the next up)
        if self.mixer.is_inactive_preloaded():
            inactive_deck = "B" if self.mixer.active_deck == "A" else "A"
            if inactive_deck == "A":
                event = self.mixer.turntable_a.get_current_event()
            else:
                event = self.mixer.turntable_b.get_current_event()
            
            if event:
                return NextUpInfo(
                    path=event.path,
                    type=event.type
                )
        
        # If no pre-loaded event, we can't peek at queue head without modifying EventQueue
        # Return None for now (queue has events but we can't see them without dequeuing)
        # This is acceptable for Phase 5 - we're not adding queue peeking capability
        return None
    
    def health(self) -> EngineHealth:
        """
        Phase 5: Return a quick health snapshot of playout + sinks.
        
        Returns:
            EngineHealth dataclass with current system state
        """
        # Get state
        state = self.state_machine.get_state()
        state_str = state.name if hasattr(state, 'name') else str(state)
        
        # Get queue length
        queue_length = self.event_queue.qsize()
        
        # Get restart requested flag
        with self._restart_lock:
            restart_requested = self._restart_requested
        
        # Get FM status
        with self._fm_lock:
            fm_ok = not self._fm_failed and self.mixer.fm_sink is not None
        
        # Get YouTube status
        youtube_connected = True  # Default to True
        for sink in self.mixer.sinks:
            if sink is not self.mixer.fm_sink:
                from outputs.youtube_sink import YouTubeSink
                if isinstance(sink, YouTubeSink):
                    youtube_connected = not sink.is_disconnected()
                    break
        
        # Get active deck
        active_deck = self.mixer.active_deck if self.mixer.is_playing() else None
        
        return EngineHealth(
            state=state_str,
            queue_length=queue_length,
            restart_requested=restart_requested,
            fm_ok=fm_ok,
            youtube_connected=youtube_connected,
            active_deck=active_deck
        )
