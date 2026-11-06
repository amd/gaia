# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Settings › Phone for the browser: a pass-through to the daemon's phone routes.

The daemon owns phone, so alerts fire whether or not the Agent UI is open.
Phone settings change who GAIA may call, so they are refused over mobile
access: they can only be changed at the PC.
"""

from __future__ import annotations

import asyncio
import json
import queue
import time
import uuid
from typing import Any, Optional

import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, Response

from gaia.logger import get_logger
from gaia.ui.security import require_local_pc

logger = get_logger(__name__)

router = APIRouter(tags=["phone"])

_TIMEOUT = 30.0

#: (method, browser path, daemon path). Anything not listed isn't forwarded.
_ROUTES = (
    ("GET", "status", "status"),
    ("GET", "settings", "settings"),
    ("PUT", "settings", "settings"),
    ("POST", "keys", "keys"),
    ("DELETE", "keys", "keys"),
    ("POST", "pin", "pin"),
    ("DELETE", "pin", "pin"),
    ("POST", "simulated", "simulated"),
    ("GET", "simulated/log", "simulated/log"),
    ("POST", "contacts", "contacts"),
    ("POST", "contacts/confirm", "contacts/confirm"),
    ("POST", "contacts/remove/confirm", "contacts/remove/confirm"),
    ("GET", "alerts", "alerts"),
    ("POST", "test", "test"),
)


def _daemon():
    from gaia.daemon.client import attach

    inst = attach(timeout=1.5, patient=False)
    if inst is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "daemon_unavailable",
                "message": "GAIA's background service isn't running, so phone "
                "settings can't be reached. Restart GAIA.",
            },
        )
    return inst


async def _forward(method: str, path: str, request: Request) -> JSONResponse:
    inst = _daemon()
    body: Optional[Any] = None
    if method in ("POST", "PUT"):
        raw = await request.body()
        body = await request.json() if raw else None
    url = f"http://{inst.host}:{inst.port}/daemon/v1/phone/{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(
                method,
                url,
                json=body,
                params=dict(request.query_params),
                headers={"Authorization": f"Bearer {inst.token}"},
            )
    except httpx.HTTPError as e:
        logger.warning("phone: daemon request %s %s failed: %s", method, path, e)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "daemon_unavailable",
                "message": "GAIA's background service didn't answer. Restart GAIA.",
            },
        ) from e
    if response.status_code == 404:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "daemon_outdated",
                "message": "GAIA's background service is from an older version. "
                "Restart GAIA to update it.",
            },
        )
    return JSONResponse(status_code=response.status_code, content=response.json())


def _make(method: str, browser_path: str, daemon_path: str):
    async def handler(request: Request) -> JSONResponse:
        return await _forward(method, daemon_path, request)

    handler.__name__ = f"phone_{method.lower()}_{browser_path.replace('/', '_')}"
    return handler


for _method, _path, _daemon_path in _ROUTES:
    # Reads are fine over mobile access; every change happens at the PC. The
    # Simulated phone's log holds live codes, so it stays on the PC too.
    local_only = _method != "GET" or _path == "simulated/log"
    deps = [Depends(require_local_pc)] if local_only else []
    router.add_api_route(
        f"/api/phone/{_path}",
        _make(_method, _path, _daemon_path),
        methods=[_method],
        dependencies=deps,
    )


@router.post(
    "/api/phone/contacts/{contact_id}/remove", dependencies=[Depends(require_local_pc)]
)
async def remove_contact(contact_id: int, request: Request) -> JSONResponse:
    return await _forward("POST", f"contacts/{contact_id}/remove", request)


@router.post("/api/phone/alerts/{alert_id}/handled")
async def alert_handled(alert_id: int, request: Request) -> JSONResponse:
    # Allowed from the phone too: opening an alert there should stop the calls.
    return await _forward("POST", f"alerts/{alert_id}/handled", request)


# ── Talking on a call ─────────────────────────────────────────────────────

STREAM_PATH = "/api/phone/stream"
INCOMING_PATH = "/api/phone/incoming"


async def _daemon_call(method: str, path: str, body: Optional[dict] = None) -> dict:
    from gaia.phone.client import call_daemon

    return await asyncio.to_thread(call_daemon, method, path, body)


def _make_voice_session(*, sink, voice: str, emit):
    from gaia.audio.lemonade_tts import LemonadeTTSClient
    from gaia.config import GaiaConfig
    from gaia.ui.routers.voice import _catalog
    from gaia.voice.lemonade import TTS_MODEL, pick_asr_model, resolve_target
    from gaia.voice.session import VoiceOptions, VoiceSession

    config = GaiaConfig.load()
    target = resolve_target()
    return VoiceSession(
        target,
        VoiceOptions(
            purpose="voice",
            asr_model=pick_asr_model("en", config.voice_asr_model),
            voice=voice,
            silence_ms=max(config.voice_silence_ms, 700),
        ),
        emit=emit,
        sink=sink,
        synthesizer=LemonadeTTSClient(target.base_url, TTS_MODEL, target.api_key),
        catalog=_catalog,
    )


def _turn_runner(app):
    """One call turn through the chat's own relay: same agent, tools, memory
    and approvals as a typed message, with ``reply_style: "voice"``."""
    from gaia.ui._chat_helpers import _dispatch_sidecar_query, _memory_mode
    from gaia.ui.models import ChatRequest
    from gaia.ui.server import _scheduled_model
    from gaia.ui.sse_handler import SSEOutputHandler
    from gaia.ui.turn_queue import turn_queue

    async def run(text: str, history, events: asyncio.Queue, answerer: dict) -> None:
        db = app.state.db
        handler = SSEOutputHandler()

        async def approve(allowed: bool, confirm_id: str) -> None:
            await asyncio.to_thread(
                handler.resolve_relay_confirmation, allowed, confirm_id
            )

        async def answer(request_id: str, value: str) -> None:
            await asyncio.to_thread(handler.resolve_relay_input, request_id, value)

        answerer.update(approve=approve, answer=answer)
        await turn_queue.acquire(f"phone:{uuid.uuid4()}")
        try:
            worker = asyncio.create_task(
                asyncio.to_thread(
                    _dispatch_sidecar_query,
                    handler,
                    ChatRequest(
                        session_id="",
                        message=text,
                        agent_type="gaia",
                        reply_style="voice",
                    ),
                    history,
                    _scheduled_model(db),
                    "gaia",
                    memory=_memory_mode({}, db),
                )
            )
            while not (worker.done() and handler.event_queue.empty()):
                try:
                    event = handler.event_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue
                if event:
                    await events.put(event)
            await worker
        finally:
            turn_queue.release()
            await events.put(None)

    return run


def _transcript_saver(app):
    """Keep the call as a chat, named for when it happened."""
    state: dict = {}

    def save(said: str, answer: str) -> None:
        db = app.state.db
        if "session_id" not in state:
            title = time.strftime("Phone call · %b %d, %H:%M")
            state["session_id"] = db.create_session(title=title, agent_type="gaia")[
                "id"
            ]
        db.add_message(state["session_id"], "user", said)
        db.add_message(state["session_id"], "assistant", answer, ends_turn=True)

    return save


@router.websocket(STREAM_PATH)
async def phone_stream(websocket: WebSocket) -> None:
    from gaia.ui.phone_talk import PhoneCall
    from gaia.ui.security import is_allowed_host

    # Twilio can't carry the mobile-access token; the call's one-time token,
    # checked with the daemon before anything is said, is what admits it.
    if not is_allowed_host(websocket.headers.get("host", ""), websocket.app):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    lock = asyncio.Lock()

    async def send(message: dict) -> None:
        async with lock:
            await websocket.send_text(json.dumps(message))

    async def receive():
        try:
            return await websocket.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            return None

    call = PhoneCall(
        receive=receive,
        send=send,
        daemon=_daemon_call,
        make_session=_make_voice_session,
        run_turn=_turn_runner(websocket.app),
        save=_transcript_saver(websocket.app),
    )
    try:
        await call.run()
    finally:
        logger.info("phone call ended: %s", call.ended_reason or "done")
        try:
            await websocket.close()
        except RuntimeError as e:
            logger.debug("phone stream already closed: %s", e)


@router.post(INCOMING_PATH)
async def phone_incoming(request: Request) -> Response:
    """Twilio's webhook for a call to GAIA's number. The daemon checks
    Twilio's signature and the caller, and writes the reply."""
    form = dict((await request.form()).items())
    url = _public_url(request)
    try:
        result = await _daemon_call(
            "POST",
            "/talk/incoming",
            {
                "url": url,
                "params": form,
                "signature": request.headers.get("x-twilio-signature", ""),
            },
        )
    except RuntimeError as e:
        logger.warning("incoming call refused: %s", e)
        return Response("<Response><Reject/></Response>", media_type="text/xml")
    return Response(result["twiml"], media_type="text/xml")


def _public_url(request: Request) -> str:
    """The URL Twilio signed: the tunnel's https address, not the loopback one."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto") or "https"
    return f"{proto}://{host}{request.url.path}"


async def publish_talk_endpoint(app) -> None:
    """While mobile access is on, tell the daemon where calls can reach the PC."""
    published: Optional[str] = ""
    while True:
        tunnel = getattr(app.state, "tunnel", None)
        url = None
        if tunnel is not None and getattr(tunnel, "active", False):
            public = (tunnel.get_status() or {}).get("url") or ""
            if public.startswith("https://"):
                url = "wss://" + public[len("https://") :].rstrip("/") + STREAM_PATH
        if url or published:
            try:
                await _daemon_call("POST", "/talk-endpoint", {"url": url})
                published = url
            except RuntimeError as e:
                logger.debug("phone: couldn't publish the call address: %s", e)
        await asyncio.sleep(60)
