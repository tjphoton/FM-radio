"""
ACE-Step music generation wrapper.

Takes a text prompt and produces an MP3 file in the staging directory.
Respects max_parallel from config.yaml (managed by generate_batch.py).
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent


def load_config() -> dict:
    with open(REPO_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def generate_track(
    prompt: str,
    output_path: Path,
    duration_seconds: int = 180,
    genre: str = "jazz",
    bpm: int = 80,
) -> bool:
    """
    Generate a music track using ACE-Step.

    Returns True on success, False on failure.
    output_path should be in the staging directory (will be renamed atomically by caller).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.wav")

    log.info("Generating track: %s", output_path.name)
    start = time.monotonic()

    try:
        cmd = _build_ace_step_cmd(prompt, tmp_path, duration_seconds, genre, bpm)
        log.debug("CMD: %s", " ".join(str(c) for c in cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode != 0:
            log.error(
                "ACE-Step failed for %s:\nstdout: %s\nstderr: %s",
                output_path.name,
                result.stdout[-500:],
                result.stderr[-500:],
            )
            return False

        if not tmp_path.exists() or tmp_path.stat().st_size < 10_000:
            log.error("ACE-Step produced no output for %s", output_path.name)
            return False

        _convert_to_mp3(tmp_path, output_path)
        tmp_path.unlink(missing_ok=True)

        elapsed = time.monotonic() - start
        log.info(
            "Track generated in %.1fs (%.1fx realtime): %s",
            elapsed,
            elapsed / duration_seconds,
            output_path.name,
        )

        if elapsed > duration_seconds * 2:
            log.warning(
                "BENCHMARK WARNING: %.1fs > 2x realtime for %ds track. "
                "Consider switching to 4h cron + 8 shorter tracks.",
                elapsed,
                duration_seconds,
            )

        return True

    except subprocess.TimeoutExpired:
        log.error("ACE-Step timed out for %s", output_path.name)
        tmp_path.unlink(missing_ok=True)
        return False
    except Exception as exc:
        log.error("Unexpected error generating %s: %s", output_path.name, exc)
        tmp_path.unlink(missing_ok=True)
        return False


def _build_ace_step_cmd(
    prompt: str,
    output_wav: Path,
    duration_seconds: int,
    genre: str,
    bpm: int,
) -> list:
    """
    Build the ACE-Step CLI command.
    ACE-Step is installed via: pip install ace-step
    CLI: acestep --prompt "..." --duration 180 --output out.wav [--genre jazz] [--bpm 80]
    """
    cmd = [
        "acestep",
        "--prompt", prompt,
        "--duration", str(duration_seconds),
        "--output", str(output_wav),
    ]
    if genre:
        cmd += ["--genre", genre]
    if bpm:
        cmd += ["--bpm", str(bpm)]
    return cmd


def _convert_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    """Convert WAV to MP3 using ffmpeg (128k, good quality/size balance for streaming)."""
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(wav_path),
            "-codec:a", "libmp3lame",
            "-qscale:a", "2",
            "-ar", "44100",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr[-300:]}")


def benchmark(cfg: dict) -> None:
    """Quick benchmark: generate a short track and report wall-clock time."""
    staging = REPO_ROOT / cfg["paths"]["staging"]
    staging.mkdir(parents=True, exist_ok=True)
    test_path = staging / "benchmark_track.mp3"

    print("Running ACE-Step benchmark (3-minute track)...")
    start = time.monotonic()
    ok = generate_track(
        prompt="Soft jazz piano trio, slow ballad, intimate late-night feel, brushed drums",
        output_path=test_path,
        duration_seconds=180,
        genre="jazz",
        bpm=65,
    )
    elapsed = time.monotonic() - start

    if ok:
        print(f"\nBenchmark result: {elapsed:.1f}s for a 180s track ({elapsed/180:.2f}x realtime)")
        if elapsed <= 360:
            print("PASS — 6h cron is viable (< 2x realtime)")
        else:
            print("FAIL — switch to 4h cron with 8 shorter tracks + bootstrap supplement")
        test_path.unlink(missing_ok=True)
    else:
        print("Benchmark FAILED — check ACE-Step installation")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()

    if "--benchmark" in sys.argv:
        benchmark(cfg)
    elif len(sys.argv) >= 3:
        prompt = sys.argv[1]
        out = Path(sys.argv[2])
        ok = generate_track(prompt, out)
        sys.exit(0 if ok else 1)
    else:
        print("Usage:")
        print("  python music_gen.py --benchmark")
        print('  python music_gen.py "prompt text" /path/to/output.mp3')
