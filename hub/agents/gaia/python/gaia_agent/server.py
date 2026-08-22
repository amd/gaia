# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``POST /v1/gaia/query`` — the flagship agent's canonical streaming surface.

Implements the frozen v2 wire contract (`docs/spec/agent-ui-query-sse-contract.md`):
a `/query` POST returning `text/event-stream`, carrying the canonical event
vocabulary and terminated by **exactly one** ``final`` or ``error``. That
guarantee is what lets the daemon relay and the Go TUI treat every agent
identically.

The event translation itself is NOT reimplemented here — it lives in
``gaia.ui.sse_translation.CanonicalTranslator``, shared with the email sidecar,
so the two agents cannot drift into private dialects of the same contract.

Scope note: this deliberately implements ``/query`` and ``/query/{run_id}/cancel``
only. ``needs_confirmation`` ends the run with a refusal (the stateless D1 stub,
same as email) rather than pretending to support server-side resume, and there is
no ``/respond`` yet — the flagship's tools are read-mostly, so neither gate is
exercised. Both are additive when a tool needs them; claiming support we haven't
built would be worse than the honest gap.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from gaia_agent import caller_auth
from gaia_agent.session_registry import SessionCapacityError
from gaia_agent.session_registry import registry as session_registry
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import StreamingResponse

from gaia.logger import get_logger
from gaia.ui.sse_translation import TERMINAL_TYPES, CanonicalTranslator

logger = get_logger(__name__)

AGENT_ID = "gaia"

#: Bumped when the wire surface changes. The TUI's ``negotiate.go`` gates
#: optional request fields on this, so it must reflect real capability.
API_VERSION = "2.12"

#: A run parked with nothing to say still has to reset the client's read-idle
#: watchdog, or a long tool call reads as a dead stream.
_HEARTBEAT_SECONDS = 10.0

#: Local inference only — the flagship runs against Lemonade.
_ALLOWED_PROVIDERS = frozenset({"lemonade"})

_DOCS_URL = "https://amd-gaia.ai/docs/guides/gaia"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryContextItem(_Strict):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def _role_known(cls, v: str) -> str:
        if v not in {"user", "assistant", "system", "tool"}:
            raise ValueError(f"unknown role {v!r}")
        return v


class QueryRequest(_Strict):
    """``POST /v1/gaia/query`` body (frozen #2015 contract, spec §2.2)."""

    query: str = Field(min_length=1)
    run_id: str = Field(
        description=(
            "Host-minted UUIDv4 run handle. Cancellation "
            "(POST /v1/gaia/query/{run_id}/cancel) keys off it, so the client "
            "must mint it before the request rather than learn it from the stream."
        )
    )
    context: List[QueryContextItem] = Field(
        description="Transcript slice, pushed in the body. May be empty, never absent."
    )
    model: Optional[str] = None
    provider: Optional[str] = None
    max_steps: Optional[int] = Field(default=None, ge=1)
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "Conversation handle (contract >= 2.12). Threaded to the agent as its "
            "UI session so indexed documents survive across turns — without it a "
            "document agent forgets what it just indexed."
        ),
    )
    can_answer_questions: Optional[bool] = Field(
        default=None,
        description=(
            "Whether the caller can answer a mid-run question (contract >= 2.6). "
            "False for a one-shot run: the agent must not park on needs_input "
            "with nobody there to answer, which reads as a hang."
        ),
    )

    @field_validator("run_id")
    @classmethod
    def _run_id_is_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"run_id must be a UUID, got {v!r}") from exc
        return v


class QueryCancelResponse(_Strict):
    run_id: str
    cancelled: bool


class QueryRespondRequest(_Strict):
    """Body of ``POST /v1/gaia/query/{run_id}/respond`` (spec §5.1)."""

    request_id: str = Field(
        description=(
            "The 'request_id' from the needs_input event being answered. An "
            "answer for a question that is no longer pending is rejected rather "
            "than silently dropped."
        )
    )
    response: str


