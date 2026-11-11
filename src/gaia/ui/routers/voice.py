# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dictation and voice mode for the browser: status, model download, the socket.

The browser streams its microphone up and plays the speech PCM that comes back;
the turn itself goes through ``/api/chat/send`` with ``reply_style: "voice"``.
See docs/plans/voice.mdx.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from gaia.config import GaiaConfig, GaiaConfigError
from gaia.logger import get_logger
from gaia.ui.security import is_allowed_host, is_allowed_origin
from gaia.voice.errors import VoiceError
from gaia.voice.lemonade import (
    ENGLISH_ASR_MODEL,
    MULTILINGUAL_ASR_MODEL,
    TTS_MODEL,
    model_entry,
    pick_asr_model,
    resolve_target,
)

logger = get_logger(__name__)

router = APIRouter(tags=["voice"])

#: The only models this router will download: the speech models voice uses.
DOWNLOADABLE = (ENGLISH_ASR_MODEL, MULTILINGUAL_ASR_MODEL, TTS_MODEL)
_LOCAL_PEERS = {"127.0.0.1", "localhost", "::1"}
_TUNNEL_COOKIE = "gaia_tunnel_token"


class VoiceModel(BaseModel):
    id: str
    purpose: str
    downloaded: bool
    size_gb: Optional[float] = None


class VoiceStatus(BaseModel):
    ready: bool
    error: Optional[str] = None
    error_code: Optional[str] = None
    language: str
    asr_model: str
    tts_model: str
    speaks: bool
    models: List[VoiceModel] = Field(default_factory=list)
    turn_taking: str
    silence_ms: int
    status_updates: bool
    captions: bool


class DownloadRequest(BaseModel):
    model: str


class VoiceSettings(BaseModel):
    language: Optional[str] = None
    asr_model: str = "auto"
    turn_taking: str = "hands_free"
    silence_ms: int = 500
    status_updates: bool = True
    captions: bool = True


class VoiceSettingsUpdate(BaseModel):
    language: Optional[str] = None
    asr_model: Optional[str] = None
    turn_taking: Optional[str] = None
    silence_ms: Optional[int] = None
    status_updates: Optional[bool] = None
    captions: Optional[bool] = None


_SETTING_KEYS = {
    "language": "voice_language",
    "asr_model": "voice_asr_model",
    "turn_taking": "voice_turn_taking",
    "silence_ms": "voice_silence_ms",
    "status_updates": "voice_status_updates",
    "captions": "voice_captions",
}


def _settings(config: GaiaConfig) -> VoiceSettings:
    return VoiceSettings(
        **{field: getattr(config, key) for field, key in _SETTING_KEYS.items()}
    )


@router.get("/api/voice/settings", response_model=VoiceSettings)
def get_voice_settings() -> VoiceSettings:
    """The listening and voice-mode settings, shared with the TUI."""
    return _settings(_config())


@router.put("/api/voice/settings", response_model=VoiceSettings)
def update_voice_settings(body: VoiceSettingsUpdate) -> VoiceSettings:
    """Save any of the voice settings; the others keep their value.

    ``language: ""`` goes back to following the app's language.
    """
    values = {}
    try:
        for field, key in _SETTING_KEYS.items():
            value = getattr(body, field)
            if value is None:
                continue
            values[key] = GaiaConfig._coerce(key, value)
    except GaiaConfigError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not values:
        return _settings(_config())
    try:
        return _settings(GaiaConfig.save_keys(values))
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _config() -> GaiaConfig:
    try:
        return GaiaConfig.load()
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _language(config: GaiaConfig, app_language: str) -> str:
    """The spoken language: the setting, else the app's own language."""
    return config.voice_language or (app_language or "en").lower()[:2]


def _catalog() -> list:
    from gaia.audio.lemonade_service import LemonadeAudioService

    target = resolve_target()
    service = LemonadeAudioService(target.base_url, TTS_MODEL, target.api_key, 15)
    return service.catalog()


@router.get("/api/voice/status", response_model=VoiceStatus)
async def voice_status(lang: str = "en") -> VoiceStatus:
    """Whether voice can start now, and which models it still needs."""
    config = _config()
    language = _language(config, lang)
    asr = pick_asr_model(language, config.voice_asr_model)
    speaks = language == "en"
    base = dict(
        language=language,
        asr_model=asr,
        tts_model=TTS_MODEL,
        speaks=speaks,
        turn_taking=config.voice_turn_taking,
        silence_ms=config.voice_silence_ms,
        status_updates=config.voice_status_updates,
        captions=config.voice_captions,
    )
    try:
        catalog = await asyncio.to_thread(_catalog)
    except VoiceError as e:
        return VoiceStatus(ready=False, error=str(e), error_code=e.code, **base)
    except ConnectionError as e:
        return VoiceStatus(
            ready=False, error=str(e), error_code="lemonade_unreachable", **base
        )
    needed = [(asr, "transcription")] + ([(TTS_MODEL, "tts")] if speaks else [])
    models = []
    for model_id, purpose in needed:
        entry = model_entry(catalog, model_id) or {}
        size = entry.get("size")
        models.append(
            VoiceModel(
                id=model_id,
                purpose=purpose,
                downloaded=entry.get("downloaded") is True,
                size_gb=float(size) if isinstance(size, (int, float)) else None,
            )
        )
    missing = [m.id for m in models if not m.downloaded]
    return VoiceStatus(
        ready=not missing,
        error=(
            None if not missing else "Voice needs speech models: " + ", ".join(missing)
        ),
        error_code=None if not missing else "model_missing",
        models=models,
        **base,
    )


