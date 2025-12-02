# Appalachia Radio – System Architecture

## 1. Executive Summary

Appalachia Radio is an automated radio station that plays music with intelligent weighted selection, integrates DJ segments (intros/outros/talk), and simultaneously broadcasts to FM transmitter (primary) and YouTube Live (secondary). The system is designed for reliability: FM output must continue even if YouTube streaming fails.

**Key Capabilities:**
- Weighted random song selection with play history tracking
- Non-blocking DJ segment integration
- Dual output: FM (always active) + YouTube (optional, non-blocking)
- Frame-based audio processing for low latency
- Graceful restart with state persistence

**Architecture Philosophy:**
- Separation of concerns: six independent layers
- Frame-based processing: no full-file loading
- Event-driven playout: queue-based scheduling
- Sink independence: FM failure is fatal, YouTube failure is non-blocking

---

## 2. Core Principles (Non-Negotiable)

1. **Frame-Based Architecture**: All audio processing operates on frame chunks (4096-8192 bytes), never full files
2. **FM Primary**: FM output is the critical path; YouTube is optional and must not block FM
3. **Non-Blocking Design**: DJ decisions and YouTube streaming must not block music playback
4. **Event Queue Model**: All playback is driven by an event queue (intro → song → outro)
5. **Pure Logic Separation**: Music selection and DJ logic are pure functions with no playback knowledge
6. **Explicit Interfaces**: Components communicate via well-defined interfaces, not implementation details

---

## 3. High-Level System Overview

### 3.1 System Layers

```
┌─────────────────────────────────────────┐
│         app/radio.py                    │
│    (Thin Orchestration Shell)           │
└─────────────────┬───────────────────────┘
                  │
    ┌─────────────┼─────────────┐
    │             │             │
    ▼             ▼             ▼
┌─────────┐  ┌─────────┐  ┌──────────────┐
│ music_  │  │ dj_     │  │ broadcast_   │
│ logic/  │  │ logic/  │  │ core/        │
│         │  │         │  │              │
│ Playlist│  │ DJEngine│  │ PlayoutEngine│
│ Manager │  │ Rules   │  │ EventQueue   │
└─────────┘  └────┬────┘  └──────┬───────┘
                  │              │
                  └──────┬────────┘
                         ▼
                  ┌──────────────┐
                  │   mixer/     │
                  │              │
                  │ MP3→PCM      │
                  │ Frame Proc   │
                  └──────┬───────┘
                         │
          ┌───────────────┼───────────────┐
          │               │               │
          ▼               ▼               ▼
    ┌─────────┐    ┌──────────┐    ┌──────────┐
    │ FMSink  │    │YouTubeSink│    │ (Future) │
    │(Primary)│    │(Optional) │    │  Sink    │
    └─────────┘    └──────────┘    └──────────┘
```

### 3.2 Directory Structure

```
appalachia-radio/
├── app/
│   └── radio.py              # Orchestration only
├── music_logic/
│   ├── playlist_manager.py   # Selection & history
│   └── library_manager.py    # File discovery
├── dj_logic/
│   ├── dj_engine.py          # DJ orchestration
│   └── (rules, cadence, matching)
├── broadcast_core/
│   ├── playout_engine.py     # Event scheduler
│   ├── event_queue.py        # Thread-safe queue
│   └── state_machine.py      # Playback states
├── mixer/
│   ├── audio_decoder.py      # FFmpeg MP3→PCM
│   └── audio_mixer.py        # Frame processing
├── outputs/
│   ├── fm_sink.py            # ALSA output
│   ├── youtube_sink.py       # RTMP stream
│   └── sink_base.py          # Abstract base
└── clock/
    └── master_clock.py        # Timing engine
```

### 3.3 Legacy vs. New Architecture

**Legacy System** (`radio/radio.py`):
- Monolithic `MusicPlayer` class
- DJ logic embedded in playback loop
- Tight coupling between components
- Single-threaded blocking operations

**New Architecture**:
- Six independent layers with clear boundaries
- DJ logic as separate recommendation system
- Event-driven, non-blocking design
- Frame-based processing for low latency

---

## 4. Locked-In Architecture Decisions

**These decisions are final and must be implemented exactly as specified.**

### 4.1 Audio Decoder: FFmpeg Pipe Output

**Decision**: Use FFmpeg subprocess with raw PCM pipe output.

