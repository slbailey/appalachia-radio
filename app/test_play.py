"""
Test harness for playing a single MP3 file.

This script allows testing audio output by playing a single known MP3 file.
"""

import argparse
import logging
import os
import sys
import time
from dotenv import load_dotenv
from broadcast_core.event_queue import AudioEvent
from mixer.audio_mixer import AudioMixer
from outputs.fm_sink import FMSink
from broadcast_core.playout_engine import PlayoutEngine
from clock.master_clock import MasterClock

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main() -> None:
    """Test play a single MP3 file."""
    parser = argparse.ArgumentParser(description="Test play a single MP3 file")
    parser.add_argument("file", type=str, help="Path to MP3 file to play")
    parser.add_argument(
        "--device",
        type=str,
        default=None,  # Will load from .env or use default
        help="ALSA device (default: from SDL_AUDIODEVICE env var or hw:1,0)"
    )
    
    args = parser.parse_args()
    
    # Load .env file
    load_dotenv()
    
    # Get device from args, env, or default
    device = args.device
    if device is None:
        device = os.getenv("SDL_AUDIODEVICE", "hw:1,0")
    
    # Build engine (must match app/radio.py pattern)
    sample_rate = 48000
    frame_size = 4096
    
    # Phase 9: Create MasterClock first
    master_clock = MasterClock(
        sample_rate=sample_rate,
        frame_size=frame_size,
        dev_mode=False
    )
    
    # Create mixer with MasterClock
    mixer = AudioMixer(
        sample_rate=sample_rate,
        channels=2,
        frame_size=frame_size,
        master_clock=master_clock
    )
    
    # Create FM sink
    fm_sink = FMSink(device=device, sample_rate=sample_rate, channels=2, frame_size=frame_size)
    mixer.add_sink(fm_sink)
    
    # Create playout engine
    playout_engine = PlayoutEngine(mixer)
    
    # Phase 9: Start MasterClock first (must be running before sinks)
    master_clock.start()
    logger.info("MasterClock started")
    
    # Start FM sink
    if not fm_sink.start():
        logger.error("Failed to start FM sink")
        master_clock.stop()
        return
    
    logger.info(f"Playing: {args.file}")
    
    # Queue event
    event = AudioEvent(path=args.file, type="song", gain=1.0)
    playout_engine.queue_event(event)
    
    # Run playout engine - it processes events in a loop
    # We'll run it in a thread and stop it after the event completes
    import threading
    
    def run_engine():
        playout_engine.run()
    
    engine_thread = threading.Thread(target=run_engine, daemon=True)
    engine_thread.start()
    
    try:
        # Wait for the event to complete (engine becomes idle)
        while not playout_engine.is_idle():
            time.sleep(0.1)
        
        # Stop the engine
        playout_engine.stop()
        engine_thread.join(timeout=2.0)
    
    except KeyboardInterrupt:
        logger.info("Interrupted")
        playout_engine.stop()
    finally:
        # Cleanup: Stop MasterClock first, then sinks
        logger.info("Stopping...")
        master_clock.stop()
        fm_sink.stop()
        logger.info("Test play complete")


if __name__ == "__main__":
    main()

