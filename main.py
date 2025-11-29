#!/usr/bin/env python3
"""
Main entry point for Appalachia Radio - runs headless on Raspberry Pi.

This module provides the main entry point for the Appalachia Radio application.
It handles:
- Command-line argument parsing
- Logging configuration (interactive vs. headless)
- Environment variable loading (.env file)
- MusicPlayer initialization
- YouTube streaming startup and monitoring
- Main playback loop with error handling
- Graceful shutdown handling

The application can run in two modes:
- Interactive mode: Colored console output with keyboard controls
- Headless mode: Logs to file, suitable for systemd service

Example:
    ```bash
    # Interactive mode
    python3 main.py --interactive
    
    # Headless mode (default)
    python3 main.py
    
    # Use local directories for testing
    python3 main.py --local
    ```
"""

import argparse
import logging
import os
import select
import signal
import sys
import threading
import time
from pathlib import Path

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    # Load .env file from project root
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
        logging.getLogger(__name__).debug(f"Loaded environment variables from {env_path}")
except ImportError:
    # python-dotenv not installed, skip .env loading
    pass

from radio.radio import MusicPlayer
from radio.constants import REGULAR_MUSIC_PATH, HOLIDAY_MUSIC_PATH, DJ_PATH

# ANSI color codes for prettier output
class Colors:
    """
    ANSI color codes for terminal output.
    
    Provides color constants for prettier console output in interactive mode.
    All colors can be combined with RESET to return to default terminal colors.
    """
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'

class ColoredFormatter(logging.Formatter):
    """
    Custom logging formatter with colors for console output.
    
    This formatter adds ANSI color codes to log messages based on log level,
    making it easier to scan output in interactive mode. It also provides
    special formatting for certain message types (e.g., "Playing:" messages).
    
    In interactive mode, it uses a simplified format (message only, no timestamps).
    In non-interactive mode, it uses standard logging format with timestamps.
    """
    
    COLORS = {
        'DEBUG': Colors.DIM + Colors.WHITE,
        'INFO': Colors.CYAN,
        'WARNING': Colors.YELLOW,
        'ERROR': Colors.RED,
        'CRITICAL': Colors.BOLD + Colors.RED,
    }
    
    def __init__(self, *args, **kwargs):
        # For interactive mode, use message-only format
        # For non-interactive, use standard format
        if 'fmt' not in kwargs:
            kwargs['fmt'] = '%(message)s'
        super().__init__(*args, **kwargs)
    
    def format(self, record):
        # Format the message
        if hasattr(self, '_is_interactive') and self._is_interactive:
            # For interactive mode, just return the message directly (no timestamps, no module names)
            msg = record.getMessage()
            levelname = record.levelname
            
            # Prettier format for interactive mode
            if levelname == 'INFO' and 'Playing:' in msg:
                song_name = msg.replace('Playing: ', '')
                return f"{Colors.GREEN}▶ {Colors.RESET}{Colors.BOLD}{song_name}{Colors.RESET}\r\n"
            elif levelname == 'WARNING':
                return f"{Colors.YELLOW}⚠ {msg}{Colors.RESET}\r\n"
            elif levelname == 'ERROR':
                return f"{Colors.RED}✗ {msg}{Colors.RESET}\r\n"
            elif levelname == 'INFO':
                # Just return the message as-is with proper line ending
                return f"{msg}\r\n"
            else:
                # Add color to levelname for other levels
                if levelname in self.COLORS:
                    colored_level = f"{self.COLORS[levelname]}{levelname}{Colors.RESET}"
                    return f"{colored_level} {msg}\r\n"
                return f"{msg}\r\n"
        else:
            # Standard format for file/non-interactive - use parent formatter
            return super().format(record)

# Global flags for interactive keyboard control
skip_song = False
exit_requested = False
player_instance = None

