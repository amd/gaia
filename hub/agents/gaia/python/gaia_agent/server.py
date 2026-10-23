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

Scope note: the surfaces here are ``/init`` (readiness preflight), ``/query``,
``/query/{run_id}/cancel``, ``/query/{run_id}/respond``,
``/query/{run_id}/followup``, ``/query/{run_id}/tool_decision`` (contract >=
2.14) and ``/sessions/{session_id}/bypass``. ``needs_input`` is answered over
``/respond`` on the run's existing stream; ``needs_confirmation`` is answered
the same way over ``/tool_decision`` — but only when there is a session to
hold the grant AND the caller declared it can answer (``can_confirm`` in
``query()``). A one-shot request (no ``session_id``, or
``can_answer_questions: false``) still ends such a run with a refusal (the
stateless D1 stub, same as email) rather than parking a run nobody can
answer.

:func:`main` also owns the binary's TRANSPORT DISPATCH: ``--serve`` runs this
HTTP surface, anything else delegates to :mod:`gaia_agent.stdio`. One
executable serves both, so the release matrix stays one artifact per platform.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from gaia_agent import caller_auth
from gaia_agent.entry import main as _entry_main
from gaia_agent.memory_dump import build_memory_dump
from gaia_agent.session_registry import (
    AGENT_BUILD_LOCK,
    PROVIDER_CLAUDE,
    PROVIDER_LOCAL,
    SessionCapacityError,
    close_agent,
)
from gaia_agent.session_registry import registry as session_registry
from gaia_agent_chat.session import validate_session_id
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.responses import StreamingResponse

from gaia.agents.base import file_snapshots
from gaia.agents.base.readiness import start_advice, version_meets_min
from gaia.daemon.broker_client import interactive_turn, queue_status_event
from gaia.logger import get_logger
from gaia.ui.sse_translation import TERMINAL_TYPES, CanonicalTranslator
from gaia.version import LEMONADE_MIN_VERSION

logger = get_logger(__name__)

AGENT_ID = "gaia"

#: Bumped when the wire surface changes. The TUI's ``negotiate.go`` gates
#: optional request fields on this, so it must reflect real capability.
#: 2.13 (#3978) added ``GET /memory`` — the daemon-transport counterpart of
#: the stdio ``MEMORY_DUMP_QUERY`` sentinel.
#: 2.14 added ``/query/{run_id}/tool_decision`` and ``/sessions/{id}/bypass``,
#: and the ``claude`` provider value.
#: 2.15 (#3620) added ``POST /query/{run_id}/followup`` for mid-turn messages.
#: 2.16 (#4166) added ``/query``'s ``documents`` field.
#: 2.17 added ``GET /skills`` for the Agent UI's Skills settings.
#: 2.18 added ``/query``'s ``allowed_paths`` and ``memory`` fields.
#: 2.19 added ``/query``'s ``changes_turn_id`` field.
API_VERSION = "2.19"

#: A run parked with nothing to say still has to reset the client's read-idle
#: watchdog, or a long tool call reads as a dead stream.
_HEARTBEAT_SECONDS = 10.0

#: Inference backends ``/query`` accepts. ``claude`` sends the conversation to
#: Anthropic's API instead of the local Lemonade server — the stdio transport
#: has always allowed that via ``--use-claude``, and refusing it here was what
#: made the daemon transport a downgrade rather than a move (see
#: docs/plans/daemon-convergence.mdx §3.2). Anything outside this set is still
#: refused loudly rather than quietly falling back to the default.
_ALLOWED_PROVIDERS = frozenset({PROVIDER_LOCAL, PROVIDER_CLAUDE})

