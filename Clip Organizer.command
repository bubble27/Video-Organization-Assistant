#!/bin/bash
# Double-click this file to launch Clip Organizer.
# It starts a small local server and opens the app in your browser.

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "================================================================"
  echo " Python 3 is required but was not found."
  echo " Install it with:   brew install python"
  echo " or download from:  https://www.python.org/downloads/"
  echo "================================================================"
  read -r -p "Press Return to close."
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "================================================================"
  echo " ffmpeg was not found (needed for thumbnails and durations)."
  echo " Double-click 'Install ffmpeg.command' first, or run:"
  echo "    brew install ffmpeg"
  echo "================================================================"
  read -r -p "Press Return to close."
  exit 1
fi

echo "Starting Clip Organizer…  (close this window to quit)"
python3 app.py
