"""Kokoro TTS with OpenAI-compatible /v1/audio/speech endpoint."""

import io
import os
import time
import uuid
from contextlib import asynccontextmanager

import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

SAMPLE_RATE = 24000
KOKORO_VOICES: list[str] = []


def _discover_voices() -> list[str]:
    try:
        from huggingface_hub import list_repo_files
        files = list_repo_files("hexgrad/Kokoro-82M", repo_type="model")
        voices = sorted(
            f.replace("voices/", "").replace(".pt", "")
            for f in files
            if f.startswith("voices/") and f.endswith(".pt")
        )
        return voices
    except Exception:
        return [
            "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
            "am_adam", "am_michael",
            "bf_emma", "bf_isabella",
            "bm_george", "bm_lewis",
        ]


_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from kokoro import KPipeline
        _pipeline = KPipeline(lang_code="a", device="cpu")
    return _pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    global KOKORO_VOICES
    KOKORO_VOICES = _discover_voices()
    print(f"Kokoro TTS: {len(KOKORO_VOICES)} voices available")
    yield
    global _pipeline
    _pipeline = None


app = FastAPI(lifespan=lifespan)


class SpeechRequest(BaseModel):
    model: str = "kokoro"
    input: str
    voice: str = "af_heart"
    response_format: str = "wav"
    speed: float = 1.0


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "kokoro", "object": "model", "owned_by": "kokoro-tts"}
        ],
    }


@app.get("/voices")
def list_voices():
    return {"voices": KOKORO_VOICES}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    if req.voice not in KOKORO_VOICES:
        raise HTTPException(status_code=400, detail=f"Unknown voice: {req.voice}. Available: {KOKORO_VOICES}")

    pipeline = _get_pipeline()
    chunks = []
    t0 = time.monotonic()

    try:
        for result in pipeline(req.input, voice=req.voice, speed=req.speed):
            chunks.append(result.audio.numpy())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not chunks:
        raise HTTPException(status_code=500, detail="No audio generated")

    import numpy as np
    audio = np.concatenate(chunks)
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format=req.response_format.upper())
    buf.seek(0)

    elapsed = time.monotonic() - t0
    print(f"tts: {len(req.input)} chars -> {len(audio)/SAMPLE_RATE:.1f}s audio in {elapsed:.1f}s (voice={req.voice})")

    return Response(
        content=buf.getvalue(),
        media_type=f"audio/{req.response_format}",
        headers={
            "X-Request-Id": str(uuid.uuid4()),
            "X-Audio-Duration": f"{len(audio)/SAMPLE_RATE:.2f}",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8880)
