#!/bin/bash
# Blue Hour Radio — Music Server launcher
# Starts the ACE-Step music generation server in the project venv.
# The server loads the model once and stays warm until killed.
#
# Usage:
#   bash music_server/start.sh              # foreground (Ctrl-C to stop)
#   bash music_server/start.sh --port 8765  # custom port
#
# Managed by launchd in production (see setup/install.sh).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO_ROOT/.venv"

if [ ! -f "$VENV/bin/python" ]; then
  echo "ERROR: venv not found at $VENV — run: bash setup/install.sh" >&2
  exit 1
fi

# MLX backend for Apple Silicon; disables MPS watermark cap
export LM_BACKEND=mlx
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0

exec "$VENV/bin/python" "$REPO_ROOT/music_server/server.py" \
  --host 127.0.0.1 \
  --port 8765 \
  "$@"
