#!/bin/bash
# Blue Hour Radio — Dependency Installer
# Run once on a fresh Mac Mini M-series (Apple Silicon).
# Installs: Liquidsoap (via opam), Icecast, ffmpeg, Piper TTS, ACE-Step, Kokoro, Python deps.
#
# Usage: bash setup/install.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIPER_MODELS_DIR="$HOME/.local/share/piper"
BREW_PREFIX="$(brew --prefix)"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }
step()  { blue "==> $*"; }
ok()    { green "  OK: $*"; }
warn()  { printf '\033[33mWARN: %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# 0. Pre-flight
# ---------------------------------------------------------------------------
step "Checking pre-flight requirements..."

if ! command -v brew &>/dev/null; then
  red "Homebrew not found. Install it first: https://brew.sh"
  exit 1
fi
ok "Homebrew found (prefix: $BREW_PREFIX)"

# kokoro→spacy→thinc→blis require Python 3.10–3.12 (use private CPython C APIs
# removed in 3.13+). Enforce Python 3.12 via Homebrew.
PYTHON312="$BREW_PREFIX/opt/python@3.12/bin/python3.12"
if [ ! -f "$PYTHON312" ]; then
  step "Installing Python 3.12 (required for kokoro/spacy dependencies)..."
  brew install python@3.12
fi
if [ ! -f "$PYTHON312" ]; then
  red "Python 3.12 not found at $PYTHON312 after install — check 'brew install python@3.12'"
  exit 1
fi
ok "Python 3.12: $($PYTHON312 --version)"

# ---------------------------------------------------------------------------
# 1. ffmpeg and Icecast (Homebrew)
# ---------------------------------------------------------------------------
step "Installing ffmpeg and Icecast..."

# Install separately so one failure doesn't block the other
brew install ffmpeg 2>/dev/null || brew upgrade ffmpeg 2>/dev/null || true
brew install icecast 2>/dev/null || brew upgrade icecast 2>/dev/null || true

ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}' || echo '?')"
if command -v icecast &>/dev/null; then
  ok "Icecast $(icecast -v 2>&1 | head -1 || echo 'installed')"
else
  warn "Icecast not found after install — check 'brew install icecast' manually"
fi

# ---------------------------------------------------------------------------
# 2. Liquidsoap via OPAM (OCaml Package Manager)
# ---------------------------------------------------------------------------
# Liquidsoap is not in homebrew-core; OPAM is the officially supported method.
step "Installing Liquidsoap via OPAM..."

brew install opam 2>/dev/null || brew upgrade opam 2>/dev/null || true
ok "opam $(opam --version 2>/dev/null || echo '?')"

# Initialize opam if not already done (--disable-sandboxing needed in some CI/macOS envs)
if [ ! -d "$HOME/.opam" ]; then
  opam init --disable-sandboxing --no-setup -y
fi

# Bring opam env into this shell
eval "$(opam env 2>/dev/null)"

# pandoc-include is required by liquidsoap's doc build step.
# Must be installed system-wide (not in the project venv) so it's on PATH
# during the opam/dune compilation. pipx is the cleanest tool for this.
if ! command -v pandoc-include &>/dev/null; then
  brew install pipx
  pipx install pandoc-include
  # pipx installs to ~/.local/bin — ensure it's on PATH for this session
  export PATH="$HOME/.local/bin:$PATH"
fi
ok "pandoc-include $(command -v pandoc-include || echo '?')"

# Install liquidsoap (takes several minutes on first run; safe to retry if interrupted)
opam install liquidsoap -y

eval "$(opam env 2>/dev/null)"
LIQUIDSOAP_BIN="$(opam var bin 2>/dev/null)/liquidsoap"

if [ -f "$LIQUIDSOAP_BIN" ]; then
  ok "Liquidsoap $("$LIQUIDSOAP_BIN" --version 2>/dev/null | head -1 || echo '?') at $LIQUIDSOAP_BIN"
else
  warn "Liquidsoap binary not found at expected path: $LIQUIDSOAP_BIN"
  LIQUIDSOAP_BIN="$(command -v liquidsoap 2>/dev/null || echo 'liquidsoap')"
fi

# ---------------------------------------------------------------------------
# 3. Python environment + Piper TTS + other packages
# ---------------------------------------------------------------------------
# Piper TTS is installed as a Python package (piper-tts) into the project venv.
# This avoids the tarball extraction issues and keeps everything in one venv.
step "Setting up Python environment and installing packages..."

VENV="$REPO_ROOT/.venv"
# Recreate venv if it was built with the wrong Python version (e.g. 3.14).
if [ -d "$VENV" ]; then
  VENV_PYTHON_VER=$("$VENV/bin/python3" --version 2>/dev/null | awk '{print $2}' | cut -d. -f1,2)
  if [ "$VENV_PYTHON_VER" != "3.12" ]; then
    warn "Existing venv uses Python $VENV_PYTHON_VER — recreating with Python 3.12"
    rm -rf "$VENV"
  fi
