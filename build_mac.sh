#!/bin/bash
# Builds "dist/NTE Lightning Capture.app" and zips it to dist/NTELightningCapture-mac.zip.
# Needs an Apple Silicon Mac with Python 3.11 or newer (python.org or Homebrew python-tk).
# The app is ad-hoc signed only (no Apple Developer ID), so macOS asks before opening it.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements-dev.txt

if [ "${1:-}" != "--no-tests" ]; then
    python -m pytest -q
fi

pyinstaller --noconfirm --windowed --name "NTE Lightning Capture" \
    --osx-bundle-identifier io.github.questlin.ntelightningcapture \
    --target-arch arm64 \
    --hidden-import ScreenCaptureKit --hidden-import CoreMedia --hidden-import dispatch \
    --hidden-import ApplicationServices --hidden-import AppKit --hidden-import Quartz \
    --hidden-import nte_lightning.macinput --hidden-import nte_lightning.maccapture \
    run_app.py

# Re-sign the whole bundle ad hoc so macOS sees one consistent signature.
codesign --force --deep --sign - "dist/NTE Lightning Capture.app"

rm -f dist/NTELightningCapture-mac.zip
ditto -c -k --keepParent "dist/NTE Lightning Capture.app" dist/NTELightningCapture-mac.zip
echo
echo "Built dist/NTE Lightning Capture.app (zipped: dist/NTELightningCapture-mac.zip)"