def read_keyboard():
    """
    Read keyboard input in a separate thread for interactive mode.
    
    This function runs in a background thread and monitors keyboard input
    for interactive controls:
    - Enter/Return: Skip to next song
    - ESC: Exit application
    - Ctrl+C: Exit application
    
    The function uses raw terminal mode to capture single keystrokes without
    requiring Enter to be pressed. It properly restores terminal settings
    on exit.
    
    Note:
        - Runs as a daemon thread (terminates when main thread exits)
        - Uses termios for terminal control (Unix/Linux only)
        - Sets global flags (skip_song, exit_requested) for main loop
        - Stops audio playback when skip is requested
    """
    global skip_song, exit_requested, player_instance
    
    import termios
    import tty
    
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    
    try:
        tty.setraw(sys.stdin.fileno())
        
        while not exit_requested:
            if select.select([sys.stdin], [], [], 0.1)[0]:
                char = sys.stdin.read(1)
                
                if char == '\n' or char == '\r':  # Enter key
                    print(f"\n{Colors.YELLOW}[ENTER] Skipping to next song...{Colors.RESET}\n", flush=True)
                    skip_song = True
                    if player_instance:
                        player_instance.audio_player.stop()
                elif char == '\x1b':  # ESC key
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        sys.stdin.read(1)  # Read rest of escape sequence
                    else:
                        print(f"\n{Colors.YELLOW}[ESC] Exiting...{Colors.RESET}\n", flush=True)
                        exit_requested = True
                        skip_song = True
                        if player_instance:
                            player_instance.audio_player.stop()
                elif char == '\x03':  # Ctrl+C
                    print(f"\n{Colors.YELLOW}[Ctrl+C] Exiting...{Colors.RESET}\n", flush=True)
                    exit_requested = True
                    skip_song = True
                    if player_instance:
                        player_instance.audio_player.stop()
                    break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def setup_logging(interactive: bool = False, log_file: Path = None):
    """
    Setup logging with appropriate formatters for the application.
    
    This function configures Python's logging system with:
    - File handler (if log_file provided): Standard format with timestamps
    - Console handler: Colored output in interactive mode, standard in headless
    
    Args:
        interactive: If True, uses colored formatter for console output.
                     If False, uses standard formatter with timestamps.
        log_file: Optional path to log file. If provided, logs are written
                  to both console and file. If None, logs only to console.
                  
    Note:
        - Log level is set to INFO by default
        - File logs always use standard format (with timestamps)
        - Console logs use colored format in interactive mode
        - Creates log directory if it doesn't exist
    """
    handlers = []
    
    # File handler (always, with standard format)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        handlers.append(file_handler)
    
    # Console handler (with colors if interactive)
    console_handler = logging.StreamHandler(sys.stdout)
    if interactive:
        formatter = ColoredFormatter(fmt='%(message)s')
        formatter._is_interactive = True
        console_handler.setFormatter(formatter)
    else:
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
    handlers.append(console_handler)
    
    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers
    )

