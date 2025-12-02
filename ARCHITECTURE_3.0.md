# Appalachia Radio – System Architecture 3.0

A DJ-Driven, Event-Timed, Prep-Window-Aware Radio Automation Engine

## 1. Executive Summary

Appalachia Radio 3.0 is a DJ-driven, event-timed, tickler-backed, and cache-based radio automation system designed to behave like a realistic human-run radio station, but with a fully deterministic playout engine.

**The core improvements over 2.0:**

### New in Architecture 3.0

- **The DJ Brain is now the central decision-maker.**
  - Songs do not "just play" — the DJ explicitly chooses every next song.

- **Lifecycle events are now formal:**
  - `on_station_start`
  - `on_station_stop`
  - `on_segment_started`
  - `on_segment_finished`

- **DJ ticklers (deferred prep tasks)** enable asset generation during safe windows.

- **Intro/Outro logic is driven entirely by cached MP3 availability.**
  - The DJ never blocks waiting for ElevenLabs.

- **Prep Window Reality:**
  - The DJ prepares assets during segment playback, not at transitions.

- **Transition Window Reality:**
  - The DJ chooses intros/outros/next songs at segment end, not at start.

The architecture matches real broadcast automation systems like RCS Zetta, ENCO DAD, and WideOrbit.

**The result is a zero dead-air, non-blocking, predictable, and extremely realistic radio automation engine.**

---

## 2. Core Principles (Updated and Final)

These principles supersede and replace prior logic constraints while retaining all audio-level guarantees from Architecture 2.0.

### 2.1 The DJ Is the Brain

The DJ Brain is the sole source of programming decisions:

- selects songs
- selects intros
- selects outros
- schedules talk segments
- inserts station IDs
- determines when to generate new content
- manages pacing, mood, and personality

### 2.2 Playback Engine Is the Metronome

The playback engine fires exact timing events:

- `on_segment_started`
- `on_segment_finished`

These define the real-time rhythm of the station.

### 2.3 Prep Window vs. Transition Window

**Prep Window** (segment start → segment end):
- DJ can perform expensive operations, e.g., ElevenLabs generation, cache cleanup, maintenance.
- No real-time pressure.

**Transition Window** (segment end → immediate next queue):
- DJ must choose next assets instantly.
- No blocking allowed.
- Only cached MP3s may be used.

### 2.4 Ticklers (Deferred DJ Tasks)

Ticklers provide a structured way for the DJ to schedule prep tasks that must run as soon as a prep window becomes available.

### 2.5 Intros & Outros Are Always Discrete MP3s

- No talkover.
- No merging.
- No real-time generation.
- All DJ audio must exist as cached MP3 assets before being used.

### 2.6 Song Choice Happens Only at Segment Finish

The "next song" is not chosen early.
It is chosen right when the previous segment finishes.
This mirrors real DJ spontaneity and keeps the station fluid and reactive.

### 2.7 Frame-Based Audio Pipeline (Unchanged)

The entire decoding/mixing/output path remains frame-based and identical to 2.0:

- FFmpeg decoder → PCM frames
- Mixer → frame-by-frame processing
- FMSink → must never block
- YouTubeSink → best-effort, non-blocking

---

## 3. System Lifecycle Events (New and Final)

### 3.1 on_station_start

Fires once on boot.

**Responsibilities:**
- Load DJ and rotation state
- Choose the first song
- Decide first intro (optional)
- Queue intro → song
- Transition to normal state

### 3.2 on_segment_started(segment)

Fires whenever playback of any segment (intro/song/outro/talk) begins.

**DJ Responsibilities:**
- Enter the Prep Window
- Consume ticklers
- Generate intros/outros for predicted future songs
- Prepare upcoming talk segments
- Refill generic intros/outros
- Maintain cache health
- Update DJ state (mood, energy, counters)

### 3.3 on_segment_finished(segment)

Fires whenever a segment ends.

**DJ Responsibilities (real-time critical):**
- Decide outro for the finished segment
- Choose the next song
- Choose intro for that next song
- Immediately queue: `[outro?] → [intro?] → [song]`
- Schedule ticklers for prep window if new assets are needed later

**Constraints:**
- No blocking allowed.
- No ElevenLabs calls permitted here.

### 3.4 on_station_stop

Fires when the station is shutdown or restarted.

**Responsibilities:**
- Save DJ state
- Save rotation state
- Save intro/outro usage cooldown
- Save ticklers backlog
- Flush playout queue
- Ensure graceful recovery on next boot

---

## 4. DJ Brain Architecture (New, Central Component)

The DJ Brain operates as a stateful autonomous agent.

### 4.1 DJ State Includes:

- rotation history
- last N played songs
- intro/outro cooldowns
- last talk time
- mood state
- block-format schedule (if any)
- generic intro/outro usage
- tickler queue
- predicted next-song candidates
- ElevenLabs job metadata
- daily or hourly personality limits

### 4.2 DJ Ticklers (Deferred Prep Tasks)

Ticklers are action objects queued during `on_segment_finished` and consumed during `on_segment_started`.

**Example ticklers:**
- `GenerateIntro(song_id)`
- `GenerateOutros(song_id)`
- `RefillGenericIntros()`
- `PrepWeatherSegment(slot)`
- `WarmupDJStory("local_fair")`

### 4.3 DJ Prep Window Behavior

Executed during `on_segment_started`:

- Generate missing intros/outros
- Clean stale assets
- Precompute matches
- Pre-generate fallback intros
- Produce weather or announcements
- Refill generic pools
- Prepare upcoming personality bits

### 4.4 DJ Transition Window Behavior

Executed during `on_segment_finished`:

- Pick outro (optional)
- Pick next song
- Pick intro (optional)
- Queue assets immediately
- Register ticklers for missing content

### 4.5 Deterministic Use of Cached Assets

The DJ must not:
- ask ElevenLabs at segment_end
- block playout
- insert variable-length network calls

All DJ audio is pre-generated during prep windows.

---

## 5. Updated Playout Engine Flow (Event-Driven)

The PlayoutEngine remains non-blocking and frame-based.

**New integration points:**
- PlayoutEngine emits `segment_started` and `segment_finished`
- DJEngine must respond synchronously
- DJEngine pushes new AudioEvents immediately after `segment_finished`

**The sequence is:**

```
1. PlayoutEngine starts segment → emits on_segment_started
2. DJ executes prep tasks
3. Segment plays
4. Segment ends → emits on_segment_finished
5. DJ chooses outro → intro → next song
6. DJ queues: [outro?][intro?][song]
7. PlayoutEngine dequeues and continues playback
```

---

## 6. Audio Event Model (Unchanged)

**AudioEvent** remains fully compatible with the 2.0 pipeline:

```python
@dataclass
class AudioEvent:
    path: str              # File path to audio file
    type: Literal["song", "intro", "outro", "talk"]  # Event type
    gain: float = 1.0     # Volume gain multiplier (0.0-1.0)
```

---

## 7. Directory Structure (Updated for DJ Brain and Ticklers)

```
appalachia-radio/
├── app/
│   └── radio.py
├── music_logic/
├── dj_logic/
│   ├── dj_engine.py
│   ├── ticklers.py
│   ├── intro_logic.py
│   ├── outro_logic.py
│   ├── talk_logic.py
│   ├── cache_manager.py
│   ├── rules/
│   ├── cadence/
│   └── scheduler/
├── broadcast_core/
├── mixer/
├── outputs/
└── clock/
```

---

## 8. Cold Start, Warm Start, and Crash Recovery (New)

### 8.1 Cold Start

- Fire `on_station_start`
- Use fallback "first-song" logic
- No outro selection
- No ticklers consumed until first segment start
- No ElevenLabs usage until first prep window

### 8.2 Warm Start (Graceful Restart)

- Restore DJ state
- Continue rotation
- Resume cooldown momentum
- Avoid repeating last played content
- Process saved ticklers
- Begin audio immediately after restart

### 8.3 Crash Recovery

- Fallback to safe first-song selection
- Rebuild minimal DJ state
- Refill caches asynchronously
- DJ should operate with degraded knowledge temporarily

---

## 9. Intro/Outro Decision Model (Fully Updated)

**Intros Chosen At:**
- `on_segment_finished` → before next song plays

**Outros Chosen At:**
- `on_segment_finished` → immediately after the ending song

**Intros/Outros Generated At:**
- `on_segment_started` → during prep window via ticklers only

**Rules:**
- Must be cached MP3s
- Must obey cooldown rules
- Must avoid repetition
- Must fit mood and block type
- Must never be generated in real-time decision flow

---

## 10. Summary of Architecture 3.0

Appalachia Radio now behaves like a real human-operated station:

- The DJ doesn't think during transitions — he decides.
- The DJ doesn't decide during songs — he prepares.
- Playback never blocks.
- ElevenLabs never causes dead air.
- Intros/outros are predictable and human-like.
- Ticklers create deferred, prioritized DJ work tasks.
- Lifecycle events are explicit and easy to reason about.
- Startup, shutdown, and crash recovery are clean.

**This design is permanent and scalable.**

---

*Document Version: 3.0 (DJ-Driven, Event-Timed)*  
*Last Updated: 2025-12-02  
*Author: System Architect & Implementation Developer*