fi
if [ ! -d "$VENV" ]; then
  "$PYTHON312" -m venv "$VENV"
fi
source "$VENV/bin/activate"
pip install --upgrade pip --quiet

pip install --quiet \
  pyyaml \
  requests \
  soundfile \
  numpy \
  kokoro \
  piper-tts \
  "fastapi>=0.111" \
  "uvicorn>=0.29"

# ace-step PyPI package has a broken sdist (setup.py reads requirements.txt
# that isn't bundled). Install from GitHub source instead.
pip install --quiet "git+https://github.com/ace-step/ACE-Step-1.5.git"

ok "Python packages installed (including piper-tts, ace-step)"

PIPER_BIN="$VENV/bin/piper"
if [ -f "$PIPER_BIN" ]; then
  ok "Piper TTS installed at $PIPER_BIN"
else
  warn "piper binary not found at $PIPER_BIN — check pip install piper-tts"
fi

# ---------------------------------------------------------------------------
# 4. Piper voice model — Ryan (warm male voice)
# ---------------------------------------------------------------------------
step "Downloading Piper voice model (en_US-ryan-high)..."

mkdir -p "$PIPER_MODELS_DIR"
MODEL_NAME="en_US-ryan-high"
MODEL_ONNX="$PIPER_MODELS_DIR/${MODEL_NAME}.onnx"
MODEL_JSON="$PIPER_MODELS_DIR/${MODEL_NAME}.onnx.json"
PIPER_MODEL_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high"

if [ ! -f "$MODEL_ONNX" ]; then
  curl -L "${PIPER_MODEL_BASE}/${MODEL_NAME}.onnx" -o "$MODEL_ONNX"
  curl -L "${PIPER_MODEL_BASE}/${MODEL_NAME}.onnx.json" -o "$MODEL_JSON"
  ok "Downloaded $MODEL_NAME model"
else
  ok "Model already present: $MODEL_ONNX"
fi

# ---------------------------------------------------------------------------
# 5. Kokoro voice model (downloads on first use, but pre-warm here)
# ---------------------------------------------------------------------------
step "Pre-warming Kokoro model (af_sky)..."
python3 -c "
from kokoro import KPipeline
print('  Kokoro loading model af_sky...')
p = KPipeline(lang_code='a')
print('  Kokoro ready.')
" || warn "Kokoro pre-warm failed — will download on first use"

# ---------------------------------------------------------------------------
# 6. Icecast config
# ---------------------------------------------------------------------------
step "Installing Icecast config..."

ICECAST_CONF_DIR="$BREW_PREFIX/etc"
SECRETS_FILE="$REPO_ROOT/secrets.yaml"

# Read passwords from secrets.yaml; fall back to placeholders with a warning
if [ -f "$SECRETS_FILE" ]; then
  ICECAST_SOURCE_PWD=$(python3 -c "import yaml; d=yaml.safe_load(open('$SECRETS_FILE')); print(d['icecast']['source_password'])" 2>/dev/null || echo "changeme")
  ICECAST_RELAY_PWD=$(python3  -c "import yaml; d=yaml.safe_load(open('$SECRETS_FILE')); print(d['icecast']['relay_password'])"  2>/dev/null || echo "changeme-relay")
  ICECAST_ADMIN_PWD=$(python3  -c "import yaml; d=yaml.safe_load(open('$SECRETS_FILE')); print(d['icecast']['admin_password'])"  2>/dev/null || echo "changeme-admin")
else
  warn "secrets.yaml not found — using placeholder passwords. Run: cp secrets.yaml.example secrets.yaml"
  ICECAST_SOURCE_PWD="changeme"
  ICECAST_RELAY_PWD="changeme-relay"
  ICECAST_ADMIN_PWD="changeme-admin"
fi

if command -v icecast &>/dev/null; then
  sed \
    -e "s|<source-password>changeme</source-password>|<source-password>$ICECAST_SOURCE_PWD</source-password>|" \
    -e "s|<relay-password>changeme-relay</relay-password>|<relay-password>$ICECAST_RELAY_PWD</relay-password>|" \
    -e "s|<admin-password>changeme-admin</admin-password>|<admin-password>$ICECAST_ADMIN_PWD</admin-password>|" \
    "$REPO_ROOT/icecast/icecast.xml" > "$ICECAST_CONF_DIR/icecast.xml"
  ok "Icecast config installed at $ICECAST_CONF_DIR/icecast.xml (with real passwords)"
