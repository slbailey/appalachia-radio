"""
Audio output sinks module.

Provides FMSink for FM transmitter output and YouTubeSink for YouTube Live streaming.
"""

from outputs.sink_base import SinkBase
from outputs.fm_sink import FMSink
from outputs.youtube_sink import YouTubeSink

__all__ = ["SinkBase", "FMSink", "YouTubeSink"]

