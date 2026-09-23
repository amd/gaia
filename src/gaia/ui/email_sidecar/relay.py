# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Relay the sidecar's canonical ``/query`` SSE loop into the UI's own SSE
vocabulary (issue #2109).

A sidecar's ``POST /v1/<agent>/query`` speaks a frozen, 8-event
canonical vocabulary (spec #2015/#2016, ``needs_input`` added by #2595) —

    status | token | tool_call | tool_result | needs_confirmation | needs_input
    | final | error

terminated by exactly one ``final`` or ``error``. The Agent UI's own SSE
consumer (``gaia.ui._chat_helpers``'s streaming trunk) speaks a different,
older vocabulary (``status`` / ``chunk`` / ``tool_start`` / ``tool_args`` /
``tool_result`` / ``answer`` / ``agent_error`` / ...). This module is the
translation layer between the two — the mirror image of
# mirrors hub/agents/email/python/gaia_agent_email/sse_translation.py
(which translates the in-process agent-loop vocabulary INTO the canonical
one; this module translates the canonical vocabulary back OUT to the UI's own
wire, one hop further downstream).

Events are emitted via direct, pinned ``handler._emit(...)`` shapes — NOT via
``SSEOutputHandler.print_streaming_text`` / ``print_tool_usage`` /
``pretty_print_json``, which add brace-buffering and tool-registry lookups
meant for the in-process agent loop and would be label-dead here now that the
prior in-process ``agent_type=email`` tool-calling loop has been fully
retired in favor of this relay (#2109).

Agent-agnostic since #4161: everything that differs between sidecars — the
contract floor, the user-facing copy, tool labels, which tools mutate — lives
in :mod:`gaia.ui.email_sidecar.profiles`, so the flagship relays through this
same code path rather than a parallel one.

Cancellation: the relay registers the live (still-open) HTTP response on
``handler.active_relay_response`` via ``query_stream``'s ``on_response`` hook
so a cancel arriving on another thread (``routers/chat.py``'s
``/api/chat/cancel``) can force a blocked socket read to error out by calling
``.close()`` on it — a between-events ``handler.cancelled`` check alone cannot
observe cancellation while parked in a blocking read. See Design 2 of the
issue #2109 plan for the full rationale.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger
from gaia.ui.email_sidecar.errors import SidecarError, SidecarHTTPError
from gaia.ui.email_sidecar.profiles import EMAIL_PROFILE, RelayProfile
from gaia.ui.sse_handler import (
    SSEOutputHandler,
    _format_tool_args,
    _strip_balanced_json_blobs,
    _summarize_tool_result,
)

logger = get_logger(__name__)

#: Terminal error surfaced when the sidecar stream closes unexpectedly — a
#: crash, a dropped connection, a forced close from the cancel path, or the
#: stream simply ending without ever producing a ``final``/``error`` event
#: (the canonical contract's "exactly one terminal event" guarantee broken).
#: Pinned as a module constant so tests can assert equality and the copy the
#: user sees stays stable.
STREAM_ENDED_UNEXPECTEDLY = EMAIL_PROFILE.stream_ended_message


def _stream_ended_message(profile: "RelayProfile") -> str:
    """The crash copy for *profile*'s agent — see ``RelayProfile``."""
    return profile.stream_ended_message


#: Surfaced both by the dispatch layer's pre-flight version gate (a pre-2.4
#: Hub binary passes the manager's MAJOR-only handshake, see
#: ``_chat_helpers._email_query_version_supported``) AND here, as the
#: backstop when a 404 on ``/query`` itself proves the same thing (the
#: manager's captured ``api_version`` was missing/stale at pre-flight time).
#: Both call sites must use this exact string.
EMAIL_QUERY_VERSION_UPGRADE_MESSAGE = EMAIL_PROFILE.version_upgrade_message

#: Canonical event types that end a ``/query`` run (mirrors
#: ``gaia_agent_email.sse_translation.TERMINAL_TYPES``).
_TERMINAL_TYPES = frozenset({"final", "error"})

#: Appended — never replacing the original text — to a terminal ``error``
#: detail that looks like a connection/timeout failure. The sidecar emits
#: ``str(exc)`` verbatim, so its most common failure (Lemonade Server down)
#: otherwise reaches the user as a raw urllib3 repr with no next step.
#: Root fix (actionable copy sidecar-side) ships via the agent release
#: pipeline, not this repo — see the #2109 PR notes.
LEMONADE_CONNECTION_HINT = EMAIL_PROFILE.lemonade_hint

#: Connection/timeout-shaped fragments of requests/urllib3 error reprs.
#: Deliberately narrow: a non-match passes through untouched.
_CONNECTION_SHAPED_RE = re.compile(
    r"connection\s+(?:refused|reset|aborted|error)"
    r"|connectionerror"
    r"|connectionpool"
    r"|failed to establish a new connection"
    r"|max retries exceeded"
    r"|newconnectionerror"
    r"|(?:read |connect(?:ion)? )?timed?\s*out"
    r"|timeout",
    re.IGNORECASE,
)


def _augment_error_detail(detail: str, profile: RelayProfile = EMAIL_PROFILE) -> str:
    """Append (never substitute) an actionable hint to connection-shaped
    error text — boundary translation, not a fallback: the original detail
    is preserved verbatim at the front."""
    if _CONNECTION_SHAPED_RE.search(detail):
        return detail + profile.lemonade_hint
    return detail


#: Email's own maps, now owned by ``profiles.EMAIL_PROFILE``. Re-exported
#: under their original names so existing importers keep working.
_MUTATING_TOOLS = EMAIL_PROFILE.mutating_tools
_TOOL_LABELS = EMAIL_PROFILE.tool_labels


def _humanize_tool(tool: str) -> str:
    return tool.replace("_", " ").strip() or "tool"


def _tool_label(tool: str, profile: RelayProfile = EMAIL_PROFILE) -> str:
    return profile.tool_labels.get(tool) or _humanize_tool(tool)


def _derive_summary(tool: str, data: Any, profile: RelayProfile = EMAIL_PROFILE) -> str:
    """Short, human summary for a ``tool_result`` — never a bare "Done"."""
    if isinstance(data, dict) and data:
        return _summarize_tool_result(data)
    return f"Ran {_tool_label(tool, profile).lower()}"


def _kind_re() -> "re.Pattern":
    # Built from the shared render-map source so the echo-strip pattern stays
    # in sync as render tools are added, without duplicating the map here.
    langs = sorted(set(SSEOutputHandler._RENDER_TOOL_TO_LANG.values()))
    alt = "|".join(re.escape(lang) for lang in langs)
    return re.compile(rf'"kind"\s*:\s*"(?:{alt})"')


def _dispatch_one(
    handler: Any, event: Dict[str, Any], profile: RelayProfile = EMAIL_PROFILE
) -> bool:
    """Emit one canonical event as a UI event. Returns True if terminal."""
    etype = event.get("type")

    if etype == "status":
        handler._emit({"type": "status", "message": str(event.get("message", ""))})

    elif etype == "token":
        delta = event.get("delta", "")
        if delta:
            handler._emit({"type": "chunk", "content": delta})

    elif etype == "tool_call":
        tool = str(event.get("tool") or "unknown")
        args = event.get("args") or {}
        label = _tool_label(tool, profile)
        handler._emit({"type": "tool_start", "tool": tool, "detail": label})
        handler._emit(
            {
                "type": "tool_args",
                "tool": tool,
                "args": args,
                "detail": _format_tool_args(tool, args) or label,
            }
        )
        if tool in profile.mutating_tools:
            handler._emit(
                {
                    "type": "status",
                    "message": f"✎ {profile.change_noun}: {tool}",
                }
            )

    elif etype == "tool_result":
        tool = str(event.get("tool") or "unknown")
        data = event.get("data")
        out: Dict[str, Any] = {
            "type": "tool_result",
            "tool": tool,
            "summary": _derive_summary(tool, data, profile),
            "success": True,
            "data": data if data is not None else {},
        }
        render = event.get("render")
        if render:
            out["render"] = render
        handler._emit(out)

    elif etype == "needs_confirmation":
        handler._emit(
            {
                "type": "needs_confirmation",
                "action": str(event.get("action", "")),
                "summary": str(event.get("summary", "")),
            }
        )

    elif etype == "needs_input":
        # Answerable, non-terminal (#2595) — unlike needs_confirmation this
        # blocks the sidecar run until POST /api/chat/user-input delivers an
        # answer, so every field the frontend needs to build and submit the
        # prompt must cross this hop; run continues on the same stream.
        options = event.get("options")
        handler._emit(
            {
                "type": "needs_input",
                "request_id": str(event.get("request_id") or ""),
                "question": str(event.get("question") or ""),
                "options": options if isinstance(options, list) else [],
                "allow_free_text": bool(event.get("allow_free_text", True)),
                "sensitive": bool(event.get("sensitive", False)),
                "timeout_seconds": event.get("timeout_seconds"),
            }
        )

    elif etype == "final":
        answer = str(event.get("answer", "") or "")
        cleaned = _strip_balanced_json_blobs(answer, _kind_re()).strip()
        handler._emit({"type": "answer", "content": cleaned})
        return True

    elif etype == "error":
        detail = (
            event.get("detail")
            or f"Unknown error from the {profile.display_name} agent."
        )
        handler._emit(
            {
                "type": "agent_error",
                "content": _augment_error_detail(str(detail), profile),
            }
        )
        return True

    else:
        handler._emit(
            {
                "type": "status",
                "message": f"[unsupported agent event: {etype}]",
            }
        )

    return False


def _best_effort_cancel(proxy: Any, rid: str) -> None:
    try:
        proxy.cancel_query(rid)
    except SidecarError as exc:
        logger.info(
            "%s relay: cancel_query for run_id=%s: %s",
            getattr(proxy, "agent_id", "sidecar"),
            rid,
            exc,
        )


def relay_query(
    handler: Any,
    proxy: Any,
    *,
    query: str,
    context: List[Dict[str, str]],
    model_id: Optional[str] = None,
    run_id: Optional[str] = None,
    max_steps: Optional[int] = None,
    read_timeout: float = 300.0,
    profile: RelayProfile = EMAIL_PROFILE,
    session_id: Optional[str] = None,
) -> None:
    """Drive one ``/query`` run and relay it as UI-vocabulary SSE events.

    Runs synchronously on the caller's thread — the UI backend already runs
    the whole per-turn producer off the event loop in a worker/daemon thread
    (``_chat_helpers._run_agent``), so a blocking HTTP read here is safe.

    Every exception this function's dependencies can raise (a dropped
    connection, a read timeout, a malformed SSE line) is caught HERE and
    translated into a terminal SSE event — nothing propagates to the caller.
    This matters: the caller's surrounding retry/reload logic
    (``_classify_chat_exception``) is tuned for local-Lemonade failure
    substrings and would misclassify a sidecar connection error as a
    retryable Lemonade error, triggering a bogus model reload. Sidecar
    exceptions must never reach it.

    Never calls ``handler.signal_done()`` — the turn-level done sentinel is
    owned by the caller (``_run_agent``'s outer ``finally``), exactly as for
    every other agent branch. Relay-level signalling would push a second
    ``None`` sentinel per turn, violating the queue's exactly-once contract.
    """
    rid = run_id or str(uuid.uuid4())
    # can_answer_questions=True (#2595): this relay DOES render needs_input
    # and POST the answer back via POST /api/chat/user-input ->
    # EmailSidecarProxy.respond_query. Omitting it defaults to the sidecar's
    # safe False (see query_routes.QueryRequest.can_answer_questions), which
    # makes ask() refuse every question with "use the Agent UI" -- even
    # though the Agent UI is the caller asking.
    body: Dict[str, Any] = {
        "query": query,
        "run_id": rid,
        "context": context,
        "can_answer_questions": True,
    }
    if model_id:
        body["model"] = model_id
    if max_steps is not None:
        body["max_steps"] = max_steps
    if profile.sends_session_id and session_id:
        # Contract >= 2.12. Without it the sidecar builds a fresh agent per
        # turn, so a document indexed on turn 1 is gone by turn 2. Gated by
        # profile because both request models are extra="forbid".
        body["session_id"] = session_id

    # So a later POST /api/chat/user-input can find where to deliver the
    # answer (#2595) — mirrors active_relay_response's lifetime exactly.
    handler.active_relay_proxy = proxy
    handler.active_relay_run_id = rid

    def _register_response(resp: Any) -> None:
        handler.active_relay_response = resp
        if handler.cancelled.is_set():
            # A cancel raced ahead of this registration: the router's forced
            # close was a no-op (active_relay_response was still None), so
            # close the just-registered response HERE — otherwise the next
            # socket read parks for the full read_timeout with nothing left
            # to interrupt it.
            try:
                resp.close()
            except Exception:  # noqa: BLE001 - best-effort, mirrors router close
                logger.debug(
                    "%s relay: failed to close raced-cancel response",
                    profile.agent_id,
                    exc_info=True,
                )

    terminated = False
    crashed = False
    crash_message = _stream_ended_message(profile)
    try:
        for event in proxy.query_stream(
            body, read_timeout=read_timeout, on_response=_register_response
        ):
            if handler.cancelled.is_set():
                break
            if _dispatch_one(handler, event, profile):
                terminated = True
                break
    except SidecarHTTPError as exc:
        if handler.cancelled.is_set():
            logger.info(
                "%s relay: stream closed for cancel (run_id=%s): %s",
                profile.agent_id,
                rid,
                exc,
            )
        else:
            crashed = True
            detail = (exc.detail or "").strip()
            if exc.status_code == 404:
                # Backstop for the pre-flight version gate (#2109 Design 3):
                # a pre-2.4 binary that somehow passed pre-flight (a stale or
                # missing manager.api_version) 404s here instead — same
                # actionable message either way.
                crash_message = profile.version_upgrade_message
            elif detail:
                # Surface the sidecar's own actionable detail (e.g. the
                # zero-connector 502) instead of masking it as a generic
                # crash — #2419. Bodyless transport failures still fall
                # through to STREAM_ENDED_UNEXPECTEDLY.
                crash_message = _augment_error_detail(detail, profile)
            logger.warning(
                "%s relay: stream failed for run_id=%s: %s",
                profile.agent_id,
                rid,
                exc,
            )
    except Exception as exc:  # noqa: BLE001 - boundary: translate, never raise
        if handler.cancelled.is_set():
            logger.info(
                "%s relay: stream closed for cancel (run_id=%s): %s",
                profile.agent_id,
                rid,
                exc,
            )
        else:
            crashed = True
            logger.warning(
                "%s relay: stream failed for run_id=%s: %s",
                profile.agent_id,
                rid,
                exc,
            )
    finally:
        handler.active_relay_response = None
        handler.active_relay_proxy = None
        handler.active_relay_run_id = None

    if handler.cancelled.is_set():
        _best_effort_cancel(proxy, rid)
        if not terminated:
            handler._emit({"type": "status", "message": "Cancelled."})
    elif crashed or not terminated:
        # No terminal event arrived — the sidecar generation is still decoding
        # on the single GPU slot; stop it or later queries death-spiral.
        _best_effort_cancel(proxy, rid)
        handler._emit({"type": "agent_error", "content": crash_message})


__all__ = [
    "relay_query",
    "RelayProfile",
    "STREAM_ENDED_UNEXPECTEDLY",
    "EMAIL_QUERY_VERSION_UPGRADE_MESSAGE",
    "LEMONADE_CONNECTION_HINT",
]
