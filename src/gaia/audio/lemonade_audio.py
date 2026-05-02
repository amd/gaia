# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Lemonade-routed audio: speech-to-text and text-to-speech via the OpenAI-
compatible /v1/audio endpoints exposed by Lemonade Server.

Why this module exists alongside whisper_asr.py and kokoro_tts.py:
  Those modules import the `whisper` and `kokoro` Python packages and run the
  models in-process. That means each GAIA process loads its own copy of those
  models — wasteful when Lemonade is already running for the LLM. Lemonade
  exposes Whisper and Kokoro as REST endpoints (port 13305 by default), so a
  single Lemonade instance can serve LLM + STT + TTS to multiple clients.

  This module is the thin HTTP client that GAIA's Agent UI (and downstream
  consumers like Beacon) use to talk to those endpoints. The original
  whisper_asr / kokoro_tts modules remain for use cases that need in-process
  execution (e.g., the `gaia talk` standalone CLI without a running server).

Endpoints:
  POST /v1/audio/transcriptions   multipart: file=<wav>, model=Whisper-Small
                                  → {"text": "..."}
  POST /v1/audio/speech           JSON: {model:"kokoro-v1", input, voice,
                                          response_format, speed}
                                  → raw audio bytes (mp3/wav/opus/pcm)
  WS   /realtime                  streaming STT (OpenAI realtime-compatible);
                                  not yet wrapped here.

Models auto-download on first request (~30s for Whisper-Small).
"""

from __future__ import annotations
import os
from pathlib import Path

import httpx


LEMONADE_URL = os.getenv("LEMONADE_BASE_URL", "http://localhost:13305")

DEFAULT_STT_MODEL = "Whisper-Small"   # English; lighter+faster than -Large
DEFAULT_TTS_MODEL = "kokoro-v1"        # only TTS model Lemonade exposes today
DEFAULT_TTS_VOICE = "shimmer"


class LemonadeAudioError(RuntimeError):
    """Raised on Lemonade audio-endpoint failures.

    GAIA's no-silent-fallback policy applies: callers must handle this error
    explicitly (retry, surface to user, fall back to text input). We do NOT
    silently fall back to whisper_asr / kokoro_tts — those modules have a
    different operational contract (in-process model loading) and using them
    as a fallback would mask real Lemonade misconfiguration.
    """


# ────────────────────────── Speech-to-text ──────────────────────────
def transcribe(
    audio_path: str | Path,
    *,
    model: str = DEFAULT_STT_MODEL,
    language: str | None = "en",
    base_url: str = LEMONADE_URL,
    timeout: float = 60.0,
) -> str:
    """POST a WAV file to Lemonade /v1/audio/transcriptions.

    Args:
        audio_path: path to a 16kHz mono WAV file (push-to-talk recordings).
        model: ``Whisper-Tiny`` | ``Whisper-Base`` | ``Whisper-Small`` |
               ``Whisper-Large`` (or any other Whisper variant Lemonade serves).
        language: ISO 639-1 code; defaults to ``"en"``. Pass ``None`` to
                  auto-detect.
        base_url: Lemonade server URL.
        timeout: HTTP timeout in seconds.

    Returns:
        The transcribed text.

    Raises:
        FileNotFoundError: if ``audio_path`` does not exist.
        LemonadeAudioError: server unreachable, non-200 status, or malformed
                            response.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    files = {"file": (audio_path.name, audio_path.read_bytes(), "audio/wav")}
    data: dict[str, str] = {"model": model}
    if language is not None:
        data["language"] = language

    try:
        r = httpx.post(
            f"{base_url}/v1/audio/transcriptions",
            files=files,
            data=data,
            timeout=timeout,
        )
    except httpx.RequestError as e:
        raise LemonadeAudioError(
            f"Lemonade STT unreachable at {base_url} — start the server with "
            f"`lemonade-server serve`, or set LEMONADE_BASE_URL. Original: {e}"
        ) from e

    if r.status_code != 200:
        raise LemonadeAudioError(
            f"Lemonade STT returned {r.status_code}: {r.text[:200]}. "
            f"Common causes: model '{model}' not yet downloaded "
            f"(first request triggers a ~30s auto-download), or audio not WAV/16kHz mono."
        )

    body = r.json()
    if "text" not in body:
        raise LemonadeAudioError(f"Unexpected STT response shape: {body!r}")
    # Lemonade returns {"text": null} when no speech was detected; normalize
    # to empty string so downstream string-handling doesn't NPE.
    return body["text"] or ""