#: Provider value that means "not local".
_CLAUDE_PROVIDER = PROVIDER_CLAUDE

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

    query: str = Field(
        min_length=1,
        description="The user's message for this turn.",
    )
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
    model: Optional[str] = Field(
        default=None,
        description=(
            "Model id for this turn. A Claude model id implies provider "
            "'claude' when provider is omitted; otherwise it is served by the "
            "local Lemonade backend. Omit to keep a retained session's current "
            "model, or to use the backend's default on a new session."
        ),
    )
    provider: Optional[str] = Field(
        default=None,
        description=(
            "Inference backend for this turn: 'lemonade' (local) or 'claude' "
            "(Anthropic's API). Omit to leave a retained session on its "
            "current backend, or to infer it from 'model' on a new session."
        ),
    )
    max_steps: Optional[int] = Field(
        default=None,
        ge=1,
        description="Cap on agent-loop steps for this turn. Omit for the agent's default.",
    )
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
    documents: Optional[List[str]] = Field(
        default=None,
        description=(
            "Absolute paths of the files the user attached to this conversation "
            "(contract >= 2.16). Each is granted as that exact file, never its "
            "folder, and indexed before the turn runs. Send the full set every "
            "turn: files already indexed in a retained session are skipped, so "
            "one attached mid-conversation is picked up on the next turn."
        ),
    )

    allowed_paths: Optional[List[str]] = Field(
        default=None,
        description=(
            "The conversation's file scope as the caller computed it (contract "
            ">= 2.18), so the agent works where the caller's UI says it does. "
            "Absolute paths. Builds a new session with exactly this scope; a "
            "retained session gains any entry it lacks and keeps what the user "
            "granted. GAIA_ALLOWED_PATHS still narrows it. Omit for the agent's "
            "default scope."
        ),
    )

    @field_validator("allowed_paths")
    @classmethod
    def _allowed_paths_absolute(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        relative = [p for p in v if not p or not Path(p).is_absolute()]
        if relative:
            raise ValueError(
                f"allowed_paths must be absolute paths; got {relative!r}. The "
                "agent's working directory is not the caller's, so a relative "
                "path would name a different folder."
            )
        return list(dict.fromkeys(v))

    memory: Optional[Literal["on", "private", "off"]] = Field(
        default=None,
        description=(
            "Whether this turn may save to memory (contract >= 2.18): 'on', "
            "'private' (a private chat) or 'off' (memory turned off in the "
            "caller's settings). With 'private' or 'off' nothing is written, "
            "including the errors and lessons the agent saves on its own. Send "
            "it every turn: a retained session keeps the last value. Omit it to "
            "leave memory as the agent has it."
        ),
    )

    changes_turn_id: Optional[str] = Field(
        default=None,
        pattern=file_snapshots.TURN_ID_PATTERN,
        description=(
            "Record this turn's file changes for undo, under this turn of the "
            "session (contract >= 2.19). Needs session_id. A copy of each file "
            "the agent writes is saved first, in the snapshot store of the "
            "agent's GAIA home, keyed by session_id and this id, and the "
            "recording is complete before the terminal event is sent. Use "
            "file_snapshots.new_turn_id() to make one. Omit it to record nothing."
        ),
    )

    @model_validator(mode="after")
    def _changes_need_a_session(self) -> "QueryRequest":
        if self.changes_turn_id is not None and not self.session_id:
            raise ValueError(
                "changes_turn_id needs session_id: a turn's file changes are "
                "recorded under the session they belong to."
            )
        return self

    @field_validator("documents")
    @classmethod
    def _documents_absolute(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        relative = [p for p in v if not p or not Path(p).is_absolute()]
        if relative:
            raise ValueError(
                f"documents must be absolute file paths; got {relative!r}. The "
                "agent's working directory is not the caller's, so a relative "
                "path would name a different file."
            )
        return list(dict.fromkeys(v))

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
    value: Optional[str] = Field(
        default=None,
        description=(
            "The answer: an option's 'value' (or its 'label'), or free text "
            "when the question set allow_free_text. The spec §5.1 name, shared "
            "with the email agent, the Agent UI and the TUI."
        ),
    )
    response: Optional[str] = Field(
        default=None,
        description="Deprecated spelling of 'value'. Send exactly one of the two.",
    )

    @model_validator(mode="after")
    def _exactly_one_answer(self) -> "QueryRespondRequest":
        if (self.value is None) == (self.response is None):
            raise ValueError(
                "send the answer in exactly one field: 'value' (preferred) or "
                "the deprecated 'response'"
            )
        return self

    @property
    def answer(self) -> str:
        return self.value if self.value is not None else self.response


class QueryRespondResponse(_Strict):
    run_id: str
    request_id: str
    delivered: bool


class QueryFollowUpRequest(_Strict):
    """Body of ``POST /v1/gaia/query/{run_id}/followup`` (contract >= 2.15)."""

    text: str = Field(
        min_length=1,
        description=(
            "What the user typed while this run was still working. The agent "
            "folds it into the running turn at its next step boundary; it does "
            "not start a new turn and does not interrupt the current one."
        ),
    )


class QueryFollowUpResponse(_Strict):
    run_id: str
    delivered: bool


#: The answers a tool confirmation accepts, matching the stdio control
#: channel's vocabulary exactly (``gaia_agent.stdio.DECISION_*``). Any other
#: spelling is refused here rather than guessed at.
_TOOL_DECISIONS = ("allow", "deny", "always", "timeout")


class ToolDecisionRequest(_Strict):
    """Body of ``POST /v1/gaia/query/{run_id}/tool_decision``.

    The HTTP twin of the stdio transport's ``tool_decision`` control message —
    the seam that lets a remote surface answer a confirmation while the agent
    thread is still parked on it.
    """

    decision: str = Field(
        description=(
            "One of 'allow', 'deny', 'always', 'timeout'. 'always' grants the "
            "pending call's scope for the rest of the session. 'timeout' denies "
            "because nobody answered, and the agent reports it as a timeout."
        )
    )
    confirm_id: Optional[str] = Field(
        default=None,
        description=(
            "The 'confirm_id' from the needs_confirmation event being answered. "
            "Without it a late answer resolves whichever confirmation replaced "
            "the one it was typed against."
        ),
    )

    @field_validator("decision")
    @classmethod
    def _known_decision(cls, v: str) -> str:
        if v not in _TOOL_DECISIONS:
            raise ValueError(
                f"decision must be one of {', '.join(_TOOL_DECISIONS)}, got {v!r}"
            )
        return v


class ToolDecisionResponse(_Strict):
    run_id: str
    decision: str
    delivered: bool


class BypassRequest(_Strict):
    """Body of ``POST /v1/gaia/sessions/{session_id}/bypass``."""

    enabled: bool


class BypassResponse(_Strict):
    session_id: str
    enabled: bool


class _QueryRun:
    """One in-flight run: the agent, its output handler, and its cancel flag."""

    def __init__(self, run_id: str, agent: Any, handler: Any) -> None:
        self.run_id = run_id
        self.agent = agent
        self.handler = handler
        self.cancel_event = threading.Event()
        #: Mid-turn follow-ups (contract >= 2.15). The agent drains this at its
        #: step boundary; see Agent._drain_followups.
        self.followups: "queue.Queue[str]" = queue.Queue()
        self.result: Optional[Dict[str, Any]] = None
        #: Records the run's file changes when the caller asked for it.
        self.recorder: Optional[file_snapshots.TurnRecorder] = None


class DuplicateRunError(RuntimeError):
    """A ``run_id`` already in flight was submitted again.

    ``run_id`` is client-minted, so this is reachable from a client bug. Taking
    the newer run would make the older one uncancellable and let either stream's
    teardown drop the other's run-table entry.
    """


#: Cap on remembered cancel-before-start ids. Each is one short string, consumed
#: the moment its run registers; this only bounds cancels whose run never came.
_MAX_PRECANCELLED = 256

#: Cap on remembered just-finished ids, which is what lets ``cancel`` tell a run
#: that ended a moment ago from one that has not registered yet.
_MAX_FINISHED = 256

_MISSING = object()


def _remember_bounded(store: Dict[str, None], key: str, cap: int) -> None:
    """Record ``key`` as the newest entry, dropping the oldest past ``cap``."""
    store.pop(key, None)  # re-insert at the end, so this is the newest
    store[key] = None
    while len(store) > cap:
        store.pop(next(iter(store)))


class _RunRegistry:
    """Process-local run table so ``/cancel`` can find a live run."""

    def __init__(self) -> None:
        self._runs: Dict[str, _QueryRun] = {}
        #: run_ids cancelled before their ``/query`` registered. Insertion
        #: ordered, so the oldest is the one evicted at the cap.
        self._precancelled: Dict[str, None] = {}
        #: run_ids whose run has already ended. Same shape, and the reason
        #: ``cancel`` does not tombstone them.
        self._finished: Dict[str, None] = {}
        self._lock = threading.Lock()

    def add(self, run: _QueryRun) -> bool:
        """Register an in-flight run; returns whether it arrives pre-cancelled.

        Raises :class:`DuplicateRunError` rather than overwriting: the run
        already under this id is live, and clobbering it strands it.
        """
        with self._lock:
            if run.run_id in self._runs:
                raise DuplicateRunError(
                    f"run_id {run.run_id} is already running on this sidecar. "
                    "run_id is minted by the caller, so mint a fresh UUID per "
                    "request — reusing one would leave the earlier run with no "
                    "way to be cancelled."
                )
            self._runs[run.run_id] = run
            return self._precancelled.pop(run.run_id, _MISSING) is not _MISSING

    def get(self, run_id: str) -> Optional[_QueryRun]:
        with self._lock:
            return self._runs.get(run_id)

    def cancel(self, run_id: str) -> bool:
        """Stop a live run, or remember the cancel for one about to register.

        Returns whether a LIVE run was stopped; an id with no live run stays
        ``False``, because a cancel racing a run's own completion should not
        claim to have stopped anything.

        Two different situations miss ``_runs``, and only one of them may be
        tombstoned. A run that ALREADY ENDED is the common, documented race, and
        arming a tombstone for it would leave an entry nothing can consume — and
        would pre-cancel the next run if the caller reused that id. A run that
        has NOT REGISTERED YET is the real ordering this exists for: the caller
        mints ``run_id`` before it POSTs ``/query``, so a cancel can genuinely
        arrive first. Only the latter is remembered, and it is consumed on use,
        so it fires at most once.
        """
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.cancel_event.set()
                run.handler.cancelled.set()
                return True
            if run_id not in self._finished:
                _remember_bounded(self._precancelled, run_id, _MAX_PRECANCELLED)
            return False

    def remove(self, run_id: str) -> None:
        """Retire a finished run, remembering the id so a late cancel knows it
        ended rather than treating it as one that has yet to start."""
        with self._lock:
            self._runs.pop(run_id, None)
            _remember_bounded(self._finished, run_id, _MAX_FINISHED)


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
            f"Local Lemonade Server is not reachable. {start_advice()} "
            f"See {_DOCS_URL}. (underlying error: {text})"
        )
    return text


def _terminal_from_run_result(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Guarantee a terminal event when the loop returned without emitting one.

    The base agent handles some failures internally and returns an actionable
    message rather than raising, so a run can finish with no ``answer`` event.
    Surfacing that message beats inventing a generic error.
    """
    if isinstance(result, dict):
        if result.get("status") == "cancelled":
            # Stopped mid-generation: the loop returns no text by design, and
            # the stdio transport ends the same turn with an empty final.
            return {"type": "final", "answer": ""}
        for key in ("answer", "response", "result", "output"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                final: Dict[str, Any] = {"type": "final", "answer": value}
                if result.get("model_failed"):
                    final["failed"] = True
                return final
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


#: Step name the attached-document indexing is reported under — the same one
#: the in-process Agent UI path uses, so both read alike.
_ATTACH_TOOL = "index_documents"


def _attach_refusal(agent: Any, path: str) -> str:
    """Why an attached *path* cannot be indexed, or ``""`` when it can."""
    target = Path(path)
    if not target.exists():
        return "file not found"
    if not target.is_file():
        return "not a file; only individual files can be attached"
    blocked, reason = agent.path_validator.is_read_blocked(path)
    return reason if blocked else ""


def _index_attached_documents(agent: Any, documents: List[str], handler: Any) -> None:
    """Index the documents the caller attached to this conversation (>= 2.16).

    Grants each as that exact file, never its folder: the scope an in-process
    Agent UI agent gets. Files already indexed are skipped, so a retained
    session pays only for documents attached since its last turn.
    """
    indexed = agent.indexed_files
    pending = [d for d in documents if d not in indexed]
    if not pending:
        return
    handler.print_tool_usage(_ATTACH_TOOL)
    handler.pretty_print_json(
        {"documents": [Path(d).name for d in pending]}, title="Arguments"
    )
    rag = agent.rag
    lines: List[str] = []
    for doc in pending:
        name = Path(doc).name
        reason = (
            "document search is not available in this session"
            if rag is None
            else _attach_refusal(agent, doc)
        )
        if reason:
            lines.append(f"{name}: not indexed: {reason}")
            continue
        agent.path_validator.add_allowed_path(doc)
        try:
            result = rag.index_document(doc)
        except Exception as exc:  # reported in the visible step below
            logger.exception("Indexing attached document %s failed", doc)
            lines.append(f"{name}: not indexed: {exc}")
            continue
        if result.get("success") and not result.get("error"):
            indexed.add(doc)
            lines.append(f"{name}: {result.get('num_chunks', 0)} chunks")
        else:
            error = result.get("error") or "the indexer gave no reason"
            lines.append(f"{name}: not indexed: {error}")
    succeeded = sum(1 for d in pending if d in indexed)
    if succeeded:
        agent.rebuild_system_prompt()
    all_indexed = succeeded == len(pending)
    handler._emit(
        {
            "type": "tool_result",
            "title": "Index Documents",
            "summary": "\n".join(lines),
            "success": all_indexed,
            # The status/message shape is what a relaying host summarizes from.
            "result_data": {
                "status": "indexed" if all_indexed else "not all indexed",
                "message": "; ".join(lines),
                "success": all_indexed,
            },
        }
    )


def _apply_memory_mode(agent: Any, mode: Optional[str]) -> None:
    """Turn *agent*'s memory writes off or back on for this turn (>= 2.18).

    "on" never re-enables memory the agent itself switched off (no store, or
    ``GAIA_MEMORY_DISABLED``).
    """
    if mode is None or not hasattr(agent, "_incognito"):
        return
    if mode == "on":
        if getattr(agent, "_memory_store", None) is not None:
            agent._incognito = False
            agent._incognito_reason = None
        return
    agent._incognito = True
    agent._incognito_reason = "private" if mode == "private" else "memory_off"


def build_query_agent(**config_kwargs: Any):
    """Construct the flagship agent for one run.

    A seam, so tests can inject a scripted agent without a model server.
    """
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    with AGENT_BUILD_LOCK:
        return GaiaAgent(config=GaiaAgentConfig(silent_mode=True, **config_kwargs))


def _probe_lemonade() -> Dict[str, Any]:
    """Read-only probe of the local model server. Never pulls or loads."""
    import requests
    from gaia_agent.agent import GaiaAgentConfig

    from gaia.llm.lemonade_client import (
        _model_ids_match,
        configured_lemonade_url,
        lemonade_auth_headers,
        resolve_default_chat_model,
        resolve_lemonade_api_key,
        resolve_lemonade_base_url,
    )

    # Already ends in /api/v1 — the requests below must not append it again.
    base = resolve_lemonade_base_url(
        configured_lemonade_url() or getattr(GaiaAgentConfig(), "base_url", None)
    ).rstrip("/")
    # GAIA's own server rejects keyless requests, which would read as "unreachable".
    headers = lemonade_auth_headers(resolve_lemonade_api_key(base_url=base))
    model_id = resolve_default_chat_model()

    out: Dict[str, Any] = {
        "base_url": base,
        "reachable": False,
        "version": None,
        "present": False,
        "ctx_size": None,
        "model_id": model_id,
    }
    try:
        r = requests.get(f"{base}/models", timeout=5, headers=headers)
        r.raise_for_status()
        out["reachable"] = True
        data = r.json().get("data") or []
        for entry in data:
            if _model_ids_match(entry.get("id"), model_id) or model_id in str(
                entry.get("checkpoint", "")
            ):
                out["present"] = True
                ctx = entry.get("ctx_size") or entry.get("context_length")
                if isinstance(ctx, int):
                    out["ctx_size"] = ctx
                break
    except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
        # The caller reports "not reachable"; the log keeps the actual cause.
        logger.warning("Lemonade probe of %s/models failed: %s", base, exc)
        return out

    try:
        rv = requests.get(f"{base}/health", timeout=5, headers=headers)
        rv.raise_for_status()
        payload = rv.json()
        out["version"] = payload.get("version") or payload.get("server_version")
    except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
        # An absent version is indeterminate (rendered as unknown), not fatal.
        logger.warning("Lemonade version probe of %s/health failed: %s", base, exc)
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
async def init() -> Dict[str, Any]:
    """Readiness preflight — the row data the TUI's preflight screen renders.

    Unlike ``/health`` (liveness only, never touches the model server) this
    probes Lemonade, checks its version against the floor, and confirms the
    default model is downloaded, so a host can distinguish "process up" from
    "ready to answer". Read-only: no pull, no load.
    """
    from starlette.responses import JSONResponse

    probe = await asyncio.to_thread(_probe_lemonade)
    compatible = (
        version_meets_min(probe["version"], LEMONADE_MIN_VERSION)
        if probe["reachable"]
        else None
    )

    hint: Optional[str] = None
    if not probe["reachable"]:
        # Off the event loop: start_advice() resolves the host's install, which
        # touches the filesystem.
        advice = await asyncio.to_thread(start_advice)
        hint = (
            f"Local Lemonade Server is not reachable at {probe['base_url']}. "
            f"{advice} Or set LEMONADE_BASE_URL to a running server. "
            f"See {_DOCS_URL}."
        )
    elif compatible is False:
        hint = (
            f"Lemonade {probe['version']} is older than the required "
            f"{LEMONADE_MIN_VERSION}. Run `gaia init --force-reinstall`, then "
            "re-check."
        )
    elif not probe["present"]:
        # `gaia download` takes no positional model — argparse exits 2 on it.
        from gaia.llm.lemonade_launcher import describe_client_hint

        pull = describe_client_hint("pull", probe["model_id"]).instruction
        hint = (
            f"The model {probe['model_id']} is not downloaded. "
            f"{pull.rstrip('.')}, then re-check."
        )

    ready = bool(probe["reachable"] and probe["present"] and compatible is not False)
    body = {
        "ready": ready,
        "lemonade": {
            "reachable": probe["reachable"],
            "base_url": probe["base_url"],
            "version": probe["version"],
            "min_version": LEMONADE_MIN_VERSION,
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


@router.get("/memory")
async def memory() -> Dict[str, Any]:
    """The ``/memory`` view's snapshot, for the daemon transport.

    Same payload as the stdio ``MEMORY_DUMP_QUERY`` sentinel
    (``gaia_agent.stdio._memory_dump_event`` -> ``build_memory_dump``), just
    without that path's JSON-in-a-final-event wrapping — this is a plain GET,
    so the dict is the whole response body. ``*SSEClient.FetchMemory`` decodes
    it straight into ``client.MemoryDump`` (``tui/internal/client/memory.go``).
    A one-shot agent is enough: the memory store lives at ``~/.gaia/memory.db``
    regardless of which agent instance opens it.
    """
    agent = None
    try:
        # Off the event loop: constructing the agent registers every tool and
        # loads its skills, which would stall concurrent runs' SSE heartbeats.
        agent = await asyncio.to_thread(build_query_agent)
        return await asyncio.to_thread(build_memory_dump, agent)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to build the memory dump: {exc}"
        ) from exc
    finally:
        if agent is not None:
            close_agent(agent)


@router.get("/debug/turns", include_in_schema=False)
async def debug_turns(limit: int = 20) -> Dict[str, Any]:
    """The last *limit* turn records, newest last — dev mode only.

    Off the contract on purpose: it exists only while ``GAIA_TURN_LOG`` is set,
    and its shape is the turn log's (``gaia.turn/1``), not a versioned API.
    """
    from gaia.agents.base.turn_metrics import RECENT_TURNS, TURN_LOG_ENV, turn_log_path

    if turn_log_path() is None:
        raise HTTPException(
            status_code=404,
            detail=f"Turn recording is off. Set {TURN_LOG_ENV}=<path> before "
            "starting the agent to record turns.",
        )
    if limit < 1:
        raise HTTPException(status_code=422, detail="limit must be 1 or more")
    return {"turns": list(RECENT_TURNS)[-limit:]}


@router.get("/skills")
async def skills() -> Dict[str, Any]:
    """The skills this agent can load, for the Agent UI's Skills settings.

    The bundled skills live inside the frozen binary, so a UI that does not
    have the agent package installed beside it can only list them from here.
    """
    from gaia_agent.agent import ENGINEERING_SKILL, GaiaAgent

    from gaia.skills.errors import SkillError
    from gaia.skills.listing import skills_listing

    try:
        # Off the event loop: discovery reads every skill folder.
        return await asyncio.to_thread(
            skills_listing, GaiaAgent.SKILL_DIRS, (ENGINEERING_SKILL,)
        )
    except SkillError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _require_valid_session_id(session_id: str) -> None:
    """Reject a session_id the agent could not persist, as the caller's error."""
    try:
        validate_session_id(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _check_model_matches_provider(provider: str, model: str) -> None:
    """400 when *model* belongs to the other backend than *provider* names.

    Either reading of such a request is wrong: honouring ``model`` sends a
    conversation the caller asked to keep local to Anthropic, and honouring
    ``provider`` points a backend at an id it cannot serve.
    """
    from gaia_agent.stdio import is_claude_model

    if is_claude_model(model) == (provider == PROVIDER_CLAUDE):
        return
    owner = PROVIDER_CLAUDE if is_claude_model(model) else PROVIDER_LOCAL
    raise HTTPException(
        status_code=400,
        detail=(
            f"model {model!r} is a {owner} model, but provider is {provider!r}. "
            f"Send provider {owner!r} with this model, or a {provider} model "
            f"id with provider {provider!r}."
        ),
    )


def _effective_provider(request: "QueryRequest") -> str:
    """The backend a request targets when it does not name one.

    ``provider`` is optional, so an absent one is decided by the model: a Claude
    id means Anthropic. That is already how a RETAINED session resolves it —
    ``switch_model`` reads the id and sets the session's provider from it — but a
    NEW session put the id straight into ``model_id`` and pointed the local
    client at something it cannot serve.

    Note this answers "which backend", not "did the caller ask to move". A
    retained session is left where it is unless the turn says otherwise, so
    ``_switch_target`` deliberately reads ``request.provider`` raw instead.
    """
    from gaia_agent.stdio import is_claude_model

    if request.provider is not None:
        return request.provider
    if request.model and is_claude_model(request.model):
        return PROVIDER_CLAUDE
    return PROVIDER_LOCAL


def _default_model_for(provider: str) -> str:
    """The model a new session on *provider* gets when the request names none."""
    if provider == PROVIDER_CLAUDE:
        from gaia_agent.agent import GaiaAgentConfig

        return GaiaAgentConfig.claude_model
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    return DEFAULT_MODEL_NAME


def _switch_target(session: Any, request: "QueryRequest") -> Optional[str]:
    """The model a retained session must move to this turn, or ``None``.

    An absent ``provider`` means "leave this session where it is", NOT the
    default backend — resolving it here would drag a Claude session back to
    Lemonade on every ordinary follow-up turn. A bare ``model`` still moves the
    session, and ``switch_model`` sets the provider from the id it is given.
    """
    if request.provider is not None and request.provider != session.provider:
        return request.model or _default_model_for(request.provider)
    if request.model and request.model != session.model_id:
        return request.model
    return None


#: How long a new turn waits for a stopped one to finish its in-flight model
#: call. Under the Agent UI relay's 300 s read timeout, so the wait never
#: outlives the caller.
STOPPED_TURN_WAIT_S = 240.0


async def _off_loop(
    fn: Callable[..., Any],
    *args: Any,
    on_abandon: Optional[Callable[[Any], None]] = None,
) -> Any:
    """Run blocking *fn* on a worker thread so other runs keep streaming.

    The thread can't be stopped, so if the request is cancelled while it runs,
    *on_abandon* is called once it finishes — with its result, or ``None`` if
    it raised — to release whatever the abandoned call took. It runs on a
    thread of its own: closing an agent blocks, and the loop must not.
    """
    task = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if on_abandon is not None:

            def _release(result: Any) -> None:
                try:
                    on_abandon(result)
                except Exception:  # pylint: disable=broad-except
                    # A failed release leaks a lock or agent; leave a trace.
                    logger.exception("Releasing an abandoned /query setup failed")

            def _cleanup(done: "asyncio.Future") -> None:
                failed = done.cancelled() or done.exception() is not None
                threading.Thread(
                    target=_release,
                    args=(None if failed else done.result(),),
                    name="query-setup-cleanup",
                    daemon=True,
                ).start()

            task.add_done_callback(_cleanup)
        raise


def _checkout_turn(session_id: str, **kwargs: Any) -> Tuple[Any, bool]:
    """The session for *session_id* and whether this request took its turn.

    A turn the user already stopped still holds the lock until its model call
    returns, which for a long answer is most of a minute. Refusing the next
    message for all that time made Stop look broken, so a stopped turn is
    waited out instead. A turn that is NOT stopped is still refused at once.
    Blocking: run it with ``_off_loop``.
    """
    session, took = session_registry.checkout(session_id, **kwargs)
    if took:
        return session, True
    stopping = session.active_cancel
    if stopping is None or not stopping.is_set():
        return session, False
    # Eviction must take the lock too, so holding it means still registered.
    return session, session.run_lock.acquire(True, STOPPED_TURN_WAIT_S)


def _release_abandoned_turn(checked_out: Optional[Tuple[Any, bool]]) -> None:
    if checked_out is not None and checked_out[1]:
        checked_out[0].run_lock.release()


async def _finish_changes(run: _QueryRun) -> List[Dict[str, Any]]:
    """Complete the run's undo recording ahead of its terminal event.

    The caller reads the recording as soon as the stream ends. Returns the
    status event to send first when it could not be saved.
    """
    if run.recorder is None:
        return []
    try:
        await asyncio.to_thread(run.recorder.finish)
    except OSError as exc:
        logger.error("Undo history for run_id=%s: %s", run.run_id, exc)
        return [
            {"type": "status", "message": f"Undo may not work for this turn: {exc}"}
        ]
    return []


@router.post("/query")
async def query(request: QueryRequest):
    """Run the flagship agent loop for one request, streaming canonical SSE."""
    from gaia.ui.sse_handler import SSEOutputHandler

    if request.provider is not None and request.provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"provider {request.provider!r} is not supported by the "
                f"{AGENT_ID} agent. Allowed: {sorted(_ALLOWED_PROVIDERS)}."
            ),
        )
    if request.provider is not None and request.model:
        _check_model_matches_provider(request.provider, request.model)
    if request.session_id:
        _require_valid_session_id(request.session_id)

    handler = SSEOutputHandler()
    # Bypass permissions are a stdio-transport affordance and must stay one
    # (#3373). The stdio parent is one local process on a private pipe; this
    # endpoint is a bound socket, and an unguarded shell reachable over it is
    # remote code execution rather than a relaxed permission model. There is no
    # request field that could ask for it — this pins that, so adding one
    # without also revisiting the reasoning fails a test instead of shipping.
    handler.full_access = False
    session = None
    #: Set only on the one-shot path. A session agent belongs to the registry
    #: and must never be closed here.
    one_shot_agent: Optional[Any] = None
    #: Whether THIS request owns the run-table entry under its run_id. A request
    #: that fails before registering must not remove that id: it may belong to
    #: the live run it collided with.
    registered = False

    def _unwind_setup() -> None:
        """Undo the setup done so far, on a path that never reaches the loop."""
        if registered:
            _registry.remove(request.run_id)
        if session is not None:
            session.active_cancel = None
            session.run_lock.release()
        if one_shot_agent is not None:
            close_agent(one_shot_agent)

    try:
        kwargs: Dict[str, Any] = {}
        if _effective_provider(request) == _CLAUDE_PROVIDER:
            # ``model`` names a CLAUDE model here, not a Lemonade one — putting
            # it in model_id would point the local client at an id it cannot
            # serve, which fails much later and much less clearly.
            kwargs["use_claude"] = True
            if request.model:
                kwargs["claude_model"] = request.model
        elif request.model:
            kwargs["model_id"] = request.model
        if request.allowed_paths is not None:
            kwargs["allowed_paths"] = request.allowed_paths
        if request.session_id:
            # Cross-turn document retention: ChatAgent persists its indexed-doc
            # set per UI session, so dropping this makes the agent forget a
            # document between the turn that indexed it and the next question.
            kwargs["ui_session_id"] = request.session_id
            # Schema 2.12 (#2829): a session_id resolves a RETAINED agent rather
            # than a throwaway. Whatever the turn puts on the instance — most
            # visibly Agent.loaded_skills — vanishes next turn otherwise, while
            # the model goes on telling the user the skill is still loaded.
            # Building an agent takes seconds; on the loop it would freeze
            # every other run's stream, heartbeats included.
            session, took_turn = await _off_loop(
                functools.partial(_checkout_turn, request.session_id, **kwargs),
                on_abandon=_release_abandoned_turn,
            )
            if not took_turn:
                session = None  # not ours to release
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"session {request.session_id} is already running a turn. "
                        "Cancel that run or wait for it to finish, then retry."
                    ),
                )
            target = _switch_target(session, request)
            if target is not None:
                # Switched in place rather than refused. Rebuilding the agent
                # (or making the caller start a new session_id, which is what
                # this used to say) throws away the conversation and every
                # loaded skill — the two things a retained session exists to
                # keep. run_lock is held here, so no turn is mid-inference.
                try:
                    # Builds the new client and checks its server is reachable.
                    display = await _off_loop(
                        session.switch_model,
                        target,
                        on_abandon=lambda _display: _unwind_setup(),
                    )
                except RuntimeError as exc:
                    # The switch is all-or-nothing: the session is still on its
                    # previous model, so this is a failed request, not a broken
                    # session.
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"could not switch session {request.session_id} to "
                            f"{target!r}: {exc}. The session is still "
                            f"running {session.model_id or 'its previous model'}."
                        ),
                    ) from exc
                logger.info(
                    "session %s switched to %s mid-conversation",
                    request.session_id,
                    display,
                )
            agent = session.agent
            if request.allowed_paths:
                # Built on an earlier turn's scope; this turn's may hold more.
                validator = getattr(agent, "path_validator", None)
                if validator is not None:
                    for path in request.allowed_paths:
                        validator.add_allowed_path(path)
            # Hand this turn's handler the session's accumulated permission
            # state — bypass, and every "always" the user has granted. Built
            # fresh per turn, so without this both reset at every turn boundary
            # and the user is re-asked for a call they already approved.
            session.permissions.attach(handler)
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
                handler.print_warning(
                    "This session was reclaimed after being idle or crowded "
                    "out by other sessions — loaded skills and other per-turn "
                    "state were reset. Reload any skill you still need."
                )
        else:
            # No session handle — a genuine one-shot. Nothing persists past this
            # turn, and the agent is told so rather than over-promising. Nothing
            # else will ever reference it either, so this run owns its teardown.
            agent = await _off_loop(
                functools.partial(build_query_agent, **kwargs),
                on_abandon=lambda built: built is not None and close_agent(built),
            )
            one_shot_agent = agent
        agent.console = handler
        # Streamed model calls are what put ``token`` events on this wire and let
        # Stop cut a call short. Set per turn so a retained agent can't drift.
        agent.streaming = True
        # A confirmation can only be carried when somebody is there to answer it
        # AND there is a session to hold the grant. ``can_answer_questions`` is
        # the caller's own declaration that a human is watching (spec >= 2.6); a
        # one-shot sets it False precisely so the agent never parks on a prompt.
        handler.answers_confirmations = (
            session is not None and request.can_answer_questions is not False
        )
        _apply_memory_mode(agent, request.memory)
        if request.can_answer_questions is False:
            # Nobody is there to answer. Let the loop know so it resolves
            # ambiguity itself instead of parking on a question forever.
            handler.answers_questions = False

        # Pushed context, never pulled (spec §2.4).
        if request.context and hasattr(agent, "conversation_history"):
            agent.conversation_history = [
                {"role": c.role, "content": c.content} for c in request.context
            ]
            # The caller chose this text, not the user — so it must not count as
            # the user speaking when the agent decides what it is allowed to
            # learn permanently (Agent.turn_content_provenance).
            mark = getattr(agent, "mark_external_content", None)
            if callable(mark):
                mark()

        run = _QueryRun(request.run_id, agent, handler)
        precancelled = _registry.add(run)
        registered = True
        if session is not None:
            session.active_cancel = run.cancel_event
        agent._cancel_event = run.cancel_event
        agent._followup_queue = run.followups
        if precancelled:
            # A /cancel for this run_id landed before it registered. The loop
            # checks the flag at its first step boundary, so it stops without
            # ever calling the model.
            run.cancel_event.set()
            handler.cancelled.set()
    except DuplicateRunError as exc:
        _unwind_setup()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        # Already an actionable status (e.g. the 409 above) — do not relabel it
        # as a generic 500.
        _unwind_setup()
        raise
    except SessionCapacityError as exc:
        # Actionable and temporary ("N sessions are already active and none
        # are idle enough to evict") — 503, not a bug-shaped 500.
        _unwind_setup()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        _unwind_setup()
        raise HTTPException(
            status_code=500, detail=f"Failed to start the query run: {exc}"
        ) from exc

    def _process() -> None:
        if request.documents:
            _index_attached_documents(agent, request.documents, handler)
        if request.max_steps is not None:
            run.result = agent.process_query(request.query, max_steps=request.max_steps)
        else:
            run.result = agent.process_query(request.query)

    def _run_agent() -> None:
        try:
            if request.changes_turn_id is None:
                _process()
            else:
                # Tools find the recorder through a context variable set here.
                with file_snapshots.recording_turn(
                    request.session_id, request.changes_turn_id
                ) as recorder:
                    run.recorder = recorder
                    _process()
        except file_snapshots.SnapshotError as exc:
            logger.error("Undo history for run_id=%s: %s", run.run_id, exc)
            handler._emit(
                {"type": "status", "message": f"Undo may not work for this turn: {exc}"}
            )
        except Exception as exc:  # surface loudly as a terminal error event
            logger.exception("%s /query run failed for run_id=%s", AGENT_ID, run.run_id)
            handler.print_error(_terminal_error_detail(exc))
        finally:
            handler.signal_done()
            # Unwire the follow-up queue the moment the loop stops draining it.
            # Left wired, a follow-up arriving in the window before the run
            # leaves the run table would be accepted with a 200 and then never
            # read by anything — the exact silent drop this route exists to
            # rule out. Guarded on identity: a retained session's NEXT turn may
            # already own the attribute, and clearing that one would disarm a
            # live run. (A narrower race survives: a POST that wins the lookup
            # microseconds before this line. The caller records a delivered
            # follow-up in its own transcript and pushes it as context on the
            # next turn, so the words stay in the conversation — they are
            # answered a turn later than asked, not lost.)
            if getattr(agent, "_followup_queue", None) is run.followups:
                agent._followup_queue = None
            # Release only after the agent is done touching the instance, so the
            # next turn on this session cannot start mid-run.
            if session is not None:
                # Collect this turn's "always" grants into the session before
                # the handler is dropped, or the next turn re-asks for them.
                session.permissions.detach(handler)
                session.active_cancel = None
                session.run_lock.release()
            # This thread is the last thing to touch a one-shot agent — the
            # stream reads only run.result and the handler — so its RAG index,
            # scratchpad DB and HTTP session go now rather than accumulating one
            # leaked agent per request for the life of the process. Covers the
            # client-disconnect path too: the stream sets the cancel flag, which
            # ends the loop, which lands here.
            if one_shot_agent is not None:
                close_agent(one_shot_agent)

    def _run_turn() -> None:
        # The user's turn: it leases ahead of background work, a wait for the
        # model reaches this stream, and /cancel ends that wait.
        with interactive_turn(
            on_wait_event=lambda event: handler._emit(queue_status_event(event)),
            cancel=run.cancel_event,
        ):
            _run_agent()

    thread = threading.Thread(target=_run_turn, daemon=True)
    try:
        thread.start()
    except Exception as exc:
        # _run_agent never got to run, so its own finally: never fires —
        # release the run_lock here or a thread-exhaustion failure leaves
        # this session_id permanently 409ing for the life of the process.
        _unwind_setup()
        raise HTTPException(
            status_code=500, detail=f"Failed to start the query run: {exc}"
        ) from exc

    can_confirm = handler.answers_confirmations

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
                    if ctype == "token" and run.cancel_event.is_set():
                        # A stopped answer's tail, e.g. a half-written JSON
                        # reply flushed at teardown, is not answer text.
                        continue
                    if ctype in TERMINAL_TYPES:
                        for warning in await _finish_changes(run):
                            yield _sse(warning)
                    yield _sse(canonical)
                    last_write = time.monotonic()
                    if ctype == "needs_input":
                        # Answerable, so the run stays alive: keep draining while
                        # the worker thread blocks waiting for /respond.
                        continue
                    if ctype == "needs_confirmation":
                        if can_confirm:
                            # Answerable, so the run stays alive: keep draining
                            # while the worker thread blocks in
                            # confirm_tool_execution waiting for
                            # /query/{run_id}/tool_decision. Same shape as
                            # needs_input above.
                            continue
                        # Nobody can answer — refusing is the honest end, and
                        # far better than parking a run on a prompt no one will
                        # ever see.
                        for warning in await _finish_changes(run):
                            yield _sse(warning)
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
                if canonical.get("type") in TERMINAL_TYPES:
                    for warning in await _finish_changes(run):
                        yield _sse(warning)
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
    """Ask a live run to stop.

    Unknown ids report ``cancelled=False``, not 404 — a cancel racing the run's
    own completion is normal, and reporting it stopped something would be a lie.
    The id is still remembered, so a run that registers *after* this call starts
    cancelled: the caller mints ``run_id`` before it POSTs ``/query``, which
    makes cancel-arrives-first a real ordering, not a hypothetical one.
    """
    return QueryCancelResponse(run_id=run_id, cancelled=_registry.cancel(run_id))


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
    if not run.handler.resolve_user_input(body.request_id, body.answer):
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


