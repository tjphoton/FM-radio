"""
ACE-Step music generation wrapper.

Takes a text prompt and produces an MP3 file in the staging directory.
Respects max_parallel from config.yaml (managed by generate_batch.py).
"""

import logging
import subprocess
import sys
import time
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent

# Lazy singleton so generate_batch.py can import this module without
# triggering a multi-GB model load at import time.
_pipeline = None


def load_config() -> dict:
    with open(REPO_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from acestep.pipeline_ace_step import ACEStepPipeline
        # MPS (Apple Silicon) is auto-detected; dtype falls back to float32 on MPS.
        # cpu_offload keeps peak RAM reasonable when not on CUDA.
        _pipeline = ACEStepPipeline(cpu_offload=False)
    return _pipeline


def generate_track(
    prompt: str,
    output_path: Path,
    duration_seconds: int = 180,
    genre: str = "jazz",
    bpm: int = 80,
) -> bool:
    """
    Generate a music track using ACE-Step Python API.

    genre and bpm are appended to the prompt text so the model sees them;
    they are not separate CLI flags.
    Returns True on success, False on failure.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_wav = output_path.with_suffix(".tmp.wav")

    # Enrich the prompt with structured tags ACE-Step understands
    full_prompt = prompt
    if genre and genre.lower() not in prompt.lower():
        full_prompt = f"{genre}, {full_prompt}"
    if bpm:
        full_prompt = f"{full_prompt}, {bpm} bpm"

    log.info("Generating track: %s", output_path.name)
    start = time.monotonic()

    try:
        pipeline = _get_pipeline()

        output_paths = pipeline(
            prompt=full_prompt,
            lyrics="",
            audio_duration=float(duration_seconds),
            save_path=str(tmp_wav),
            format="wav",
            infer_step=60,
            guidance_scale=15.0,
        )

        # output_paths is [wav_path, json_path]; pick the first .wav
        actual_wav = None
        for p in output_paths:
            if isinstance(p, str) and p.endswith(".wav"):
                actual_wav = Path(p)
                break

        if actual_wav is None or not actual_wav.exists() or actual_wav.stat().st_size < 10_000:
            log.error("ACE-Step produced no usable output for %s", output_path.name)
            return False

        _convert_to_mp3(actual_wav, output_path)
        actual_wav.unlink(missing_ok=True)
        # Remove the companion JSON params file if present
        json_companion = actual_wav.with_suffix(".json")
        json_companion.unlink(missing_ok=True)

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

    except Exception as exc:
        log.error("Unexpected error generating %s: %s", output_path.name, exc, exc_info=True)
        tmp_wav.unlink(missing_ok=True)
        return False


def _convert_to_mp3(wav_path: Path, mp3_path: Path) -> None:
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
