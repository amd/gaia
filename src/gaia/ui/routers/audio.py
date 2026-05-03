# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Audio router — exposes STT (/voice/transcribe), TTS (/voice/speech), a
health probe (/voice/health), and a browser test page (/voice/test) so
contributors can verify the audio path end-to-end without writing code.

Two backends are supported:

  - ``lemonade`` (default) — routes to :mod:`gaia.audio.lemonade_audio`,
    which POSTs to Lemonade Server's OpenAI-compatible /v1/audio/* endpoints.
    Single inference server for LLM + STT + TTS. Required for the
    ruggedized-Ryzen-AI fielding story. **Currently does not work on macOS**
    (Lemonade's ``whispercpp`` recipe is Linux/Windows-only as of v10.2).

  - ``in-process`` — falls through to the legacy
    :class:`gaia.audio.whisper_asr.WhisperAsr` and
    :class:`gaia.audio.kokoro_tts.KokoroTTS` classes which load the
    ``openai-whisper`` and ``kokoro`` Python packages locally. Heavier
    install footprint (torch, CUDA wheels, spaCy) but works on macOS where
    Lemonade audio doesn't.

Backend is selected at request time via the ``GAIA_VOICE_BACKEND`` env var
(``lemonade`` or ``in-process``; default ``lemonade``). Both backends are
shipped together until Lemonade adds macOS support for whispercpp / Kokoro;
no silent fallback between them — if the selected backend is unreachable
or its deps are missing, the route returns a clear error.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
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


# ────────────────────────── Backend selector ──────────────────────────
_LEMONADE = "lemonade"
_IN_PROCESS = "in-process"


def _backend() -> str:
    """Resolve the requested voice backend. Default ``lemonade``."""
    val = os.getenv("GAIA_VOICE_BACKEND", _LEMONADE).lower()
    if val not in (_LEMONADE, _IN_PROCESS):
        logger.warning(
            "Unknown GAIA_VOICE_BACKEND=%r; falling back to %r", val, _LEMONADE
        )
        return _LEMONADE
    return val


# Map Lemonade STT model names → openai-whisper-package names. Used when
# the in-process backend is selected.
_WHISPER_MODEL_TO_PACKAGE = {
    "Whisper-Tiny":  "tiny",
    "Whisper-Base":  "base",
    "Whisper-Small": "small",
    "Whisper-Large": "large",
}


def _to_whisper_package_name(name: str) -> str:
    """Lemonade name → in-process whisper-package name. Pass-through if already short."""
    return _WHISPER_MODEL_TO_PACKAGE.get(name, name.lower())


# ────────────────────────── /voice/transcribe (STT) ──────────────────────────
@router.post("/transcribe")
async def voice_transcribe(
    audio: UploadFile = File(..., description="Audio file (WAV preferred, 16kHz mono)"),
    model: str = Form(DEFAULT_STT_MODEL),
    language: str | None = Form("en"),
):
    """Transcribe an uploaded audio clip via the configured backend.

    Returns ``{"text": "<transcript>", "model": "<used-model>", "backend": "<lemonade|in-process>"}``.
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio upload")

    backend = _backend()

    if backend == _IN_PROCESS:
        # WhisperAsr loads the openai-whisper model in-process. Heavier on
        # cold start (~3-10s for the model load on first call), but works
        # on macOS where Lemonade's whispercpp recipe currently does not.
        try:
            from gaia.audio.whisper_asr import WhisperAsr
        except ImportError as e:
            raise HTTPException(
                status_code=503,
                detail=(
                    "in-process voice backend missing deps — install with "
                    '`uv pip install -e ".[talk]"`. ' + str(e)
                ),
            ) from e

        package_name = _to_whisper_package_name(model)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        try:
            asr = WhisperAsr(model_size=package_name)
            text = asr.transcribe_file(tmp_path)
        except FileNotFoundError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except ImportError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return {"text": text, "model": model, "backend": _IN_PROCESS}

    # Default: Lemonade-routed path.
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
    return {"text": text, "model": model, "backend": _LEMONADE}


# ────────────────────────── /voice/speech (TTS) ──────────────────────────
@router.post("/speech")
async def voice_speech(req: SpeechRequest):
    """Synthesize speech via the configured backend.

    Body validated by :class:`SpeechRequest`. Returns the raw audio bytes
    with a Content-Type matching ``response_format``.

    In-process backend caveat: Kokoro produces float32 audio at 24 kHz; we
    encode it server-side as WAV regardless of ``response_format`` (no MP3
    encoder is bundled with the in-process path). The response sets
    ``Content-Type: audio/wav`` in that case so the browser plays it correctly.
    """
    backend = _backend()

    if backend == _IN_PROCESS:
        try:
            from gaia.audio.kokoro_tts import KokoroTTS
        except ImportError as e:
            raise HTTPException(
                status_code=503,
                detail=(
                    "in-process voice backend missing deps — install with "
                    '`uv pip install -e ".[talk]"`. ' + str(e)
                ),
            ) from e

        try:
            import soundfile as sf
        except ImportError as e:
            raise HTTPException(
                status_code=503,
                detail="`soundfile` is required to encode in-process TTS output to WAV. " + str(e),
            ) from e

        try:
            tts = KokoroTTS()
            # KokoroTTS catalog uses Kokoro-native names ("af_bella" etc.). If
            # the caller passed an OpenAI voice ("shimmer"), Kokoro will fall
            # back to whatever default it has — we set the requested voice
            # explicitly so the failure mode is the caller's, not ours.
            tts.set_voice(req.voice)
            audio_array, _phonemes, meta = tts.generate_speech(req.input)
        except ImportError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001 — Kokoro raises various errors
            raise HTTPException(status_code=502, detail=f"in-process TTS failed: {e}") from e

        sample_rate = meta.get("sample_rate", 24_000)
        buf = io.BytesIO()
        sf.write(buf, audio_array, samplerate=sample_rate, format="WAV", subtype="PCM_16")
        return Response(content=buf.getvalue(), media_type="audio/wav")

    # Default: Lemonade-routed path.
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
        # PCM has no universally-played MIME without sample-rate parameter;
        # clients asking for raw PCM are expected to know the format.
        "pcm":  "application/octet-stream",
    }.get(req.response_format, "application/octet-stream")
    return Response(content=audio, media_type=media)


# ────────────────────────── /voice/health ──────────────────────────
@router.get("/health")
def voice_health():
    """Report the active backend and probe its readiness.

    For the ``lemonade`` backend, this proxies to Lemonade's /api/v1/health.
    For ``in-process``, it just confirms the local imports resolve.
    """
    backend = _backend()
    if backend == _IN_PROCESS:
        deps_ok = True
        detail: str | None = None
        try:
            import whisper  # noqa: F401  (required by WhisperAsr)
            import kokoro   # noqa: F401  (required by KokoroTTS)
        except ImportError as e:
            deps_ok = False
            detail = (
                'in-process backend missing deps — install with `uv pip install -e ".[talk]"`. '
                + str(e)
            )
        return {
            "backend": _IN_PROCESS,
            "ready": deps_ok,
            "detail": detail,
            "stt_default": "small",
            "tts_default": "af_bella",
        }

    # Default: lemonade
    try:
        body = lemonade_health()
    except LemonadeAudioError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "backend": _LEMONADE,
        "lemonade": body,
        "stt_default": DEFAULT_STT_MODEL,
        "tts_default": DEFAULT_TTS_MODEL,
    }


# ────────────────────────── /voice/test (browser harness) ──────────────────────────
_TEST_HTML_PATH = Path(__file__).parent.parent / "static" / "voice_test.html"


@router.get("/test", response_class=HTMLResponse)
def voice_test_page():
    """Serve a single-page browser harness for STT + TTS smoke testing.

    Open in a browser at ``http://localhost:<ui-port>/voice/test``. The page
    auto-probes /voice/health on load and displays which backend is active.
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