@router.post("/query/{run_id}/followup", response_model=QueryFollowUpResponse)
async def followup_to_query(
    run_id: str, body: QueryFollowUpRequest
) -> QueryFollowUpResponse:
    """Hand a live run something the user typed after it started.

    The run keeps going on its existing SSE stream — this neither interrupts it
    nor starts a second turn. The agent folds the text in at its next agent-loop
    step boundary, so a follow-up sent during a five-minute turn is answered in
    that turn instead of waiting it out.

    An unknown run is a loud 404, not a quiet accept: the caller has to know the
    message did not land so it can hold it for the next turn instead of showing
    the user a message that went nowhere.
    """
    run = _registry.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No run {run_id!r} is in flight, so the follow-up was not "
                "delivered. It may have already finished or been cancelled — "
                "send it as a new query instead."
            ),
        )
    enqueue = getattr(run.agent, "queue_followup", None)
    if not callable(enqueue) or not enqueue(body.text):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run {run_id!r} cannot take a follow-up — its agent is not "
                "accepting mid-turn input. Send it as a new query instead."
            ),
        )
    return QueryFollowUpResponse(run_id=run_id, delivered=True)


@router.post("/query/{run_id}/tool_decision", response_model=ToolDecisionResponse)
async def tool_decision(run_id: str, body: ToolDecisionRequest):
    """Answer a ``needs_confirmation`` while the agent is still parked on it.

    The HTTP twin of the stdio transport's ``tool_decision`` control message.
    Without this the daemon transport could not run a gated tool at all — the
    stream refused the confirmation and cancelled the run, because there was
    nowhere for an answer to come from.

    A decision for a prompt that is no longer pending is rejected rather than
    silently dropped: dropping it would leave the caller believing it approved
    something that never ran.
    """
    run = _registry.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No run {run_id!r} is in flight. It may have already finished "
                "or been cancelled; the decision was not delivered."
            ),
        )
    approved = body.decision in ("allow", "always")
    delivered = run.handler.resolve_tool_confirmation(
        approved=approved,
        always=body.decision == "always",
        confirm_id=body.confirm_id,
        timed_out=body.decision == "timeout",
    )
    if not delivered:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No tool confirmation is pending on run {run_id!r}"
                + (f" for confirm_id {body.confirm_id!r}" if body.confirm_id else "")
                + " — it was already answered, timed out, or never asked."
            ),
        )
    return ToolDecisionResponse(run_id=run_id, decision=body.decision, delivered=True)


