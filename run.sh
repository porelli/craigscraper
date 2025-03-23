#!/bin/bash

# To be used for docker only

crawler() {
    python3 /app/main.py
}

ui() {
    # Use UI_PORT from environment variable if set, otherwise default to 8501
    UI_PORT=${UI_PORT:-8501}
    streamlit run /app/ui/ui.py --server.port=${UI_PORT} --server.address=0.0.0.0
}

# Launch both UI and crawler with error handling
launch_services() {
    # Start UI in background
    ui &
    UI_PID=${!}

    # Start crawler in background
    crawler &
    CRAWLER_PID=${!}

    # Wait for either process to exit
    wait -n

    # If we get here, one process exited
    EXIT_CODE=${?}
    echo "A process crashed with exit code ${EXIT_CODE}. Restarting both services..." >&2

    # Kill any remaining processes
    kill ${UI_PID} ${CRAWLER_PID} 2>/dev/null || true

    # Short delay before restart
    sleep 1
    return ${EXIT_CODE}
}

# Trap to ensure we clean up all background processes on exit
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT

# Keep restarting services if they crash
until launch_services; do
    echo "Restarting services..." >&2
    sleep 1
done