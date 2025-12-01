# Graceful Restart Guide

The Appalachia Radio station supports graceful restarts that wait for the current song to finish before restarting, minimizing stream interruption.

## How It Works

1. **SIGUSR1 Signal**: Sending `SIGUSR1` to the running process triggers a graceful restart
2. **Stop Queuing**: The station stops accepting new songs immediately
3. **Wait for Completion**: The station waits for the current song (and any queued events) to finish playing
4. **Clean Exit**: Once playback is idle, the process exits cleanly
5. **External Restart**: An external process (wrapper script, systemd, etc.) restarts the application

## Manual Restart

### Using the restart script:

```bash
./scripts/restart_gracefully.sh
```

This script:
- Finds the running process via PID file (`/tmp/appalachia-radio.pid`)
- Sends `SIGUSR1` to trigger graceful restart
- The process will exit after the current song finishes

### Using kill directly:

```bash
# Find the PID
cat /tmp/appalachia-radio.pid

# Send restart signal
kill -USR1 $(cat /tmp/appalachia-radio.pid)
```

## Automatic Restart on Code Changes

The `watch_and_restart.sh` script monitors for code changes and automatically restarts:

```bash
./scripts/watch_and_restart.sh
```

**Note**: This requires `inotifywait` (install with `sudo apt-get install inotify-tools`)

## Systemd Service (Recommended for Production)

Create `/etc/systemd/system/appalachia-radio.service`:

```ini
[Unit]
Description=Appalachia Radio Station
After=network.target

[Service]
Type=simple
User=steve
WorkingDirectory=/home/steve/appalachia-radio
Environment="PATH=/usr/bin:/usr/local/bin"
ExecStart=/usr/bin/python3 -m app.radio
Restart=always
RestartSec=5
PIDFile=/tmp/appalachia-radio.pid

# Graceful restart on code deployment
ExecReload=/bin/kill -USR1 $MAINPID

[Install]
WantedBy=multi-user.target
```

Then:

```bash
# Enable and start
sudo systemctl enable appalachia-radio
sudo systemctl start appalachia-radio

# Graceful restart after code changes
sudo systemctl reload appalachia-radio
```

## Important Notes

1. **YouTube Stream**: The YouTube stream will disconnect during restart, but `YouTubeSink` automatically reconnects on startup
2. **FM Stream**: The FM stream will have a brief interruption (typically < 1 second)
3. **Current Song**: The current song will play to completion before restart
4. **State**: No state is preserved between restarts (playlist continues from where it left off naturally)

## Troubleshooting

- **PID file not found**: The process may not be running, or PID file location differs
- **Process not responding**: Check if the process is actually running: `ps aux | grep app.radio`
- **Restart takes too long**: If a very long song is playing, you may need to wait for it to finish

