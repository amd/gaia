# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/voice/*`` — dictation and voice mode for the TUI.

The TUI is a terminal, so it has no microphone or speaker of its own: the
daemon records this machine's microphone and plays speech through its speaker
with ``sounddevice``, and the TUI only exchanges JSON events over the socket.
The turn itself goes through the TUI's ordinary ``/v1/gaia/query`` with
``reply_style: "voice"``. Mirrors the Agent UI's ``gaia.ui.routers.voice``.

The daemon has no echo cancellation, so capture pauses while speech plays and
resumes once playback drains; barge-in is a key in the TUI (``interrupt``).
See docs/plans/voice.mdx.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import secrets
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from gaia.config import GaiaConfig, GaiaConfigError
from gaia.voice.audio import ASR_RATE, TTS_RATE
from gaia.voice.errors import VoiceError
from gaia.voice.lemonade import (
    ENGLISH_ASR_MODEL,
    MULTILINGUAL_ASR_MODEL,
    TTS_MODEL,
    model_entry,
    pick_asr_model,
    resolve_target,
)
from gaia.voice.session import FRAME_MS, VoiceOptions, VoiceSession

logger = logging.getLogger(__name__)

PREFIX = "/daemon/v1/voice"
#: The only models this router will download: the speech models voice uses.
DOWNLOADABLE = (ENGLISH_ASR_MODEL, MULTILINGUAL_ASR_MODEL, TTS_MODEL)
#: How far ahead of the speaker synthesized audio may be queued. Keeps the
#: ``level`` events in step with what is heard, and bounds what a flush drops.
MAX_AHEAD_SECONDS = 0.3
#: Room echo after the last sample; capture stays paused this much longer.
ECHO_TAIL_SECONDS = 0.25
_WS_LIBRARIES = ("websockets", "wsproto")


def websocket_support() -> Optional[str]:
    """None when uvicorn can serve WebSockets here, else why it can't."""
    if any(importlib.util.find_spec(name) for name in _WS_LIBRARIES):
        return None
    return (
        "The GAIA service can't open a voice connection: its install is missing "
        "the Python package 'websockets', which serves WebSockets. Updating or "
        "reinstalling GAIA adds it."
    )


# ---------------------------------------------------------------- devices


def _sounddevice():
    """Import sounddevice, or raise ``mic_missing`` saying what is absent."""
    try:
        import sounddevice  # pylint: disable=import-outside-toplevel
    except ImportError as e:
        raise VoiceError(
            "mic_missing",
            "Voice can't reach the microphone: the GAIA service's install is "
            "missing the Python package 'sounddevice'. Updating or reinstalling "
            "GAIA adds it.",
            detail=str(e)[:300],
        ) from e
    except OSError as e:
        # sounddevice imports fine but can't load the PortAudio library.
        raise VoiceError(
            "mic_missing",
            "Voice can't reach the microphone: the PortAudio audio library is "
            "missing on this PC. On Linux it ships as libportaudio2.",
            detail=str(e)[:300],
        ) from e
    return sounddevice


def _audio_errors(sd) -> tuple:
    """What PortAudio raises: its own error, or a bad device argument."""
    return (getattr(sd, "PortAudioError", OSError), ValueError, OSError)


def _privacy_pane() -> str:
    if sys.platform == "win32":
        return (
            "Settings › Privacy & security › Microphone, and turn on "
            "'Let desktop apps access your microphone'"
        )
    if sys.platform == "darwin":
        return "System Settings › Privacy & Security › Microphone"
    return "your desktop's sound settings, and check your user may record audio"


def _open_error(e: Exception, what: str) -> VoiceError:
    """Map a PortAudio failure opening a stream to a stable voice code."""
    text = str(e)
    lowered = text.lower()
    if any(w in lowered for w in ("permission", "denied", "not authorized", "access")):
        return VoiceError(
            "mic_denied",
            f"This PC refused GAIA access to the {what}. Allow it in "
            f"{_privacy_pane()}, then try again.",
            detail=text[:300],
        )
    if any(w in lowered for w in ("unavailable", "busy", "in use", "-9985")):
        return VoiceError(
            "mic_busy",
            f"Your {what} is in use by another app. Close it, then try again.",
            detail=text[:300],
        )
    return VoiceError(
        "mic_missing",
        f"GAIA couldn't open the {what} on this PC.",
        detail=text[:300],
    )


