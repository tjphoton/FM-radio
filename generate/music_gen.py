"""
Music generation client for Blue Hour Radio.

Sends requests to the music_server (localhost:8765) which keeps ACE-Step 1.5
loaded in a separate process. This module has zero ACE-Step / MLX imports.
"""

import logging
import sys
import time
from pathlib import Path

import requests
import yaml

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
_SERVER_URL = None  # resolved lazily from config


def load_config() -> dict:
    with open(REPO_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def _server_url(cfg: dict | None = None) -> str:
    global _SERVER_URL
    if _SERVER_URL is None:
        cfg = cfg or load_config()
        host = cfg.get("music_server", {}).get("host", "127.0.0.1")
        port = cfg.get("music_server", {}).get("port", 8765)
        _SERVER_URL = f"http://{host}:{port}"
    return _SERVER_URL


def server_healthy(cfg: dict | None = None) -> bool:
    try:
        r = requests.get(f"{_server_url(cfg)}/health", timeout=3)
        return r.ok and r.json().get("status") == "ready"
    except Exception:
        return False


def generate_track(
    prompt: str,
    output_path: Path,
    duration_seconds: int = 180,
    genre: str = "jazz",
    bpm: int = 80,
    cfg: dict | None = None,
) -> bool:
    """
    Request a music track from the music server and save it to output_path.

    Returns True on success, False on failure.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    url = f"{_server_url(cfg)}/generate"
    payload = {
        "prompt": prompt,
        "duration": duration_seconds,
        "genre": genre,
        "bpm": bpm if bpm else None,
    }

    log.info("Generating track via music server: %s", output_path.name)
    start = time.monotonic()

    # Allow 6× realtime plus a 5-minute overhead for model scheduling / VAE decode.
    # A 180 s track on a Mac Mini M2 takes ~650 s; a flat 600 s cap reliably kills it.
    http_timeout = duration_seconds * 6 + 300

    try:
        resp = requests.post(url, json=payload, timeout=http_timeout)
    except requests.ConnectionError:
        log.error(
            "Music server not reachable at %s — start it with: bash music_server/start.sh",
            url,
        )
        return False
    except requests.Timeout:
        log.error(
            "Music server timed out after %ds for %s",
            http_timeout,
            output_path.name,
        )
        return False

    if not resp.ok:
        log.error("Music server error %d for %s: %s", resp.status_code, output_path.name, resp.text[:300])
        return False

    if len(resp.content) < 10_000:
        log.error("Music server returned suspiciously small response (%d bytes)", len(resp.content))
        return False

    output_path.write_bytes(resp.content)

    elapsed = time.monotonic() - start
    server_elapsed = float(resp.headers.get("X-Elapsed", elapsed))
    log.info(
        "Track saved in %.1fs (server: %.1fs, %.2fx realtime): %s",
        elapsed,
        server_elapsed,
        server_elapsed / duration_seconds,
        output_path.name,
    )

    if server_elapsed > duration_seconds * 2:
        log.warning(
            "Server took %.1fs for a %ds track (> 2x realtime). "
            "Consider switching to 4h cron with 8 shorter tracks.",
            server_elapsed,
            duration_seconds,
        )

    return True


def benchmark(cfg: dict) -> None:
    staging = REPO_ROOT / cfg["paths"]["staging"]
    staging.mkdir(parents=True, exist_ok=True)
    test_path = staging / "benchmark_track.mp3"

    print(f"Checking music server at {_server_url(cfg)}...")
    if not server_healthy(cfg):
        print(
            "ERROR: Music server is not running or not ready.\n"
            "Start it first: bash music_server/start.sh"
        )
        return

    track_dur = cfg.get("pipeline", {}).get("track_duration_seconds", 90)
    print(f"Running ACE-Step 1.5 benchmark ({track_dur}s track, turbo mode)...")
    start = time.monotonic()
    ok = generate_track(
        prompt="Soft jazz piano trio, slow ballad, intimate late-night feel, brushed drums",
        output_path=test_path,
        duration_seconds=track_dur,
        genre="jazz",
        bpm=65,
        cfg=cfg,
    )
    elapsed = time.monotonic() - start

    if ok:
        ratio = elapsed / track_dur
        print(f"\nBenchmark result: {elapsed:.1f}s for a {track_dur}s track ({ratio:.2f}x realtime)")
        # At 4h cron with 8 × 90 s tracks: need each track < 4h/8 = 1800s (20× realtime)
        # Practical target: < 5× realtime to leave headroom for TTS and Claude calls
        if ratio <= 5.0:
            print("PASS — 4h cron with 8 × 90 s tracks is viable")
        else:
            print("WARN — generation is slow; reduce tracks_per_batch or track_duration_seconds in config.yaml")
        test_path.unlink(missing_ok=True)
    else:
        print("Benchmark FAILED — check music server logs")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()

    if "--benchmark" in sys.argv:
        benchmark(cfg)
    elif len(sys.argv) >= 3:
        prompt = sys.argv[1]
        out = Path(sys.argv[2])
        duration = int(sys.argv[3]) if len(sys.argv) >= 4 else 90
        ok = generate_track(prompt, out, duration_seconds=duration, cfg=cfg)
        sys.exit(0 if ok else 1)
    else:
        print("Usage:")
        print("  python music_gen.py --benchmark")
        print('  python music_gen.py "prompt text" /path/to/output.mp3 [duration_seconds]')
        print()
        print("The music server must be running: bash music_server/start.sh")
