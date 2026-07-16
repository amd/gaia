# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Relay the sidecar's canonical ``/query`` SSE loop into the UI's own SSE
vocabulary (issue #2109).

The email sidecar's ``POST /v1/email/query`` speaks a frozen, 7-event
canonical vocabulary (spec #2015/#2016) —

    status | token | tool_call | tool_result | needs_confirmation | final | error

terminated by exactly one ``final`` or ``error``. The Agent UI's own SSE
consumer (``gaia.ui._chat_helpers``'s streaming trunk) speaks a different,
older vocabulary (``status`` / ``chunk`` / ``tool_start`` / ``tool_args`` /
``tool_result`` / ``answer`` / ``agent_error`` / ...). This module is the
translation layer between the two — the mirror image of
# mirrors hub/agents/python/email/gaia_agent_email/sse_translation.py
(which translates the in-process agent-loop vocabulary INTO the canonical
one; this module translates the canonical vocabulary back OUT to the UI's own
wire, one hop further downstream).

Events are emitted via direct, pinned ``handler._emit(...)`` shapes — NOT via
``SSEOutputHandler.print_streaming_text`` / ``print_tool_usage`` /
``pretty_print_json``, which add brace-buffering and tool-registry lookups
meant for the in-process agent loop and would be wrong (and, once
``proxy_agent.py``'s ``@tool`` registrations are deleted, label-dead) for
already-clean events relayed from the sidecar.

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
from gaia.ui.email_sidecar.errors import SidecarError
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
STREAM_ENDED_UNEXPECTEDLY = (
    "Email agent stream ended unexpectedly (the sidecar may have crashed). "
    "Check the sidecar log under ~/.gaia/logs/ and retry."
)

#: Canonical event types that end a ``/query`` run (mirrors
#: ``gaia_agent_email.sse_translation.TERMINAL_TYPES``).
_TERMINAL_TYPES = frozenset({"final", "error"})

#: Mutating email tools that execute WITHOUT confirmation under ``/query``
#: (``CONFIRMATION_REQUIRED_TOOLS`` gates only send/RSVP/forward/
#: permanent-delete/quarantine/calendar-create — see
#: ``gaia_agent_email.agent.EmailTriageAgent.CONFIRMATION_REQUIRED_TOOLS``).
#: Their effects are persistent mailbox/preference changes, so the relay also
#: emits a visible status line for them — never buried in the collapsed
#: activity panel just because no confirmation gate fired.
_MUTATING_TOOLS = frozenset(
    {
        "archive_message",
        "archive_message_batch",
        "undo_archive_batch",
        "mark_read",
        "mark_unread",
        "mark_read_batch",
        "mark_unread_batch",
        "add_star",
        "remove_star",
        "add_star_batch",
        "remove_star_batch",
        "label_message",
        "label_message_batch",
        "move_to_label",
        "move_to_label_batch",
        "trash_message",
        "restore_message",
        "snooze_message",
        "cancel_scheduled_job",
        "unquarantine_message",
        "set_priority_sender",
        "set_low_priority_sender",
        "set_category_default",
        "clear_session_preferences",
        "build_voice_profile",
        "clear_voice_profile",
    }
)

#: Small static label map for the ``tool_start`` "detail" string — the relay
#: has no in-process tool registry to consult (``get_tool_display_label``
#: only knows tools registered via ``@tool`` in THIS process, and the email
#: tools live in the sidecar), so it owns a friendly-label fallback here,
#: falling back further to a humanized tool name for anything unlisted.
_TOOL_LABELS: Dict[str, str] = {
    "pre_scan_inbox": "Scanning inbox",
    "triage_inbox": "Triaging inbox",
    "search_messages": "Searching mail",
    "list_inbox": "Listing inbox",
    "get_message": "Reading message",
    "get_thread": "Reading thread",
    "summarize_thread": "Summarizing thread",
    "summarize_message": "Summarizing message",
    "list_labels": "Listing labels",
    "check_followups": "Checking follow-ups",
    "profile_inbox": "Profiling inbox",
    "draft_reply": "Drafting reply",
    "draft_forward": "Drafting forward",
    "send_draft": "Sending draft",
    "send_now": "Sending message",
    "forward_message": "Forwarding message",
    "schedule_send": "Scheduling send",
    "archive_message": "Archiving message",
    "archive_message_batch": "Archiving messages",
    "undo_archive_batch": "Undoing archive",
    "mark_read": "Marking read",
    "mark_unread": "Marking unread",
    "mark_read_batch": "Marking messages read",
    "mark_unread_batch": "Marking messages unread",
    "add_star": "Starring message",
    "remove_star": "Unstarring message",
    "add_star_batch": "Starring messages",
    "remove_star_batch": "Unstarring messages",
    "label_message": "Labeling message",
    "label_message_batch": "Labeling messages",
    "move_to_label": "Moving message",
    "move_to_label_batch": "Moving messages",
    "trash_message": "Trashing message",
    "restore_message": "Restoring message",
    "permanent_delete": "Permanently deleting message",
    "snooze_message": "Snoozing message",
    "cancel_scheduled_job": "Cancelling scheduled job",
    "list_scheduled_jobs": "Listing scheduled jobs",
    "list_calendar_events": "Checking calendar",
    "accept_invite": "Accepting invite",
    "decline_invite": "Declining invite",
    "create_event_from_email": "Creating calendar event",
    "detect_meeting_request": "Detecting meeting request",
    "detect_calendar_conflicts": "Checking calendar conflicts",
    "quarantine_phishing_message": "Quarantining suspicious message",
    "unquarantine_message": "Restoring quarantined message",
    "set_priority_sender": "Updating priority sender",
    "set_low_priority_sender": "Updating low-priority sender",
    "set_category_default": "Updating category preference",
    "clear_session_preferences": "Clearing session preferences",
    "build_voice_profile": "Building voice profile",
    "clear_voice_profile": "Clearing voice profile",
}


def _humanize_tool(tool: str) -> str:
    return tool.replace("_", " ").strip() or "tool"


def _tool_label(tool: str) -> str:
    return _TOOL_LABELS.get(tool) or _humanize_tool(tool)


def _derive_summary(tool: str, data: Any) -> str:
    """Short, human summary for a ``tool_result`` — never a bare "Done"."""
    if isinstance(data, dict) and data:
        return _summarize_tool_result(data)
    return f"Ran {_tool_label(tool).lower()}"


def _kind_re() -> "re.Pattern":
    # Built from the shared render-map source so the echo-strip pattern stays
    # in sync as render tools are added, without duplicating the map here.
    langs = sorted(set(SSEOutputHandler._RENDER_TOOL_TO_LANG.values()))
    alt = "|".join(re.escape(lang) for lang in langs)
    return re.compile(rf'"kind"\s*:\s*"(?:{alt})"')


def _dispatch_one(handler: Any, event: Dict[str, Any]) -> bool:
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
        handler._emit({"type": "tool_start", "tool": tool, "detail": _tool_label(tool)})
        handler._emit(
            {
                "type": "tool_args",
                "tool": tool,
                "args": args,
                "detail": _format_tool_args(tool, args) or _tool_label(tool),
            }
        )
        if tool in _MUTATING_TOOLS:
            handler._emit(
                {
                    "type": "status",
                    "message": f"✎ mailbox change: {tool}",
                }
            )

    elif etype == "tool_result":
        tool = str(event.get("tool") or "unknown")
        data = event.get("data")
        out: Dict[str, Any] = {
            "type": "tool_result",
            "tool": tool,
            "summary": _derive_summary(tool, data),
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

    elif etype == "final":
        answer = str(event.get("answer", "") or "")
        cleaned = _strip_balanced_json_blobs(answer, _kind_re()).strip()
        handler._emit({"type": "answer", "content": cleaned})
        return True

    elif etype == "error":
        detail = event.get("detail") or "Unknown error from the email agent."
        handler._emit({"type": "agent_error", "content": str(detail)})
        return True

    else:
        handler._emit(
            {
                "type": "status",
                "message": f"[unsupported agent event: {etype}]",
            }
        )

    return False


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

    Always calls ``handler.signal_done()`` exactly once, on every path.
    """
    rid = run_id or str(uuid.uuid4())
    body: Dict[str, Any] = {"query": query, "run_id": rid, "context": context}
    if model_id:
        body["model"] = model_id
    if max_steps is not None:
        body["max_steps"] = max_steps

    def _register_response(resp: Any) -> None:
        handler.active_relay_response = resp

    terminated = False
    crashed = False
    try:
        for event in proxy.query_stream(
            body, read_timeout=read_timeout, on_response=_register_response
        ):
            if handler.cancelled.is_set():
                break
            if _dispatch_one(handler, event):
                terminated = True
                break
    except Exception as exc:  # noqa: BLE001 - boundary: translate, never raise
        if handler.cancelled.is_set():
            logger.info(
                "email relay: stream closed for cancel (run_id=%s): %s", rid, exc
            )
        else:
            crashed = True
            logger.warning("email relay: stream failed for run_id=%s: %s", rid, exc)
    finally:
        handler.active_relay_response = None

    if handler.cancelled.is_set():
        try:
            proxy.cancel_query(rid)
        except SidecarError as exc:
            logger.info("email relay: cancel_query for run_id=%s: %s", rid, exc)
        if not terminated:
            handler._emit({"type": "status", "message": "Cancelled."})
    elif crashed or not terminated:
        handler._emit({"type": "agent_error", "content": STREAM_ENDED_UNEXPECTEDLY})

    handler.signal_done()


__all__ = ["relay_query", "STREAM_ENDED_UNEXPECTEDLY"]