def _list_devices(sd) -> Dict[str, List[Dict[str, Any]]]:
    try:
        devices = list(sd.query_devices())
        default_in, default_out = _default_devices(sd)
    except _audio_errors(sd) as e:
        raise _open_error(e, "microphone") from e
    inputs, outputs = [], []
    for index, dev in enumerate(devices):
        name = str(dev.get("name", f"device {index}"))
        if int(dev.get("max_input_channels", 0)) > 0:
            inputs.append(
                {"index": index, "name": name, "default": index == default_in}
            )
        if int(dev.get("max_output_channels", 0)) > 0:
            outputs.append(
                {"index": index, "name": name, "default": index == default_out}
            )
    return {"inputs": inputs, "outputs": outputs}


def _default_devices(sd):
    default = getattr(sd, "default", None)
    pair = getattr(default, "device", None) if default is not None else None
    try:
        return int(pair[0]), int(pair[1])
    except (TypeError, ValueError, IndexError):
        return -1, -1


def require_microphone(sd) -> Dict[str, Any]:
    """The default input device, or ``mic_missing`` when there is none."""
    inputs = _list_devices(sd)["inputs"]
    if not inputs:
        raise VoiceError(
            "mic_missing",
            "No microphone found on this PC. Plug one in, then try again.",
        )
    return next((d for d in inputs if d["default"]), inputs[0])


class Speaker:
    """Speech out through the local speaker: an :class:`AudioSink`.

    Writes queue PCM for a PortAudio callback to play. ``write`` waits while
    more than :data:`MAX_AHEAD_SECONDS` is queued, so the session's level
    events track what is heard; ``flush`` drops everything queued at once.
    """

    def __init__(self, sd, rate: int = TTS_RATE):
        self._errors = _audio_errors(sd)
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._last_sound = 0.0
        self._max_ahead = int(rate * MAX_AHEAD_SECONDS) * 2
        try:
            self._stream = sd.RawOutputStream(
                samplerate=rate,
                channels=1,
                dtype="int16",
                blocksize=rate * FRAME_MS // 1000 // 2,
                callback=self._callback,
            )
            self._stream.start()
        except self._errors as e:
            raise _open_error(e, "speaker") from e

    def _callback(self, outdata, frames, _time, _status) -> None:
        want = frames * 2
        with self._lock:
            chunk = bytes(self._buffer[:want])
            del self._buffer[:want]
        if chunk:
            self._last_sound = time.monotonic()
        outdata[: len(chunk)] = chunk
        if len(chunk) < want:
            outdata[len(chunk) : want] = b"\x00" * (want - len(chunk))

    @property
    def busy(self) -> bool:
        """Audio is queued, playing, or just finished (room echo)."""
        with self._lock:
            if self._buffer:
                return True
        return time.monotonic() - self._last_sound < ECHO_TAIL_SECONDS

    async def write(self, frame: bytes) -> None:
        with self._lock:
            self._buffer.extend(frame)
        while True:
            with self._lock:
                ahead = len(self._buffer)
            if ahead <= self._max_ahead:
                return
            await asyncio.sleep(0.02)

    async def flush(self) -> None:
        with self._lock:
            self._buffer.clear()

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except self._errors as e:
            # Closing a stream PortAudio already tore down; nothing is left open.
            logger.info("speaker close: %s", e)


class Microphone:
    """The local microphone as ~100 ms PCM16 16 kHz frames on a queue."""

    def __init__(self, sd, loop: asyncio.AbstractEventLoop):
        self._errors = _audio_errors(sd)
        self._loop = loop
        self.frames: "asyncio.Queue[bytes]" = asyncio.Queue(maxsize=50)
        try:
            self._stream = sd.RawInputStream(
                samplerate=ASR_RATE,
                channels=1,
                dtype="int16",
                blocksize=ASR_RATE * FRAME_MS // 1000,
                callback=self._callback,
            )
            self._stream.start()
        except self._errors as e:
            raise _open_error(e, "microphone") from e

    def _callback(self, indata, _frames, _time, _status) -> None:
        data = bytes(indata)
        try:
            self._loop.call_soon_threadsafe(self._put, data)
        except RuntimeError:
            # A last PortAudio callback after the session closed its loop.
            logger.debug("microphone frame dropped after the session closed")

    def _put(self, data: bytes) -> None:
        if self.frames.full():
            # Behind by five seconds: the oldest audio is the least useful.
            self.frames.get_nowait()
        self.frames.put_nowait(data)

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except self._errors as e:
            # Closing a stream PortAudio already tore down; nothing is left open.
            logger.info("microphone close: %s", e)


