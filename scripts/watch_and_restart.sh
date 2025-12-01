#!/bin/bash
# Watch for code changes and automatically restart the radio station
# Usage: ./scripts/watch_and_restart.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Default to /var/run if writable, otherwise project directory
# (systemd uses private /tmp directories which cause path issues)
if [ -w /var/run ]; then
    DEFAULT_PID_FILE="/var/run/appalachia-radio.pid"
else
    DEFAULT_PID_FILE="$SCRIPT_DIR/appalachia-radio.pid"
fi
PID_FILE="${PID_FILE:-$DEFAULT_PID_FILE}"
RESTART_SCRIPT="$SCRIPT_DIR/scripts/restart_gracefully.sh"

# Function to start the radio
start_radio() {
    cd "$SCRIPT_DIR" || exit 1
    python3 -m app.radio &
    echo $! > "$PID_FILE"
    echo "Radio started with PID: $(cat $PID_FILE)"
}

# Function to restart gracefully
restart_radio() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Code changed, triggering graceful restart..."
        bash "$RESTART_SCRIPT"
        # Wait a bit for the process to exit
        sleep 2
    fi
    
    # Start new instance
    start_radio
}

# Initial start
start_radio

# Watch for Python file changes
echo "Watching for code changes..."
inotifywait -m -r -e modify,create,delete --include '\.(py)$' "$SCRIPT_DIR" 2>/dev/null | while read -r directory event file; do
    # Ignore changes in __pycache__ and .git
    if [[ "$directory" == *"__pycache__"* ]] || [[ "$directory" == *".git"* ]]; then
        continue
    fi
    
    echo "Detected change: $directory$file"
    restart_radio
done