@router.post("/api/voice/models/download")
async def download_voice_model(body: DownloadRequest) -> dict:
    """Download one speech model through Lemonade, after the user asked."""
    from gaia.audio.lemonade_service import LemonadeAudioService

    if body.model not in DOWNLOADABLE:
        raise HTTPException(
            status_code=422,
            detail=f"{body.model} isn't a voice model. Pick one of: "
            + ", ".join(DOWNLOADABLE),
        )
    label = "tts" if body.model == TTS_MODEL else "transcription"
    try:
        target = resolve_target()
    except VoiceError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    service = LemonadeAudioService(target.base_url, body.model, target.api_key, 30)
    service.error_cls = RuntimeError
    try:
        await asyncio.to_thread(service.ensure_model_pulled, label)
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"model": body.model, "downloaded": True}


def socket_refusal(websocket: WebSocket) -> Optional[str]:
    """Why this socket may not connect, or None. Mirrors the HTTP guards,
    which never see WebSocket scopes."""
    app = websocket.app
    host = websocket.headers.get("host", "")
    if not is_allowed_host(host, app):
        return "host not allowed"
    origin = websocket.headers.get("origin", "")
    if not is_allowed_origin(origin, host, app):
        return "origin not allowed"
    tunnel = getattr(app.state, "tunnel", None)
    if tunnel is None or not getattr(tunnel, "active", False):
        return None
    peer = websocket.client.host if websocket.client else ""
    forwarded = any(
        h in websocket.headers
        for h in ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto")
    )
    if peer in _LOCAL_PEERS and not forwarded:
        return None
    token = websocket.cookies.get(_TUNNEL_COOKIE) or websocket.query_params.get("token")
    if not token or not tunnel.validate_token(token):
        return "mobile access token missing or invalid"
    return None


@router.websocket("/api/voice/session")
async def voice_session(websocket: WebSocket) -> None:
    from gaia.audio.lemonade_tts import DEFAULT_VOICE, LemonadeTTSClient
    from gaia.voice.protocol import run_voice_socket
    from gaia.voice.session import VoiceOptions, VoiceSession

    refusal = socket_refusal(websocket)
    if refusal:
        logger.warning("voice socket refused: %s", refusal)
        await websocket.close(code=4403, reason=refusal)
        return
    await websocket.accept()
    send_lock = asyncio.Lock()

    async def send_json(event: dict) -> None:
        async with send_lock:
            try:
                await websocket.send_json(event)
            except WebSocketDisconnect as e:
                # The browser left; the socket loop ends on its next receive.
                raise ConnectionError("the voice client disconnected") from e

    class _Sink:
        """Speech PCM to the browser, which plays and flushes it locally."""

        async def write(self, frame: bytes) -> None:
            async with send_lock:
                await websocket.send_bytes(frame)

        async def flush(self) -> None:
            await send_json({"type": "flush"})

    async def receive():
        try:
            message = await websocket.receive()
        except WebSocketDisconnect:
            return None
        if message.get("type") == "websocket.disconnect":
            return None
        if message.get("bytes") is not None:
            return message["bytes"]
        return message.get("text")

    async def make_session(start: dict) -> VoiceSession:
        config = GaiaConfig.load()
        language = _language(config, str(start.get("language") or "en"))
        target = resolve_target()
        tts = LemonadeTTSClient(target.base_url, TTS_MODEL, target.api_key)
        options = VoiceOptions(
            purpose=str(start.get("purpose") or "voice"),
            asr_model=pick_asr_model(language, config.voice_asr_model),
            tts_model=TTS_MODEL,
            voice=config.read_aloud_voice or DEFAULT_VOICE,
            speed=config.read_aloud_speed / 100,
            silence_ms=config.voice_silence_ms,
            hands_free=config.voice_turn_taking == "hands_free",
            speak=language == "en",
            english=language == "en",
        )
        return VoiceSession(
            target,
            options,
            emit=send_json,
            sink=_Sink(),
            synthesizer=tts,
            catalog=_catalog,
        )

    try:
        await run_voice_socket(
            receive, send_json, make_session, accepts_mic_frames=True
        )
    finally:
        try:
            await websocket.close()
        except RuntimeError as e:
            logger.debug("voice socket already closed: %s", e)