class QueryRespondResponse(_Strict):
    run_id: str
    request_id: str
    delivered: bool


class _QueryRun:
    """One in-flight run: the agent, its output handler, and its cancel flag."""

    def __init__(self, run_id: str, agent: Any, handler: Any) -> None:
        self.run_id = run_id
        self.agent = agent
        self.handler = handler
        self.cancel_event = threading.Event()
        self.result: Optional[Dict[str, Any]] = None


class _RunRegistry:
    """Process-local run table so ``/cancel`` can find a live run."""

    def __init__(self) -> None:
        self._runs: Dict[str, _QueryRun] = {}
        self._lock = threading.Lock()

    def add(self, run: _QueryRun) -> None:
        with self._lock:
            self._runs[run.run_id] = run

    def get(self, run_id: str) -> Optional[_QueryRun]:
        with self._lock:
            return self._runs.get(run_id)

    def remove(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)


_registry = _RunRegistry()


def _sse(event: Dict[str, Any]) -> str:
    """Frame one canonical event as a single SSE ``data:`` line."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _terminal_error_detail(exc: BaseException) -> str:
    """Actionable copy for a run-killing exception.

    Lemonade being unreachable is by far the most common failure and its raw
    urllib3 repr tells a user nothing, so it gets named copy with the fix.
    Anything else is surfaced verbatim — a generic 'something went wrong' would
    hide the one detail that makes a bug reportable.
    """
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if any(
        s in lowered
        for s in (
            "connection refused",
            "max retries",
            "failed to establish",
            "newconnectionerror",
        )
    ):
        return (
            "Local Lemonade Server is not reachable. Start it, then retry — "
            f"run `lemonade-server serve`, or see {_DOCS_URL}. "
            f"(underlying error: {text})"
        )
    return text


def _terminal_from_run_result(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Guarantee a terminal event when the loop returned without emitting one.

    The base agent handles some failures internally and returns an actionable
    message rather than raising, so a run can finish with no ``answer`` event.
    Surfacing that message beats inventing a generic error.
    """
    if isinstance(result, dict):
        for key in ("answer", "response", "result", "output"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return {"type": "final", "answer": value}
        error = result.get("error")
        if isinstance(error, str) and error.strip():
            return {"type": "error", "detail": error, "status": 500}
    return {
        "type": "error",
        "detail": (
            "The agent finished without producing an answer. This is a bug — "
            f"please report it with the daemon log ({_DOCS_URL})."
        ),
        "status": 500,
    }


def _confirmation_refusal(action: str) -> Dict[str, Any]:
    """Terminal refusal for a confirmation-gated tool (the stateless D1 stub)."""
    return {
        "type": "final",
        "answer": (
            f"I stopped before running '{action}' because it needs your explicit "
            "approval, and this streaming surface cannot collect that yet. "
            "Re-run the request through a surface that supports confirmation, or "
            "perform the action directly."
        ),
    }


def build_query_agent(**config_kwargs: Any):
    """Construct the flagship agent for one run.

    A seam, so tests can inject a scripted agent without a model server.
    """
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    return GaiaAgent(config=GaiaAgentConfig(silent_mode=True, **config_kwargs))


#: Lemonade floor. Kept in lock-step with gaia_agent_email.version and
#: gaia.installer.init_command — a machine provisioned for one agent must not be
#: below the floor of another.
MIN_LEMONADE_VERSION = "10.2.0"


def _version_meets_min(version: Optional[str], minimum: str) -> Optional[bool]:
    """``True``/``False``, or ``None`` when the version cannot be compared.

    ``None`` is *indeterminate*, not a pass — the caller renders it as an
    unknown row rather than a green one, so an unparseable version never
    silently reads as compatible.
    """
    if not version:
        return None
    try:
        got = tuple(int(p) for p in str(version).strip().lstrip("v").split(".")[:3])
        want = tuple(int(p) for p in minimum.split(".")[:3])
    except (ValueError, AttributeError):
        return None
    return got >= want


def _probe_lemonade() -> Dict[str, Any]:
    """Read-only probe of the local model server. Never pulls or loads."""
    import os

    import requests
    from gaia_agent.agent import GaiaAgentConfig

    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    base = (
        os.environ.get("LEMONADE_BASE_URL")
        or getattr(GaiaAgentConfig(), "base_url", None)
        or "http://localhost:13305"
    ).rstrip("/")
    model_id = DEFAULT_MODEL_NAME

    out: Dict[str, Any] = {
        "base_url": base,
        "reachable": False,
        "version": None,
        "present": False,
        "ctx_size": None,
        "model_id": model_id,
    }
    try:
        r = requests.get(f"{base}/api/v1/models", timeout=5)
        r.raise_for_status()
        out["reachable"] = True
        data = r.json().get("data") or []
        for entry in data:
            if entry.get("id") == model_id or model_id in str(
                entry.get("checkpoint", "")
            ):
                out["present"] = True
                ctx = entry.get("ctx_size") or entry.get("context_length")
                if isinstance(ctx, int):
                    out["ctx_size"] = ctx
                break
    except Exception:  # noqa: BLE001 - reachability probe; the caller reports it
        return out

    try:
        rv = requests.get(f"{base}/api/v1/health", timeout=5)
        if rv.ok:
            payload = rv.json()
            out["version"] = payload.get("version") or payload.get("server_version")
    except Exception:  # noqa: BLE001 - an absent version is indeterminate, not fatal
        pass
    return out


async def require_caller_token(request: Request) -> None:
    """Reject a request that does not carry this session's bearer token.

    No-ops when auth was never configured (the product server and the OpenAPI
    export mount this router without it) or when no token is set (dev mode,
    warned about at startup) — Host/Origin still apply in both cases.
    """
    config = caller_auth.get_config()
    if config is None or caller_auth.is_exempt_path(request.url.path):
        return
    if not caller_auth.token_ok(config, request.headers.get("authorization", "")):
        raise HTTPException(
            status_code=401,
            detail=(
                "Unauthorized: this sidecar requires the per-session bearer "
                "token minted by the process that spawned it. Send "
                "'Authorization: Bearer <token>'. Hosts get the token from "
                f"{caller_auth.TOKEN_FILE_ENV_VAR} (a 0600 file) or "
                f"{caller_auth.TOKEN_ENV_VAR}."
            ),
        )


router = APIRouter(
    tags=[f"{AGENT_ID}-query"], dependencies=[Depends(require_caller_token)]
)


@router.get("/init")
async def init(response: Any = None) -> Dict[str, Any]:
    """Readiness preflight — the row data the TUI's preflight screen renders.

    Unlike ``/health`` (liveness only, never touches the model server) this
    probes Lemonade, checks its version against the floor, and confirms the
    default model is downloaded, so a host can distinguish "process up" from
    "ready to answer". Read-only: no pull, no load.
    """
    from starlette.responses import JSONResponse

    probe = await asyncio.to_thread(_probe_lemonade)
    compatible = (
        _version_meets_min(probe["version"], MIN_LEMONADE_VERSION)
        if probe["reachable"]
        else None
    )

    hint: Optional[str] = None
    if not probe["reachable"]:
        hint = (
            f"Local Lemonade Server is not reachable at {probe['base_url']} — start it "
            f"with `lemonade-server serve`, or set LEMONADE_BASE_URL to a running server."
        )
    elif compatible is False:
        hint = (
            f"Lemonade {probe['version']} is older than the required "
            f"{MIN_LEMONADE_VERSION}. Update it, then re-check."
        )
    elif not probe["present"]:
        hint = (
            f"The model {probe['model_id']} is not downloaded. Run "
            f"`gaia download {probe['model_id']}`, then re-check."
        )

    ready = bool(probe["reachable"] and probe["present"] and compatible is not False)
    body = {
        "ready": ready,
        "lemonade": {
            "reachable": probe["reachable"],
            "base_url": probe["base_url"],
            "version": probe["version"],
            "min_version": MIN_LEMONADE_VERSION,
            "compatible": compatible,
        },
        "model": {
            "id": probe["model_id"],
            "present": probe["present"],
            "loadable": probe["present"] or None,
            "ctx_size": probe["ctx_size"],
        },
        "hint": hint,
    }
    # 503 when not ready, mirroring the email sidecar so one client code path
    # handles both agents.
    return JSONResponse(body, status_code=200 if ready else 503)


@router.post("/query")
async def query(request: QueryRequest):
    """Run the flagship agent loop for one request, streaming canonical SSE."""
    from gaia.ui.sse_handler import SSEOutputHandler

    if request.provider is not None and request.provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"provider {request.provider!r} is not supported: the {AGENT_ID} agent "
                f"runs local inference only. Allowed: {sorted(_ALLOWED_PROVIDERS)}."
            ),
        )

    handler = SSEOutputHandler()
    session = None
    try:
        kwargs: Dict[str, Any] = {}
        if request.model:
            kwargs["model_id"] = request.model
        if request.session_id:
            # Cross-turn document retention: ChatAgent persists its indexed-doc
            # set per UI session, so dropping this makes the agent forget a
            # document between the turn that indexed it and the next question.
            kwargs["ui_session_id"] = request.session_id
            # Schema 2.12 (#2829): a session_id resolves a RETAINED agent rather
            # than a throwaway. Whatever the turn puts on the instance — most
            # visibly Agent.loaded_skills — vanishes next turn otherwise, while
            # the model goes on telling the user the skill is still loaded.
            session = session_registry.get_or_create(request.session_id, **kwargs)
            if not session.run_lock.acquire(blocking=False):
                session = None  # not ours to release
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"session {request.session_id} is already running a turn. "
                        "Cancel that run or wait for it to finish, then retry."
                    ),
                )
            agent = session.agent
            if session.reclaimed_after_eviction:
                # Consume once: reset before the warning reaches the caller so
                # a later turn on this same still-live session isn't re-warned.
                session.reclaimed_after_eviction = False
                logger.warning(
                    "%s session %s was evicted (LRU cap or idle timeout) and "
                    "reclaimed with a fresh agent; loaded skills and other "
                    "per-turn state did not survive",
                    AGENT_ID,
                    request.session_id,
                )
                handler._emit(
                    {
                        "type": "status",
                        "status": "warning",
                        "message": (
                            "This session was reclaimed after being idle or "
                            "crowded out by other sessions — loaded skills "
                            "and other per-turn state were reset. Reload any "
                            "skill you still need."
                        ),
                    }
                )
        else:
            # No session handle — a genuine one-shot. Nothing persists past this
            # turn, and the agent is told so rather than over-promising.
            agent = build_query_agent(**kwargs)
        agent.console = handler
        if request.can_answer_questions is False:
            # Nobody is there to answer. Let the loop know so it resolves
            # ambiguity itself instead of parking on a question forever.
            handler.can_answer_questions = False

        # Pushed context, never pulled (spec §2.4).
        if request.context and hasattr(agent, "conversation_history"):
            agent.conversation_history = [
                {"role": c.role, "content": c.content} for c in request.context
            ]

        run = _QueryRun(request.run_id, agent, handler)
        _registry.add(run)
        agent._cancel_event = run.cancel_event
    except HTTPException:
        # Already an actionable status (e.g. the 409 above) — do not relabel it
        # as a generic 500.
        _registry.remove(request.run_id)
        if session is not None:
            session.run_lock.release()
        raise
    except SessionCapacityError as exc:
        # Actionable and temporary ("N sessions are already active and none
        # are idle enough to evict") — 503, not a bug-shaped 500.
        _registry.remove(request.run_id)
        if session is not None:
            session.run_lock.release()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        _registry.remove(request.run_id)
        if session is not None:
            session.run_lock.release()
        raise HTTPException(
            status_code=500, detail=f"Failed to start the query run: {exc}"
        ) from exc

    def _run_agent() -> None:
        try:
            if request.max_steps is not None:
                run.result = agent.process_query(
                    request.query, max_steps=request.max_steps
                )
            else:
                run.result = agent.process_query(request.query)
        except Exception as exc:  # surface loudly as a terminal error event
            logger.exception("%s /query run failed for run_id=%s", AGENT_ID, run.run_id)
            handler._emit(
                {"type": "agent_error", "content": _terminal_error_detail(exc)}
            )
        finally:
            handler.signal_done()
            # Release only after the agent is done touching the instance, so the
            # next turn on this session cannot start mid-run.
            if session is not None:
                session.run_lock.release()

    thread = threading.Thread(target=_run_agent, daemon=True)
    try:
        thread.start()
    except Exception as exc:
        # _run_agent never got to run, so its own finally: never fires —
        # release the run_lock here or a thread-exhaustion failure leaves
        # this session_id permanently 409ing for the life of the process.
        _registry.remove(request.run_id)
        if session is not None:
            session.run_lock.release()
        raise HTTPException(
            status_code=500, detail=f"Failed to start the query run: {exc}"
        ) from exc

    async def _stream():
        translator = CanonicalTranslator(request.run_id, agent_id=AGENT_ID)
        terminated = False
        last_write = time.monotonic()
        try:
            while True:
                try:
                    event = handler.event_queue.get_nowait()
                except queue.Empty:
                    if not thread.is_alive() and handler.event_queue.empty():
                        break
                    # `:` lines are SSE comments — every conformant reader skips
                    # them and resets its read-idle timer.
                    if time.monotonic() - last_write >= _HEARTBEAT_SECONDS:
                        last_write = time.monotonic()
                        yield ": keepalive\n\n"
                    await asyncio.sleep(0.03)
                    continue

                if event is None:  # signal_done sentinel → stream close (spec §3)
                    break

                for canonical in translator.translate(event):
                    ctype = canonical.get("type")
                    yield _sse(canonical)
                    last_write = time.monotonic()
                    if ctype == "needs_input":
                        # Answerable, so the run stays alive: keep draining while
                        # the worker thread blocks waiting for /respond.
                        continue
                    if ctype == "needs_confirmation":
                        yield _sse(_confirmation_refusal(canonical.get("action", "")))
                        handler.cancelled.set()
                        run.cancel_event.set()
                        terminated = True
                        return
                    if ctype in TERMINAL_TYPES:
                        terminated = True
                        return

            # Queue closed. Flush any buffered tool_call, then guarantee the one
            # terminal event the contract mandates.
            for canonical in translator.flush():
                yield _sse(canonical)
                if canonical.get("type") in TERMINAL_TYPES:
                    terminated = True
            if not terminated:
                yield _sse(_terminal_from_run_result(run.result))
        finally:
            # A client that disconnected mid-run should not leave the loop running.
            handler.cancelled.set()
            run.cancel_event.set()
            _registry.remove(run.run_id)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/query/{run_id}/cancel", response_model=QueryCancelResponse)