**Exact Implementation**:
```bash
ffmpeg -i input.mp3 -f s16le -ac 2 -ar 48000 pipe:1
```
- `-f s16le`: 16-bit signed little-endian PCM
- `-ac 2`: Stereo (2 channels)
- `-ar 48000`: 48kHz sample rate
- `pipe:1`: Output raw PCM bytes to stdout

**Interface**: Streaming generator that yields PCM frame chunks (4096-8192 bytes)

**Rationale**: Stable, low CPU, works with all formats, perfect for live streaming

**Rejected**: ❌ pydub (loads entire file), ❌ python-vlc (unpredictable latency)

### 4.2 Frame-Based Architecture

**Decision**: Entire audio pipeline operates on frame chunks, not full files.

**Flow**:
- Decoder: `for frame in decoder.stream_frames(): yield frame`
- Mixer: `mixer.push_frame(frame)` → processes → outputs to sinks
- Sinks: `sink.write_frame(frame)` receives frame chunks

**Frame Size**: 4096-8192 bytes (~46-92ms of audio at 48kHz stereo)

**Benefits**: Low latency, memory efficient, real-time processing

### 4.3 PlayoutEngine Interface

**Exact Interface** (must match exactly):
```python
class PlayoutEngine:
    def queue_event(self, event: AudioEvent) -> None:
        """Add an audio event to the playout queue."""
        
    def run(self) -> None:
        """Main non-blocking loop that processes events."""
        
    def current_state(self) -> PlaybackState:
        """Get current playback state."""
        
    def is_idle(self) -> bool:
        """Check if engine is idle (no events playing)."""
```

### 4.4 AudioEvent Definition

**Exact Structure** (must match exactly):
```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class AudioEvent:
    path: str              # File path to audio file
    type: Literal["song", "intro", "outro", "talk"]  # Event type
    gain: float = 1.0     # Volume gain multiplier (0.0-1.0)
```

### 4.5 SinkBase Interface

**Exact Interface** (must match exactly):
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

---

## 5. System Components & Responsibilities

### 5.1 Music Logic Layer (`music_logic/`)

**Purpose**: Pure music selection logic with no knowledge of playback or DJ.

**Components**:

#### PlaylistManager
- Maintains play history (last N songs with timestamps)
- Tracks play counts per song
- Calculates weighted probabilities for selection
- Handles holiday season detection
- **No direct file I/O** (receives file lists from LibraryManager)

#### LibraryManager
- Discovers music files (regular + holiday)
- Provides file lists to PlaylistManager
- Handles directory scanning and caching

**Key Principle**: Pure functions—take inputs (file lists, history) and return selection probabilities. No side effects.

### 5.2 DJ Logic Layer (`dj_logic/`)

**Purpose**: Independent system for DJ segment decision-making.

**Components**:

#### DJEngine
- Main orchestrator for DJ system
- Decides when to play intros, outros, or talk segments
- Manages DJ file discovery and caching
- Provides non-blocking API: "Should I play a DJ segment now?"

#### Rules Engine
- Dynamic probability (increases over time)
- Track attribute matching (genre, mood, etc.)
- Time-of-day rules
- Returns recommendations, not commands

#### Cadence Manager
- Minimum time between DJ segments
- Maximum DJ segment frequency
- Intro vs outro preference logic

#### Track Matcher
- Matches DJ files to songs
- Multiple variant support (intro1, intro2, etc.)
- Fallback logic
- Caches file lists for performance

**Key Principle**: DJ logic is completely separate from music playback. Provides recommendations that playout engine can accept or reject.

### 5.3 Playout Core (`broadcast_core/`)

**Purpose**: Non-blocking playout scheduling and state management.

**Components**:

#### PlayoutEngine
- Manages queue of audio events (intro → song → outro)
- Non-blocking scheduler that continuously processes events
- Coordinates with mixer for audio delivery (frame-by-frame)
- Handles event timing and transitions

#### EventQueue
- Thread-safe queue of `AudioEvent` objects
- Event types: `"song"`, `"intro"`, `"outro"`, `"talk"`
- Priority handling (DJ segments can interrupt)

