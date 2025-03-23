#!/bin/bash

# To be used for docker only

crawler() {
    python3 /app/main.py
}

trap 'kill $(jobs -p)' EXIT; until crawler & wait; do
    echo "Scraper crashed with exit code $?. Re-spawning.." >&2
    sleep 1
done