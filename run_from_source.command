#!/bin/bash
# Double-click in Finder (or run in Terminal) to start the app from source on a Mac.
# When run this way, macOS asks for Screen Recording and Accessibility for Terminal, not for the app.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -q -r requirements.txt
python run_app.py
