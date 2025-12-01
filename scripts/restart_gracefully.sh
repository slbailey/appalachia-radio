#!/bin/bash
# Graceful restart script for Appalachia Radio
# Sends SIGUSR1 to the running process to trigger graceful restart after current song

# Default to /var/run if writable, otherwise project directory
# (systemd uses private /tmp directories which cause path issues)
if [ -w /var/run ]; then
    DEFAULT_PID_FILE="/var/run/appalachia-radio.pid"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    DEFAULT_PID_FILE="$SCRIPT_DIR/appalachia-radio.pid"
fi
PID_FILE="${PID_FILE:-$DEFAULT_PID_FILE}"

if [ ! -f "$PID_FILE" ]; then
    echo "Error: PID file not found at $PID_FILE"
    echo "Is the radio station running?"
    exit 1
fi

PID=$(cat "$PID_FILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Error: Process $PID is not running"
    rm -f "$PID_FILE"
    exit 1
fi

echo "Sending SIGUSR1 to process $PID for graceful restart..."
kill -USR1 "$PID"

if [ $? -eq 0 ]; then
    echo "Restart signal sent. The station will restart after the current song finishes."
else
    echo "Error: Failed to send restart signal"
    exit 1
fi

