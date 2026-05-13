"""
TTS generation wrappers for Piper (DJ voice) and Kokoro (meditation guide voice).

Both write to the staging directory; the caller handles atomic rename into the library.
"""

import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent


def load_config() -> dict:
    with open(REPO_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Piper TTS — DJ voice
# ---------------------------------------------------------------------------

def synthesize_dj(
    text: str,
    output_path: Path,
    cfg: dict,
) -> bool:
    """
    Synthesize DJ speech using Piper TTS.

    Piper CLI: echo "text" | piper --model /path/to/model.onnx --output_file out.wav
    Then convert WAV → MP3 with ffmpeg.
    """
    piper_cfg = cfg.get("piper", {})
    model_name = piper_cfg.get("model", "en_US-ryan-high.onnx")
    models_dir = Path.home() / ".local" / "share" / "piper"
    model_path = models_dir / model_name

    if not model_path.exists():
        log.error(
            "Piper model not found: %s — run setup/install.sh to download models",
            model_path,
        )
        return False

    # piper-tts is installed into the project venv; prefer the venv binary so
    # this works even when the venv isn't activated in the calling shell.
    venv_piper = REPO_ROOT / ".venv" / "bin" / "piper"
    piper_bin = str(venv_piper) if venv_piper.exists() else "piper"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_wav = output_path.with_suffix(".tmp.wav")

    clean_text = _clean_for_tts(text)

    try:
        # Timeout scales with text length: ~200 chars/s for Piper on M-series,
        # plus 60 s fixed overhead. A 5000-char show script needs ~90 s.
        piper_timeout = max(120, len(clean_text) // 3 + 60)
        proc = subprocess.run(
            [
                piper_bin,
                "--model", str(model_path),
                "--output_file", str(tmp_wav),
            ],
            input=clean_text,
            capture_output=True,
            text=True,
            timeout=piper_timeout,
        )
        if proc.returncode != 0:
            log.error("Piper failed: %s", proc.stderr[-300:])
            return False

        if not tmp_wav.exists() or tmp_wav.stat().st_size < 1000:
            log.error("Piper produced no output for %s", output_path.name)
            return False

        _wav_to_mp3(tmp_wav, output_path)
        tmp_wav.unlink(missing_ok=True)
        log.info("DJ segment generated: %s", output_path.name)
        return True

    except subprocess.TimeoutExpired:
        log.error("Piper timed out after %ds for %s", piper_timeout, output_path.name)
        tmp_wav.unlink(missing_ok=True)
        return False
    except Exception as exc:
        log.error("Piper error for %s: %s", output_path.name, exc)
        tmp_wav.unlink(missing_ok=True)
        return False


# ---------------------------------------------------------------------------
# Kokoro TTS — meditation guide voice
# ---------------------------------------------------------------------------

def synthesize_meditation(
    text: str,
    output_path: Path,
    cfg: dict,
) -> bool:
    """
    Synthesize meditation guide speech using Kokoro-82M.

    Uses the kokoro Python package:
      python -c "from kokoro import KPipeline; ..."
    Writes segments for [pause] markers, then concatenates with silence.
    """
    kokoro_cfg = cfg.get("kokoro", {})
    model = kokoro_cfg.get("model", "af_sky")
    speed = float(kokoro_cfg.get("speed", 0.9))

    output_path.parent.mkdir(parents=True, exist_ok=True)

    segments = _split_on_pauses(text)
    segment_wavs = []

    try:
        for i, segment in enumerate(segments):
            if segment == "[PAUSE]":
                silence_path = _generate_silence(2.0, output_path.parent / f"_silence_{i}.wav")
                segment_wavs.append(silence_path)
            else:
                seg_wav = output_path.parent / f"_seg_{i}.wav"
                ok = _kokoro_synthesize(segment.strip(), seg_wav, model, speed)
                if not ok:
                    return False
                segment_wavs.append(seg_wav)

        if not segment_wavs:
            return False

        concat_wav = output_path.with_suffix(".tmp.wav")
        _concatenate_wavs(segment_wavs, concat_wav)

        _wav_to_mp3(concat_wav, output_path)
        concat_wav.unlink(missing_ok=True)

        for p in segment_wavs:
            p.unlink(missing_ok=True)

        log.info("Meditation segment generated: %s", output_path.name)
        return True

    except Exception as exc:
        log.error("Kokoro error for %s: %s", output_path.name, exc)
        for p in segment_wavs:
            p.unlink(missing_ok=True)
        return False


def _kokoro_synthesize(text: str, output_wav: Path, model: str, speed: float) -> bool:
    script = f"""
import soundfile as sf
from kokoro import KPipeline
pipeline = KPipeline(lang_code='a')
generator = pipeline(
    {repr(text)},
    voice={repr(model)},
    speed={speed},
    split_pattern=r'\\n+'
)
import numpy as np
samples = []
sample_rate = 24000
for gs, ps, audio in generator:
    samples.append(audio)
if samples:
    combined = np.concatenate(samples)
    sf.write({repr(str(output_wav))}, combined, sample_rate)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        log.error("Kokoro synthesis failed: %s", result.stderr[-500:])
        return False
    return output_wav.exists() and output_wav.stat().st_size > 1000


def _generate_silence(duration_seconds: float, output_path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r=24000:cl=mono",
            "-t", str(duration_seconds),
            str(output_path),
        ],
        capture_output=True,
        check=True,
        timeout=10,
    )
    return output_path


def _concatenate_wavs(wav_paths: list[Path], output_path: Path) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for p in wav_paths:
            f.write(f"file '{p}'\n")
        list_file = f.name
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", list_file,
                "-c", "copy",
                str(output_path),
            ],
            capture_output=True,
            check=True,
            timeout=60,
        )
    finally:
        os.unlink(list_file)


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(wav_path),
            "-codec:a", "libmp3lame",
            "-qscale:a", "2",
            "-ar", "44100",
            str(mp3_path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )


def _clean_for_tts(text: str) -> str:
    """Remove markdown, stage directions, and normalize whitespace."""
    text = re.sub(r'\[pause\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[*_`#]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _split_on_pauses(text: str) -> list[str]:
    """Split script on [pause] markers, returning segments and PAUSE sentinels."""
    parts = re.split(r'\[pause\]', text, flags=re.IGNORECASE)
    result = []
    for i, part in enumerate(parts):
        clean = part.strip()
        if clean:
            result.append(clean)
        if i < len(parts) - 1:
            result.append("[PAUSE]")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()

    if len(sys.argv) < 4:
        print("Usage: python tts_gen.py [dj|meditation] 'text to speak' /path/to/output.mp3")
        sys.exit(1)

    voice_type = sys.argv[1]
    text = sys.argv[2]
    output = Path(sys.argv[3])

    if voice_type == "dj":
        ok = synthesize_dj(text, output, cfg)
    elif voice_type == "meditation":
        ok = synthesize_meditation(text, output, cfg)
    else:
        print(f"Unknown voice type: {voice_type} (use 'dj' or 'meditation')")
        sys.exit(1)

    sys.exit(0 if ok else 1)