#### StateMachine
- Manages playback state transitions:
  - `IDLE`: No audio playing
  - `PLAYING_INTRO`: Playing DJ intro segment
  - `PLAYING_SONG`: Playing music track
  - `PLAYING_OUTRO`: Playing DJ outro segment
  - `TRANSITIONING`: Crossfading between tracks
  - `ERROR`: Error state
- Ensures valid state sequences
- Provides state change callbacks

**Key Principle**: PlayoutEngine is a scheduler, not a player. It doesn't decode audio or output sound—it manages what should play when.

### 5.4 Mixer Layer (`mixer/`)

**Purpose**: Audio processing and format conversion.

**Components**:

#### AudioDecoder
- **Implementation**: FFmpeg subprocess with pipe output (LOCKED IN)
- **Command**: `ffmpeg -i input.mp3 -f s16le -ac 2 -ar 48000 pipe:1`
- **Interface**: Streaming generator that yields PCM frame chunks
- **Frame Size**: 4096-8192 bytes per frame (configurable)
- **Error Handling**: Handles corrupted files, missing files, FFmpeg process failures

#### AudioMixer
- **Frame-Based Architecture**: Processes and outputs PCM frames/chunks
- **Streaming Interface**:
  - Receives frames from decoder: `mixer.push_frame(frame)`
  - Outputs frames to sinks: `sink.write_frame(frame)`
- **Frame Processing**:
  - Mixes multiple audio sources (future: voice ducking)
  - Applies crossfade between tracks (frame-by-frame)
  - Handles volume normalization per frame
- **Continuous Stream**: Outputs frame chunks continuously, maintaining low latency

**Key Principle**: Mixer is the only component that touches audio files. All other components work with metadata and file paths.

### 5.5 Output Sinks (`outputs/`)

**Purpose**: Audio output sinks that consume PCM streams.

**Components**:

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
- Handles common functionality:
  - Frame buffer management
  - Error recovery
  - Status reporting
- Allows future sinks (e.g., Icecast, local recording)

**Key Principle**: Sinks are independent consumers. FMSink failure is fatal, YouTubeSink failure is non-blocking.

### 5.6 Orchestration Layer (`app/radio.py`)

**Purpose**: Thin orchestration shell that wires everything together.

**Responsibilities**:
- Initialize all components
- Wire up data flow between layers
- Handle system signals (SIGTERM, SIGUSR1 for graceful restart)
- Provide main event loop
- Logging and monitoring coordination
- PID file management
- State persistence (playlist history)

**What it does NOT do**:
- ❌ No business logic
- ❌ No audio processing
- ❌ No file I/O (beyond initialization)
- ❌ No state management

---

## 6. Playback Lifecycle + Event Flow

### 6.1 Complete Playback Cycle

```
1. SONG SELECTION
   music_logic/PlaylistManager
   • Get file lists from LibraryManager
   • Calculate probabilities
   • Select song using weighted random
   │
   ▼
2. DJ DECISION
   dj_logic/DJEngine
   • Check if intro should play
   • Check if outro should play
   • Match DJ files to song
   │
   ▼
3. EVENT QUEUE BUILDING
   broadcast_core/PlayoutEngine
   • Create AudioEvent objects:
     AudioEvent(path, "intro", 1.0)? →
     AudioEvent(path, "song", 1.0) →
     AudioEvent(path, "outro", 1.0)?
   • queue_event() for each event
   • run() loop processes queue
   │
   ▼
4. AUDIO DECODING
   mixer/AudioDecoder
   • FFmpeg: -f s16le -ac 2 -ar 48000
   • Decode MP3 → raw PCM bytes
   • Stream frame chunks (4096-8192B)
   • Yield frames to AudioMixer
   │
   ▼
5. AUDIO MIXING
   mixer/AudioMixer
   • Receive frames: push_frame(frame)
   • Apply crossfade (if transitioning)
   • Apply ducking (if DJ talking)
   • Normalize volume per frame
   • Output frame chunks to sinks
   │
   ▼
6. OUTPUT DELIVERY
   outputs/FMSink + YouTubeSink
   • FMSink: ALSA device (always)
   • YouTubeSink: RTMP stream (optional)
   │
   ▼
7. HISTORY UPDATE
   music_logic/PlaylistManager
   • Update play history
   • Increment play count
   • Save state to disk (for graceful restart)
```

### 6.2 State Machine Transitions

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

### 6.3 Data Flow Diagram

