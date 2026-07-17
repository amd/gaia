# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Streaming reverse-proxy for agent routes — the daemon's data plane
(``ANY /v1/<agent>/*``, issue #2150 / V2-7).

Clients present only the DAEMON client token; the daemon swaps it for the
per-agent sidecar bearer server-side (via ``SidecarRegistry.connection``), so
sidecar credentials never leave the daemon for relayed traffic. The
``/daemon/v1/*`` control plane is untouched — this router is the second,
data-plane mount over the same registry.

Behavior by upstream response type:

- ``text/event-stream`` — relayed UNBUFFERED (``httpx.AsyncClient(stream=True)``
  chunks → ``StreamingResponse``): each upstream chunk is forwarded the moment
  it arrives. A passive :class:`_SSEWatcher` observes the bytes (never altering
  them) to learn the ``run_id`` and whether the frozen 7-event contract's
  single terminal ``final``/``error`` event has passed.
- anything else — buffered passthrough with status, headers, and body
  preserved (fixed-function routes; per V2-7 the buffered path is fine there).

Crash semantics (§0.13): if the stream ends — EOF or transport error — WITHOUT
a terminal event, the relay appends one synthetic terminal error frame::

    data: {"type": "error", "detail": "<actionable message>", "source": "daemon_relay"}

and ends the response cleanly. The shape is the canonical contract's own
``error`` event plus an additive ``source`` marker so downstream front-doors
(V2-8 CLI, V2-17 ``gaia api``) can tell a relay-synthesized terminal from a
sidecar-authored one.

Cancel semantics: a client disconnect (or a non-terminal stream end) triggers a
best-effort ``POST /v1/<agent>/query/<run_id>/cancel`` upstream — mirroring
``gaia.ui.email_sidecar.relay``'s crash/cancel handling — so an abandoned run
stops occupying the single-tenant LLM slot. Best-effort by design: the sidecar
may already be dead, so transport failures are logged, never raised.

Loud errors, no silent fallbacks: unknown agent → 404 naming the registered
ids; registered-but-not-running → 503 naming the ensure remedy; upstream
unreachable mid-flight → 502 carrying the transport error.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

# Module-level on purpose (matches sidecars/routes.py): this module is itself
# imported lazily from create_app, and the endpoint annotations below must be
# resolvable from module globals under PEP 563 (`request: Request`).
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from gaia.daemon.sidecars.errors import SidecarNotRunningError, UnknownAgentError
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Connect timeout — a dead sidecar should fail fast on TCP connect.
CONNECT_TIMEOUT = 10.0
#: Read timeout between upstream chunks — spans a whole agent-loop step, so it
#: matches the sidecar proxy's long per-request budget, not the connect one.
READ_TIMEOUT = 300.0
#: Cancel POST timeout — best-effort cleanup must never wait out READ_TIMEOUT.
CANCEL_TIMEOUT = 10.0

#: Canonical event types that terminate a ``/query`` stream (frozen 7-event
#: contract, spec #2015/#2016 — mirrors ``gaia_agent_email.sse_translation``).
TERMINAL_TYPES = frozenset({"final", "error"})

#: Methods the relay accepts — everything a sidecar route can serve.
RELAY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# Hop-by-hop headers never forwarded in either direction (RFC 9110 §7.6.1).
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
# Request headers the relay owns: host (rewritten by httpx), authorization
# (client token swapped for the sidecar bearer), content-length (recomputed),
# accept-encoding (forced to identity so relayed bytes stay uncompressed and
# the SSE watcher can read them).
_REQUEST_DROP = _HOP_BY_HOP | {
    "host",
    "authorization",
    "content-length",
    "accept-encoding",
}
# Response headers the relay owns: length/encoding are recomputed for the
# (decoded) body; date/server would duplicate uvicorn's own.
_RESPONSE_DROP = _HOP_BY_HOP | {"content-length", "content-encoding", "date", "server"}

# Cap on the watcher's partial-frame buffer: a stream that never frames (not
# SSE-shaped after all) must not grow memory without bound. Far above any
# plausible single canonical event — a frame this size means the stream is not
# SSE-shaped, and the watcher degrades to "terminal unknown" rather than
# misclassifying a valid oversized final as a crash.
_WATCHER_BUFFER_CAP = 16 << 20

# Strong refs to fire-and-forget cancel tasks spawned from a cancelled response
# generator (asyncio only weak-refs pending tasks).
_background_tasks: "set[asyncio.Task]" = set()


def _spawn_background(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Loop already gone (server teardown): cleanup cannot run, so the
        # upstream response/client leak until GC — name it instead of hiding it.
        logger.warning(
            "daemon relay: no running loop for background cancel/cleanup task; "
            "upstream connection will be reclaimed by GC"
        )
        coro.close()
        return
    task = loop.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def stream_ended_unexpectedly_detail(agent_id: str) -> str:
    """Actionable detail for the synthetic terminal error frame (§0.13)."""
    return (
        f"agent '{agent_id}' stream ended unexpectedly without a terminal "
        "event — the sidecar may have crashed mid-run. Check its log under "
        f"~/.gaia/agents/{agent_id}/logs/, re-ensure it "
        f"(POST /daemon/v1/agents/{agent_id}/ensure), and retry."
    )


def _synthetic_error_frame(detail: str, *, terminate_partial: bool) -> bytes:
    """One canonical terminal ``error`` SSE frame, relay-authored.

    *terminate_partial* prepends a frame separator so a half-forwarded upstream
    frame can never concatenate with (and corrupt) the synthetic one.
    """
    frame = (
        b"data: "
        + json.dumps(
            {"type": "error", "detail": detail, "source": "daemon_relay"}
        ).encode("utf-8")
        + b"\n\n"
    )
    return (b"\n\n" + frame) if terminate_partial else frame


def _run_id_from_body(body: bytes) -> Optional[str]:
    """``run_id`` from a JSON request body, if it carries one (host-minted per
    the /query contract). A non-JSON or run_id-less body legitimately has none."""
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict):
        rid = payload.get("run_id")
        if isinstance(rid, str) and rid:
            return rid
    return None


class _SSEWatcher:
    """Passive observer of the relayed SSE byte stream.

    Feeds on the exact bytes forwarded to the client (never altering them) and
    tracks two facts the crash/cancel semantics depend on: whether a terminal
    ``final``/``error`` event has passed, and the current ``run_id`` (seeded
    from the request body, updated from any event that carries one). A
    malformed ``data:`` payload is logged loudly but does not fail the relay —
    the bytes reach the client verbatim either way; only the daemon-side
    crash classification would degrade.
    """

    def __init__(self, run_id: Optional[str] = None):
        self._buf = b""
        self._pending_cr = b""
        self._overflowed = False
        # Sticky: once bytes were dropped on overflow, a terminal event may
        # have passed unseen — terminal classification is unknown from then on.
        self.degraded = False
        self.terminal_seen = False
        self.run_id = run_id

    @property
    def mid_frame(self) -> bool:
        """True when the last forwarded byte was inside an unterminated frame."""
        return bool(self._buf) or bool(self._pending_cr) or self._overflowed

    def feed(self, chunk: bytes) -> None:
        # Normalize CRLF incrementally (a trailing '\r' may pair with the next
        # chunk's '\n') so '\r\n\r\n'-framed streams still detect frames. The
        # normalization is watcher-internal — forwarded bytes are untouched.
        data = self._pending_cr + chunk
        self._pending_cr = b""
        if data.endswith(b"\r"):
            self._pending_cr = b"\r"
            data = data[:-1]
        self._buf += data.replace(b"\r\n", b"\n")
        while True:
            frame, sep, rest = self._buf.partition(b"\n\n")
            if not sep:
                break
            self._buf = rest
            self._overflowed = False
            self._scan_frame(frame)
        if len(self._buf) > _WATCHER_BUFFER_CAP:
            if not self._overflowed:
                logger.warning(
                    "daemon relay: SSE watcher buffer exceeded %d bytes without "
                    "a frame separator — upstream is not emitting SSE frames; "
                    "terminal-event detection is degraded for this stream",
                    _WATCHER_BUFFER_CAP,
                )
            self._overflowed = True
            self.degraded = True
            self._buf = b""

    def _scan_frame(self, frame: bytes) -> None:
        for raw_line in frame.split(b"\n"):
            line = raw_line.strip(b"\r")
            if not line.startswith(b"data:"):
                continue
            payload = line[len(b"data:") :].strip()
            if not payload:
                continue
            try:
                event = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                logger.warning(
                    "daemon relay: unparseable SSE data payload (%.120r) — "
                    "forwarded verbatim, but terminal/run_id tracking cannot "
                    "read it",
                    payload,
                )
                continue
            if not isinstance(event, dict):
                continue
            rid = event.get("run_id")
            if isinstance(rid, str) and rid:
                self.run_id = rid
            if event.get("type") in TERMINAL_TYPES:
                self.terminal_seen = True


def build_relay_router(token: str, registry):
    """Token-guarded APIRouter relaying ``ANY /v1/{agent_id}/*`` to the
    agent's running sidecar (same guard as the ``/daemon/v1/*`` control
    plane — ``build_require_token`` — so the 401 contract never forks)."""
    from gaia.daemon.app import build_require_token

    require_token = build_require_token(token)
    router = APIRouter(dependencies=[Depends(require_token)])

    @router.api_route("/v1/{agent_id}/{path:path}", methods=RELAY_METHODS)
    async def relay(agent_id: str, path: str, request: Request):
        try:
            base_url, bearer = registry.connection(agent_id)
        except UnknownAgentError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except SidecarNotRunningError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

        body = await request.body()
        upstream_headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _REQUEST_DROP
        }
        upstream_headers["Authorization"] = f"Bearer {bearer}"
        upstream_headers["Accept-Encoding"] = "identity"
        url = f"{base_url}/v1/{agent_id}/{path}"

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
        )
        try:
            upstream = await client.send(
                client.build_request(
                    request.method,
                    url,
                    content=body,
                    headers=upstream_headers,
                    params=request.url.query or None,
                ),
                stream=True,
            )
        except httpx.HTTPError as e:
            await client.aclose()
            raise HTTPException(
                status_code=502,
                detail=(
                    f"sidecar for agent '{agent_id}' at {base_url} did not "
                    f"answer ({e.__class__.__name__}: {e}). It may have died "
                    "after registration — re-ensure it (POST "
                    f"/daemon/v1/agents/{agent_id}/ensure) and retry."
                ),
            ) from e

        content_type = upstream.headers.get("content-type", "")
        response_headers = {
            k: v for k, v in upstream.headers.items() if k.lower() not in _RESPONSE_DROP
        }

        if not content_type.lower().startswith("text/event-stream"):
            # Fixed-function route: buffered passthrough, verbatim envelope.
            try:
                payload = await upstream.aread()
            except httpx.HTTPError as e:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"sidecar for agent '{agent_id}' dropped the "
                        f"connection mid-response on /v1/{agent_id}/{path} "
                        f"({e.__class__.__name__}: {e}). Re-ensure it (POST "
                        f"/daemon/v1/agents/{agent_id}/ensure) and retry."
                    ),
                ) from e
            finally:
                await upstream.aclose()
                await client.aclose()
            return Response(
                content=payload,
                status_code=upstream.status_code,
                headers=response_headers,
            )

        watcher = _SSEWatcher(run_id=_run_id_from_body(body))

        async def _finalize(reason: str) -> None:
            """Propagate cancel upstream if the run never terminated, then
            close the upstream response + client. Cancel is best-effort by
            design (the sidecar may be the thing that just died)."""
            try:
                if not watcher.terminal_seen:
                    rid = watcher.run_id
                    if rid:
                        cancel_url = f"{base_url}/v1/{agent_id}/query/{rid}/cancel"
                        try:
                            resp = await client.post(
                                cancel_url,
                                headers={"Authorization": f"Bearer {bearer}"},
                                timeout=CANCEL_TIMEOUT,
                            )
                            logger.info(
                                "daemon relay: %s — cancel for agent '%s' "
                                "run_id=%s answered HTTP %s",
                                reason,
                                agent_id,
                                rid,
                                resp.status_code,
                            )
                        except httpx.HTTPError as exc:
                            logger.warning(
                                "daemon relay: %s — best-effort cancel for "
                                "agent '%s' run_id=%s failed at transport "
                                "level: %s",
                                reason,
                                agent_id,
                                rid,
                                exc,
                            )
                    else:
                        logger.warning(
                            "daemon relay: %s for agent '%s' with no run_id "
                            "observed — cannot propagate cancel upstream",
                            reason,
                            agent_id,
                        )
            finally:
                await upstream.aclose()
                await client.aclose()

        async def _relay_sse():
            disconnected = False
            try:
                try:
                    async for chunk in upstream.aiter_bytes():
                        watcher.feed(chunk)
                        yield chunk
                except (GeneratorExit, asyncio.CancelledError):
                    # Client went away mid-stream. This generator is being
                    # torn down and can no longer await safely, so cleanup +
                    # cancel-propagation move to a detached task.
                    disconnected = True
                    _spawn_background(_finalize("client disconnected"))
                    raise
                except httpx.HTTPError as exc:
                    # Upstream transport died mid-stream (crash/reset/timeout).
                    if not watcher.terminal_seen:
                        logger.warning(
                            "daemon relay: agent '%s' stream broke before its "
                            "terminal event (%s: %s) — emitting synthetic "
                            "terminal error",
                            agent_id,
                            exc.__class__.__name__,
                            exc,
                        )
                        yield _synthetic_error_frame(
                            stream_ended_unexpectedly_detail(agent_id),
                            terminate_partial=watcher.mid_frame,
                        )
                else:
                    # Clean EOF — but the contract requires exactly one
                    # terminal event; a silent early EOF is a crash (§0.13).
                    # A degraded watcher (buffer overflow dropped bytes) cannot
                    # know whether a terminal passed — appending a synthetic
                    # error then could double-terminate a completed run, so it
                    # only logs; cancel below stays best-effort either way.
                    if not watcher.terminal_seen:
                        if watcher.degraded:
                            logger.warning(
                                "daemon relay: agent '%s' stream hit EOF with "
                                "terminal state UNKNOWN (watcher buffer "
                                "overflowed earlier) — not appending a "
                                "synthetic error; propagating best-effort "
                                "cancel only",
                                agent_id,
                            )
                        else:
                            logger.warning(
                                "daemon relay: agent '%s' stream hit EOF "
                                "before its terminal event — emitting "
                                "synthetic terminal error",
                                agent_id,
                            )
                            yield _synthetic_error_frame(
                                stream_ended_unexpectedly_detail(agent_id),
                                terminate_partial=watcher.mid_frame,
                            )
            finally:
                if not disconnected:
                    await _finalize("stream finished")

        return StreamingResponse(
            _relay_sse(),
            status_code=upstream.status_code,
            headers=response_headers,
        )

    return router
