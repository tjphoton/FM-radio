"""
Blue Hour Radio — Music Generation Server

Loads ACE-Step 1.5 once on startup and keeps it warm.
The main FM radio pipeline (generate_batch.py) is a thin HTTP client;
it never imports ACE-Step or MLX directly.

Endpoints:
  GET  /health          → {"status": "ready"|"loading", "model": "..."}
  POST /generate        → MP3 audio bytes (Content-Type: audio/mpeg)
                          Header X-Elapsed: <seconds as float>
"""

import logging
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

# Must be set before any torch/MPS import.
# Disables the 80% MPS memory watermark so unified memory is used freely.
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")
# Route ACE-Step to the MLX backend on Apple Silicon.
os.environ.setdefault("LM_BACKEND", "mlx")

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

_dit_handler = None
_model_name = "acestep-v15-turbo"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _dit_handler
    log.info("Loading ACE-Step 1.5 (%s)…", _model_name)
    t0 = time.monotonic()

    from acestep.handler import AceStepHandler

    handler = AceStepHandler()
    status, ok = handler.initialize_service(
        project_root=None,
        config_path=_model_name,
        device="auto",
        use_mlx_dit=True,
    )
    if not ok:
        log.error("ACE-Step initialization failed: %s", status)
        raise RuntimeError(status)

    _dit_handler = handler
    log.info("ACE-Step ready in %.1fs: %s", time.monotonic() - t0, status)
    yield
    log.info("Music server shutting down.")


app = FastAPI(title="Blue Hour Music Server", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    prompt: str
    duration: int = 180     # seconds
    bpm: int | None = None  # None → model auto-selects
    genre: str = "jazz"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ready" if _dit_handler is not None else "loading",
        "model": _model_name,
    }


@app.post("/generate")
def generate(req: GenerateRequest) -> Response:
    """Generate a music track and return raw MP3 bytes."""
    if _dit_handler is None:
        raise HTTPException(503, detail="Model not ready yet")

    from acestep.inference import GenerationConfig, GenerationParams, generate_music

    full_prompt = req.prompt
    if req.genre and req.genre.lower() not in full_prompt.lower():
        full_prompt = f"{req.genre}, {full_prompt}"

    params = GenerationParams(
        caption=full_prompt,
        lyrics="[Instrumental]",
        duration=float(req.duration),
        bpm=req.bpm,
        inference_steps=8,   # turbo
        thinking=False,       # no LLM reasoning needed for background music
        enable_normalization=True,
    )
    config = GenerationConfig(
        batch_size=1,
        use_random_seed=True,
        audio_format="mp3",
        mp3_bitrate="192k",
        mp3_sample_rate=44100,
    )

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp_dir:
        result = generate_music(
            dit_handler=_dit_handler,
            llm_handler=None,
            params=params,
            config=config,
            save_dir=tmp_dir,
        )
        elapsed = time.monotonic() - t0

        if not result.success or not result.audios:
            raise HTTPException(500, detail=result.error or "generation produced no audio")

        saved = result.audios[0].get("path", "")
        if not saved or not Path(saved).exists():
            raise HTTPException(500, detail="output file missing after generation")

        audio_bytes = Path(saved).read_bytes()

    log.info("Generated %.0fs track in %.1fs (%.2fx realtime)", req.duration, elapsed, elapsed / req.duration)
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={"X-Elapsed": f"{elapsed:.2f}"},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Blue Hour Music Generation Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default="acestep-v15-turbo",
                        help="ACE-Step config to load (e.g. acestep-v15-turbo)")
    args = parser.parse_args()

    _model_name = args.model
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
