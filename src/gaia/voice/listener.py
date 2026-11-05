# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One live connection to Lemonade's realtime transcription WebSocket.

Audio goes in as 16 kHz mono PCM16; out come ``speech_started``,
``speech_stopped``, ``partial`` and ``final`` events. Protocol: Lemonade's
``WS /realtime`` (OpenAI realtime transcription subset).
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import AsyncIterator, Optional
from urllib.parse import urlencode, urlparse

import aiohttp

from gaia.logger import get_logger
from gaia.voice.errors import VoiceError
from gaia.voice.lemonade import LemonadeTarget

log = get_logger(__name__)

CONNECT_TIMEOUT = 10.0
#: Loading a recognizer the first time can take a while on a cold disk.
LOAD_TIMEOUT = 180.0
DEFAULT_SILENCE_MS = 500

_EVENT_MAP = {
    "input_audio_buffer.speech_started": "speech_started",
    "input_audio_buffer.speech_stopped": "speech_stopped",
    "conversation.item.input_audio_transcription.delta": "partial",
    "conversation.item.input_audio_transcription.completed": "final",
}


def realtime_url(target: LemonadeTarget, health: dict, model: str) -> str:
    """The realtime URL for this server.

    Older servers serve realtime only on the dedicated ``websocket_port``;
    newer ones also serve it on the main port. Both speak the same protocol,
    so the dedicated port is used whenever the server reports one.
    """
    parsed = urlparse(target.base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query = {"model": model}
    if target.api_key:
        query["api_key"] = target.api_key
    port = health.get("websocket_port")
    if isinstance(port, int) and port > 0:
        return f"{scheme}://{parsed.hostname}:{port}/realtime?{urlencode(query)}"
    return f"{scheme}://{parsed.netloc}/v1/realtime?{urlencode(query)}"


class RealtimeListener:
    """Use as ``async with RealtimeListener(...) as listener:``."""

    def __init__(
        self,
        target: LemonadeTarget,
        model: str,
        *,
        silence_ms: int = DEFAULT_SILENCE_MS,
        vad: bool = True,
        http: Optional[aiohttp.ClientSession] = None,
    ):
        self.target = target
        self.model = model
        self.silence_ms = silence_ms
        self.vad = vad
        self._http = http
        self._own_http = http is None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._partial = ""

    async def __aenter__(self) -> "RealtimeListener":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._http is None:
            self._http = aiohttp.ClientSession()
        health_url = f"{self.target.base_url}/health"
        try:
            async with self._http.get(
                health_url,
                headers=self.target.headers,
                timeout=aiohttp.ClientTimeout(total=CONNECT_TIMEOUT),
            ) as response:
                if response.status == 401:
                    raise VoiceError(
                        "lemonade_unreachable",
                        "Lemonade rejected GAIA's API key, so voice can't start.",
                    )
                response.raise_for_status()
                health = await response.json()
        except VoiceError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise VoiceError(
                "lemonade_unreachable",
                f"Lemonade Server isn't reachable at {self.target.origin}.",
                detail=str(e),
            ) from e
        await self._load_model()
        url = realtime_url(self.target, health, self.model)
        try:
            self._ws = await self._http.ws_connect(
                url,
                headers=self.target.headers,
                timeout=aiohttp.ClientWSTimeout(ws_close=CONNECT_TIMEOUT),
                heartbeat=20,
            )
            created = await asyncio.wait_for(self._ws.receive(), CONNECT_TIMEOUT)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise VoiceError(
                "asr_failed",
                f"Couldn't open live transcription with {self.model}.",
                detail=str(e),
            ) from e
        message = _decode(created)
        if message.get("type") == "error":
            raise VoiceError(
                "asr_failed",
                f"Lemonade refused live transcription with {self.model}: "
                f"{_error_text(message)}",
            )
        if message.get("type") != "session.created":
            raise VoiceError(
                "asr_failed",
                f"Unexpected first message from Lemonade realtime: "
                f"{message.get('type')!r}",
            )
        session = {"model": self.model}
        session["turn_detection"] = (
            {"silence_duration_ms": int(self.silence_ms)} if self.vad else None
        )
        await self._send({"type": "session.update", "session": session})

    async def _load_model(self) -> None:
        """The realtime endpoint serves only a loaded model; it never loads one."""
        assert self._http is not None
        try:
            async with self._http.post(
                f"{self.target.base_url}/load",
                json={"model_name": self.model},
                headers=self.target.headers,
                timeout=aiohttp.ClientTimeout(total=LOAD_TIMEOUT),
            ) as response:
                if response.status >= 400:
                    text = (await response.text())[:300]
                    raise VoiceError(
                        "asr_failed",
                        f"Lemonade couldn't load the speech recognizer {self.model}: "
                        f"{text}",
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise VoiceError(
                "asr_failed",
                f"Lemonade didn't finish loading the speech recognizer {self.model}.",
                detail=str(e),
            ) from e

    async def send_pcm16(self, pcm: bytes) -> None:
        if not pcm:
            return
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
        )

    async def commit(self) -> None:
        await self._send({"type": "input_audio_buffer.commit"})

    async def clear(self) -> None:
        self._partial = ""
        await self._send({"type": "input_audio_buffer.clear"})

    async def events(self) -> AsyncIterator[dict]:
        """Yield normalized events until the connection closes."""
        ws = self._require_ws()
        async for raw in ws:
            if raw.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                break
            if raw.type == aiohttp.WSMsgType.ERROR:
                raise VoiceError(
                    "session_lost",
                    "Lost the connection to live transcription.",
                    detail=str(ws.exception()),
                )
            message = _decode(raw)
            kind = message.get("type")
            if kind == "error":
                raise VoiceError(
                    "asr_failed",
                    f"Live transcription failed: {_error_text(message)}",
                )
            mapped = _EVENT_MAP.get(kind or "")
            if mapped == "partial":
                # Deltas replace the interim text rather than append to it.
                self._partial = str(message.get("delta") or "")
                yield {"type": "partial", "text": self._partial}
            elif mapped == "final":
                self._partial = ""
                text = str(message.get("transcript") or "").strip()
                yield {"type": "final", "text": text}
            elif mapped:
                yield {"type": mapped}
        if not ws.closed or ws.close_code not in (None, 1000):
            raise VoiceError(
                "session_lost",
                "Lost the connection to live transcription.",
                detail=f"close code {ws.close_code}",
            )

    async def close(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._own_http and self._http is not None:
            await self._http.close()
            self._http = None

    async def _send(self, message: dict) -> None:
        ws = self._require_ws()
        try:
            await ws.send_str(json.dumps(message))
        except (aiohttp.ClientError, ConnectionResetError, RuntimeError) as e:
            raise VoiceError(
                "session_lost",
                "Lost the connection to live transcription.",
                detail=str(e),
            ) from e

    def _require_ws(self) -> aiohttp.ClientWebSocketResponse:
        if self._ws is None:
            raise RuntimeError("RealtimeListener is not connected; call connect()")
        return self._ws


def _decode(raw) -> dict:
    if raw.type != aiohttp.WSMsgType.TEXT:
        raise VoiceError(
            "asr_failed", f"Unexpected {raw.type.name} frame from Lemonade realtime."
        )
    try:
        message = json.loads(raw.data)
    except ValueError as e:
        raise VoiceError(
            "asr_failed", "Lemonade realtime sent a message that isn't JSON."
        ) from e
    if not isinstance(message, dict):
        raise VoiceError("asr_failed", "Lemonade realtime sent a non-object message.")
    return message


def _error_text(message: dict) -> str:
    error = message.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    return str(error or message)[:300]
