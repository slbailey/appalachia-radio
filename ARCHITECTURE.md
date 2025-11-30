# Radio Broadcast System - Architecture Documentation

## Table of Contents

1. [System Overview](#system-overview)
2. [Current Architecture](#current-architecture)
3. [Rearchitected System Design](#rearchitected-system-design)
4. [Component Responsibilities](#component-responsibilities)
5. [Data Flow Diagrams](#data-flow-diagrams)
6. [Event Lifecycle](#event-lifecycle)
7. [Dual-Output Architecture](#dual-output-architecture)
8. [DJ Integration Without Blocking](#dj-integration-without-blocking)
9. [Future Scalability](#future-scalability)

---

## System Overview

The Radio Broadcast System is an automated radio station that:
- Plays music with intelligent weighted selection based on play history
- Integrates DJ intros/outros and talk segments
- Simultaneously outputs to FM transmitter (primary) and YouTube Live (secondary)
- Ensures FM broadcast continues even if YouTube streaming fails

### Key Requirements

1. **Music Playback**: Weighted random selection with play history tracking
2. **DJ System**: Separate, non-blocking system for intros, outros, and talk segments
3. **Dual Output**: 
   - FM transmitter (always active, critical path)
   - YouTube Live stream (optional, must not block FM if offline)
4. **Resilience**: Local FM output must work independently of internet connectivity

---

## 🔒 Locked-In Implementation Decisions

**These decisions are final and must be implemented exactly as specified:**

### 1. Audio Decoder: FFmpeg Pipe Output (LOCKED IN)

**Decision**: Use FFmpeg subprocess with raw PCM pipe output.

**Implementation**:
- **Command**: `ffmpeg -i input.mp3 -f s16le -ac 2 -ar 48000 pipe:1`
  - `-f s16le`: 16-bit signed little-endian PCM
  - `-ac 2`: Stereo (2 channels)
  - `-ar 48000`: 48kHz sample rate
  - `pipe:1`: Output raw PCM bytes to stdout
- **Interface**: Streaming generator that yields PCM frame chunks
- **Frame Size**: 4096-8192 bytes per frame (configurable)
- **Rationale**: Stable, low CPU, works with all formats, perfect for live streaming

**Rejected Alternatives**:
- ❌ pydub: Loads entire file into memory (bad for live streaming)
- ❌ python-vlc: Unpredictable latency and drift

### 2. Frame-Based Architecture (LOCKED IN)

**Decision**: Entire audio pipeline operates on frame chunks, not full files.

**Implementation**:
- **Decoder**: `for frame in decoder.stream_frames(): yield frame`
- **Mixer**: `mixer.push_frame(frame)` → processes → outputs to sinks
- **Sinks**: `sink.write_frame(frame)` - receives frame chunks
- **Frame Size**: 4096-8192 bytes (typically ~46-92ms of audio at 48kHz stereo)

**Benefits**:
- Low latency (no need to decode entire file)
- Memory efficient (only small buffers in memory)
- Real-time processing (suitable for live streaming)

**Rejected Approach**: ❌ Loading full PCM files into memory

### 3. PlayoutEngine Interface (LOCKED IN)

**Decision**: Explicit, minimal interface for PlayoutEngine.

**Implementation**:
```python
class PlayoutEngine:
    def queue_event(self, event: AudioEvent) -> None:
        """Add an audio event to the playout queue."""
        
    def run(self) -> None:
        """Main non-blocking loop that ticks mixer and processes events."""
        
    def current_state(self) -> PlaybackState:
        """Get current playback state."""
```

**AudioEvent Definition**:
```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class AudioEvent:
    path: str              # File path to audio file
    type: Literal["song", "intro", "outro", "talk"]  # Event type
    gain: float = 1.0     # Volume gain multiplier (0.0-1.0)
```

**Rationale**: Clear, minimal interface prevents implementation assumptions and ensures testability.

---

---

## Current Architecture

### Current Structure

The existing system (`radio/radio.py`) uses a monolithic `MusicPlayer` class that:
- Orchestrates all components (playlist, DJ, audio, YouTube)
- Embeds DJ logic directly in the playback loop
- Uses a pipe-based audio system for dual output
- Handles YouTube streaming as an optional component

**Current Components:**
- `MusicPlayer`: Main orchestrator
- `PlaylistManager`: Song selection and history tracking
- `DJManager`: DJ file discovery and caching
- `PipeAudioPlayer`: MP3 decoding to named pipe
- `ALSAOutputManager`: Reads pipe, outputs to FM transmitter
- `YouTubeStreamer`: Reads pipe, streams to YouTube Live

**Current Limitations:**
- DJ logic is embedded in music playback (tight coupling)
- No clear separation between selection logic and playout
- Single-threaded blocking operations
- Limited extensibility for future features

---

## Rearchitected System Design

### High-Level Architecture

The rearchitected system separates concerns into six independent layers:

```
┌─────────────────────────────────────────────────────────────┐
│                    /app/radio.py                            │
│              (Thin Orchestration Shell)                      │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ music_logic/ │   │  dj_logic/   │   │broadcast_core│
│              │   │              │   │              │
│ PlaylistMgr  │   │  DJEngine    │   │ PlayoutEngine│
│ SongHistory  │   │  Rules/Cadence│  │  Queue/State │
│ Probabilities│   │  Track Match │   │  Scheduling  │
└──────────────┘   └──────────────┘   └──────────────┘
                            │                   │
                            └─────────┬─────────┘
                                      ▼
                            ┌──────────────┐
                            │   mixer/     │
                            │              │
                            │ MP3→PCM      │
                            │ Ducking      │
                            │ Crossfade    │
                            └──────────────┘
                                      │
                    ┌─────────────────┴─────────────────┐
                    │                                   │
                    ▼                                   ▼
            ┌──────────────┐                   ┌──────────────┐
            │  outputs/    │                   │  outputs/    │
            │              │                   │              │
            │   FMSink     │                   │ YouTubeSink  │
            │ (Always On)  │                   │ (Optional)   │
            └──────────────┘                   └──────────────┘
```

### Directory Structure

```
appalachia-radio/
├── app/
│   └── radio.py              # Thin orchestration shell
├── music_logic/
│   ├── __init__.py
│   ├── playlist_manager.py   # PlaylistManager (existing, refactored)
│   ├── song_history.py       # History tracking & weighted selection
│   └── probability_engine.py # Weight calculation algorithms
├── dj_logic/
│   ├── __init__.py
│   ├── dj_engine.py          # DJEngine (rewritten from DJManager)
│   ├── rules_engine.py       # Rules for when to play DJ segments
│   ├── cadence_manager.py    # Timing and cadence logic
│   └── track_matcher.py      # Match DJ files to songs
├── broadcast_core/
│   ├── __init__.py
│   ├── playout_engine.py     # PlayoutEngine (non-blocking)
│   ├── event_queue.py        # Queue of audio events
│   └── state_machine.py      # Playback state management
├── mixer/
│   ├── __init__.py
│   ├── audio_decoder.py      # MP3 → PCM conversion (FFmpeg pipe, LOCKED IN)
│   ├── audio_mixer.py        # Frame-based mixing, ducking, crossfade
│   └── pcm_buffer.py         # PCM frame buffer management
├── outputs/
│   ├── __init__.py
│   ├── fm_sink.py            # FMSink (always active)
│   ├── youtube_sink.py       # YouTubeSink (optional, reconnects)
│   └── sink_base.py          # Base class for audio sinks
└── ARCHITECTURE.md           # This file
```

---

## Component Responsibilities

### 1. music_logic/

**Purpose**: Pure music selection logic with no knowledge of playback or DJ.

#### PlaylistManager
- Maintains play history (last N songs with timestamps)
- Tracks play counts per song
- Calculates weighted probabilities for song selection
- Handles holiday season detection and probability
- No direct file I/O (receives file lists from outside)

#### SongHistory
- Manages play history queue
- Provides time-based queries (e.g., "songs played in last hour")
- Calculates recency penalties
- Tracks never-played songs

#### ProbabilityEngine
- Implements weighting algorithms:
  - Recent play penalty (queue-like system)
  - Time-based bonus (old songs get priority)
  - Never-played bonus
  - Play count balance
- Normalizes probabilities for random selection

**Key Principle**: These components are pure functions - they take inputs (file lists, history) and return selection probabilities. No side effects.

---

### 2. dj_logic/

**Purpose**: Independent system for DJ segment decision-making and management.

#### DJEngine
- Main orchestrator for DJ system
- Decides when to play intros, outros, or talk segments
- Manages DJ file discovery and caching
- Coordinates with rules engine and cadence manager
- Provides non-blocking API: "Should I play a DJ segment now?"

#### RulesEngine
- Implements rules for DJ segment selection:
  - Dynamic probability (increases over time)
  - Track attribute matching (genre, mood, etc.)
  - Time-of-day rules
  - Special event rules
- Returns recommendations, not commands

#### CadenceManager
- Manages timing and cadence:
  - Minimum time between DJ segments
  - Maximum DJ segment frequency
  - Intro vs outro preference logic
  - Talk segment scheduling

#### TrackMatcher
- Matches DJ files to songs:
  - Intro/outro file discovery
  - Multiple variant support (intro1, intro2, etc.)
  - Fallback logic
- Caches file lists for performance

**Key Principle**: DJ logic is completely separate from music playback. It provides recommendations that the playout engine can accept or reject.

---

### 3. broadcast_core/

**Purpose**: Non-blocking playout scheduling and state management.

#### PlayoutEngine
- **Explicit Interface** (LOCKED IN):
  ```python
  class PlayoutEngine:
      def queue_event(self, event: AudioEvent) -> None:
          """Add an audio event to the playout queue."""
          
      def run(self) -> None:
          """Main non-blocking loop that ticks mixer and processes events."""
          
      def current_state(self) -> PlaybackState:
          """Get current playback state."""
  ```
- Manages queue of audio events (intro → song → outro)
- Non-blocking scheduler that continuously processes events
- State machine for playback states:
  - `IDLE`: No audio playing
  - `PLAYING_INTRO`: Playing DJ intro segment
  - `PLAYING_SONG`: Playing music track
  - `PLAYING_OUTRO`: Playing DJ outro segment
  - `TRANSITIONING`: Crossfading between tracks
- Coordinates with mixer for audio delivery (frame-by-frame)
- Handles event timing and transitions

#### EventQueue
- Thread-safe queue of audio events
- **AudioEvent Definition** (LOCKED IN):
  ```python
  from dataclasses import dataclass
  from typing import Literal
  
  @dataclass
  class AudioEvent:
      path: str              # File path to audio file
      type: Literal["song", "intro", "outro", "talk"]  # Event type
      gain: float = 1.0     # Volume gain multiplier (0.0-1.0)
  ```
- Event types map to `AudioEvent.type`:
  - `"song"`: Music track to play
  - `"intro"`: DJ intro segment
  - `"outro"`: DJ outro segment
  - `"talk"`: Standalone DJ talk segment
- Priority handling (DJ segments can interrupt)
- Event metadata (duration, fade points, etc.) - may be extended in future

#### StateMachine
- Manages playback state transitions
- Ensures valid state sequences
- Handles error states and recovery
- Provides state change callbacks

**Key Principle**: PlayoutEngine is a scheduler, not a player. It doesn't decode audio or output sound - it manages what should play when.

---

### 4. mixer/

**Purpose**: Audio processing and format conversion.

#### AudioDecoder
- **Implementation**: FFmpeg subprocess with pipe output (LOCKED IN)
- **Command Format**: `ffmpeg -i input.mp3 -f s16le -ac 2 -ar 48000 pipe:1`
  - `-f s16le`: 16-bit signed little-endian PCM format
  - `-ac 2`: 2 channels (stereo)
  - `-ar 48000`: 48kHz sample rate
  - `pipe:1`: Output to stdout as raw PCM bytes
- **Interface**: Streaming generator that yields PCM frame chunks
- **Frame Size**: Configurable (typically 4096-8192 bytes per frame)
- **Error Handling**: Handles corrupted files, missing files, FFmpeg process failures
- **Why FFmpeg**: Stable, low CPU usage, works with all audio formats, perfect for live streaming

#### AudioMixer
- **Frame-Based Architecture**: Processes and outputs PCM frames/chunks, not full files
- **Streaming Interface**: 
  - Receives frames from decoder: `mixer.push_frame(frame)`
  - Outputs frames to sinks: `sink.write_frame(frame)`
- **Frame Processing**:
  - Mixes multiple audio sources (future: voice ducking)
  - Applies crossfade between tracks (frame-by-frame)
  - Handles volume normalization per frame
  - Manages audio effects (compression, EQ - future)
- **Continuous Stream**: Outputs frame chunks continuously, maintaining low latency
- **Frame Size**: Matches decoder output (typically 4096-8192 bytes)

#### PCMBuffer
- Manages PCM frame buffers
- Handles buffer underrun/overrun
- Provides thread-safe read/write operations
- Manages buffer size and latency

**Key Principle**: Mixer is the only component that touches audio files. All other components work with metadata and file paths.

**Frame-Based Architecture**: The entire audio pipeline operates on frame chunks, not full files. This enables:
- Low latency (no need to decode entire file before playback)
- Memory efficiency (only small buffers in memory)
- Real-time processing (suitable for live streaming)

---

### 5. outputs/

**Purpose**: Audio output sinks that consume PCM streams.

#### FMSink
- Always active (critical path)
- Consumes PCM frames from mixer
- Outputs to ALSA device (FM transmitter)
- Handles device errors gracefully
- Never blocks or fails silently
- Reconnects automatically on device errors

#### YouTubeSink
- Optional secondary output
- Consumes same PCM stream as FMSink
- Connects/reconnects independently
- Handles network failures gracefully
- Must not block FMSink if offline
- Provides status callbacks (connected/disconnected)

#### SinkBase
- Abstract base class for all sinks
- **Interface** (LOCKED IN):
  ```python
  class SinkBase(ABC):
      @abstractmethod
      def write_frame(self, pcm_frame: bytes) -> None:
          """Write a single PCM frame chunk to the sink."""
          
      @abstractmethod
      def start(self) -> bool:
          """Start the sink (e.g., open device, connect stream)."""
          
      @abstractmethod
      def stop(self) -> None:
          """Stop the sink (e.g., close device, disconnect stream)."""
  ```
- Handles common functionality:
  - Frame buffer management
  - Error recovery
  - Status reporting
- Allows future sinks (e.g., Icecast, local recording)

**Key Principle**: Sinks are independent consumers. FMSink failure is fatal, YouTubeSink failure is non-blocking.

---

### 6. /app/radio.py

**Purpose**: Thin orchestration shell that wires everything together.

**Responsibilities:**
- Initialize all components
- Wire up data flow between layers
- Handle system signals (SIGTERM, etc.)
- Provide main event loop (if needed)
- Logging and monitoring coordination

**What it does NOT do:**
- No business logic
- No audio processing
- No file I/O (beyond initialization)
- No state management

**Example Structure:**
```python
def main():
    # Initialize components
    playlist_mgr = PlaylistManager()
    dj_engine = DJEngine()
    playout_engine = PlayoutEngine()
    mixer = AudioMixer()
    fm_sink = FMSink()
    youtube_sink = YouTubeSink()  # Optional
    
    # Wire up data flow
    playout_engine.set_mixer(mixer)
    mixer.add_sink(fm_sink)
    if youtube_enabled:
        mixer.add_sink(youtube_sink)
    
    # Start components
    fm_sink.start()
    if youtube_enabled:
        youtube_sink.start()
    playout_engine.start()
    
    # Main loop: request songs and let playout engine handle it
    while True:
        # Get song recommendation from music_logic
        song = playlist_mgr.select_next_song()
        
        # Check if DJ wants to add segments
        dj_events = dj_engine.should_play_segments(song)
        
        # Queue events in playout engine
        playout_engine.queue_events(dj_events + [song])
        
        # Wait for current queue to finish
        playout_engine.wait_for_queue_empty()
```

---

## Data Flow Diagrams

### Overall System Flow

```
┌─────────────┐
│ music_logic │
│             │──[Song Selection]──┐
└─────────────┘                    │
                                   ▼
┌─────────────┐              ┌──────────────┐
│  dj_logic   │──[DJ Events]─┤broadcast_core│
│             │              │              │
└─────────────┘              │ PlayoutEngine│
                             └──────┬───────┘
                                    │
                           [Event Queue]
                                    │
                                    ▼
                             ┌──────────┐
                             │  mixer/  │
                             │          │
                             │ MP3→PCM  │
                             └────┬─────┘
                                  │
                    ┌─────────────┼─────────────┐
                    │             │             │
                    ▼             ▼             ▼
              ┌─────────┐  ┌──────────┐  ┌──────────┐
              │FMSink   │  │YouTubeSink│  │(Future)  │
              │(PCM)    │  │  (PCM)    │  │  Sink    │
              └─────────┘  └──────────┘  └──────────┘
```

### Song Selection Flow

```
FileManager
    │
    ├─[Regular Files]──┐
    │                  │
    └─[Holiday Files]───┤
                       ▼
              ┌─────────────────┐
              │ PlaylistManager │
              │                 │
              │ • Get history   │
              │ • Calculate     │
              │   probabilities │
              │ • Apply weights │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ ProbabilityEngine│
              │                 │
              │ • Recent penalty │
              │ • Time bonus     │
              │ • Play count     │
              │ • Normalize     │
              └────────┬────────┘
                       │
                       ▼
              [Selected Song]
```

### DJ Decision Flow

```
PlayoutEngine
    │
    ├─[Current Song]──┐
    │                 │
    └─[State]─────────┤
                      ▼
            ┌─────────────────┐
            │   DJEngine      │
            │                 │
            │ 1. Check rules  │
            │ 2. Check cadence│
            │ 3. Match files  │
            └────────┬────────┘
                     │
         ┌───────────┼───────────┐
         │           │           │
         ▼           ▼           ▼
    ┌────────┐ ┌─────────┐ ┌─────────┐
    │ Rules  │ │ Cadence │ │ Matcher │
    │ Engine │ │ Manager │ │         │
    └────────┘ └─────────┘ └─────────┘
         │           │           │
         └───────────┼───────────┘
                     │
                     ▼
            [DJ Event or None]
```

### Audio Processing Flow

```
PlayoutEngine
    │
    ├─[Event: song.mp3]──┐
    │                    │
    └─[Event: intro.mp3]─┤
                         ▼
                ┌──────────────┐
                │ AudioDecoder │
                │              │
                │ MP3 → PCM    │
                │ (FFmpeg pipe)│
                │ stream_frames│
                └──────┬───────┘
                       │
                  [PCM Frames]
                       │
                       ▼
                ┌──────────────┐
                │  AudioMixer  │
                │              │
                │ • Crossfade  │
                │ • Ducking    │
                │ • Normalize  │
                │ push_frame() │
                └──────┬───────┘
                       │
                  [PCM Frames]
                       │
         ┌─────────────┼─────────────┐
         │             │             │
         ▼             ▼             ▼
    ┌─────────┐  ┌──────────┐  ┌──────────┐
    │FMSink   │  │YouTubeSink│  │(Future)  │
    │         │  │          │  │          │
    │write()  │  │ write()  │  │ write()  │
    │ ALSA    │  │  RTMP    │  │  File    │
    └─────────┘  └──────────┘  └──────────┘
```

---

## Event Lifecycle

### Complete Playback Cycle

```
1. SONG SELECTION
   ┌─────────────────────────────────────┐
   │ music_logic/PlaylistManager         │
   │ • Get file lists from FileManager   │
   │ • Calculate probabilities           │
   │ • Select song using weighted random │
   └──────────────┬──────────────────────┘
                  │
                  ▼
2. DJ DECISION
   ┌─────────────────────────────────────┐
   │ dj_logic/DJEngine                   │
   │ • Check if intro should play        │
   │ • Check if outro should play        │
   │ • Match DJ files to song            │
   └──────────────┬──────────────────────┘
                  │
                  ▼
3. EVENT QUEUE BUILDING
   ┌─────────────────────────────────────┐
   │ broadcast_core/PlayoutEngine        │
   │ • Create AudioEvent objects:         │
   │   AudioEvent(path, "intro", 1.0)? →  │
   │   AudioEvent(path, "song", 1.0) →    │
   │   AudioEvent(path, "outro", 1.0)?    │
   │ • queue_event() for each event       │
   │ • run() loop processes queue         │
   └──────────────┬───────────────────────┘
                  │
                  ▼
4. AUDIO DECODING
   ┌─────────────────────────────────────┐
   │ mixer/AudioDecoder                   │
   │ • FFmpeg: -f s16le -ac 2 -ar 48000   │
   │ • Decode MP3 → raw PCM bytes         │
   │ • Stream frame chunks (4096-8192B)   │
   │ • Yield frames to AudioMixer         │
   └──────────────┬───────────────────────┘
                  │
            [PCM Frame Chunks]
                  │
                  ▼
5. AUDIO MIXING
   ┌─────────────────────────────────────┐
   │ mixer/AudioMixer                     │
   │ • Receive frames: push_frame(frame)  │
   │ • Apply crossfade (if transitioning)│
   │ • Apply ducking (if DJ talking)      │
   │ • Normalize volume per frame         │
   │ • Output frame chunks to sinks       │
   └──────────────┬───────────────────────┘
                  │
            [PCM Frame Chunks]
                  │
                  │
         ┌────────┼────────┐
         │        │        │
         ▼        ▼        ▼
6. OUTPUT DELIVERY
   ┌─────────┐ ┌──────────┐ ┌──────────┐
   │FMSink   │ │YouTubeSink│ │(Future)  │
   │         │ │          │ │          │
   │ ALSA    │ │  RTMP    │ │  File    │
   │ Device  │ │  Stream  │ │  Record  │
   └────┬────┘ └────┬─────┘ └────┬─────┘
        │           │            │
        └───────────┼────────────┘
                    │
                    ▼
7. HISTORY UPDATE
   ┌─────────────────────────────────────┐
   │ music_logic/PlaylistManager         │
   │ • Update play history                │
   │ • Increment play count               │
   │ • Update DJ talk counter             │
   └─────────────────────────────────────┘
```

### State Machine Transitions

```
[IDLE]
  │
  ├─ queue_event(intro) ──► [PLAYING_INTRO]
  │
  └─ queue_event(song)  ──► [PLAYING_SONG]

[PLAYING_INTRO]
  │
  └─ intro_finished ──► [PLAYING_SONG]

[PLAYING_SONG]
  │
  ├─ song_finished ──► [IDLE] (if no outro)
  │
  └─ song_finished ──► [PLAYING_OUTRO] (if outro queued)

[PLAYING_OUTRO]
  │
  └─ outro_finished ──► [IDLE]

[TRANSITIONING]
  │
  └─ transition_complete ──► [PLAYING_SONG]
```

---

## Dual-Output Architecture

### Key Design Principles

1. **FMSink is Primary**: Always active, critical path
2. **YouTubeSink is Secondary**: Optional, must not block FM
3. **Shared PCM Stream**: Both sinks consume from same mixer output
4. **Independent Failure**: YouTube failure does not affect FM

### Architecture Diagram

```
                    ┌──────────────┐
                    │ AudioMixer   │
                    │              │
                    │ Outputs PCM  │
                    │   buffers    │
                    └──────┬───────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
            ▼              ▼              ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  FMSink     │ │ YouTubeSink │ │ (Future)    │
    │             │ │             │ │             │
    │ • Always on │ │ • Optional  │ │ • Extensible│
    │ • ALSA      │ │ • RTMP       │ │             │
    │ • Blocking  │ │ • Non-block  │ │             │
    │   errors    │ │   on fail    │ │             │
    │   are fatal │ │ • Auto-      │ │             │
    │             │ │   reconnect  │ │             │
    └─────────────┘ └─────────────┘ └─────────────┘
```

### Failure Handling

#### FMSink Failure
- **Detection**: ALSA device errors, process crashes
- **Response**: Log error, attempt reconnection
- **Impact**: System enters error state (may need restart)
- **Recovery**: Automatic reconnection with exponential backoff

#### YouTubeSink Failure
- **Detection**: Network errors, RTMP handshake failures, process crashes
- **Response**: Log warning, mark as disconnected, continue FM output
- **Impact**: None on FM output
- **Recovery**: Background reconnection thread, periodic health checks

### Implementation Strategy

```python
class AudioMixer:
    def __init__(self):
        self.sinks = []
        self.fm_sink = None  # Primary sink
    
    def add_sink(self, sink):
        if isinstance(sink, FMSink):
            self.fm_sink = sink
        self.sinks.append(sink)
    
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
            except Exception as e:
                # FM sink failure is critical
                logger.critical(f"FM sink error: {e}")
                raise
        
        # Write to other sinks (non-blocking)
        for sink in self.sinks:
            if sink is not self.fm_sink:
                try:
                    sink.write_frame(pcm_frame)
                except Exception as e:
                    # Non-FM sink failures are non-critical
                    logger.warning(f"Sink {sink} error: {e}")
                    # Continue to other sinks
```

### Frame-Based Buffer Management

- **Frame Size**: Typically 4096-8192 bytes per frame (configurable)
- **FMSink**: Synchronous frame writes (blocks until written to ALSA)
  - No internal buffering - direct write to device
  - Frame drops are fatal (indicates device problem)
- **YouTubeSink**: Asynchronous frame writes with internal buffer
  - Internal ring buffer (e.g., 1-2 seconds of audio)
  - If buffer full, drop oldest frames (don't block mixer)
  - Maintains quality when connected, degrades gracefully when disconnected
- **Mixer**: Processes one frame at a time
  - Receives frame from decoder → processes → outputs to all sinks
  - No accumulation of full files in memory

---

## DJ Integration Without Blocking

### Problem Statement

In the current system, DJ logic is embedded in the music playback loop, causing:
- Tight coupling between music and DJ systems
- Blocking operations during DJ segment playback
- Difficulty in extending DJ functionality

### Solution: Event-Based Architecture

DJ system operates independently and provides recommendations that the playout engine can accept or reject.

### Design Pattern

```
┌──────────────┐
│ PlayoutEngine│
│              │
│ State: IDLE  │
└──────┬───────┘
       │
       │ 1. Request DJ recommendation
       ▼
┌──────────────┐
│  DJEngine    │
│              │
│ • Check rules│
│ • Check      │
│   cadence    │
│ • Return     │
│   event or   │
│   None       │
└──────┬───────┘
       │
       │ 2. Return DJEvent or None
       ▼
┌──────────────┐
│ PlayoutEngine│
│              │
│ • If DJEvent:│
│   Queue it   │
│ • Continue   │
│   with song  │
└──────────────┘
```

### Non-Blocking Flow

1. **PlayoutEngine requests DJ recommendation** (non-blocking call)
2. **DJEngine evaluates rules** (fast, no I/O blocking)
3. **DJEngine returns recommendation** (DJEvent or None)
4. **PlayoutEngine queues events** (intro → song → outro)
5. **Audio processing happens asynchronously** (mixer handles decoding)

### DJ Decision Timing

```
Before Song:
  ┌─────────────────┐
  │ PlayoutEngine   │
  │                 │
  │ "Should I play  │
  │  an intro?"     │
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │ DJEngine        │
  │                 │
  │ • Check prob    │
  │ • Check cadence │
  │ • Match files   │
  │                 │
  │ Returns:        │
  │ • DJIntroEvent  │
  │ • None          │
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │ PlayoutEngine   │
  │                 │
  │ Queue:          │
  │ [intro?] → song │
  └─────────────────┘

After Song:
  ┌─────────────────┐
  │ PlayoutEngine   │
  │                 │
  │ "Should I play  │
  │  an outro?"     │
  │ (only if no     │
  │  intro played)  │
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │ DJEngine        │
  │                 │
  │ • Check prob    │
  │ • Check cadence │
  │ • Match files   │
  │                 │
  │ Returns:        │
  │ • DJOutroEvent  │
  │ • None          │
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │ PlayoutEngine   │
  │                 │
  │ Queue:          │
  │ song → [outro?] │
  └─────────────────┘
```

### Benefits

1. **Separation of Concerns**: DJ logic is independent
2. **Non-Blocking**: DJ decisions are fast (no file I/O in decision path)
3. **Extensible**: Easy to add new DJ rules or segment types
4. **Testable**: DJ logic can be tested independently
5. **Flexible**: PlayoutEngine can accept/reject DJ recommendations

---

## Future Scalability

### Extensibility Points

#### 1. Additional Output Sinks

The sink architecture allows easy addition of new outputs:

```
outputs/
├── sink_base.py      # Abstract base class
├── fm_sink.py        # FM transmitter
├── youtube_sink.py   # YouTube Live
├── icecast_sink.py   # Future: Icecast streaming
├── file_sink.py      # Future: Local recording
└── rtmp_sink.py      # Future: Generic RTMP
```

**Implementation**: Inherit from `SinkBase`, implement `write_pcm_frames()`, register with mixer.

#### 2. Advanced Audio Processing

The mixer layer can be extended with:

- **Voice Ducking**: Automatically lower music when DJ talks
- **Audio Effects**: Compression, EQ, reverb
- **Crossfade Types**: Linear, exponential, custom curves
- **Loudness Normalization**: EBU R128, ITU-R BS.1770

#### 3. DJ Rule Extensions

The rules engine can support:

- **Time-of-Day Rules**: Different DJ behavior by hour
- **Genre-Based Rules**: Match DJ segments to song genres
- **Mood-Based Rules**: Match DJ segments to song mood
- **Special Events**: Holiday-specific rules, event calendars
- **External Triggers**: API endpoints for manual DJ segments

#### 4. Playout Features

- **Scheduled Events**: Play specific songs at specific times
- **Live Interruptions**: Pause automation for live content
- **Multiple Playlists**: Different playlists for different times
- **Ad Insertion**: Commercial break support
- **Emergency Alerts**: Weather alerts, EAS integration

#### 5. Monitoring and Analytics

- **Playback Metrics**: Track what's playing, when, for how long
- **DJ Analytics**: Track DJ segment frequency and types
- **Output Health**: Monitor sink status and quality
- **Listener Analytics**: Track YouTube viewer count (if available)
- **Error Tracking**: Comprehensive error logging and alerting

### Performance Considerations

#### Current Bottlenecks (to address)

1. **MP3 Decoding**: Single-threaded, blocking
   - **Solution**: Pre-decode next song in background
   - **Solution**: Use hardware acceleration if available

2. **File I/O**: Directory scanning for DJ files
   - **Solution**: Inotify-based file watching
   - **Solution**: Persistent cache with file system events

3. **YouTube Streaming**: Network latency
   - **Solution**: Already non-blocking (good)
   - **Enhancement**: Adaptive bitrate based on connection

#### Scalability Targets

- **Song Library**: Support 10,000+ songs
- **DJ Files**: Support 1,000+ DJ segments
- **Concurrent Outputs**: Support 5+ simultaneous sinks
- **Latency**: <100ms from event queue to audio output
- **CPU Usage**: <20% on Raspberry Pi 4

### Migration Path

#### Phase 1: Core Refactoring
- Extract music_logic from MusicPlayer
- Create broadcast_core with PlayoutEngine
- Implement mixer layer

#### Phase 2: DJ Separation
- Rewrite DJManager as DJEngine
- Implement rules engine and cadence manager
- Integrate with PlayoutEngine

#### Phase 3: Output Refactoring
- Extract FMSink and YouTubeSink
- Implement sink base class
- Test dual-output reliability

#### Phase 4: Enhancement
- Add advanced audio processing
- Implement monitoring and analytics
- Add extensibility features

---

## Summary

The rearchitected Radio Broadcast System separates concerns into six independent layers:

1. **music_logic/**: Pure selection algorithms, no playback knowledge
2. **dj_logic/**: Independent DJ decision-making system
3. **broadcast_core/**: Non-blocking playout scheduling
4. **mixer/**: Audio processing and format conversion
5. **outputs/**: Independent audio sinks (FM primary, YouTube secondary)
6. **/app/radio.py**: Thin orchestration shell

**Key Benefits:**
- Clear separation of concerns
- Non-blocking architecture
- Resilient dual-output (FM always works)
- Extensible for future features
- Testable components
- Maintainable codebase

**Next Steps:**
1. Review and approve this architecture
2. Begin implementation with music_logic/ extraction
3. Iterate on design as implementation reveals issues
4. Maintain backward compatibility during migration

---

---

## Implementation Notes for Developers

### Critical Implementation Requirements

1. **AudioDecoder MUST use FFmpeg with exact command specified**
   - No fallback to pydub or other libraries
   - Must stream frames, not load full files
   - Frame size: 4096-8192 bytes (configurable constant)

2. **All audio processing MUST be frame-based**
   - Mixer receives frames, processes frames, outputs frames
   - Sinks receive frames, not full PCM buffers
   - No accumulation of full files in memory

3. **PlayoutEngine MUST implement exact interface specified**
   - `queue_event(event: AudioEvent)` - no variations
   - `run()` - main non-blocking loop
   - `current_state() -> PlaybackState` - state query only

4. **AudioEvent MUST be exactly as specified**
   - Dataclass with `path`, `type`, `gain` fields
   - `type` must be Literal["song", "intro", "outro", "talk"]
   - No additional required fields (extensions optional)

### Testing Requirements

- AudioDecoder: Test FFmpeg subprocess spawning and frame streaming
- AudioMixer: Test frame-by-frame processing (not full-file)
- PlayoutEngine: Test interface compliance (exact method signatures)
- Sinks: Test frame-based writes (not buffer accumulation)

---

*Document Version: 1.1*  
*Last Updated: [Current Date]*  
*Author: System Architect (ChatGPT) & Implementation Developer (Cursor)*  
*Refinements: Locked-in FFmpeg decoder, frame-based architecture, explicit interfaces*

