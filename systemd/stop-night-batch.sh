#!/bin/bash
# Stop night batch analysis by sending SIGTERM to the Python process
PID=$(pgrep -f "night_batch.py" | head -1)
if [ -n "$PID" ]; then
    echo "Stopping night_batch (PID=$PID)..."
    kill -TERM "$PID"
    echo "Sent SIGTERM. Waiting for graceful shutdown..."
else
    echo "night_batch.py not running."
fi