class DaemonVoiceSession(VoiceSession):
    """A :class:`VoiceSession` fed by the local microphone.

    Capture is held while speech plays (no echo cancellation here) and, in
    push-to-talk, until the TUI says the key is down.
    """

    def __init__(self, *args, microphone: Microphone, speaker: Optional[Speaker], **kw):
        super().__init__(*args, sink=speaker, **kw)
        self.microphone = microphone
        self.speaker = speaker
        self.talk_held = False
        self._capture: Optional[asyncio.Task] = None

    @property
    def capture_open(self) -> bool:
        if self.options.purpose == "dictation":
            return True
        if self.speaking or (self.speaker is not None and self.speaker.busy):
            return False
        return self.options.hands_free or self.talk_held

    async def start(self) -> None:
        await super().start()
        self._capture = asyncio.create_task(self._capture_loop())

    async def _capture_loop(self) -> None:
        while True:
            frame = await self.microphone.frames.get()
            if self.capture_open:
                await self.feed_mic(frame)

    async def close(self) -> None:
        if self._capture is not None:
            self._capture.cancel()
            try:
                await self._capture
            except (asyncio.CancelledError, VoiceError):
                pass
        try:
            await super().close()
        finally:
            self.microphone.close()
            if self.speaker is not None:
                self.speaker.close()


# ---------------------------------------------------------------- routes


class VoiceModel(BaseModel):
    id: str
    purpose: str
    downloaded: bool
    size_gb: Optional[float] = None


class MicrophoneStatus(BaseModel):
    available: bool
    name: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None


class VoiceStatus(BaseModel):
    ready: bool
    error: Optional[str] = None
    error_code: Optional[str] = None
    language: str
    asr_model: str
    tts_model: str
    speaks: bool
    models: List[VoiceModel] = Field(default_factory=list)
    microphone: MicrophoneStatus
    turn_taking: str
    silence_ms: int
    status_updates: bool
    captions: bool


class DownloadRequest(BaseModel):
    model: str


def _config() -> GaiaConfig:
    try:
        return GaiaConfig.load()
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _language(config: GaiaConfig) -> str:
    # The daemon has no app language of its own; the TUI is English.
    return config.voice_language or "en"


def _catalog() -> list:
    from gaia.audio.lemonade_service import LemonadeAudioService

    target = resolve_target()
    service = LemonadeAudioService(target.base_url, TTS_MODEL, target.api_key, 15)
    return service.catalog()


def _microphone_status() -> MicrophoneStatus:
    try:
        device = require_microphone(_sounddevice())
    except VoiceError as e:
        return MicrophoneStatus(available=False, error=str(e), error_code=e.code)
    return MicrophoneStatus(available=True, name=device["name"])


def _authorized(websocket: WebSocket, token: str) -> bool:
    scheme, _, credential = websocket.headers.get("authorization", "").partition(" ")
    return (
        scheme.lower() == "bearer"
        and bool(credential)
        and secrets.compare_digest(credential, token)
    )


def build_voice_router(token: str) -> APIRouter:
    """Status, devices, model download and the voice socket."""
    from gaia.daemon.app import build_require_token

    require_token = build_require_token(token)
    router = APIRouter(tags=["voice"])
    unsupported = websocket_support()
    if unsupported:
        logger.warning("voice socket unavailable: %s", unsupported)

    @router.get(f"{PREFIX}/status", response_model=VoiceStatus)
    async def voice_status(_: None = Depends(require_token)) -> VoiceStatus:
        """Whether voice can start now, and what it still needs."""
        config = _config()
        language = _language(config)
        asr = pick_asr_model(language, config.voice_asr_model)
        speaks = language == "en"
        microphone = await asyncio.to_thread(_microphone_status)
        base = dict(
            language=language,
            asr_model=asr,
            tts_model=TTS_MODEL,
            speaks=speaks,
            microphone=microphone,
            turn_taking=config.voice_turn_taking,
            silence_ms=config.voice_silence_ms,
            status_updates=config.voice_status_updates,
            captions=config.voice_captions,
        )
        if unsupported:
            return VoiceStatus(
                ready=False, error=unsupported, error_code="session_lost", **base
            )
        if not microphone.available:
            return VoiceStatus(
                ready=False,
                error=microphone.error,
                error_code=microphone.error_code,
                **base,
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
                None
                if not missing
                else "Voice needs speech models: " + ", ".join(missing)
            ),
            error_code=None if not missing else "model_missing",
            models=models,
            **base,
        )

    @router.get(f"{PREFIX}/devices")
    async def voice_devices(_: None = Depends(require_token)) -> dict:
        """This machine's audio inputs and outputs."""
        try:
            return await asyncio.to_thread(lambda: _list_devices(_sounddevice()))
        except VoiceError as e:
            raise HTTPException(status_code=503, detail=e.to_event()) from e

    @router.post(f"{PREFIX}/models/download")
    async def download_voice_model(
        body: DownloadRequest, _: None = Depends(require_token)
    ) -> dict:
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

    @router.get(f"{PREFIX}/session")
    async def voice_session_without_upgrade(_: None = Depends(require_token)):
        """Reached only when this server can't upgrade to a WebSocket."""
        raise HTTPException(
            status_code=503 if unsupported else 400,
            detail=unsupported
            or "The voice session is a WebSocket; connect with an upgrade request.",
        )

    @router.websocket(f"{PREFIX}/session")
    async def voice_session(websocket: WebSocket) -> None:
        if not _authorized(websocket, token):
            logger.warning("voice socket refused: missing or invalid client token")
            await _refuse(websocket)
            return
        await websocket.accept()
        await serve_voice_socket(websocket)

    return router


