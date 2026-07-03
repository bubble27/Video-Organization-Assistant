#!/bin/bash
# Double-click this once to install ffmpeg (required for previews).

if command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is already installed. You're all set."
  read -r -p "Press Return to close."
  exit 0
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "================================================================"
  echo " Homebrew is not installed. Install it first by pasting this"
  echo " into Terminal, then run this file again:"
  echo ""
  echo '   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  echo "================================================================"
  read -r -p "Press Return to close."
  exit 1
fi

echo "Installing ffmpeg via Homebrew…"
brew install ffmpeg
echo ""
echo "Done. You can now launch 'Clip Organizer.command'."
read -r -p "Press Return to close."