```
┌─────────────┐
│ music_logic │──[Song Selection]──┐
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

---

## 7. Audio Pipeline & PCM Frame Architecture

### 7.1 Frame-Based Processing Flow

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
                  (4096-8192B)
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

### 7.2 Frame Size & Timing

- **Frame Size**: 4096-8192 bytes per frame
- **Duration**: ~46-92ms of audio at 48kHz stereo
- **Processing**: One frame per MasterClock tick (~21.333ms for 4096-byte frames)
- **Memory**: Only small buffers in memory (no full-file loading)

### 7.3 Frame Buffer Management

- **FMSink**: Synchronous frame writes (blocks until written to ALSA)
  - No internal buffering—direct write to device
  - Frame drops are fatal (indicates device problem)
- **YouTubeSink**: Asynchronous frame writes with internal buffer
  - Internal ring buffer (e.g., 1-2 seconds of audio)
  - If buffer full, drop oldest frames (don't block mixer)
  - Maintains quality when connected, degrades gracefully when disconnected
- **Mixer**: Processes one frame at a time
  - Receives frame from decoder → processes → outputs to all sinks
  - No accumulation of full files in memory

---

## 8. Dual Output Redundancy Model (FM Primary, YouTube Secondary)

### 8.1 Design Principles

1. **FMSink is Primary**: Always active, critical path
2. **YouTubeSink is Secondary**: Optional, must not block FM
3. **Shared PCM Stream**: Both sinks consume from same mixer output
4. **Independent Failure**: YouTube failure does not affect FM

### 8.2 Architecture

```
                    ┌──────────────┐
                    │ AudioMixer   │
                    │              │
                    │ Outputs PCM  │
                    │   frames     │
                    └──────┬───────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
            ▼              ▼              ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  FMSink     │ │ YouTubeSink │ │ (Future)    │
    │             │ │             │ │             │
    │ • Always on │ │ • Optional  │ │ • Extensible│
    │ • ALSA      │ │ • RTMP      │ │             │
    │ • Blocking  │ │ • Non-block │ │             │
    │   errors    │ │   on fail   │ │             │
    │   are fatal │ │ • Auto-     │ │             │
    │             │ │   reconnect │ │             │
    └─────────────┘ └─────────────┘ └─────────────┘
```

### 8.3 Failure Handling

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

### 8.4 Implementation Pattern

```python
class AudioMixer:
    def push_frame(self, pcm_frame: bytes) -> None:
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

---

## 9. DJ Integration Model (Non-Blocking, Plan-Per-Song)

### 9.1 Problem Statement

DJ logic must not block music playback. Decisions should be fast (no file I/O in decision path) and recommendations should be optional (playout engine can accept/reject).

### 9.2 Design Pattern

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

### 9.3 Non-Blocking Flow

1. **PlayoutEngine requests DJ recommendation** (non-blocking call)
2. **DJEngine evaluates rules** (fast, no I/O blocking)
3. **DJEngine returns recommendation** (DJEvent or None)
4. **PlayoutEngine queues events** (intro → song → outro)
5. **Audio processing happens asynchronously** (mixer handles decoding)

### 9.4 DJ Decision Timing

**Before Song:**
```
PlayoutEngine → "Should I play an intro?"
DJEngine → Check prob, cadence, match files
Returns: DJIntroEvent or None
PlayoutEngine → Queue: [intro?] → song
```

**After Song:**
```
PlayoutEngine → "Should I play an outro?" (only if no intro played)
DJEngine → Check prob, cadence, match files
Returns: DJOutroEvent or None
PlayoutEngine → Queue: song → [outro?]
```

### 9.5 Benefits

- **Separation of Concerns**: DJ logic is independent
- **Non-Blocking**: DJ decisions are fast (no file I/O in decision path)
- **Extensible**: Easy to add new DJ rules or segment types
- **Testable**: DJ logic can be tested independently
- **Flexible**: PlayoutEngine can accept/reject DJ recommendations

---

## 10. Future Scalability Targets

### 10.1 Performance Targets

- **Song Library**: Support 10,000+ songs
- **DJ Files**: Support 1,000+ DJ segments
- **Concurrent Outputs**: Support 5+ simultaneous sinks
- **Latency**: <100ms from event queue to audio output
- **CPU Usage**: <20% on Raspberry Pi 4

### 10.2 Extensibility Points

