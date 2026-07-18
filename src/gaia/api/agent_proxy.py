# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Thin data-plane proxy exposing ``/v1/<agent>/query`` on ``gaia api`` (#2178 /
V2-17).

The API server is a thin client of the always-on GAIA daemon: it forwards
``ANY /v1/<agent>/*`` to the daemon's streaming relay (#2150 / V2-7) carrying
only the DAEMON client token. The daemon swaps that for the per-agent sidecar
bearer server-side, so the API server never learns sidecar coordinates nor holds
a sidecar bearer — the whole point of the thin-host model.

SSE bodies stream UNBUFFERED (``httpx.AsyncClient(stream=True)`` chunks →
``StreamingResponse``): each daemon chunk is forwarded the moment it arrives.
The daemon owns the crash/cancel synthetic-terminal contract (§0.13) — this proxy
forwards those frames verbatim and only authors its OWN synthetic terminal
``error`` when the DAEMON connection itself drops mid-stream (a failure the
daemon relay cannot report, being the thing that died). A client disconnect
closes the upstream connection, which the daemon relay sees as ITS client
disconnect and propagates as an upstream cancel to the sidecar.

Auth: the ``/v1/<agent>/*`` surface exposes the agentic loop, and ``gaia api``
may bind beyond loopback, so it is gated by an API key (``GAIA_API_KEY``) —
stricter than the daemon's loopback trust (§0.33). ``/v1/chat/completions`` and
``/v1/models`` are untouched. No silent fallback: an unset key, a missing/invalid
key, an unreachable daemon, and a dead sidecar each raise a loud, actionable HTTP
error (the daemon's own 404/503/502 envelope is preserved for the last two).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from typing import Optional

# Module-level import (matches gaia.daemon.relay): the endpoint annotation
# ``request: Request`` below must resolve from module globals under PEP 563.
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.convertors import Convertor, register_url_convertor

from gaia.daemon.errors import DaemonError
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Environment variable holding the API key that gates the agent query surface.
API_KEY_ENV = "GAIA_API_KEY"
#: Auth scheme for the API key (mirrors the daemon's Bearer contract).
AUTH_SCHEME = "Bearer"

#: Connect timeout — a dead daemon should fail fast on TCP connect.
CONNECT_TIMEOUT = 10.0
#: Read timeout between chunks — spans a whole agent-loop step relayed by the
#: daemon, so it matches the daemon relay's own long per-request budget.
READ_TIMEOUT = 300.0

#: HTTP methods the fixed-function relay accepts. OPTIONS/HEAD are intentionally
#: left to the CORS middleware / FastAPI defaults.
RELAY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]

#: First-segment ids owned by the OpenAI-compatible surface — never relayed as
#: agents. The ``/v1/<agent>/query`` routes use the default ``{agent_id}``
#: convertor and reject these ids in-handler (a stray ``/v1/chat/query`` →
#: 404). The fixed-function catch-all instead uses the ``relayagent`` convertor
#: below, whose regex refuses these ids at *routing* time so it never shadows
#: the OpenAI routes' native 404/405 (e.g. ``GET /v1/chat/completions`` → 405).
_RESERVED_AGENT_IDS = frozenset({"chat", "models"})


class _RelayAgentConvertor(Convertor):
    """Path convertor for the fixed-function relay's ``{agent_id}`` segment.

    Matches any single segment EXCEPT the reserved OpenAI ids (``chat`` /
    ``models``). A plain ``{agent_id}`` would match ``GET /v1/chat/completions``
    and steal FastAPI's native 405; refusing reserved ids at match time leaves
    those paths to the OpenAI routes' own 404/405 semantics.
    """

    regex = "(?!chat/)(?!models/)[^/]+"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


class _RelaySubpathConvertor(Convertor):
    """Path convertor for the relay's trailing sub-path — like ``:path`` but
    NON-empty, so ``/v1/<agent>`` and ``/v1/nonexistent`` (no sub-path) fall
    through to FastAPI's 404 instead of being relayed as an agent root."""

    regex = ".+"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor("relayagent", _RelayAgentConvertor())
register_url_convertor("relaysubpath", _RelaySubpathConvertor())

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
# Request headers the proxy owns: host (rewritten by httpx), authorization (the
# API key is swapped for the daemon client token), content-length (recomputed),
# accept-encoding (forced to identity so relayed SSE bytes stay uncompressed).
_REQUEST_DROP = _HOP_BY_HOP | {
    "host",
    "authorization",
    "content-length",
    "accept-encoding",
}
# Response headers the proxy owns: length/encoding are recomputed for the
# (decoded) body; date/server would duplicate uvicorn's own.
_RESPONSE_DROP = _HOP_BY_HOP | {"content-length", "content-encoding", "date", "server"}

# Strong refs to fire-and-forget cleanup tasks spawned from a cancelled response
# generator (asyncio only weak-refs pending tasks).
_background_tasks: "set[asyncio.Task]" = set()


def _spawn_background(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Loop already gone (server teardown): cleanup cannot run, so the
        # upstream response/client leak until GC — name it instead of hiding it.
        logger.warning(
            "gaia api proxy: no running loop for background cleanup task; "
            "upstream connection will be reclaimed by GC"
        )
        coro.close()
        return
    task = loop.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def daemon_stream_dropped_detail(agent_id: str) -> str:
    """Actionable detail for the proxy's synthetic terminal error frame — used
    only when the DAEMON connection drops mid-stream (the daemon relay cannot
    author its own §0.13 terminal because it is the thing that died)."""
    return (
        f"the GAIA daemon connection dropped mid-stream while relaying agent "
        f"'{agent_id}' — the daemon may have exited. Check `gaia daemon status` "
        "and `gaia daemon logs`, then retry."
    )


def _synthetic_error_frame(detail: str) -> bytes:
    """One canonical terminal ``error`` SSE frame, proxy-authored.

    Same shape as the daemon relay's frame (the frozen contract's ``error``
    event) with a distinct ``source`` marker so a downstream consumer can tell a
    proxy-synthesized terminal (daemon died) from a daemon-synthesized one
    (sidecar died) from a sidecar-authored one.
    """
    return (
        b"data: "
        + json.dumps({"type": "error", "detail": detail, "source": "gaia_api"}).encode(
            "utf-8"
        )
        + b"\n\n"
    )


def build_require_api_key():
    """FastAPI dependency enforcing the ``GAIA_API_KEY`` on the agent surface.

    No silent fallback: an unset key disables the surface with a loud 503 naming
    the remedy (rather than allowing unauthenticated access to the agentic loop);
    a missing/malformed/invalid key is a 401 naming what to send.
    """

    def require_api_key(authorization: Optional[str] = Header(default=None)) -> None:
        expected = os.environ.get(API_KEY_ENV)
        if not expected:
            raise HTTPException(
                status_code=503,
                detail=(
                    "The agent query surface (POST /v1/<agent>/query) is disabled: "
                    f"no API key is configured. Set {API_KEY_ENV} in the API "
                    "server's environment (e.g. "
                    f"`export {API_KEY_ENV}=$(openssl rand -hex 32)`), restart "
                    "`gaia api`, and send it as "
                    f"'Authorization: {AUTH_SCHEME} <key>'."
                ),
            )
        if not authorization:
            raise HTTPException(
                status_code=401,
                detail=(
                    f"Missing API key. Send 'Authorization: {AUTH_SCHEME} <key>' "
                    f"matching {API_KEY_ENV} on the API server."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != AUTH_SCHEME.lower() or not credential:
            raise HTTPException(
                status_code=401,
                detail=(
                    f"Malformed Authorization header. Expected "
                    f"'{AUTH_SCHEME} <key>'."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )
        if not secrets.compare_digest(credential, expected):
            raise HTTPException(
                status_code=401,
                detail=(
                    f"Invalid API key. It must match {API_KEY_ENV} on the API "
                    "server."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )

    return require_api_key


async def _acquire_daemon():
    """``start_or_attach`` off the event loop; loud HTTPException on failure.

    ``start_or_attach`` does blocking ``requests`` I/O (probe + possible detached
    spawn), so it runs in a threadpool. A daemon that cannot be reached or
    started is a loud 503 — never a silent in-process fallback (#2178)."""
    from gaia.daemon.client import start_or_attach

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, start_or_attach)
    except DaemonError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "the GAIA daemon is unavailable, so the agent query surface "
                f"cannot relay: {e} Check `gaia daemon status` and "
                "`gaia daemon logs`, then retry."
            ),
        ) from e


def build_agent_proxy_router() -> APIRouter:
    """API-key-guarded router relaying the ``/v1/<agent>/*`` surface to the
    daemon's relay (#2150). The daemon does the sidecar-bearer swap, so this
    proxy holds only the daemon client token.

    Two kinds of route, both API-key gated:

    * ``POST /v1/{agent}/query`` and ``.../query/{run_id}/cancel`` — the
      streaming agent loop (SSE, relayed unbuffered).
    * ``{METHOD} /v1/{agent}/{subpath}`` — the fixed-function agent surface
      (e.g. the email agent's ``/v1/email/{triage,draft,send,health,…}``),
      buffered passthrough. Without this, those routes 404 on ``gaia api`` even
      though the sidecar serves them (#2176).

    The fixed-function catch-all uses the ``relayagent`` convertor so it refuses
    the reserved OpenAI ids at routing time (never shadowing ``GET
    /v1/chat/completions``'s 405) and the ``relaysubpath`` convertor so a bare
    ``/v1/<agent>`` / ``/v1/nonexistent`` still yields FastAPI's 404. The
    ``/query`` routes are declared first so they win for that exact path.
    """
    require_api_key = build_require_api_key()
    router = APIRouter(dependencies=[Depends(require_api_key)])

    @router.post("/v1/{agent_id}/query")
    async def proxy_query(agent_id: str, request: Request):
        return await _relay(agent_id, "query", request)

    @router.post("/v1/{agent_id}/query/{run_id}/cancel")
    async def proxy_query_cancel(agent_id: str, run_id: str, request: Request):
        return await _relay(agent_id, f"query/{run_id}/cancel", request)

    @router.api_route(
        "/v1/{agent_id:relayagent}/{path:relaysubpath}", methods=RELAY_METHODS
    )
    async def proxy_fixed(agent_id: str, path: str, request: Request):
        return await _relay(agent_id, path, request)

    async def _relay(agent_id: str, path: str, request: Request):
        if agent_id in _RESERVED_AGENT_IDS:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"'/v1/{agent_id}/{path}' is not an agent route. The "
                    "OpenAI-compatible surface is POST /v1/chat/completions and "
                    "GET /v1/models; agent routes are /v1/<agent>/query."
                ),
            )

        inst = await _acquire_daemon()
        body = await request.body()
        upstream_headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _REQUEST_DROP
        }
        upstream_headers["Authorization"] = f"{AUTH_SCHEME} {inst.token}"
        upstream_headers["Accept-Encoding"] = "identity"
        url = f"{inst.base_url}/v1/{agent_id}/{path}"

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
                    f"the GAIA daemon at {inst.base_url} did not answer the relay "
                    f"for agent '{agent_id}' ({e.__class__.__name__}: {e}). It "
                    "may have exited — check `gaia daemon status` and "
                    "`gaia daemon logs`, then retry."
                ),
            ) from e

        content_type = upstream.headers.get("content-type", "")
        response_headers = {
            k: v for k, v in upstream.headers.items() if k.lower() not in _RESPONSE_DROP
        }

        if not content_type.lower().startswith("text/event-stream"):
            # Fixed-function / non-streaming daemon response (including the
            # daemon's own loud 404/503/502 envelopes): buffered passthrough,
            # verbatim status + headers + body.
            try:
                payload = await upstream.aread()
            except httpx.HTTPError as e:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"the GAIA daemon dropped the connection mid-response on "
                        f"/v1/{agent_id}/{path} ({e.__class__.__name__}: {e}). "
                        "Check `gaia daemon status` and retry."
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

        async def _close() -> None:
            await upstream.aclose()
            await client.aclose()

        async def _relay_sse():
            disconnected = False
            try:
                try:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                except (GeneratorExit, asyncio.CancelledError):
                    # Client went away mid-stream. Closing the upstream
                    # connection makes the daemon relay see ITS client
                    # disconnect and propagate cancel to the sidecar. This
                    # generator is being torn down and can no longer await
                    # safely, so cleanup moves to a detached task.
                    disconnected = True
                    _spawn_background(_close())
                    raise
                except httpx.HTTPError as exc:
                    # The daemon connection broke mid-stream — the daemon relay
                    # (now unreachable) cannot author its §0.13 terminal, so we
                    # emit our own so the consumer never hangs on a truncated
                    # stream.
                    logger.warning(
                        "gaia api proxy: relay to daemon for agent '%s' broke "
                        "mid-stream (%s: %s) — emitting synthetic terminal error",
                        agent_id,
                        exc.__class__.__name__,
                        exc,
                    )
                    yield _synthetic_error_frame(daemon_stream_dropped_detail(agent_id))
            finally:
                if not disconnected:
                    await _close()

        return StreamingResponse(
            _relay_sse(),
            status_code=upstream.status_code,
            headers=response_headers,
        )

    return router
