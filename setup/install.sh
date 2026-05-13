#!/bin/bash
# Blue Hour Radio — Dependency Installer
# Run once on a fresh Mac Mini M-series.
# Installs: Liquidsoap, Icecast, ffmpeg, Piper TTS, ACE-Step, Kokoro, Python deps.
#
# Usage: bash setup/install.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIPER_MODELS_DIR="$HOME/.local/share/piper"

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
ok "Homebrew found"

if ! command -v python3 &>/dev/null; then
  red "python3 not found"
  exit 1
fi
PYTHON_VER=$(python3 --version | awk '{print $2}')
ok "Python $PYTHON_VER"

# ---------------------------------------------------------------------------
# 1. Streaming stack
# ---------------------------------------------------------------------------
step "Installing Liquidsoap and Icecast..."

brew install liquidsoap icecast ffmpeg 2>/dev/null || \
  brew upgrade liquidsoap icecast ffmpeg 2>/dev/null || true

ok "Liquidsoap $(liquidsoap --version 2>/dev/null | head -1 || echo '?')"
ok "Icecast $(icecast -v 2>&1 | head -1 || echo '?')"
ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}' || echo '?')"

# ---------------------------------------------------------------------------
# 2. Piper TTS
# ---------------------------------------------------------------------------
step "Installing Piper TTS..."

if ! command -v piper &>/dev/null; then
  # Try Homebrew first; fall back to direct download
  brew install rhasspy/homebrew-tap/piper 2>/dev/null || {
    warn "Homebrew piper tap failed — trying direct download"
    PIPER_TAG="2023.11.14-2"
    PIPER_URL="https://github.com/rhasspy/piper/releases/download/${PIPER_TAG}/piper_macos_aarch64.tar.gz"
    curl -L "$PIPER_URL" | tar -xz -C /usr/local/bin/
  }
fi
ok "Piper $(piper --version 2>/dev/null || echo '?')"

# ---------------------------------------------------------------------------
# 3. Piper voice model — Ryan (warm male voice)
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
# 4. Python environment + packages
# ---------------------------------------------------------------------------
step "Setting up Python environment..."

VENV="$REPO_ROOT/.venv"
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"
pip install --upgrade pip --quiet

pip install --quiet \
  pyyaml \
  requests \
  soundfile \
  numpy \
  kokoro \
  ace-step

ok "Python packages installed"

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

ICECAST_CONF_DIR="/usr/local/etc"
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

if [ -d "$ICECAST_CONF_DIR" ]; then
  sed \
    -e "s|<source-password>changeme</source-password>|<source-password>$ICECAST_SOURCE_PWD</source-password>|" \
    -e "s|<relay-password>changeme-relay</relay-password>|<relay-password>$ICECAST_RELAY_PWD</relay-password>|" \
    -e "s|<admin-password>changeme-admin</admin-password>|<admin-password>$ICECAST_ADMIN_PWD</admin-password>|" \
    "$REPO_ROOT/icecast/icecast.xml" > "$ICECAST_CONF_DIR/icecast.xml"
  ok "Icecast config installed at $ICECAST_CONF_DIR/icecast.xml (with real passwords)"
else
  warn "Could not find $ICECAST_CONF_DIR — run: sudo mkdir -p $ICECAST_CONF_DIR"
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

# Read source password from secrets.yaml
SECRETS_FILE="$REPO_ROOT/secrets.yaml"
if [ ! -f "$SECRETS_FILE" ]; then
  warn "secrets.yaml not found — copy from secrets.yaml.example and set real passwords"
  ICECAST_PWD="changeme"
else
  ICECAST_PWD=$(python3 -c "import yaml; d=yaml.safe_load(open('$SECRETS_FILE')); print(d['icecast']['source_password'])" 2>/dev/null || echo "changeme")
fi

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
    <string>$(command -v liquidsoap)</string>
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
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    <key>RADIO_ROOT</key>
    <string>$REPO_ROOT</string>
    <key>ICECAST_SOURCE_PASSWORD</key>
    <string>$ICECAST_PWD</string>
  </dict>
</dict>
</plist>
PLIST

ok "Liquidsoap plist installed at $PLIST_PATH"
echo "  To start: launchctl load $PLIST_PATH"

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
echo "  3. Change the passwords in icecast/icecast.xml and liquidsoap/radio.liq"
echo "  4. Start Icecast: brew services start icecast"
echo "  5. Start Liquidsoap: launchctl load $PLIST_PATH"
echo "  6. Test: open http://localhost:8000/live.mp3 in VLC"
echo "  7. Run first batch: python generate/generate_batch.py"
