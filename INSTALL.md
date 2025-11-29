# Installation Guide for Raspberry Pi

This guide will help you set up Appalachia Radio to run automatically on boot on your Raspberry Pi.

## Prerequisites

- Raspberry Pi with audio output configured
- Python 3.8 or higher
- Music files in the configured directories

## Step 1: Install the Application

1. Copy the application to your Pi (e.g., `/home/pi/appalachia-radio`)
2. Navigate to the directory:
   ```bash
   cd /home/pi/appalachia-radio
   ```

3. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

4. Install dependencies:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

## Step 2: Configure Paths

Edit `radio/constants.py` to set your music directories:

**Option 1: Via `.env` file (recommended):**

Add to your `.env` file:
```bash
REGULAR_MUSIC_PATH=~/source/appalachia-radio/songs
HOLIDAY_MUSIC_PATH=~/source/appalachia-radio/holiday_songs
DJ_PATH=~/source/appalachia-radio/julie
```

Or use absolute paths:
```bash
REGULAR_MUSIC_PATH=/home/pi/music/songs
HOLIDAY_MUSIC_PATH=/home/pi/music/holiday_songs
DJ_PATH=/home/pi/music/julie
```

**Option 2: Edit `radio/constants.py` directly:**

```python
REGULAR_MUSIC_PATH = os.path.expanduser('~/radio/songs')
HOLIDAY_MUSIC_PATH = os.path.expanduser('~/radio/holiday_songs')
DJ_PATH = os.path.expanduser('~/radio/julie')
```

Or use absolute paths:
```python
REGULAR_MUSIC_PATH = '/home/pi/music/songs'
HOLIDAY_MUSIC_PATH = '/home/pi/music/holiday_songs'
DJ_PATH = '/home/pi/music/julie'
```

Paths can use `~` for home directory expansion. If not set in `.env`, defaults are used.

## Step 3: Test the Application

Test that it works before setting up auto-start:

```bash
source venv/bin/activate
python main.py
```

Press `Ctrl+C` to stop. If it works, proceed to the next step.

## Step 4: Set Up Systemd Service

1. Edit the service file to match your setup:
   ```bash
   nano appalachia-radio.service
   ```
   
   Update these lines if needed:
   - `User=pi` - Change if using a different user
   - `WorkingDirectory=/home/pi/appalachia-radio` - Update to your path
   - `ExecStart=/home/pi/appalachia-radio/venv/bin/python` - Update to your path

2. Copy the service file to systemd:
   ```bash
   sudo cp appalachia-radio.service /etc/systemd/system/
   ```

3. Reload systemd:
   ```bash
   sudo systemctl daemon-reload
   ```

4. Enable the service to start on boot:
   ```bash
   sudo systemctl enable appalachia-radio.service
   ```

5. Start the service:
   ```bash
   sudo systemctl start appalachia-radio.service
   ```

## Step 5: Verify It's Running

Check the status:
```bash
sudo systemctl status appalachia-radio.service
```

View logs:
```bash
# Systemd logs
sudo journalctl -u appalachia-radio.service -f

# Application logs
tail -f /home/pi/appalachia-radio/logs/radio.log
```

## Managing the Service

**Stop the service:**
```bash
sudo systemctl stop appalachia-radio.service
```

**Start the service:**
```bash
sudo systemctl start appalachia-radio.service
```

**Restart the service:**
```bash
sudo systemctl restart appalachia-radio.service
```

**Disable auto-start on boot:**
```bash
sudo systemctl disable appalachia-radio.service
```

**View recent logs:**
```bash
sudo journalctl -u appalachia-radio.service -n 50
```

## Troubleshooting

### Service won't start
- Check the service status: `sudo systemctl status appalachia-radio.service`
- Check logs: `sudo journalctl -u appalachia-radio.service`
- Verify paths in the service file are correct
- Make sure the virtual environment exists and has pygame installed

### No audio output
- Verify audio is configured: `aplay /usr/share/sounds/alsa/Front_Left.wav`
- Check if user has audio permissions (may need to add user to `audio` group)
- Verify pygame can access audio devices

### Service keeps restarting
- Check the application logs: `tail -f /home/pi/appalachia-radio/logs/radio.log`
- Verify music directories exist and contain MP3 files
- Check file permissions on music directories

## Audio Configuration for Raspberry Pi

If you're using a USB audio device or HDMI audio:

1. List audio devices:
   ```bash
   aplay -l
   ```

2. Set default audio device in `/etc/asound.conf` or `~/.asoundrc`

3. Test audio:
   ```bash
   speaker-test -t wav -c 2
   ```

The radio player should work with the default ALSA configuration.

