#!/bin/bash
# Double-click in Finder to install what is needed (Python) and start the MIDI Footswitch Programmer.
cd "$(dirname "$0")" || exit 1

find_python() {
  for p in python3.13 python3.12 python3.11 python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)' 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  return 1
}

PY=$(find_python)
if [ -z "$PY" ]; then
  echo "Python 3.11+ not found."
  if command -v brew >/dev/null 2>&1; then
    echo "Installing with Homebrew..."
    brew install python@3.13 && PY=$(find_python)
  fi
fi
if [ -z "$PY" ]; then
  echo "Downloading the official Python installer. Click through it, then double-click this file again."
  PKG=/tmp/python-installer.pkg
  curl -L -o "$PKG" "https://www.python.org/ftp/python/3.13.1/python-3.13.1-macos11.pkg" && open "$PKG"
  read -r -p "Press Enter to close..."
  exit 0
fi
"$PY" tools/dev.py run || read -r -p "Something failed. Press Enter to close..."
