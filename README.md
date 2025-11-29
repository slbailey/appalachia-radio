# Appalachia Radio

An intelligent music player with smart playlist management, holiday season awareness, and DJ intro/outro support. Designed to run headless on a Raspberry Pi with automatic startup.

## Features

- **Smart Queue-like Playlist**: Recently played songs move to the back of the queue, ensuring variety while maintaining randomness
- **Holiday Season Awareness**: Automatically increases holiday music probability during Nov 1 - Dec 31 (1% to 33%)
- **Dynamic DJ Intro/Outro**: DJ talk probability increases over time (starts at 20%, up to 85% after 8 songs)
- **No Double DJ Talk**: DJ never plays both intro and outro for the same song
- **YouTube Live Streaming**: Simulcast your FM broadcast to YouTube Live (optional)
- **Graceful Shutdown**: Handles SIGTERM/SIGHUP signals for clean termination
- **Comprehensive Logging**: Detailed logging with probability debugging in interactive mode
- **Interactive Testing Mode**: Test locally with keyboard controls (Enter to skip, ESC to exit)

## Requirements

- Python 3.8+
- pygame 2.0.0+
- FFmpeg (for YouTube streaming) - Install with: `sudo apt-get install ffmpeg`

## Installation

1. Clone or download this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Edit `radio/constants.py` to configure paths and settings:

```python
REGULAR_MUSIC_PATH = os.path.expanduser('~/radio/songs')
HOLIDAY_MUSIC_PATH = os.path.expanduser('~/radio/holiday_songs')
DJ_PATH = os.path.expanduser('~/radio/julie')
```

### Tuning Parameters

**Playlist Management:**
- `HISTORY_SIZE`: Number of recent songs to track (default: 48)
- `IMMEDIATE_REPEAT_PENALTY`: Weight for the very last song (default: 0.01 = 1% chance)
- `RECENT_PLAY_WINDOW`: Number of recent songs to apply penalties to (default: 20)
- `RECENT_PLAY_BASE_PENALTY`: Base penalty for recently played songs (default: 0.1 = 10% weight)
- `RECENT_PLAY_DECAY`: How quickly penalty decreases (default: 0.15)
- `NEVER_PLAYED_BONUS`: Weight multiplier for unplayed songs (default: 3.0)
- `MAX_TIME_BONUS`: Maximum time-based weight bonus (default: 2.0)

**DJ Settings:**
- `DJ_BASE_PROBABILITY`: Starting chance to play intro/outro (default: 0.2 = 20%)
- `DJ_MAX_PROBABILITY`: Maximum chance after long silence (default: 0.85 = 85%)
- `DJ_SONGS_BEFORE_INCREASE`: Songs before probability starts increasing (default: 3)
- `DJ_MAX_SONGS_FOR_MAX_PROB`: Songs without DJ talk to reach max probability (default: 8)

**Holiday Settings:**
- Holiday probability automatically calculated based on date (Nov 1: 1%, Dec 25-31: 33%)

**YouTube Streaming Settings:**
- Configure via environment variables (see YouTube Streaming section below)

## Usage

### Production Mode (Headless)

Run the radio player:

```bash
python main.py
```

### Interactive Testing Mode

Test locally with keyboard controls:

```bash
python main.py --interactive --local
```

Or use the helper script:

```bash
./run_test.sh
```

**Interactive Controls:**
- `[ENTER]` - Skip to next song
- `[ESC]` - Exit
- `[Ctrl+C]` - Exit

