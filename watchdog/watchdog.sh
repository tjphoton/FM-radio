#!/bin/bash
# Blue Hour Radio — Watchdog Script
# Runs every 5 minutes via cron or launchd.
#
# Cron entry (add with: crontab -e):
#   */5 * * * * RADIO_ROOT/watchdog/watchdog.sh >> RADIO_ROOT/radio-library/logs/watchdog.log 2>&1
#
# Checks:
#   1. Icecast stream health (HTTP 200 on stats endpoint)
#   2. Liquidsoap process alive
#   3. Audio buffer depth (alert if < 2 hours)
#   4. Generation pipeline freshness (alert if no batch in 8 hours)

set -euo pipefail

REPO_ROOT="RADIO_ROOT"
LOG="$REPO_ROOT/radio-library/logs/watchdog.log"
ICECAST_URL="http://localhost:8000/status-json.xsl"
ICECAST_STREAM_URL="http://localhost:8000/live.mp3"
BUFFER_ALERT_HOURS=2
GENERATION_LOG="$REPO_ROOT/radio-library/logs/generation.log"
MUSIC_DIR="$REPO_ROOT/radio-library/music"
BOOTSTRAP_DIR="$REPO_ROOT/radio-library/bootstrap"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log_json() {
  echo "{\"ts\":\"$(ts)\", $1}" >> "$LOG"
}

notify() {
  local title="$1" message="$2"
  osascript -e "display notification \"$message\" with title \"$title\"" 2>/dev/null || true
}

restart_icecast() {
  log_json "\"event\":\"restart\",\"service\":\"icecast\""
  brew services restart icecast 2>/dev/null || true
  sleep 3
}

restart_liquidsoap() {
  log_json "\"event\":\"restart\",\"service\":\"liquidsoap\""
  # Liquidsoap running via launchd
  local plist_label="com.bluehour.liquidsoap"
  local uid
  uid=$(id -u)
  launchctl kickstart -k "gui/$uid/$plist_label" 2>/dev/null || \
    launchctl stop "$plist_label" 2>/dev/null || true
  sleep 5
}

# ---------------------------------------------------------------------------
# Check 1: Icecast health
# ---------------------------------------------------------------------------

icecast_ok=true
http_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$ICECAST_URL" 2>/dev/null || echo "000")

if [ "$http_status" != "200" ]; then
  log_json "\"event\":\"icecast_down\",\"http_status\":\"$http_status\""
  notify "Blue Hour Radio — Alert" "Icecast down (HTTP $http_status). Restarting..."
  restart_icecast
  sleep 5

  # Verify recovery
  http_status2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$ICECAST_URL" 2>/dev/null || echo "000")
  if [ "$http_status2" != "200" ]; then
    log_json "\"event\":\"icecast_restart_failed\",\"http_status\":\"$http_status2\""
    notify "Blue Hour Radio — CRITICAL" "Icecast restart failed. Manual intervention needed."
    icecast_ok=false
  else
    log_json "\"event\":\"icecast_recovered\""
  fi
fi

# ---------------------------------------------------------------------------
# Check 2: Liquidsoap process
# ---------------------------------------------------------------------------

if ! pgrep -x "liquidsoap" > /dev/null 2>&1; then
  log_json "\"event\":\"liquidsoap_down\""
  notify "Blue Hour Radio — Alert" "Liquidsoap not running. Restarting..."
  restart_liquidsoap
  sleep 8

  if ! pgrep -x "liquidsoap" > /dev/null 2>&1; then
    log_json "\"event\":\"liquidsoap_restart_failed\""
    notify "Blue Hour Radio — CRITICAL" "Liquidsoap restart failed. Manual intervention needed."
  else
    log_json "\"event\":\"liquidsoap_recovered\""
  fi
fi

# ---------------------------------------------------------------------------
# Check 3: Buffer depth
# ---------------------------------------------------------------------------

# Estimate hours of audio: 128kbps MP3 ≈ 57.6 MB/hour
total_bytes=0
if [ -d "$MUSIC_DIR" ]; then
  music_bytes=$(du -sb "$MUSIC_DIR" 2>/dev/null | awk '{print $1}' || echo 0)
  total_bytes=$((total_bytes + music_bytes))
fi
if [ -d "$BOOTSTRAP_DIR" ]; then
  bootstrap_bytes=$(du -sb "$BOOTSTRAP_DIR" 2>/dev/null | awk '{print $1}' || echo 0)
  total_bytes=$((total_bytes + bootstrap_bytes))
fi

# Convert bytes to hours (57.6 MB/hour = 60,397,977 bytes/hour)
bytes_per_hour=60397977
if [ "$total_bytes" -gt 0 ] && [ "$bytes_per_hour" -gt 0 ]; then
  buffer_hours=$(echo "scale=1; $total_bytes / $bytes_per_hour" | bc 2>/dev/null || echo "?")
else
  buffer_hours="0"
fi

# Alert if below threshold
if [ "$buffer_hours" != "?" ]; then
  buffer_int=${buffer_hours%.*}
  if [ "${buffer_int:-0}" -lt "$BUFFER_ALERT_HOURS" ] 2>/dev/null; then
    log_json "\"event\":\"buffer_alert\",\"hours\":$buffer_hours"
    notify "Blue Hour Radio — Buffer Low" "Only ${buffer_hours}h of audio buffered (threshold: ${BUFFER_ALERT_HOURS}h)"
  fi
fi

# ---------------------------------------------------------------------------
# Check 4: Generation pipeline freshness
# ---------------------------------------------------------------------------

if [ -f "$GENERATION_LOG" ]; then
  # Find last batch_done entry timestamp
  last_batch_ts=$(grep '"event":"batch_done"' "$GENERATION_LOG" 2>/dev/null | tail -1 | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('ts',''))" 2>/dev/null || echo "")

  if [ -n "$last_batch_ts" ]; then
    last_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$last_batch_ts" "+%s" 2>/dev/null || \
                 date -d "$last_batch_ts" "+%s" 2>/dev/null || echo "0")
    now_epoch=$(date +%s)
    age_hours=$(( (now_epoch - last_epoch) / 3600 ))

    if [ "$age_hours" -ge 8 ]; then
      log_json "\"event\":\"generation_stale\",\"last_batch_hours_ago\":$age_hours"
      notify "Blue Hour Radio — Generation Stale" "No batch completed in ${age_hours}h. Check generation pipeline."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

log_json "\"event\":\"heartbeat\",\"icecast\":\"${http_status}\",\"buffer_hours\":\"${buffer_hours}\""
