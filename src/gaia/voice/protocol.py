# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The voice socket protocol, shared by the Agent UI server and the daemon.

Client → server (JSON text frames):
    {"type": "start", "purpose": "dictation"|"voice"}   first message, once
    {"type": "speak", "text": str, "kind": "spoken"|"status"}
    {"type": "speak_end"}            the spoken block is complete
    {"type": "interrupt"}            barge-in: drop queued and playing speech
    {"type": "commit"}               end the utterance now
    {"type": "playback", "on": bool} the host is (not) still playing GAIA's speech
    {"type": "expect", "on": bool}   a yes/no answer is awaited: short words count
    {"type": "stop"}                 close the session
Client → server (binary): PCM16 16 kHz mono mic frames, when the host has no
microphone of its own.

Server → client (JSON): ``ready``, ``speech_started``, ``speech_stopped``,
``partial``, ``final``, ``level``, ``speaking``, ``spoken_piece``,
``speaking_done``, ``speech_stopped_by_user``, ``limit``, ``error``, ``closed``.
Server → client (binary): PCM16 24 kHz mono speech, when the client plays it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, Optional, Union

from gaia.logger import get_logger
from gaia.voice.errors import VoiceError
from gaia.voice.session import VoiceSession

log = get_logger(__name__)

Received = Union[str, bytes, None]  # None means the client went away
MAX_TEXT = 4000
MAX_FRAME_BYTES = 64 * 1024


async def run_voice_socket(
    receive: Callable[[], Awaitable[Received]],
    send_json: Callable[[dict], Awaitable[None]],
    make_session: Callable[[dict], Awaitable[VoiceSession]],
    *,
    accepts_mic_frames: bool,
) -> None:
    """Drive one socket until the client stops or goes away."""
    session: Optional[VoiceSession] = None
    try:
        first = await receive()
        start = _parse(first)
        if start is None or start.get("type") != "start":
            raise VoiceError("bad_request", "The first voice message must be start.")
        session = await make_session(start)
        await session.start()
        while True:
            message = await receive()
            if message is None:
                return
            if isinstance(message, bytes):
                if not accepts_mic_frames:
                    raise VoiceError(
                        "bad_request", "This host records its own microphone."
                    )
                if len(message) > MAX_FRAME_BYTES or len(message) % 2:
                    raise VoiceError("bad_request", "Bad microphone frame.")
                await session.feed_mic(message)
                continue
            command = _parse(message)
            if command is None:
                continue
            kind = command.get("type")
            if kind == "speak":
                text = str(command.get("text") or "")[:MAX_TEXT]
                await session.speak(text, str(command.get("kind") or "spoken"))
            elif kind == "speak_end":
                await session.speak_end()
            elif kind == "interrupt":
                await session.interrupt()
            elif kind == "commit":
                await session.commit()
            elif kind == "playback":
                session.playback(bool(command.get("on")))
            elif kind == "expect":
                session.expecting_answer = bool(command.get("on"))
            elif kind == "stop":
                return
            else:
                raise VoiceError("bad_request", f"Unknown voice message {kind!r}.")
    except VoiceError as e:
        log.info("voice session ended: %s (%s)", e.code, e)
        await _safe_send(send_json, e.to_event())
    finally:
        if session is not None:
            await session.close()
        await _safe_send(send_json, {"type": "closed"})


def _parse(message: Received) -> Optional[dict]:
    if message is None or isinstance(message, bytes):
        return None
    try:
        data = json.loads(message)
    except ValueError as e:
        raise VoiceError("bad_request", "Voice messages must be JSON.") from e
    if not isinstance(data, dict):
        raise VoiceError("bad_request", "Voice messages must be JSON objects.")
    return data


async def _safe_send(send_json, event: dict) -> None:
    # The client may already be gone; nothing else can be told then.
    try:
        await send_json(event)
    except (RuntimeError, ConnectionError, asyncio.CancelledError) as e:
        log.debug("voice event not delivered (%s): %s", event.get("type"), e)