The player will:
1. Load all MP3 files from the configured directories
2. Select songs based on weighted probabilities (queue-like system)
3. Check if DJ should talk (intro probability)
4. Play intro clip (if selected) before song
5. Play the selected song
6. Check if DJ should talk (outro probability, only if intro didn't play)
7. Play outro clip (if selected) after song
8. Continue indefinitely until interrupted

### Stopping the Player

- Press `Ctrl+C` for graceful shutdown
- Send SIGTERM signal: `kill <pid>`
- In interactive mode: Press `ESC`

## Directory Structure

```
appalachia-radio/
├── radio/
│   ├── __init__.py
│   ├── audio_player.py      # Audio playback handling
│   ├── constants.py         # Configuration constants
│   ├── dj_manager.py        # Intro/outro file management (with caching)
│   ├── file_manager.py      # File operations with directory caching
│   ├── playlist_manager.py  # Playlist logic and probabilities
│   └── radio.py             # Main MusicPlayer class
├── main.py                  # Entry point (supports --interactive and --local)
├── run_test.sh             # Helper script for interactive testing
├── run_main.sh             # Helper script for production mode
├── appalachia-radio.service # Systemd service file for auto-start
├── INSTALL.md              # Raspberry Pi installation guide
├── requirements.txt        # Python dependencies
└── README.md               # This file
```

## DJ Intro/Outro Files

The player looks for intro and outro files in the DJ directory with these naming patterns:

- `songname_intro.mp3` - Single intro file
- `songname_intro1.mp3`, `songname_intro2.mp3`, etc. - Multiple intro files (up to 5)
- `songname_outro.mp3` - Single outro file
- `songname_outro1.mp3`, `songname_outro2.mp3`, etc. - Multiple outro files (up to 5)

**Important:** The DJ will **never** play both intro and outro for the same song. Each song gets either an intro OR an outro, never both.

**Selection Logic:**
1. Before song: Check if DJ should talk (dynamic probability, starts at 20%)
2. If yes and intro files exist: Play a random intro
3. Play the song
4. After song: If intro didn't play, check if DJ should talk again
5. If yes and outro files exist: Play a random outro

If multiple intro/outro files exist for a song, one will be randomly selected.

## Holiday Season Logic

During the holiday season (November 1 - December 31), the player automatically adjusts song selection:

- **November 1**: 1% chance of selecting a holiday song
- **November 1 - December 25**: Linear progression from 1% to 33%
- **December 12-13** (midpoint): ~17% chance
- **December 25-31**: 33% chance (maximum)

**Selection Process:**
1. Calculate holiday probability based on current date
2. Roll random number
3. If roll < probability AND holiday files exist: Pick random holiday song
4. Otherwise: Use weighted selection from regular songs (with queue-like penalties)

This ensures holiday songs are more frequent during the season, but regular music still dominates (67% even at peak season).

## Logging

The player uses Python's logging module with different formats for interactive and production modes.

**Interactive Mode:**
- Clean, color-coded output
- Shows probabilities and random rolls
- Real-time feedback on song selection and DJ decisions
- No timestamps or module names (cleaner output)

**Production Mode:**
- Logs to both file (`logs/radio.log`) and console
- Standard format with timestamps
- All events logged for debugging

**Debug Information Shown:**
- Holiday selection probability and roll
- Selected song type (HOLIDAY or REGULAR)
- DJ probability and songs since last talk
- Intro/outro file availability and rolls
- Whether intro/outro was played or skipped

Log level can be adjusted by modifying the `logging.basicConfig()` call in `main.py`.

## Queue-like Song Selection

The player uses a queue-like system to ensure variety:

- **Last song played**: 1% chance (almost eliminated)
- **Recent songs (last 20)**: Gradual recovery from 10% to 100% weight
- **Older songs (20+ songs ago)**: Full weight, normal selection
- **Never played songs**: 3x bonus to ensure all songs get played

This creates a natural "queue" where recently played songs move to the back, but the system remains random and doesn't enforce strict ordering.

## Performance Optimizations

- **Directory Caching**: File listings are cached for 5 seconds to reduce I/O
- **Automatic Cache Refresh**: Cache invalidates when directories are modified (detects new files)
- **DJ File Caching**: DJ directory listings cached to avoid repeated scans

## Error Handling

The player includes comprehensive error handling:
- Missing directories are logged as warnings
- Missing files are logged as errors
- Playback errors don't crash the player (continues to next song)
- Invalid paths are handled gracefully
- Automatic retry with backoff on consecutive errors (max 10)

## YouTube Live Streaming

The radio player can simulcast your FM broadcast to YouTube Live. The audio from your radio player is captured and streamed to YouTube in real-time.

### Prerequisites

1. **Install FFmpeg** (required for streaming):
   ```bash
   sudo apt-get update
   sudo apt-get install ffmpeg
   ```

2. **Get your YouTube Stream Key**:
   - Go to [YouTube Studio](https://studio.youtube.com)
   - Click "Go Live" or "Stream"
   - In Stream Settings, copy your Stream Key
   - Keep this key secret - it's like a password!

### Configuration

YouTube streaming is configured via environment variables. You can set them in several ways:

**Option 1: .env File (Recommended)**

The application automatically loads environment variables from a `.env` file in the project root.

1. Copy the example file:
```bash
cp env.example .env
```

2. Edit `.env` with your actual values:
```bash
nano .env
```

3. Fill in your configuration (see `env.example` for all options):
```bash
YOUTUBE_STREAM_KEY=your-stream-key-here
YOUTUBE_ENABLED=true
YOUTUBE_VIDEO_SOURCE=video
YOUTUBE_VIDEO_FILE=/path/to/your/video.mp4
YOUTUBE_VIDEO_SIZE=1280x720
YOUTUBE_VIDEO_FPS=30
```

4. Secure the file:
```bash
chmod 600 .env
```

5. Install python-dotenv:
```bash
pip install python-dotenv
```

The `.env` file is automatically loaded when you run the application. See `ENV_SETUP.md` for detailed instructions.

**Option 2: Environment Variables**
```bash
export YOUTUBE_STREAM_KEY="your-stream-key-here"
export YOUTUBE_ENABLED="true"
# ... other variables ...
```

**Option 3: Systemd Service File**
Edit `appalachia-radio.service` and add to the `[Service]` section:
```ini
Environment="YOUTUBE_STREAM_KEY=your-stream-key-here"
Environment="YOUTUBE_ENABLED=true"
```

Or use `EnvironmentFile=/path/to/.env` to load from a file.

### Audio Device Configuration

The streamer captures audio from your system's audio output. The default configuration uses PulseAudio:

- **PulseAudio (default)**: Works on most Linux systems including Raspberry Pi OS
  - Device: `default` (uses default PulseAudio sink)
  - Format: `pulse`
  
- **ALSA**: If you need to use ALSA directly
  - Device: e.g., `hw:0,0` (check with `arecord -l`)
  - Format: `alsa`

To find your audio device:
```bash
# For PulseAudio
pactl list short sinks

# For ALSA
arecord -l
```

### Video Track Configuration

YouTube Live requires a video track, even for audio-only streams. You can configure the video source:

**Video Source Types:**
- `color` (default): Solid color background (minimal resource usage)
- `image`: Static image file (PNG, JPG, etc.) - loops continuously
- `video`: Video file - loops continuously
- `none`: Audio-only (may not work with YouTube)

**Examples:**

Solid color (default - black):
```bash
export YOUTUBE_VIDEO_SOURCE="color"
export YOUTUBE_VIDEO_COLOR="black"
export YOUTUBE_VIDEO_SIZE="1280x720"
export YOUTUBE_VIDEO_FPS="2"
```

Custom color:
```bash
export YOUTUBE_VIDEO_SOURCE="color"
export YOUTUBE_VIDEO_COLOR="#1a1a1a"  # Dark gray hex color
export YOUTUBE_VIDEO_SIZE="1920x1080"  # Full HD
export YOUTUBE_VIDEO_FPS="2"
```

Static image:
```bash
export YOUTUBE_VIDEO_SOURCE="image"
export YOUTUBE_VIDEO_FILE="/path/to/your/logo.png"
export YOUTUBE_VIDEO_SIZE="1280x720"
export YOUTUBE_VIDEO_FPS="2"
```

Video file:
```bash
export YOUTUBE_VIDEO_SOURCE="video"
export YOUTUBE_VIDEO_FILE="/path/to/your/background.mp4"
export YOUTUBE_VIDEO_SIZE="1280x720"
export YOUTUBE_VIDEO_FPS="30"  # Match your video's framerate
```

**Note:** Lower framerates (2-5 fps) use fewer resources and are fine for static content. Use higher framerates (24-30 fps) only if using actual video files.

### Starting the Stream

Once configured, the YouTube stream will automatically start when you run the radio player:

```bash
python main.py
```

The stream will:
- Start automatically when the player starts (with retry logic for network issues)
- Run continuously in the background
- Automatically reconnect if disconnected (checks every 60 seconds)
- Reconnect to the same YouTube Live stream if still active after power outages
- Create a new stream automatically if the old one timed out
- Stop gracefully when the player shuts down

**Important:** Enable "Auto-start" in YouTube Studio (Stream Settings) so that when you reconnect, YouTube automatically starts the live stream without requiring manual intervention.

### Monitoring

Check the logs to verify streaming status:
```bash
tail -f logs/radio.log
```

Look for messages like:
- `YouTube streaming enabled`
- `Starting YouTube stream...`
- `YouTube stream started successfully`

### Troubleshooting

**Stream won't start:**
- Verify FFmpeg is installed: `ffmpeg -version`
- Check that your stream key is correct
- Ensure your internet connection is stable
- Check logs for FFmpeg error messages

**No audio on YouTube:**
- Verify audio is playing locally (check speakers/headphones)
- Check audio device configuration matches your system
- For PulseAudio, ensure audio is being routed to the default sink
- Try different audio devices/formats if needed

**Stream disconnects:**
- Check internet connection stability
- YouTube may disconnect if stream is inactive for too long
- The streamer will attempt to reconnect automatically every 60 seconds
- After power outages, the stream will automatically reconnect on startup
- If the stream timed out, YouTube will create a new stream automatically (if Auto-start is enabled)

**Power outages and reconnection:**
- The application automatically starts streaming when it starts
- It will reconnect to the same YouTube Live stream if it's still active
- If the stream timed out (usually after a few minutes of no data), YouTube will create a new stream
- With Auto-start enabled in YouTube Studio, you don't need to manually start streams after reconnection

### Security Note

**Never commit your YouTube stream key to version control!** 
- The `.env` file is automatically excluded from git (see `.gitignore`)
- Always use environment variables or the `.env` file for secrets
- Use `chmod 600 .env` to restrict file permissions
- See `ENV_SETUP.md` for detailed configuration instructions

## Raspberry Pi Installation

See `INSTALL.md` for detailed instructions on setting up the radio player to run automatically on boot on a Raspberry Pi.

## License

This project is provided as-is for personal use.