@router.post("/sessions/{session_id}/bypass", response_model=BypassResponse)
async def set_bypass(session_id: str, body: BypassRequest):
    """Turn unattended tool approval on or off for a session.

    Session-scoped rather than run-scoped because it must outlive any one turn —
    that is the whole point of bypass — and it takes effect on the very next
    gated tool, including one in a turn already running.

    Only an EXISTING session is accepted: creating one here would build a whole
    agent as a side effect of a settings toggle, and would silently succeed
    against a typo'd session id.
    """
    _require_valid_session_id(session_id)
    session = session_registry.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No session {session_id!r} exists. Send a query on that "
                "session first; bypass applies to a conversation, not to the "
                "server."
            ),
        )
    session.permissions.set_full_access(body.enabled)
    return BypassResponse(session_id=session_id, enabled=body.enabled)


def _log_caller_auth_state(auth_config: Any) -> None:
    """Report which caller-auth channel this server came up on.

    Emitted from the app's LIFESPAN, not from ``build_app``: importing this
    module is also how the frozen binary reaches the stdio transport, whose
    stdout is the event wire — a line logged at import time lands in the middle
    of the JSON stream and the reader renders it as a malformed event.
    """
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
        return
    logger.warning(
        "GAIA sidecar: caller authentication DISABLED — neither %s nor %s "
        "is in the environment. This is intended for LOCAL DEVELOPMENT "
        "only; the shipped product spawns the sidecar with a per-session "
        "token. Host/Origin protection is still enforced.",
        caller_auth.TOKEN_FILE_ENV_VAR,
        caller_auth.TOKEN_ENV_VAR,
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

    # Loopback is not access control: without this, any page the user visits can
    # drive an agent that has shell and file tools. Wired ONLY here, on the
    # sidecar app the frozen binary serves.
    auth_config = caller_auth.config_from_environment()
    caller_auth.configure(auth_config)

    @contextlib.asynccontextmanager
    async def _lifespan(_app: FastAPI):
        """Load the model before the first question instead of during it.

        Without this the first turn pays the model load *and* the first pass
        over a large system prompt, which reads as a 60-90s "Getting started"
        hang on a freshly opened chat. Backgrounded so readiness is not
        delayed, and never fatal — a cold first turn is slow, not broken.
        """
        _log_caller_auth_state(auth_config)
        task = asyncio.create_task(asyncio.to_thread(_warmup_blocking))
        # Held so it is not garbage-collected while in flight.
        _app.state.warmup_task = task
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(
        title="GAIA Agent",
        version=__version__,
        lifespan=_lifespan,
        # Swagger UI loads its JS from a CDN — an unexpected outbound network
        # call for an embedder running this sidecar offline. /openapi.json
        # stays served; only the interactive /docs page is disabled.
        docs_url=None,
    )
    app.add_middleware(caller_auth.HostOriginMiddleware)

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

    # require_caller_token is a plain Request dependency (not a
    # fastapi.security class), so FastAPI never emits securitySchemes for it
    # (#4605) — overlay the real, conditional (bearer-or-none) posture.
    caller_auth.install_openapi_security(app)
    return app


def _default_model_is_servable() -> bool:
    """Whether warming the default model would load it rather than download it.

    Building the warm-up agent preloads its model, and Lemonade downloads a
    missing one on load — gigabytes the user never asked for when they chose
    a cloud model and skipped the local one.
    """
    from gaia.llm.lemonade_client import (
        LemonadeClient,
        cloud_model_provider,
        resolve_default_chat_model,
    )

    model = resolve_default_chat_model()
    if cloud_model_provider(model):
        return True
    if not LemonadeClient(verbose=False).get_model_info(model)["downloaded"]:
        logger.info(
            "GAIA sidecar: warm-up skipped (%s is not downloaded; a turn that "
            "uses it downloads it)",
            model,
        )
        return False
    return True


def _warmup_blocking() -> None:
    """Make the first real question cheap.

    Three costs move off the first turn: importing the agent stack (faiss,
    RAG, tool mixins), loading the model into its Lemonade slot, and the
    first pass over the ~20K-token prompt prefix. The agent's own
    :meth:`warm_up` sends that prefix exactly as a turn would (composed system
    prompt and tool schemas), and every session agent renders the same one, so
    the first question in any session reads it from Lemonade's cache.

    Never fatal — a cold first turn is slow, not broken — but a failure is
    logged as a warning with its cause, not hidden.
    """
    from gaia_agent.session_registry import build_session_agent

    try:
        if not _default_model_is_servable():
            return
        agent = build_session_agent()
    except Exception as e:  # noqa: BLE001 - reported, and chat still works
        logger.warning(
            "GAIA sidecar: warm-up failed before the model was reached (%s). "
            "Chat still works, but the first answer will take longer; check "
            "that Lemonade is running and the model is downloaded.",
            e,
            exc_info=True,
        )
        return
    try:
        result = agent.warm_up()
        logger.info(
            "GAIA sidecar: warmed %s in %ss (%s of %s prompt tokens cached)",
            getattr(agent, "model_id", "?"),
            result.get("seconds"),
            result.get("cached_tokens", "?"),
            result.get("prompt_tokens", "?"),
        )
    except Exception as e:  # noqa: BLE001 - reported, and chat still works
        logger.warning(
            "GAIA sidecar: warm-up request to %s failed (%s). Chat still works, "
            "but the first answer will take longer; check the Lemonade log.",
            getattr(agent, "model_id", "?"),
            e,
            exc_info=True,
        )
    finally:
        close_agent(agent)


app = build_app()


def serve_http(argv: List[str]) -> int:
    """Run the sidecar over HTTP. Bound to loopback by default — this speaks for
    the user's documents and memory and has no business on a LAN interface."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(
        prog="gaia-agent --serve", description="GAIA flagship agent HTTP sidecar"
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Serve the HTTP sidecar (implied by --host/--port).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    args = parser.parse_args(argv)

    # The warm-up swallows agent-build errors, so a stale GAIA_SKILL_SET or a
    # bad GAIA_ALLOWED_PATHS would otherwise leave a healthy-looking sidecar.
    from gaia_agent.agent import check_skill_set_selection

    from gaia.security import AllowedPathsEnvError, env_allowed_paths
    from gaia.skills.errors import SkillSetError

    try:
        check_skill_set_selection()
        env_allowed_paths()
    except (SkillSetError, AllowedPathsEnvError) as exc:
        print(f"gaia-agent: {exc}", file=sys.stderr)
        return 2

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


# The transport split lives in ``gaia_agent.entry`` so the stdio wire never has
# to import FastAPI to find out it was not selected. Re-exported here because
# the frozen binary's entry point (packaging/server.py) reaches ``main`` through
# this module — one implementation, two names, rather than two dispatchers that
# can disagree.
main = _entry_main


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