def transcribe_bytes(
    audio_bytes: bytes,
    filename: str = "audio.wav",
    *,
    model: str = DEFAULT_STT_MODEL,
    language: str | None = "en",
    base_url: str = LEMONADE_URL,
    timeout: float = 60.0,
) -> str:
    """Like :func:`transcribe` but takes raw WAV bytes (no temp file needed).

    Useful for FastAPI handlers that accept :class:`UploadFile` and want to
    forward the bytes directly to Lemonade without disk I/O.
    """
    files = {"file": (filename, audio_bytes, "audio/wav")}
    data: dict[str, str] = {"model": model}
    if language is not None:
        data["language"] = language

    try:
        r = httpx.post(
            f"{base_url}/v1/audio/transcriptions",
            files=files,
            data=data,
            timeout=timeout,
        )
    except httpx.RequestError as e:
        raise LemonadeAudioError(
            f"Lemonade STT unreachable at {base_url} — start the server with "
            f"`lemonade-server serve`, or set LEMONADE_BASE_URL. Original: {e}"
        ) from e

    if r.status_code != 200:
        raise LemonadeAudioError(
            f"Lemonade STT returned {r.status_code}: {r.text[:200]}"
        )
    body = r.json()
    if "text" not in body:
        raise LemonadeAudioError(f"Unexpected STT response shape: {body!r}")
    # Lemonade returns {"text": null} when no speech was detected; normalize
    # to empty string so downstream string-handling doesn't NPE.
    return body["text"] or ""


# ────────────────────────── Text-to-speech ──────────────────────────
def synthesize(
    text: str,
    out_path: str | Path,
    *,
    voice: str = DEFAULT_TTS_VOICE,
    model: str = DEFAULT_TTS_MODEL,
    response_format: str = "mp3",
    speed: float = 1.0,
    base_url: str = LEMONADE_URL,
    timeout: float = 60.0,
) -> str:
    """POST text to Lemonade /v1/audio/speech and write the audio bytes.

    Args:
        text: text to synthesize. Keep ≤ ~500 chars for low-latency replies.
        out_path: file path to write the audio bytes to.
        voice: OpenAI voices (``"alloy"``, ``"shimmer"``, ``"ash"``, …) or
               Kokoro voices (``"af_sky"``, ``"am_echo"``, …).
        model: must be ``"kokoro-v1"`` as of Lemonade v9.4.
        response_format: ``"mp3"`` | ``"wav"`` | ``"opus"`` | ``"pcm"``.
        speed: 0.25–4.0 (default 1.0).
        base_url: Lemonade server URL.
        timeout: HTTP timeout in seconds.

    Returns:
        Absolute path string of the written file.
    """
    out_path = Path(out_path)
    audio_bytes = synthesize_bytes(
        text,
        voice=voice,
        model=model,
        response_format=response_format,
        speed=speed,
        base_url=base_url,
        timeout=timeout,
    )
    out_path.write_bytes(audio_bytes)
    return str(out_path)


def synthesize_bytes(
    text: str,
    *,
    voice: str = DEFAULT_TTS_VOICE,
    model: str = DEFAULT_TTS_MODEL,
    response_format: str = "mp3",
    speed: float = 1.0,
    base_url: str = LEMONADE_URL,
    timeout: float = 60.0,
) -> bytes:
    """Like :func:`synthesize` but returns the audio bytes (no file write).

    Useful for FastAPI handlers that stream the audio directly back to the
    client without touching disk.
    """
    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": response_format,
        "speed": speed,
    }
    try:
        r = httpx.post(
            f"{base_url}/v1/audio/speech",
            json=payload,
            timeout=timeout,
        )
    except httpx.RequestError as e:
        raise LemonadeAudioError(
            f"Lemonade TTS unreachable at {base_url} — start the server with "
            f"`lemonade-server serve`, or set LEMONADE_BASE_URL. Original: {e}"
        ) from e

    if r.status_code != 200:
        raise LemonadeAudioError(
            f"Lemonade TTS returned {r.status_code}: {r.text[:200]}"
        )
    return r.content


# ────────────────────────── Health probe ──────────────────────────
def lemonade_health(base_url: str = LEMONADE_URL, timeout: float = 5.0) -> dict:
    """Probe Lemonade health and return the JSON body.

    Lemonade exposes the health endpoint at ``/api/v1/health`` (its native
    namespace). Some installations also serve it at ``/v1/health`` and ``/health``
    for compatibility, but ``/api/v1/health`` is what the rest of GAIA uses
    (see ``gaia.llm.lemonade_client.LemonadeClient.get_health``).
    """
    try:
        r = httpx.get(f"{base_url}/api/v1/health", timeout=timeout)
    except httpx.RequestError as e:
        raise LemonadeAudioError(
            f"Lemonade unreachable at {base_url}. Start it with "
            f"`lemonade-server serve` or set LEMONADE_BASE_URL. Original: {e}"
        ) from e
    if r.status_code != 200:
        raise LemonadeAudioError(
            f"Lemonade /api/v1/health returned {r.status_code}: {r.text[:200]}"
        )
    try:
        return r.json()
    except ValueError as e:
        raise LemonadeAudioError(
            f"Lemonade /api/v1/health returned non-JSON: {r.text[:200]}"
        ) from e