else
  warn "Icecast not installed — skipping config injection. Install icecast first, then re-run."
fi

# ---------------------------------------------------------------------------
# 7. Library directories
# ---------------------------------------------------------------------------
step "Creating library directories..."

for d in music dj-segments shows station-ids bootstrap logs .staging/music .staging/dj-segments .staging/shows; do
  mkdir -p "$REPO_ROOT/radio-library/$d"
done
touch "$REPO_ROOT/radio-library/logs/generation.log"
touch "$REPO_ROOT/radio-library/logs/watchdog.log"
ok "Library directories ready"

# ---------------------------------------------------------------------------
# 8. Disable Mac Mini sleep
# ---------------------------------------------------------------------------
step "Disabling Mac Mini sleep (required for 24/7 operation)..."
sudo pmset -a sleep 0 disksleep 0 2>/dev/null || warn "Could not set pmset — disable sleep in System Settings > Energy Saver"

# ---------------------------------------------------------------------------
# 9. Launchd plist for Liquidsoap
# ---------------------------------------------------------------------------
step "Installing Liquidsoap launchd plist..."

OPAM_BIN_DIR="$(opam var bin 2>/dev/null || echo "$HOME/.opam/default/bin")"

PLIST_PATH="$HOME/Library/LaunchAgents/com.bluehour.liquidsoap.plist"
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.bluehour.liquidsoap</string>
  <key>ProgramArguments</key>
  <array>
    <string>$LIQUIDSOAP_BIN</string>
    <string>$REPO_ROOT/liquidsoap/radio.liq</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$REPO_ROOT/radio-library/logs/liquidsoap.log</string>
  <key>StandardErrorPath</key>
  <string>$REPO_ROOT/radio-library/logs/liquidsoap.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$OPAM_BIN_DIR:$BREW_PREFIX/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>RADIO_ROOT</key>
    <string>$REPO_ROOT</string>
    <key>ICECAST_SOURCE_PASSWORD</key>
    <string>$ICECAST_SOURCE_PWD</string>
  </dict>
</dict>
</plist>
PLIST

ok "Liquidsoap plist installed at $PLIST_PATH"
echo "  To start: launchctl load $PLIST_PATH"

# ---------------------------------------------------------------------------
# 10. Launchd plist for Music Server (ACE-Step)
# ---------------------------------------------------------------------------
step "Installing Music Server launchd plist..."

MUSIC_SERVER_LOG="$REPO_ROOT/radio-library/logs/music_server.log"
MUSIC_PLIST_PATH="$HOME/Library/LaunchAgents/com.bluehour.musicserver.plist"
cat > "$MUSIC_PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.bluehour.musicserver</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>$REPO_ROOT/music_server/server.py</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8765</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$MUSIC_SERVER_LOG</string>
  <key>StandardErrorPath</key>
  <string>$MUSIC_SERVER_LOG</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTORCH_MPS_HIGH_WATERMARK_RATIO</key>
    <string>0.0</string>
    <key>LM_BACKEND</key>
    <string>mlx</string>
    <key>ACESTEP_GENERATION_TIMEOUT</key>
    <string>1200</string>
    <key>PATH</key>
    <string>$BREW_PREFIX/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
PLIST

touch "$MUSIC_SERVER_LOG"
ok "Music server plist installed at $MUSIC_PLIST_PATH"
echo "  To start: launchctl load $MUSIC_PLIST_PATH"

# ---------------------------------------------------------------------------
# 10. Cron entries reminder
# ---------------------------------------------------------------------------
cat <<CRON_MSG

$(blue '==> Add these cron entries (crontab -e):')

  # Blue Hour Radio — content generation every 6 hours
  0 */6 * * * cd "$REPO_ROOT" && "$VENV/bin/python" generate/generate_batch.py >> radio-library/logs/generation.log 2>&1

  # Blue Hour Radio — watchdog every 5 minutes
  */5 * * * * "$REPO_ROOT/watchdog/watchdog.sh" >> "$REPO_ROOT/radio-library/logs/watchdog.log" 2>&1

CRON_MSG

green "==> Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Run: bash setup/bootstrap_gen.sh  (generates 2h emergency fallback)"
echo "  2. Update config.yaml with your actual GPS coordinates"
echo "  3. Start Music Server: launchctl load $MUSIC_PLIST_PATH"
echo "     (or foreground: bash music_server/start.sh)"
echo "  4. Benchmark: $VENV/bin/python generate/music_gen.py --benchmark"
echo "  5. Start Icecast: brew services start icecast"
echo "  6. Start Liquidsoap: launchctl load $PLIST_PATH"
echo "  7. Test stream: curl -I http://localhost:8000/live.mp3"
echo "  8. Run first batch: $VENV/bin/python generate/generate_batch.py"
