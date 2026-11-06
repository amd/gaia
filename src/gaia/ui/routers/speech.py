# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Read replies aloud: the voice settings, and speech for one piece of text.

Speech comes from Lemonade's Kokoro model. The settings live in
``~/.gaia/config.json`` so the TUI reads replies in the same voice.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from gaia.config import (
    READ_ALOUD_MAX_SPEED,
    READ_ALOUD_MIN_SPEED,
    GaiaConfig,
    GaiaConfigError,
    check_read_aloud_speed,
    check_read_aloud_voice,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["speech"])

#: Longest text one request may speak; the app splits a reply into pieces
#: this size so the first one plays while the next is made.
MAX_SPEECH_CHARS = 1000


class Voice(BaseModel):
    id: str
    name: str
    accent: str
    gender: str
    quality: str


class SpeechSettings(BaseModel):
    voice: str
    speed: int
    auto: bool
    min_speed: int = READ_ALOUD_MIN_SPEED
    max_speed: int = READ_ALOUD_MAX_SPEED
    voices: List[Voice]


class SpeechSettingsUpdate(BaseModel):
    voice: Optional[str] = None
    speed: Optional[int] = None
    auto: Optional[bool] = None


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SPEECH_CHARS)
    voice: Optional[str] = None
    speed: Optional[int] = None


def _settings(config: GaiaConfig) -> SpeechSettings:
    from gaia.audio.lemonade_tts import DEFAULT_VOICE, voice_catalog

    return SpeechSettings(
        voice=config.read_aloud_voice or DEFAULT_VOICE,
        speed=config.read_aloud_speed,
        auto=config.read_aloud_auto,
        voices=[Voice(**v) for v in voice_catalog()],
    )


def _load_config() -> GaiaConfig:
    try:
        return GaiaConfig.load()
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/speech/settings", response_model=SpeechSettings)
def get_speech_settings() -> SpeechSettings:
    """The read-aloud voice, speed and auto-read choice, with every voice offered."""
    return _settings(_load_config())


@router.put("/api/speech/settings", response_model=SpeechSettings)
def update_speech_settings(body: SpeechSettingsUpdate) -> SpeechSettings:
    """Save any of voice, speed and auto-read; the others keep their value."""
    values: Dict[str, object] = {}
    try:
        if body.voice is not None:
            values["read_aloud_voice"] = check_read_aloud_voice(body.voice)
        if body.speed is not None:
            values["read_aloud_speed"] = check_read_aloud_speed(body.speed)
        if body.auto is not None:
            values["read_aloud_auto"] = body.auto
    except GaiaConfigError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not values:
        return _settings(_load_config())
    try:
        return _settings(GaiaConfig.save_keys(values))
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _speak(text: str, voice: str, speed: int) -> bytes:
    from gaia.audio.lemonade_tts import LemonadeTTSClient, write_wav

    client = LemonadeTTSClient()
    client.ensure_model()
    samples, rate = client.synthesize(text, voice=voice, speed=speed / 100)
    buffer = io.BytesIO()
    write_wav(buffer, samples, rate)
    return buffer.getvalue()


@router.post("/api/speech")
async def speak(body: SpeakRequest) -> Response:
    """Speak *text* as a 16-bit mono WAV, in the saved voice unless one is named."""
    from gaia.audio.lemonade_tts import DEFAULT_VOICE, LemonadeTTSError
    from gaia.llm.local_only import CloudDisabledError

    if not body.text.strip():
        raise HTTPException(status_code=422, detail="There is no text to read aloud.")
    config = _load_config()
    try:
        voice = check_read_aloud_voice(body.voice or config.read_aloud_voice)
        speed = check_read_aloud_speed(
            body.speed if body.speed is not None else config.read_aloud_speed
        )
    except GaiaConfigError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    try:
        audio = await asyncio.to_thread(
            _speak, body.text, voice or DEFAULT_VOICE, speed
        )
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except (LemonadeTTSError, CloudDisabledError) as e:
        logger.error("Read aloud failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    return Response(content=audio, media_type="audio/wav")
