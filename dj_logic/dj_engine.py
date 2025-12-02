"""
DJ Engine for managing DJ intros, outros, and talk segments.

Phase 7: Implements decision logic for when to play DJ segments per song.
DJ-Driven Architecture: DJ becomes the single decision-maker.

===========================================================
DJ ENGINE CONCURRENCY CONTRACT (MUST NOT BE BROKEN)
===========================================================

The DJEngine is pure logic. It makes decisions about whether to
speak and what song/intro/outro events to schedule. It MUST be
non-blocking and must NEVER interact with real-time locks.

THREADING RULES
---------------

1. DJEngine has no locks of its own.

2. DJEngine is always invoked from PlayoutEngine callbacks,
   AFTER Mixer has released its internal locks.

3. DJEngine MUST:
       - Return quickly (milliseconds)
       - Perform only in-memory computations
       - NOT perform blocking I/O
       - NOT sleep()
       - NOT inspect mixer internals or decoders

4. DJEngine MAY:
       - Examine current song metadata
       - Choose next events (intro/song/outro)
       - Update cadence counters
       - Return a list of AudioEvent objects

5. DJEngine MUST NOT call Mixer methods directly.

Allowed flows:
    PlayoutEngine → DJEngine.on_station_started()
    PlayoutEngine → DJEngine.on_song_started(deck, event)

NOT allowed:
    Mixer → DJEngine (directly)
    DJEngine → Mixer (directly)
    DJEngine performing long operations

FUTURE-PROOF:
-------------
This contract ensures DJEngine can later be made async, external,
or AI-driven without affecting the real-time audio pipeline.

"""

import logging
import os
import random
from dataclasses import dataclass
from typing import List, Literal, Optional
from broadcast_core.event_queue import AudioEvent
from dj_logic.track_matcher import TrackMatcher
from dj_logic.rules_engine import RulesEngine
from dj_logic.cadence_manager import CadenceManager

logger = logging.getLogger(__name__)


@dataclass
class DJSegment:
    """
    Represents a DJ segment (intro or outro).
    
    Attributes:
        file_name: Name of the DJ file (e.g., "MySong_intro.mp3")
        segment_type: Type of segment ("intro" or "outro")
    """
    file_name: str
    segment_type: Literal["intro", "outro"]


@dataclass
class DJContext:
    """
    Context passed to DJ decision-making.
    
    Attributes:
        last_song: Last song that played (or None if first song)
        playlog: Playlog instance for history
        cadence_counter: Current cadence counter (songs since last segment)
    """
    last_song: Optional[AudioEvent]
    playlog: object  # Playlog type
    cadence_counter: int