async def cancel_query(run_id: str) -> QueryCancelResponse:
    """Ask a live run to stop. Unknown ids report ``cancelled=False``, not 404 —
    a race between the client's cancel and the run's own completion is normal."""
    run = _registry.get(run_id)
    if run is None:
        return QueryCancelResponse(run_id=run_id, cancelled=False)
    run.cancel_event.set()
    run.handler.cancelled.set()
    return QueryCancelResponse(run_id=run_id, cancelled=True)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8141  # 8131 is the email sidecar; never 4001.


@router.post("/query/{run_id}/respond", response_model=QueryRespondResponse)
async def respond_to_query(
    run_id: str, body: QueryRespondRequest
) -> QueryRespondResponse:
    """Deliver a user's answer to a mid-run ``needs_input`` question.

    The run continues on its existing SSE stream — this does not open a new one.
    An unknown run or a question that is no longer pending is a loud 404/409
    rather than a quiet no-op: silently dropping the answer would leave the
    agent blocked until its own timeout, which the user reads as a hang.
    """
    run = _registry.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No run {run_id!r} is in flight. It may have already finished or "
                "been cancelled; the answer was not delivered."
            ),
        )
    if not run.handler.resolve_user_input(body.request_id, body.response):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Question {body.request_id!r} is not pending on run {run_id!r} — "
                "it was already answered, timed out, or never asked."
            ),
        )
    return QueryRespondResponse(
        run_id=run_id, request_id=body.request_id, delivered=True
    )


