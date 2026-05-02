# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Audio router — exposes Lemonade-routed STT (/voice/transcribe) and TTS
(/voice/speech) plus a small browser test page (/voice/test) so contributors
can verify the audio path end-to-end without writing any code.

Backed by gaia.audio.lemonade_audio — see that module's docstring for the
endpoint contract on the Lemonade side.
"""

from __future__ import annotations
import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from gaia.audio.lemonade_audio import (
    DEFAULT_STT_MODEL,
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_VOICE,
    LemonadeAudioError,
    lemonade_health,
    synthesize_bytes,
    transcribe_bytes,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["audio"])


class SpeechRequest(BaseModel):
    """Body schema for POST /voice/speech (mirrors Lemonade's contract)."""

    input: str = Field(..., min_length=1, description="Text to synthesize")
    voice: str = Field(DEFAULT_TTS_VOICE, description="OpenAI or Kokoro voice name")
    model: str = Field(DEFAULT_TTS_MODEL, description="TTS model (only kokoro-v1 today)")
    response_format: str = Field("mp3", description="mp3 | wav | opus | pcm")
    speed: float = Field(1.0, ge=0.25, le=4.0)


# ────────────────────────── /voice/transcribe (STT) ──────────────────────────
@router.post("/transcribe")
async def voice_transcribe(
    audio: UploadFile = File(..., description="Audio file (WAV preferred, 16kHz mono)"),
    model: str = Form(DEFAULT_STT_MODEL),
    language: str | None = Form("en"),
):
    """Forward an uploaded audio clip to Lemonade /v1/audio/transcriptions.

    Returns ``{"text": "<transcript>", "model": "<used-model>"}``.
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio upload")
    try:
        text = transcribe_bytes(
            audio_bytes,
            filename=audio.filename or "audio.wav",
            model=model,
            language=language or None,
        )
    except LemonadeAudioError as e:
        # 502 = upstream Lemonade failure (we forwarded faithfully but it errored).
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"text": text, "model": model}


# ────────────────────────── /voice/speech (TTS) ──────────────────────────
@router.post("/speech")
async def voice_speech(req: SpeechRequest):
    """Forward a TTS request to Lemonade /v1/audio/speech and stream audio back.

    Body validated by :class:`SpeechRequest`. Returns the raw audio bytes
    with a Content-Type matching ``response_format``.
    """
    try:
        audio = synthesize_bytes(
            req.input,
            voice=req.voice,
            model=req.model,
            response_format=req.response_format,
            speed=req.speed,
        )
    except LemonadeAudioError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    media = {
        "mp3":  "audio/mpeg",
        "wav":  "audio/wav",
        "opus": "audio/opus",
        # PCM has no universally-played MIME w/o sample-rate parameter; clients
        # that ask for raw PCM are expected to know the format from request.
        "pcm":  "application/octet-stream",
    }.get(req.response_format, "application/octet-stream")
    return Response(content=audio, media_type=media)


# ────────────────────────── /voice/health ──────────────────────────
@router.get("/health")
def voice_health():
    """Probe Lemonade and confirm audio path is wired."""
    try:
        body = lemonade_health()
    except LemonadeAudioError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"lemonade": body, "stt_default": DEFAULT_STT_MODEL, "tts_default": DEFAULT_TTS_MODEL}


# ────────────────────────── /voice/test (browser harness) ──────────────────────────
_TEST_HTML_PATH = Path(__file__).parent.parent / "static" / "voice_test.html"


@router.get("/test", response_class=HTMLResponse)
def voice_test_page():
    """Serve a single-page browser harness for STT + TTS smoke testing.

    Open in a browser at http://localhost:<ui-port>/voice/test
    """
    try:
        return HTMLResponse(_TEST_HTML_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=500,
            detail=(
                f"voice_test.html missing at {_TEST_HTML_PATH}. "
                "Reinstall gaia or restore src/gaia/ui/static/voice_test.html."
            ),
        ) from e
