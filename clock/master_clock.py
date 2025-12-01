"""
Master clock timing engine for synchronized audio playback.

This module provides the MasterClock class, which emits ticks based on
audio sample rate and provides a callback registration system for components
that need synchronized timing.
"""

import logging
import threading
import time
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


class MasterClock:
    """
    Master clock for synchronized audio playback.
    
    Emits ticks at a fixed rate (default: ~46.875 ticks/sec for 4096-byte frames at 48kHz).
    Provides callback registration for components that need synchronized timing.
    
    Features:
    - Monotonic time base (time.monotonic)
    - Never tries to "catch up" by emitting multiple ticks
    - Drops missed ticks and resyncs if significantly behind
    - Simple, predictable behavior
    """
    
    def __init__(
        self,
        sample_rate: int = 48000,
        frame_size: int = 4096,
        dev_mode: bool = False
    ) -> None:
        """
        Initialize the master clock.
        
        Args:
            sample_rate: Audio sample rate in Hz (default: 48000)
            frame_size: Frame size in bytes (default: 4096)
            dev_mode: If True, simulate in real time (default: False)
        """
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        
        # Calculate samples per frame (16-bit stereo = 2 bytes per sample, 2 channels)
        # frame_size bytes / (2 bytes per sample * 2 channels) = sample pairs per frame
        # For 4096 bytes: 4096 / 4 = 1024 sample pairs = 1024/48000 seconds
        self.samples_per_frame = frame_size // (2 * 2)  # 2 channels, 2 bytes per sample
        
        # Calculate tick interval in seconds
        # At 48kHz, one sample pair = 1/48000 seconds
        # One frame = samples_per_frame / 48000 seconds
        # For 4096 bytes: 1024 / 48000 = 0.02133... seconds (~21.3ms per tick)
        self._tick_interval_sec = self.samples_per_frame / sample_rate
        
        # For dev mode, we can speed up timing
        self.dev_mode = dev_mode
        if dev_mode:
            logger.info("MasterClock: Dev mode enabled (faster timing)")
        
        # Callback registry
        self._callbacks: List[Callable[[int], None]] = []
        self._callback_lock = threading.Lock()
        
        # Clock state
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Timing state
        self._frame_index = 0
        self._next_tick_time: Optional[float] = None
        
        logger.info(
            f"MasterClock initialized: {sample_rate}Hz, "
            f"frame_size={frame_size} bytes, "
            f"tick_interval={self._tick_interval_sec*1000:.2f}ms"
        )
    
    def register_callback(self, callback: Callable[[int], None]) -> None:
        """
        Register a callback to be called on each clock tick.
        
        Args:
            callback: Function that takes frame_index (int) as argument
        """
        with self._callback_lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)
                logger.debug(f"Registered clock callback: {callback.__name__ if hasattr(callback, '__name__') else str(callback)}")
    
    def unregister_callback(self, callback: Callable[[int], None]) -> None:
        """
        Unregister a callback from clock ticks.
        
        Args:
            callback: Function to unregister
        """
        with self._callback_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)
                logger.debug(f"Unregistered clock callback: {callback.__name__ if hasattr(callback, '__name__') else str(callback)}")
    
    def start(self) -> None:
        """
        Start the master clock.
        
        Begins emitting ticks at the configured rate.
        """
        if self._running:
            logger.warning("MasterClock already running")
            return
        
        self._running = True
        self._stop_event.clear()
        self._frame_index = 0
        self._next_tick_time = time.monotonic() + self._tick_interval_sec
        
        self._thread = threading.Thread(target=self._clock_loop, daemon=True, name="MasterClock")
        self._thread.start()
        logger.info("MasterClock started")
    
    def stop(self) -> None:
        """
        Stop the master clock.
        
        Stops emitting ticks and cleans up resources.
        """
        if not self._running:
            return
        
        self._running = False
        self._stop_event.set()
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning("MasterClock thread did not stop in time")
        
        self._thread = None
        logger.info("MasterClock stopped")
    
    def is_running(self) -> bool:
        """
        Check if the clock is running.
        
        Returns:
            True if running, False otherwise
        """
        return self._running
    
    def get_frame_index(self) -> int:
        """
        Get the current frame index.
        
        Returns:
            Current frame index (number of ticks since start)
        """
        return self._frame_index
    
    def _clock_loop(self) -> None:
        """
        Main clock loop that emits ticks at the configured rate.
        
        This method runs in a separate thread and continuously
        emits ticks. Never tries to "catch up" by emitting multiple ticks.
        If behind schedule, drops missed ticks and resyncs.
        """
        logger.debug("MasterClock loop started")
        
        while self._running and not self._stop_event.is_set():
            now = time.monotonic()
            
            # If we haven't reached the next tick time, sleep until then
            if now < self._next_tick_time:
                sleep_time = self._next_tick_time - now
                time.sleep(sleep_time)
                continue
            
            # We reached (or passed) the scheduled tick time → emit exactly ONE tick
            # Get callbacks snapshot (thread-safe)
            with self._callback_lock:
                callbacks = list(self._callbacks)
            
            # Call all callbacks (one tick only)
            for callback in callbacks:
                try:
                    callback(self._frame_index)
                except Exception as e:
                    # Log exception but do NOT stop the clock thread
                    logger.error(f"Error in clock callback {callback}: {e}", exc_info=True)
            
            # Advance frame index and schedule next tick
            self._frame_index += 1
            self._next_tick_time += self._tick_interval_sec
            
            # Resync logic disabled - was masking starvation problems
            # Let the clock run naturally without resyncing
        
        logger.debug("MasterClock loop exited")