def build_app() -> FastAPI:
    """The sidecar ASGI app.

    Three surfaces, each with a different consumer:

    - ``GET /health``  — liveness.
    - ``GET /version`` — the DAEMON's contract probe. It reads ``apiVersion``
      and ``agentVersion`` from here and refuses to attach on a major mismatch,
      so the key names are a contract, not a convention.
    - ``GET /v1/gaia/version`` — the TUI's ``negotiate.go`` probe, which gates
      optional request fields on ``apiVersion``.
    """
    from gaia_agent import __version__

    app = FastAPI(title="GAIA Agent", version=__version__)

    # Loopback is not access control: without this, any page the user visits can
    # drive an agent that has shell and file tools. Wired ONLY here, on the
    # sidecar app the frozen binary serves.
    auth_config = caller_auth.config_from_environment()
    caller_auth.configure(auth_config)
    app.add_middleware(caller_auth.HostOriginMiddleware)
    if auth_config.token:
        channel = (
            f"0600 secret file ({caller_auth.TOKEN_FILE_ENV_VAR})"
            if os.environ.get(caller_auth.TOKEN_FILE_ENV_VAR)
            else f"{caller_auth.TOKEN_ENV_VAR} env var (legacy delivery)"
        )
        logger.info(
            "GAIA sidecar: caller authentication ENABLED via %s "
            "(per-session bearer token required on /v1/%s/* requests).",
            channel,
            AGENT_ID,
        )
    else:
        logger.warning(
            "GAIA sidecar: caller authentication DISABLED — neither %s nor %s "
            "is in the environment. This is intended for LOCAL DEVELOPMENT "
            "only; the shipped product spawns the sidecar with a per-session "
            "token. Host/Origin protection is still enforced.",
            caller_auth.TOKEN_FILE_ENV_VAR,
            caller_auth.TOKEN_ENV_VAR,
        )

    @app.get("/health", include_in_schema=True)
    async def health() -> Dict[str, str]:
        return {"status": "ok", "service": f"gaia-agent-{AGENT_ID}"}

    @app.get("/version", include_in_schema=True)
    async def version() -> Dict[str, str]:
        return {"apiVersion": API_VERSION, "agentVersion": __version__}

    @app.get(f"/v1/{AGENT_ID}/version", include_in_schema=True)
    async def agent_version() -> Dict[str, str]:
        return {"apiVersion": API_VERSION, "version": __version__, "agent": AGENT_ID}

    app.include_router(router, prefix=f"/v1/{AGENT_ID}")
    return app


app = build_app()


def main(argv: Optional[List[str]] = None) -> int:
    """Run the sidecar. Bound to loopback by default — this speaks for the
    user's documents and memory and has no business on a LAN interface."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="GAIA flagship agent sidecar")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    args = parser.parse_args(argv)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "app",
    "build_app",
    "main",
    "router",
    "build_query_agent",
    "API_VERSION",
    "AGENT_ID",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
]
