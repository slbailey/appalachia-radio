"""
Test harness for playing a single MP3 file.

This script allows testing audio output by playing a single known MP3 file.
"""

import argparse
import logging
import sys
import time
from broadcast_core.event_queue import AudioEvent
from mixer.audio_mixer import AudioMixer
from outputs.fm_sink import FMSink
from broadcast_core.playout_engine import PlayoutEngine

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
        default="hw:1,0",
        help="ALSA device (default: hw:1,0)"
    )
    
    args = parser.parse_args()
    
    # Build engine
    mixer = AudioMixer()
    fm_sink = FMSink(device=args.device)
    mixer.add_sink(fm_sink)
    playout_engine = PlayoutEngine(mixer)
    
    # Start FM sink
    if not fm_sink.start():
        logger.error("Failed to start FM sink")
        return
    
    logger.info(f"Playing: {args.file}")
    
    # Queue event
    event = AudioEvent(path=args.file, type="song", gain=1.0)
    playout_engine.queue_event(event)
    
    # Play until done
    try:
        while playout_engine.mixer.is_playing():
            playout_engine.run()
            import time
            time.sleep(0.01)
        
        # Wait for final frames
        for _ in range(100):
            playout_engine.run()
            time.sleep(0.01)
    
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        fm_sink.stop()
        logger.info("Test play complete")


if __name__ == "__main__":
    main()