async def _refuse(websocket: WebSocket) -> None:
    """A 401 the TUI can tell apart from other failures, so it re-reads a
    rotated token; a plain close where the server can't send one."""
    if "websocket.http.response" in websocket.scope.get("extensions", {}):
        from fastapi.responses import JSONResponse

        await websocket.send_denial_response(
            JSONResponse(
                {"detail": "Missing or invalid daemon client token."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        )
        return
    await websocket.close(code=4401, reason="missing or invalid client token")


async def serve_voice_socket(websocket: WebSocket) -> None:
    """Run one accepted voice socket against this machine's mic and speaker."""
    from gaia.audio.lemonade_tts import DEFAULT_VOICE, LemonadeTTSClient
    from gaia.voice.protocol import run_voice_socket

    send_lock = asyncio.Lock()
    holder: Dict[str, DaemonVoiceSession] = {}

    async def send_json(event: dict) -> None:
        async with send_lock:
            await websocket.send_json(event)

    async def receive():
        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                return None
            if message.get("type") == "websocket.disconnect":
                return None
            if message.get("bytes") is not None:
                return message["bytes"]
            text = message.get("text")
            # Push-to-talk is the daemon's business: it gates its own capture.
            kind = _peek_type(text)
            session = holder.get("session")
            if kind == "ptt_down" and session is not None:
                session.talk_held = True
                continue
            if kind == "ptt_up" and session is not None:
                session.talk_held = False
                return json.dumps({"type": "commit"})
            return text

    async def make_session(start: dict) -> VoiceSession:
        config = GaiaConfig.load()
        language = _language(config)
        purpose = str(start.get("purpose") or "voice")
        target = resolve_target()
        sd = await asyncio.to_thread(_sounddevice)
        await asyncio.to_thread(require_microphone, sd)
        microphone = Microphone(sd, asyncio.get_running_loop())
        speaker = None
        try:
            if purpose == "voice" and language == "en":
                speaker = Speaker(sd)
            options = VoiceOptions(
                purpose=purpose,
                asr_model=pick_asr_model(language, config.voice_asr_model),
                tts_model=TTS_MODEL,
                voice=config.read_aloud_voice or DEFAULT_VOICE,
                speed=config.read_aloud_speed / 100,
                silence_ms=config.voice_silence_ms,
                hands_free=config.voice_turn_taking == "hands_free",
                speak=language == "en",
            )
            session = DaemonVoiceSession(
                target,
                options,
                emit=send_json,
                microphone=microphone,
                speaker=speaker,
                synthesizer=LemonadeTTSClient(
                    target.base_url, TTS_MODEL, target.api_key
                ),
                catalog=_catalog,
            )
        except BaseException:
            microphone.close()
            if speaker is not None:
                speaker.close()
            raise
        holder["session"] = session
        return session

    try:
        await run_voice_socket(
            receive, send_json, make_session, accepts_mic_frames=False
        )
    finally:
        try:
            await websocket.close()
        except RuntimeError as e:
            logger.debug("voice socket already closed: %s", e)


def _peek_type(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None  # run_voice_socket reports it as bad_request
    return data.get("type") if isinstance(data, dict) else None
