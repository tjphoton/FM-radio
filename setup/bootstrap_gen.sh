#!/bin/bash
# Blue Hour Radio — Bootstrap Playlist Generator
# Generates a 2-hour emergency fallback library of AI music.
# This runs ONCE during setup. These files are never deleted — they cover
# any gap where the generation pipeline fails.
#
# Usage: bash setup/bootstrap_gen.sh
#
# Takes: roughly 2-8 hours depending on ACE-Step speed on your hardware.
# Run it the night before you want the station live.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BOOTSTRAP_DIR="$REPO_ROOT/radio-library/bootstrap"
VENV="$REPO_ROOT/.venv"
PYTHON="${VENV}/bin/python"

mkdir -p "$BOOTSTRAP_DIR"

blue()  { printf '\033[34m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

# 2 hours @ ~3 min/track = 40 tracks needed for coverage
# We generate 24 tracks: 8 per mood block (morning/evening/night)
# This gives ~72 minutes of music — supplement with real generation ASAP.
# Generate more by re-running this script (tracks accumulate).

TRACKS=(
  # Morning block — upbeat folk and country
  "Acoustic fingerpicked guitar with light banjo, morning folk song, hopeful and bright, 95 BPM, warm"
  "Country folk duet, acoustic guitar and fiddle, sunny and open, 100 BPM, uplifting"
  "Fingerpicked acoustic guitar solo, gentle folk melody, soft morning light feel, 85 BPM"
  "Country guitar and mandolin, rolling rhythmic groove, pastoral and warm, 90 BPM"
  "Upbeat folk with strummed guitar and light percussion, breezy and optimistic, 100 BPM"
  "Acoustic guitar instrumental, folk waltz, tender and reflective, 75 BPM"
  "Country acoustic guitar with brushed drums, easy morning groove, 88 BPM"
  "Folk guitar and harmonica, wandering melody, open space feel, 80 BPM"

  # Evening / jazz block
  "Jazz piano trio, slow ballad, intimate and late-night, brushed drums and upright bass, 60 BPM"
  "Cool jazz guitar and piano, mellow evening improvisation, 70 BPM, blue and reflective"
  "Jazz quartet, warm brass and piano, mid-tempo swing feel, 80 BPM, dusk atmosphere"
  "Solo piano jazz, slow and spacious, minor key, emotional and searching, 55 BPM"
  "Jazz flute and guitar duo, bossa nova feel, light and airy, 75 BPM"
  "Upright bass jazz solo with brushed drums, deep and resonant, 65 BPM"
  "Jazz piano and trumpet, soft ballad, romantic and wistful, 58 BPM"
  "Cool jazz piano trio, medium tempo, swinging and warm, 85 BPM"

  # Night / ambient block
  "Ambient country guitar, slow arpeggios, quiet and meditative, 45 BPM, sleep-friendly"
  "Soft acoustic guitar drone, gentle fingerpicking, minimal, 40 BPM, peaceful"
  "Ambient folk, sparse guitar and soft pads, drifting and calm, 38 BPM"
  "Night jazz piano, sparse and slow, impressionistic, 48 BPM, dreamlike"
  "Acoustic guitar and cello, slow and tender, minimal arrangement, 42 BPM"
  "Ambient country pedal steel and guitar, slow and melancholic, 45 BPM"
  "Solo guitar at night, slow and introspective, fingerstyle, 38 BPM"
  "Soft piano and ambient texture, 40 BPM, quiet and still, sleep-friendly"
)

total=${#TRACKS[@]}
blue "Generating $total bootstrap tracks in $BOOTSTRAP_DIR"
blue "This may take several hours. Leave it running."
echo ""

success=0
failed=0

for i in "${!TRACKS[@]}"; do
  idx=$((i + 1))
  prompt="${TRACKS[$i]}"
  filename="bootstrap_$(printf '%03d' "$idx").mp3"
  output="$BOOTSTRAP_DIR/$filename"

  if [ -f "$output" ]; then
    echo "  [$idx/$total] Already exists: $filename"
    success=$((success + 1))
    continue
  fi

  echo "  [$idx/$total] Generating: $filename"
  echo "            Prompt: ${prompt:0:60}..."

  if "$PYTHON" "$REPO_ROOT/generate/music_gen.py" "$prompt" "$output"; then
    echo "            OK"
    success=$((success + 1))
  else
    echo "            FAILED — continuing"
    failed=$((failed + 1))
  fi
done

echo ""
if [ "$success" -gt 0 ]; then
  # Estimate duration: 24 tracks * 3 min = 72 min ≈ 1.2 hours
  hours_estimate=$(echo "scale=1; $success * 3 / 60" | bc 2>/dev/null || echo "?")
  green "Bootstrap complete: $success tracks (~${hours_estimate}h of audio)"
fi

if [ "$failed" -gt 0 ]; then
  echo "  $failed tracks failed — re-run this script to retry (existing tracks are skipped)"
fi

echo ""
echo "Bootstrap library: $BOOTSTRAP_DIR"
echo "Track count: $(ls "$BOOTSTRAP_DIR"/*.mp3 2>/dev/null | wc -l | tr -d ' ') files"
