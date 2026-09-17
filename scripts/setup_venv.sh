#!/bin/bash
# Create the venv ON the iMac (never through the SMB mount) and install deps.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=/opt/homebrew/bin/python3
if [ ! -x "$PYTHON" ]; then
    echo "homebrew python not found at $PYTHON" >&2
    exit 1
fi

"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo "venv ready: $(pwd)/.venv"