class DJEngine:
    """
    Main orchestrator for DJ system - the single decision-maker.
    
    DJ-Driven Architecture: All decisions happen here.
    Decides when to play intros or outros for each song based on:
    - Cadence (minimum spacing between segments)
    - Probability ramp (increases with songs since last segment)
    - File availability (only plays if matching files exist)
    """
    
    def __init__(
        self,
        dj_path: str,
        music_path: str,
        library_manager=None,
        playlist_manager=None,
        playlog=None
    ) -> None:
        """
        Initialize the DJ engine.
        
        Args:
            dj_path: Path to DJ files directory
            music_path: Path to music files directory (unused, kept for compatibility)
            library_manager: LibraryManager instance (for getting available tracks)
            playlist_manager: PlaylistManager instance (for selecting next song)
            playlog: Playlog instance (for history context)
        """
        self.dj_path = dj_path
        self.music_path = music_path
        self.library_manager = library_manager
        self.playlist_manager = playlist_manager
        self.playlog = playlog
        
        # Initialize components
        self.cadence_manager = CadenceManager()
        self.rules_engine = RulesEngine(self.cadence_manager)
        self.track_matcher = TrackMatcher(dj_path)
        
        # Track last song for context
        self._last_song: Optional[AudioEvent] = None
        
        # Callback for queuing events (set by Station)
        self._queue_events_callback = None
    
    def build_events_for_song(self, song_path: str) -> List[AudioEvent]:
        """
        Build a list of AudioEvents for a song, including optional intro/outro.
        
        Decision logic:
        1. Always register that a song is about to be played
        2. Try intro first (if cadence allows and probability hits)
        3. Always play the song
        4. If no intro played, try outro (if cadence allows and probability hits)
        5. At most one DJ segment per song (intro OR outro, never both)
        
        Returns: [ intro? ] → AudioEvent(song) → [ outro? ]
        
        Args:
            song_path: Full path to the song file
            
        Returns:
            List of AudioEvent objects in play order
        """
        events: List[AudioEvent] = []
        played_segment = False
        
        # Always register that a song is about to be played
        self.cadence_manager.register_song_played()
        
        # 1) Try intro first (if allowed by cadence + probability)
        if self.rules_engine.can_consider_speaking():
            p_intro = self.rules_engine.intro_probability()
            roll = random.random()
            
            if roll < p_intro:
                intro_path = self.track_matcher.find_intro(song_path)
                if intro_path:
                    events.append(AudioEvent(path=intro_path, type="intro", gain=1.0))
                    self.cadence_manager.register_segment_played()
                    played_segment = True
                    logger.info(
                        f"[DJ] Will speak: INTRO '{os.path.basename(intro_path)}' before song '{os.path.basename(song_path)}' "
                        f"(probability={p_intro:.1%}, roll={roll:.3f})"
                    )
                    logger.debug(
                        f"[DJ] Playing intro '{os.path.basename(intro_path)}' for song '{os.path.basename(song_path)}' "
                        f"(roll={roll:.3f} < p_intro={p_intro:.3f})"
                    )
                else:
                    logger.debug(
                        f"[DJ] Intro roll={roll:.3f} < p_intro={p_intro:.3f} but no intro file found for "
                        f"'{os.path.basename(song_path)}'"
                    )
            else:
                logger.debug(
                    f"[DJ] Intro roll={roll:.3f} >= p_intro={p_intro:.3f} – skipping intro for "
                    f"'{os.path.basename(song_path)}'"
                )
        else:
            # Cadence blocked - log it
            songs_since = self.cadence_manager.get_songs_since_last_segment()
            logger.debug(
                f"[DJ] Cadence blocked segment for '{os.path.basename(song_path)}' "
                f"(only {songs_since} songs since last segment, need 3+)"
            )
        
        # 2) Always play the song
        events.append(AudioEvent(path=song_path, type="song", gain=1.0))
        
        # 3) If no intro played, consider outro
        if not played_segment and self.rules_engine.can_consider_speaking():
            p_outro = self.rules_engine.outro_probability()
            roll = random.random()
            
            if roll < p_outro:
                outro_path = self.track_matcher.find_outro(song_path)
                if outro_path:
                    events.append(AudioEvent(path=outro_path, type="outro", gain=1.0))
                    self.cadence_manager.register_segment_played()
                    played_segment = True
                    logger.info(
                        f"[DJ] Will speak: OUTRO '{os.path.basename(outro_path)}' after song '{os.path.basename(song_path)}' "
                        f"(probability={p_outro:.1%}, roll={roll:.3f})"
                    )
                    logger.debug(
                        f"[DJ] Playing outro '{os.path.basename(outro_path)}' for song '{os.path.basename(song_path)}' "
                        f"(roll={roll:.3f} < p_outro={p_outro:.3f})"
                    )
                else:
                    logger.debug(
                        f"[DJ] Outro roll={roll:.3f} < p_outro={p_outro:.3f} but no outro file found for "
                        f"'{os.path.basename(song_path)}'"
                    )
            else:
                logger.debug(
                    f"[DJ] Outro roll={roll:.3f} >= p_outro={p_outro:.3f} – skipping outro for "
                    f"'{os.path.basename(song_path)}'"
                )
        
        # Log if no DJ segment will play
        if not played_segment:
            songs_since = self.cadence_manager.get_songs_since_last_segment()
            if songs_since < 3:
                logger.info(f"[DJ] Will NOT speak for '{os.path.basename(song_path)}' (cadence: only {songs_since} songs since last segment, need 3+)")
            else:
                # Cadence allows, but probability roll missed or no files found
                logger.info(f"[DJ] Will NOT speak for '{os.path.basename(song_path)}' (probability roll missed or no matching files found)")
        
        return events
    
    def decide_between_songs(
        self,
        current_song_path: str,
        next_song_path: str
    ) -> Optional[DJSegment]:
        """
        Decide what DJ segment to play between current song and next song.
        
        Called when preloading the next song. Decides what to say between
        the current song (that just started) and the next song (being preloaded).
        
        Considers:
        - Outro of current song (if it exists)
        - Intro of next song (if it exists)
        - Prefers intro of next song over outro of current
        
        Args:
            current_song_path: Path to the currently playing song
            next_song_path: Path to the next song being preloaded
            
        Returns:
            DJSegment to play between songs, or None if no DJ segment
        """
        import random
        
        # Get songs since last DJ talk
        songs_since_dj = self.cadence_manager.get_songs_since_last_segment()
        
        # Check cadence (should we play DJ segments now?)
        if not self.cadence_manager.can_play_segment():
            return None
        
        # Calculate probabilities
        p_intro = self.rules_engine.intro_probability()
        p_outro = self.rules_engine.outro_probability()
        
        # Check what files are available
        current_outro_path = self.track_matcher.find_outro(current_song_path)
        next_intro_path = self.track_matcher.find_intro(next_song_path)
        
        # Decision logic: prefer intro of next song, fallback to outro of current
        should_play_intro = False
        should_play_outro = False
        
        # If next song has intro files, consider playing intro
        if next_intro_path:
            should_play_intro = random.random() < p_intro
        
        # If intro wasn't chosen, consider outro of current song
        if not should_play_intro and current_outro_path:
            should_play_outro = random.random() < p_outro
        
        # Create segment if we decided to play something
        if should_play_intro and next_intro_path:
            intro_filename = os.path.basename(next_intro_path)
            return DJSegment(file_name=intro_filename, segment_type="intro")
        elif should_play_outro and current_outro_path:
            outro_filename = os.path.basename(current_outro_path)
            return DJSegment(file_name=outro_filename, segment_type="outro")
        
        return None
    
    def set_queue_events_callback(self, callback) -> None:
        """
        Set callback for queuing events (called by Station).
        
        Args:
            callback: Function(events: list[AudioEvent]) to queue events
        """
        self._queue_events_callback = callback
    
    def on_station_started(self) -> List[AudioEvent]:
        """
        DJ chooses the first song + intro/outro events.
        
        Called when station starts up. Returns events for Deck A.
        
        Returns:
            List of AudioEvent objects for Deck A (in play order: [intro?] -> song -> [outro?])
        """
        logger.debug("[DJ] Station startup decision")
        context = DJContext(
            last_song=None,
            playlog=self.playlog,
            cadence_counter=self.cadence_manager.get_songs_since_last_segment()
        )
        events = self.make_decision(deck="A", event=None, context=context)
        return events if events else []
    
    def on_song_started(self, deck: Literal["A", "B"], event: AudioEvent) -> List[AudioEvent]:
        """
        DJ chooses what should play AFTER this song.
        
        Called when a song starts playing on a deck. Returns events for the OPPOSITE deck.
        Only called for song events (not intro/outro).
        
        Args:
            deck: Which deck started playing ("A" or "B")
            event: The AudioEvent that started (must be type "song")
            
        Returns:
            List of AudioEvent objects for the opposite deck (in play order: [intro?] -> song -> [outro?])
        """
        # Only make decisions for song events (not intro/outro)
        if event.type != "song":
            return []
        
        # Register this song as played for cadence purposes (counts toward spacing)
        # This ensures cadence is correct when making decision for the next song
        self.cadence_manager.register_song_played()
        
        # Update last song
        self._last_song = event
        
        # Get opposite deck
        opposite_deck = "B" if deck == "A" else "A"
        
        logger.debug(f"[DJ] Deciding next track for deck {opposite_deck}")
        
        context = DJContext(
            last_song=self._last_song,
            playlog=self.playlog,
            cadence_counter=self.cadence_manager.get_songs_since_last_segment()
        )
        
        events = self.make_decision(deck=opposite_deck, event=event, context=context)
        return events if events else []
    
    def make_decision(
        self,
        deck: Literal["A", "B"],
        event: Optional[AudioEvent],
        context: DJContext
    ) -> List[AudioEvent]:
        """
        Single decision function - determines what to preload into the specified deck.
        
        When preloading a song, decides if that song should have an intro or outro.
        Decision is made for the song being preloaded, not about transitions.
        
        Args:
            deck: Which deck to preload into ("A" or "B")
            event: Current event (if any) - None for first decision
            context: DJContext with history and cadence info
            
        Returns:
            List of AudioEvent objects to queue (in play order: [intro?] -> song -> [outro?])
        """
        events: List[AudioEvent] = []
        
        # Get next song
        if not self.library_manager or not self.playlist_manager:
            logger.error("[DJ] LibraryManager or PlaylistManager not set")
            return events
        
        available_tracks = self.library_manager.get_all_tracks()
        if not available_tracks:
            logger.warning("[DJ] No tracks available")
            return events
        
        next_track = self.playlist_manager.select_next_song(available_tracks)
        next_filename = os.path.basename(next_track)
        
        # Decision logic: decide for the song being preloaded
        # Prefer intro, fallback to outro, at most one segment per song
        played_segment = False
        
        # Check cadence
        if self.cadence_manager.can_play_segment():
            # Calculate probabilities
            p_intro = self.rules_engine.intro_probability()
            p_outro = self.rules_engine.outro_probability()
            
            # 1) Try intro first (if allowed by cadence + probability)
            next_intro_path = self.track_matcher.find_intro(next_track)
            if next_intro_path:
                roll = random.random()
                if roll < p_intro:
                    events.append(AudioEvent(path=next_intro_path, type="intro", gain=1.0))
                    self.cadence_manager.register_segment_played()
                    played_segment = True
                    logger.info(f"[DJ] Speaking: INTRO {os.path.basename(next_intro_path)}")
            
            # 2) If no intro, try outro
            if not played_segment:
                next_outro_path = self.track_matcher.find_outro(next_track)
                if next_outro_path:
                    roll = random.random()
                    if roll < p_outro:
                        events.append(AudioEvent(path=next_outro_path, type="outro", gain=1.0))
                        self.cadence_manager.register_segment_played()
                        played_segment = True
                        logger.info(f"[DJ] Speaking: OUTRO {os.path.basename(next_outro_path)}")
            
            # Log if no segment
            if not played_segment:
                songs_since = self.cadence_manager.get_songs_since_last_segment()
                logger.info(f"[DJ] Silent (cadence {songs_since}/3)")
        else:
            # Cadence blocked
            songs_since = self.cadence_manager.get_songs_since_last_segment()
            logger.info(f"[DJ] Silent (cadence {songs_since}/3)")
        
        # Always add the song itself
        events.append(AudioEvent(path=next_track, type="song", gain=1.0))
        
        return events
