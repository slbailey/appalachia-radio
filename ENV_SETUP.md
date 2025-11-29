# Environment Configuration Setup

This guide shows how to configure YouTube streaming secrets without hardcoding them in the codebase.

## Option 1: .env File (Recommended)

The application automatically loads environment variables from a `.env` file in the project root.

1. Copy the example file:
```bash
cd /home/pi/appalachia-radio
cp env.example .env
```

2. Edit `.env` with your actual values:
```bash
nano .env
```

3. Fill in your configuration:
```bash
YOUTUBE_STREAM_KEY=your-actual-stream-key-here
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

5. The systemd service is already configured to load from `.env` via `EnvironmentFile`. If you need to update it, ensure this line exists in `appalachia-radio.service`:
```ini
EnvironmentFile=/home/pi/appalachia-radio/.env
```

6. Install python-dotenv if not already installed:
```bash
source venv/bin/activate
pip install python-dotenv
```

7. Reload and restart the service:
```bash
sudo systemctl daemon-reload
sudo systemctl restart appalachia-radio.service
```

**Note:** The `.env` file is automatically loaded by the Python application, so it works even when running manually (not just via systemd).

## Option 2: Environment Variables in Systemd Service

Edit `appalachia-radio.service` and uncomment/add the environment variables:

```ini
[Service]
# ... other settings ...

# YouTube Streaming Configuration
Environment="YOUTUBE_STREAM_KEY=your-actual-stream-key-here"
Environment="YOUTUBE_ENABLED=true"
Environment="YOUTUBE_VIDEO_SOURCE=video"
Environment="YOUTUBE_VIDEO_FILE=/path/to/your/video.mp4"
Environment="YOUTUBE_VIDEO_SIZE=1280x720"
Environment="YOUTUBE_VIDEO_FPS=30"
```

Then reload and restart:
```bash
sudo systemctl daemon-reload
sudo systemctl restart appalachia-radio.service
```

## Option 3: Export Environment Variables (For Testing)

For local testing, you can export variables in your shell:

```bash
export YOUTUBE_STREAM_KEY="your-stream-key"
export YOUTUBE_ENABLED="true"
export YOUTUBE_VIDEO_SOURCE="video"
export YOUTUBE_VIDEO_FILE="/path/to/video.mp4"
python main.py
```

## Available Environment Variables

### Required for YouTube Streaming:
- `YOUTUBE_STREAM_KEY` - Your YouTube stream key (from YouTube Studio)
- `YOUTUBE_ENABLED` - Set to `true` to enable streaming

### Video Configuration:
- `YOUTUBE_VIDEO_SOURCE` - `color`, `image`, `video`, or `none` (default: `color`)
- `YOUTUBE_VIDEO_FILE` - Path to video/image file (required if using `image` or `video`)
- `YOUTUBE_VIDEO_COLOR` - Color for solid color video (default: `black`)
- `YOUTUBE_VIDEO_SIZE` - Resolution like `1280x720` (default: `1280x720`)
- `YOUTUBE_VIDEO_FPS` - Framerate (default: `2`)

### Audio Configuration (Optional):
- `YOUTUBE_AUDIO_DEVICE` - Audio device (default: `default`)
- `YOUTUBE_AUDIO_FORMAT` - `pulse` or `alsa` (default: `pulse`)
- `YOUTUBE_SAMPLE_RATE` - Sample rate in Hz (default: `48000`)
- `YOUTUBE_BITRATE` - Audio bitrate (default: `128k`)

## Security Notes

- **Never commit `.env` files or service files with real secrets to git**
- The `.env` file is already in `.gitignore`
- Use `chmod 600 .env` to restrict file permissions
- Consider using a secrets management system for production deployments

