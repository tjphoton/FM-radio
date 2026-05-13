"""
ACE-Step 1.5 music generation wrapper.

Uses the ACE-Step 1.5 Python API directly (AceStepHandler + generate_music).
Runs in turbo mode (8 steps) with MLX acceleration on Apple Silicon.
"""

import logging
import os
import sys
import time
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent

# Lazy singleton: avoid model load at import time.
_dit_handler = None


def load_config() -> dict:
    with open(REPO_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def _get_handler():
    global _dit_handler
    if _dit_handler is None:
        # MPS default watermark is 0.8 × available RAM (~9 GB on 16 GB machines).
        # The turbo DiT alone exceeds that threshold; disable the cap so MPS can
        # use unified memory freely. The OS will page if truly out of RAM.
        os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")

        from acestep.handler import AceStepHandler

        handler = AceStepHandler()
        # cpu_offload=True streams model layers CPU↔MPS so peak GPU memory stays lower.
        # use_mlx_dit=True enables native MLX acceleration on Apple Silicon.
        status, ok = handler.initialize_service(
            project_root=None,
            config_path="acestep-v15-turbo",
            device="auto",
            use_mlx_dit=True,
            cpu_offload=True,
        )
        if not ok:
            raise RuntimeError(f"ACE-Step failed to initialize: {status}")
        log.info("ACE-Step 1.5 initialized: %s", status)
        _dit_handler = handler
    return _dit_handler


def generate_track(
    prompt: str,
    output_path: Path,
    duration_seconds: int = 180,
    genre: str = "jazz",
    bpm: int = 80,
) -> bool:
    """
    Generate a music track using ACE-Step 1.5.

    Saves an MP3 directly to output_path.
    Returns True on success, False on failure.
    """
    from acestep.inference import GenerationConfig, GenerationParams, generate_music

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Enrich prompt with genre tag if not already mentioned
    full_prompt = prompt
    if genre and genre.lower() not in prompt.lower():
        full_prompt = f"{genre}, {full_prompt}"

    log.info("Generating track: %s", output_path.name)
    start = time.monotonic()

    # Use a temp dir so generate_music can save the UUID-named file,
    # then we rename it to the caller's desired path.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            params = GenerationParams(
                caption=full_prompt,
                lyrics="[Instrumental]",
                duration=float(duration_seconds),
                bpm=bpm if bpm else None,
                inference_steps=8,       # turbo mode
                thinking=False,           # skip LLM reasoning — not needed for bg music
                enable_normalization=True,
            )
            config = GenerationConfig(
                batch_size=1,
                use_random_seed=True,
                audio_format="mp3",
                mp3_bitrate="192k",
                mp3_sample_rate=44100,
            )

            handler = _get_handler()
            result = generate_music(
                dit_handler=handler,
                llm_handler=None,
                params=params,
                config=config,
                save_dir=tmp_dir,
            )

            if not result.success or not result.audios:
                log.error("ACE-Step generation failed for %s: %s", output_path.name, result.error)
                return False

            saved_path = result.audios[0].get("path", "")
            if not saved_path or not Path(saved_path).exists():
                log.error("ACE-Step produced no output file for %s", output_path.name)
                return False

            # Move out of temp dir before it's cleaned up
            import shutil
            shutil.copy2(saved_path, output_path)

        except Exception as exc:
            log.error("Unexpected error generating %s: %s", output_path.name, exc, exc_info=True)
            return False

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


def benchmark(cfg: dict) -> None:
    staging = REPO_ROOT / cfg["paths"]["staging"]
    staging.mkdir(parents=True, exist_ok=True)
    test_path = staging / "benchmark_track.mp3"

    print("Running ACE-Step 1.5 benchmark (3-minute track, turbo mode)...")
    print("First run will download the model (~4 GB) — this may take several minutes.")
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
        print("Benchmark FAILED — check logs above")


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