#### Additional Output Sinks
- Icecast streaming
- Local file recording
- Generic RTMP sink
- **Implementation**: Inherit from `SinkBase`, implement `write_frame()`, register with mixer

#### Advanced Audio Processing
- Voice ducking (automatically lower music when DJ talks)
- Audio effects (compression, EQ, reverb)
- Crossfade types (linear, exponential, custom curves)
- Loudness normalization (EBU R128, ITU-R BS.1770)

#### DJ Rule Extensions
- Time-of-day rules (different DJ behavior by hour)
- Genre-based rules (match DJ segments to song genres)
- Mood-based rules (match DJ segments to song mood)
- Special events (holiday-specific rules, event calendars)
- External triggers (API endpoints for manual DJ segments)

#### Playout Features
- Scheduled events (play specific songs at specific times)
- Live interruptions (pause automation for live content)
- Multiple playlists (different playlists for different times)
- Ad insertion (commercial break support)
- Emergency alerts (weather alerts, EAS integration)

#### Monitoring and Analytics
- Playback metrics (track what's playing, when, for how long)
- DJ analytics (track DJ segment frequency and types)
- Output health (monitor sink status and quality)
- Listener analytics (track YouTube viewer count if available)
- Error tracking (comprehensive error logging and alerting)

### 10.3 Performance Considerations

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

---

## 11. Developer Implementation Requirements

### 11.1 Critical Implementation Requirements

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
   - `is_idle() -> bool` - idle check

4. **AudioEvent MUST be exactly as specified**
   - Dataclass with `path`, `type`, `gain` fields
   - `type` must be `Literal["song", "intro", "outro", "talk"]`
   - No additional required fields (extensions optional)

5. **SinkBase MUST implement exact interface specified**
   - `write_frame(pcm_frame: bytes) -> None`
   - `start() -> bool`
   - `stop() -> None`

### 11.2 Testing Requirements

- **AudioDecoder**: Test FFmpeg subprocess spawning and frame streaming
- **AudioMixer**: Test frame-by-frame processing (not full-file)
- **PlayoutEngine**: Test interface compliance (exact method signatures)
- **Sinks**: Test frame-based writes (not buffer accumulation)
- **DJ Engine**: Test non-blocking decision logic
- **State Machine**: Test valid state transitions

### 11.3 Implementation Checklist

#### Phase 1: Core Refactoring ✅
- [x] Extract music_logic from MusicPlayer
- [x] Create broadcast_core with PlayoutEngine
- [x] Implement mixer layer
- [x] Implement frame-based audio processing

#### Phase 2: DJ Separation ✅
- [x] Rewrite DJManager as DJEngine
- [x] Implement rules engine and cadence manager
- [x] Integrate with PlayoutEngine

#### Phase 3: Output Refactoring ✅
- [x] Extract FMSink and YouTubeSink
- [x] Implement sink base class
- [x] Test dual-output reliability

#### Phase 4: Enhancement
- [ ] Add advanced audio processing
- [ ] Implement monitoring and analytics
- [ ] Add extensibility features
- [ ] Performance optimization

#### Phase 5: Graceful Restart & State Persistence ✅
- [x] Implement graceful restart (SIGUSR1)
- [x] Add playlist state persistence
- [x] Fix PID file path for systemd
- [x] Suppress buffer underrun warnings during shutdown

---

## Summary

The Appalachia Radio system separates concerns into six independent layers:

1. **music_logic/**: Pure selection algorithms, no playback knowledge
2. **dj_logic/**: Independent DJ decision-making system
3. **broadcast_core/**: Non-blocking playout scheduling
4. **mixer/**: Audio processing and format conversion
5. **outputs/**: Independent audio sinks (FM primary, YouTube secondary)
6. **app/radio.py**: Thin orchestration shell

**Key Benefits:**
- Clear separation of concerns
- Non-blocking architecture
- Resilient dual-output (FM always works)
- Extensible for future features
- Testable components
- Maintainable codebase

**Locked-In Decisions:**
- FFmpeg pipe decoder (exact command specified)
- Frame-based architecture (no full-file loading)
- Explicit interfaces (PlayoutEngine, AudioEvent, SinkBase)
- FM primary, YouTube secondary (failure model)

---

*Document Version: 2.0 (Refactored)*  
*Last Updated: 2025-12-01*  
*Author: System Architect & Implementation Developer*
