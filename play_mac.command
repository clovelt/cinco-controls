#!/bin/bash
# Double-click to play. First run sets up everything on its own (private
# Python environment in core/venv, dependencies installed into it); every
# run after that skips straight to launching, unless requirements.txt has
# changed since.
cd "$(dirname "$0")/core"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install it from https://www.python.org/downloads/"
  echo "(or \"brew install python3\" if you use Homebrew), then try again."
  read -n 1 -s -r -p "Press any key to close this window..."
  exit 1
fi

if [ ! -x "venv/bin/python3" ]; then
  echo "First run: setting up a private Python environment in core/venv ..."
  python3 -m venv venv || {
    echo "Failed to create the virtual environment -- see above."
    read -n 1 -s -r -p "Press any key to close this window..."
    exit 1
  }
fi

if command -v sha256sum >/dev/null 2>&1; then
  HASH_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
  HASH_CMD="shasum -a 256"
else
  HASH_CMD=""
fi
HASH_FILE="venv/.requirements.sha256"
NEW_HASH=$([ -n "$HASH_CMD" ] && $HASH_CMD requirements.txt | awk '{print $1}')
OLD_HASH=$(cat "$HASH_FILE" 2>/dev/null)

if [ -z "$NEW_HASH" ] || [ "$NEW_HASH" != "$OLD_HASH" ]; then
  echo "Installing/updating dependencies ..."
  venv/bin/python3 -m pip install --quiet --upgrade pip
  venv/bin/python3 -m pip install --quiet -r requirements.txt || {
    echo "Dependency install failed -- see above."
    read -n 1 -s -r -p "Press any key to close this window..."
    exit 1
  }
  [ -n "$NEW_HASH" ] && echo "$NEW_HASH" > "$HASH_FILE"
fi

venv/bin/python3 dashboard.py "$@"
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "Exited with an error (code $status) -- see above."
  read -n 1 -s -r -p "Press any key to close this window..."
fi