def main() -> None:
    """
    Main entry point for the radio player application.
    
    This function orchestrates the entire application lifecycle:
    1. Parse command-line arguments
    2. Determine music directory paths
    3. Setup logging (interactive vs. headless)
    4. Initialize MusicPlayer
    5. Register signal handlers for graceful shutdown
    6. Start YouTube streaming (if enabled) with retry logic
    7. Start keyboard input thread (if interactive mode)
    8. Run main playback loop with:
       - Periodic YouTube stream health checks
       - Automatic reconnection on failures
       - Error handling and recovery
       - Consecutive error tracking
    
    The main loop continues until:
    - User interrupts (Ctrl+C, ESC in interactive mode)
    - Too many consecutive errors occur
    - Exit is requested via signal
    
    Command-line arguments:
        --interactive, -i: Enable interactive mode with keyboard controls
        --local: Use local project directories instead of configured paths
    
    Note:
        - Loads environment variables from .env file if present
        - Handles SIGTERM and SIGHUP for systemd compatibility
        - Implements exponential backoff for YouTube reconnection
        - Logs all errors with full tracebacks for debugging
        - Performs graceful shutdown on exit
    """
    global skip_song, exit_requested, player_instance
    
    parser = argparse.ArgumentParser(description='Appalachia Radio - Continuous Music Player')
    parser.add_argument(
        '--interactive', '-i',
        action='store_true',
        help='Enable interactive mode with keyboard controls (Enter to skip, ESC to exit)'
    )
    parser.add_argument(
        '--local',
        action='store_true',
        help='Use local project directories instead of configured paths (for testing)'
    )
    args = parser.parse_args()
    
    # Determine paths
    if args.local:
        project_root = Path(__file__).parent
        regular_music_path = str(project_root / 'songs')
        holiday_music_path = str(project_root / 'holiday_songs')
        dj_path = str(project_root / 'julie')
    else:
        regular_music_path = REGULAR_MUSIC_PATH
        holiday_music_path = HOLIDAY_MUSIC_PATH
        dj_path = DJ_PATH
    
    # Setup logging
    project_root = Path(__file__).parent
    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / 'radio.log' if not args.interactive else None
    
    setup_logging(interactive=args.interactive, log_file=log_file)
    logger = logging.getLogger(__name__)
    
    # Startup banner
    if args.interactive:
        print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}  Appalachia Radio - Interactive Mode{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.RESET}")
        print(f"{Colors.DIM}Regular music: {regular_music_path}{Colors.RESET}")
        print(f"{Colors.DIM}Holiday music: {holiday_music_path}{Colors.RESET}")
        print(f"{Colors.DIM}DJ path: {dj_path}{Colors.RESET}")
        print(f"\n{Colors.BOLD}Controls:{Colors.RESET}")
        print(f"  {Colors.GREEN}[ENTER]{Colors.RESET} - Skip to next song")
        print(f"  {Colors.GREEN}[ESC]{Colors.RESET}   - Exit")
        print(f"  {Colors.GREEN}[Ctrl+C]{Colors.RESET} - Exit")
        print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.RESET}\n")
    else:
        logger.info("=" * 60)
        logger.info("Appalachia Radio - Starting up")
        if log_file:
            logger.info(f"Log file: {log_file}")
        logger.info(f"Regular music: {regular_music_path}")
        logger.info(f"Holiday music: {holiday_music_path}")
        logger.info(f"DJ path: {dj_path}")
        logger.info("=" * 60)
    
    # Check directories and count files
    for path_name, path in [
        ('Regular music', regular_music_path),
        ('Holiday music', holiday_music_path),
        ('DJ', dj_path)
    ]:
        if os.path.exists(path):
            mp3_count = len([f for f in os.listdir(path) if f.endswith('.mp3')])
            if args.interactive:
                print(f"{Colors.DIM}{path_name}: {mp3_count} MP3 files{Colors.RESET}")
            else:
                logger.info(f"{path_name} directory: {mp3_count} MP3 files found")
        else:
            if args.interactive:
                print(f"{Colors.YELLOW}Warning: {path_name} directory does not exist: {path}{Colors.RESET}")
            else:
                logger.warning(f"{path_name} directory does not exist: {path}")
    
    # Create an instance of MusicPlayer
    try:
        player = MusicPlayer(regular_music_path, holiday_music_path, dj_path)
        player_instance = player
    except Exception as e:
        logger.error(f"Failed to initialize MusicPlayer: {e}", exc_info=True)
        sys.exit(1)

    # Handle SIGTERM to allow for graceful shutdown
    signal.signal(signal.SIGTERM, player.sigterm_handler)
    if not args.interactive:
        # Also handle SIGHUP for systemd
        signal.signal(signal.SIGHUP, player.sigterm_handler)
    
    # Start YouTube streaming if enabled (with retry logic)
    youtube_started = False
    if player.youtube_streamer:
        logger.info("Starting YouTube live stream...")
        # Try to start with retries (in case of temporary network issues)
        max_startup_retries = 5
        retry_delay = 5  # seconds
        for attempt in range(1, max_startup_retries + 1):
            youtube_started = player.start_youtube_stream()
            if youtube_started:
                if args.interactive:
                    print(f"{Colors.GREEN}✓ YouTube streaming started{Colors.RESET}")
                logger.info("YouTube streaming started successfully")
                break
            else:
                if attempt < max_startup_retries:
                    logger.warning(f"YouTube stream failed to start (attempt {attempt}/{max_startup_retries}), retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    if args.interactive:
                        print(f"{Colors.YELLOW}⚠ YouTube streaming failed to start after {max_startup_retries} attempts{Colors.RESET}")
                    logger.warning(f"YouTube streaming failed to start after {max_startup_retries} attempts")
                    logger.info("Stream will continue attempting to reconnect automatically...")
    
    # Start keyboard input thread if interactive
    keyboard_thread = None
    if args.interactive:
        keyboard_thread = threading.Thread(target=read_keyboard, daemon=True)
        keyboard_thread.start()
        print(f"{Colors.CYAN}Radio player started - playing music continuously...{Colors.RESET}\n")
    else:
        logger.info("Radio player started - playing music continuously...")
    
    # Loop indefinitely to play random MP3s
    consecutive_errors = 0
    max_consecutive_errors = 10
    last_youtube_check = time.time()
    youtube_check_interval = 60  # Check YouTube stream health every 60 seconds
    youtube_reconnect_attempts = 0
    last_youtube_reconnect = 0
    youtube_reconnect_cooldown = 300  # Wait 5 minutes between reconnection attempts
    
    try:
        while not exit_requested:
            skip_song = False
            
            # Periodically check YouTube stream health and auto-reconnect (with rate limiting)
            current_time = time.time()
            if player.youtube_streamer and (current_time - last_youtube_check) >= youtube_check_interval:
                last_youtube_check = current_time
                
                # Rate limiting: don't reconnect too frequently
                time_since_last_reconnect = current_time - last_youtube_reconnect
                if time_since_last_reconnect < youtube_reconnect_cooldown:
                    logger.debug(f"YouTube reconnect cooldown active ({youtube_reconnect_cooldown - time_since_last_reconnect:.0f}s remaining)")
                elif not player.youtube_streamer.is_active():
                    # Stream process died - restart it
                    logger.warning("YouTube stream process not running - attempting to reconnect...")
                    last_youtube_reconnect = current_time
                    youtube_reconnect_attempts += 1
                    if player.youtube_streamer.start():
                        logger.info("YouTube stream reconnected successfully")
                        youtube_reconnect_attempts = 0  # Reset on success
                    else:
                        logger.warning("Failed to reconnect YouTube stream, will retry on next check")
                elif not player.youtube_streamer.check_health():
                    # Stream is running but not sending data - restart it
                    logger.warning("YouTube stream health check failed - attempting restart...")
                    last_youtube_reconnect = current_time
                    youtube_reconnect_attempts += 1
                    if player.youtube_streamer.restart():
                        logger.info("YouTube stream restarted successfully")
                        youtube_reconnect_attempts = 0  # Reset on success
                    else:
                        logger.warning("Failed to restart YouTube stream, will retry on next check")
                
                # Exponential backoff for repeated failures
                if youtube_reconnect_attempts > 3:
                    youtube_reconnect_cooldown = min(600, 300 * (2 ** (youtube_reconnect_attempts - 3)))  # Max 10 minutes
                    logger.warning(f"Increasing reconnect cooldown to {youtube_reconnect_cooldown}s due to repeated failures")
            
            try:
                success = player.play_random_mp3()
                
                if exit_requested:
                    break
                
                if success:
                    consecutive_errors = 0
                else:
                    consecutive_errors += 1
                    logger.warning(f"Failed to play song (consecutive errors: {consecutive_errors}/{max_consecutive_errors})")
                    if consecutive_errors >= max_consecutive_errors:
                        logger.error(f"Too many consecutive errors ({consecutive_errors}), exiting to prevent infinite error loop")
                        exit_requested = True
                        break
                    # Add delay before retrying to avoid rapid error loops
                    time.sleep(min(consecutive_errors * 2, 30))  # Max 30 seconds delay
            except KeyboardInterrupt:
                if args.interactive:
                    print(f"\n{Colors.YELLOW}Interrupted by user, shutting down...{Colors.RESET}")
                else:
                    logger.info("Interrupted by user, shutting down...")
                exit_requested = True
                break
            except KeyboardInterrupt:
                # Re-raise KeyboardInterrupt to be handled by outer try/except
                raise
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error during playback: {e}", exc_info=True)
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive errors ({consecutive_errors}), exiting to prevent infinite error loop")
                    exit_requested = True
                    break
                # Exponential backoff: wait longer with each error
                wait_time = min(5 * (2 ** (consecutive_errors - 1)), 60)  # Max 60 seconds
                logger.info(f"Waiting {wait_time}s before retrying...")
                time.sleep(wait_time)
            
            # If skip was requested, continue to next song immediately
            if skip_song and not exit_requested and args.interactive:
                time.sleep(0.3)  # Brief pause before next song
                
    finally:
        # Graceful shutdown
        logger.info("Shutting down...")
        try:
        player.audio_player.stop()
        except Exception as e:
            logger.warning(f"Error stopping audio player: {e}")
        
        try:
            player.stop_youtube_stream()
        except Exception as e:
            logger.warning(f"Error stopping YouTube stream: {e}")
        
        if args.interactive:
            print(f"\n{Colors.CYAN}Session ended.{Colors.RESET}\n")
        else:
            logger.info("Session ended.")

if __name__ == "__main__":
    main()