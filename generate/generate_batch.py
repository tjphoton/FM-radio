#!/usr/bin/env python3
"""
Main pipeline orchestrator — runs every 6 hours via cron.

Steps:
  0. Fetch weather
  1. claude CLI → music prompts + DJ intro scripts + talk show script
  2. ACE-Step → music MP3s (parallel, max_parallel workers)
  3. Piper/Kokoro → TTS MP3s
  4. Atomic rename from .staging/ into library directories
  5. Log results; alert if buffer < alert threshold
"""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yaml

# Add generate/ to path for sibling imports
sys.path.insert(0, str(Path(__file__).parent))

from claude_scripts import (
    current_show_block,
    fetch_weather,
    generate_dj_intros,
    generate_music_prompts,
    generate_talkshow_script,
    load_config,
    time_context,
)
from music_gen import generate_track
from tts_gen import synthesize_dj, synthesize_meditation

REPO_ROOT = Path(__file__).parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _log_entry(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _log_event(log_path: Path, status: str, item_type: str, name: str, error: str = "") -> None:
    entry = {
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "type": item_type,
        "name": name,
    }
    if error:
        entry["error"] = error
    _log_entry(log_path, entry)


# ---------------------------------------------------------------------------
# Atomic handoff
# ---------------------------------------------------------------------------

def _atomic_move(src: Path, dst_dir: Path) -> Path:
    """
    Move src into dst_dir atomically via os.rename().
    Both must be on the same filesystem — staging dir is inside radio-library/
    to guarantee this (never use /tmp on macOS).
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    os.rename(src, dst)
    return dst


# ---------------------------------------------------------------------------
# Buffer check
# ---------------------------------------------------------------------------

def _estimate_audio_hours(music_dir: Path, bootstrap_dir: Path) -> float:
    """
    Rough estimate of buffered audio hours by summing MP3 sizes.
    Assumes ~1MB per minute at 128kbps (conservative).
    """
    total_bytes = 0
    for d in (music_dir, bootstrap_dir):
        if d.exists():
            for f in d.glob("*.mp3"):
                total_bytes += f.stat().st_size
    mb = total_bytes / (1024 * 1024)
    return mb / 1.0  # ~1 MB/min → hours = MB/60... use actual: 128kbps=1MB/min
    # 128kbps * 60s = 7.68MB/min → 1 hour = ~460MB
    # Simpler conservative estimate: 1MB = ~1 min = 1/60 hour
    return mb / 60.0


def estimate_audio_hours(music_dir: Path, bootstrap_dir: Path) -> float:
    total_bytes = 0
    for d in (music_dir, bootstrap_dir):
        if d.exists():
            for f in d.glob("*.mp3"):
                total_bytes += f.stat().st_size
    # 128kbps MP3: ~960KB/min → 1 hour ≈ 57.6 MB
    mb = total_bytes / (1024 * 1024)
    return mb / 57.6


def _mac_notify(title: str, message: str) -> None:
    try:
        import subprocess
        subprocess.run(
            [
                "osascript", "-e",
                f'display notification "{message}" with title "{title}"',
            ],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main batch
# ---------------------------------------------------------------------------

def run_batch(cfg: dict, dry_run: bool = False) -> dict:
    paths = cfg["paths"]
    staging_music = REPO_ROOT / paths["staging"] / "music"
    staging_dj = REPO_ROOT / paths["staging"] / "dj-segments"
    staging_shows = REPO_ROOT / paths["staging"] / "shows"
    music_dir = REPO_ROOT / paths["music"]
    dj_dir = REPO_ROOT / paths["dj_segments"]
    shows_dir = REPO_ROOT / paths["shows"]
    bootstrap_dir = REPO_ROOT / paths["bootstrap"]
    log_path = REPO_ROOT / paths["logs"] / "generation.log"

    for d in (staging_music, staging_dj, staging_shows):
        d.mkdir(parents=True, exist_ok=True)

    pipeline_cfg = cfg["pipeline"]
    tracks_count = pipeline_cfg["tracks_per_batch"]
    dj_count = pipeline_cfg["dj_segments_per_batch"]
    max_parallel = cfg["music"]["max_parallel"]

    batch_ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    results = {"success": [], "failed": [], "ts": batch_ts}

    _log_entry(log_path, {"ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "event": "batch_start"})

    # Step 0: context
    log.info("=== Batch %s ===", batch_ts)
    loc = cfg["location"]
    weather = fetch_weather(loc["latitude"], loc["longitude"])
    ctx = time_context()
    show_block = current_show_block()
    log.info("Context: %s / %s / block=%s / weather=%s %.1f°C",
             ctx["time_of_day"], ctx["season"], show_block,
             weather["description"], weather["temp"])

    # Step 1: Generate content specs via claude CLI
    log.info("Step 1: Generating content specs via claude CLI...")

    music_prompts = []
    dj_intros = []
    show_script = None

    try:
        music_prompts = generate_music_prompts(cfg, weather, ctx, show_block, count=tracks_count)
        log.info("  Got %d music prompts", len(music_prompts))
    except Exception as exc:
        log.error("Failed to generate music prompts: %s", exc)
        _log_event(log_path, "error", "claude", "music_prompts", str(exc))

    try:
        if music_prompts:
            dj_intros = generate_dj_intros(cfg, weather, ctx, show_block, music_prompts)
            log.info("  Got %d DJ intros", len(dj_intros))
    except Exception as exc:
        log.error("Failed to generate DJ intros: %s", exc)
        _log_event(log_path, "error", "claude", "dj_intros", str(exc))

    try:
        show_script = generate_talkshow_script(cfg, weather, ctx, show_block)
        log.info("  Got talk show script (%d chars)", len(show_script or ""))
    except Exception as exc:
        log.error("Failed to generate talk show script: %s", exc)
        _log_event(log_path, "error", "claude", "talkshow", str(exc))

    if dry_run:
        log.info("DRY RUN — stopping before audio generation")
        return results

    # Step 2: Music generation (parallel)
    log.info("Step 2: Generating music tracks (max %d parallel)...", max_parallel)

    def _gen_track(item: dict) -> tuple[bool, str]:
        idx = item["id"]
        name = f"{batch_ts}_track_{idx:03d}.mp3"
        stage_path = staging_music / name
        ok = generate_track(
            prompt=item["prompt"],
            output_path=stage_path,
            duration_seconds=180,
            genre=item.get("genre", "jazz"),
            bpm=item.get("bpm_estimate", 80),
        )
        if ok:
            _atomic_move(stage_path, music_dir)
            _log_event(log_path, "ok", "music", name)
        else:
            _log_event(log_path, "error", "music", name, "generation failed")
            stage_path.unlink(missing_ok=True)
        return ok, name

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        futures = {pool.submit(_gen_track, item): item for item in music_prompts}
        for fut in as_completed(futures):
            ok, name = fut.result()
            if ok:
                results["success"].append(name)
            else:
                results["failed"].append(name)

    log.info("  Music: %d ok, %d failed", len(results["success"]), len(results["failed"]))

    # Step 3: TTS generation (sequential — fast enough)
    log.info("Step 3: Generating TTS segments...")

    intro_map = {item["song_id"]: item["script"] for item in dj_intros}

    for item in music_prompts:
        idx = item["id"]
        script = intro_map.get(idx, "")
        if not script:
            continue
        name = f"{batch_ts}_intro_{idx:03d}.mp3"
        stage_path = staging_dj / name
        ok = synthesize_dj(script, stage_path, cfg)
        if ok:
            _atomic_move(stage_path, dj_dir)
            _log_event(log_path, "ok", "dj_segment", name)
        else:
            _log_event(log_path, "error", "dj_segment", name, "TTS failed")
            stage_path.unlink(missing_ok=True)

    if show_script:
        show_name = f"{batch_ts}_show_{show_block}.mp3"
        stage_path = staging_shows / show_name
        voice_fn = (
            synthesize_meditation
            if show_block == "meditation_mondays"
            else synthesize_dj
        )
        ok = voice_fn(show_script, stage_path, cfg)
        if ok:
            _atomic_move(stage_path, shows_dir)
            _log_event(log_path, "ok", "show", show_name)
        else:
            _log_event(log_path, "error", "show", show_name, "TTS failed")
            stage_path.unlink(missing_ok=True)

    # Step 5: Buffer check
    hours_buffered = estimate_audio_hours(music_dir, bootstrap_dir)
    alert_threshold = pipeline_cfg["buffer_alert_hours"]
    log.info("Buffer estimate: %.1f hours", hours_buffered)

    if hours_buffered < alert_threshold:
        msg = f"Buffer low: {hours_buffered:.1f}h < {alert_threshold}h threshold"
        log.warning(msg)
        _mac_notify("Blue Hour Radio — Buffer Alert", msg)
        _log_entry(log_path, {
            "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": "buffer_alert",
            "hours": hours_buffered,
        })

    _log_entry(log_path, {
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": "batch_done",
        "ok": len(results["success"]),
        "failed": len(results["failed"]),
        "buffer_hours": round(hours_buffered, 1),
    })

    log.info("=== Batch complete: %d ok, %d failed ===",
             len(results["success"]), len(results["failed"]))
    return results


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    cfg = load_config()
    results = run_batch(cfg, dry_run=dry_run)
    failed = len(results["failed"])
    sys.exit(1 if failed > 0 else 0)
