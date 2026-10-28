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

from gaia.agents.base.call_risk import RISK_NOTE_ARG
from gaia.logger import get_logger
from gaia.ui.email_sidecar.errors import SidecarError, SidecarHTTPError
from gaia.ui.email_sidecar.profiles import EMAIL_PROFILE, RelayProfile
from gaia.ui.event_narration import derive_narration
from gaia.ui.sse_handler import (
    SSEOutputHandler,
    _format_tool_args,
    _strip_balanced_json_blobs,
    _summarize_tool_result,
    tool_result_failed,
)
from gaia.ui.sse_translation import MCP_TOOL_FIELDS, SUMMARY_VIEW_KEYS
from gaia.ui.turn_insights import turn_record_problem

logger = get_logger(__name__)

#: Surfaced both by the dispatch layer's pre-flight version gate (a pre-2.4
#: Hub binary passes the manager's MAJOR-only handshake, see
#: ``profiles.api_version_supported``) AND here, as the
#: backstop when a 404 on ``/query`` itself proves the same thing (the
#: manager's captured ``api_version`` was missing/stale at pre-flight time).
#: Both call sites must use this exact string.
EMAIL_QUERY_VERSION_UPGRADE_MESSAGE = EMAIL_PROFILE.version_upgrade_message

#: Appended — never replacing the original text — to a terminal ``error``
#: detail that looks like a connection/timeout failure. The sidecar emits
#: ``str(exc)`` verbatim, so its most common failure (Lemonade Server down)
#: otherwise reaches the user as a raw urllib3 repr with no next step.
#: Root fix (actionable copy sidecar-side) ships via the agent release
#: pipeline, not this repo — see the #2109 PR notes.
LEMONADE_CONNECTION_HINT = EMAIL_PROFILE.lemonade_hint

#: ``source`` on the terminal ``error`` the daemon synthesizes when an agent's
#: stream ends with no terminal event (``gaia.daemon.relay``).
_DAEMON_RELAY_SOURCE = "daemon_relay"

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
    if profile.lemonade_hint and _CONNECTION_SHAPED_RE.search(detail):
        from gaia.llm.lemonade_launcher import describe_start_hint

        return f"{detail}{profile.lemonade_hint} {describe_start_hint().instruction}"
    return detail


#: Email's own maps, now owned by ``profiles.EMAIL_PROFILE``. Re-exported
#: under their original names so existing importers keep working.
_MUTATING_TOOLS = EMAIL_PROFILE.mutating_tools
_TOOL_LABELS = EMAIL_PROFILE.tool_labels


#: ``final.usage`` fields copied onto the UI's ``answer`` event, under the
#: names :meth:`SSEOutputHandler.print_final_answer` gives them.
_ANSWER_USAGE_KEYS = (
    "steps",
    "tools_used",
    "elapsed",
    "tokens",
    "ttft",
    "tok_per_s",
    "input_tokens",
    "cached_tokens",
    "metrics",
)


def _humanize_tool(tool: str) -> str:
    return tool.replace("_", " ").strip() or "tool"


def _tool_label(tool: str, profile: RelayProfile = EMAIL_PROFILE) -> str:
    return profile.tool_labels.get(tool) or _humanize_tool(tool)


def _is_summary_view(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("summary"), str)
        and set(data) <= set(SUMMARY_VIEW_KEYS)
    )


def _derive_summary(tool: str, data: Any, profile: RelayProfile = EMAIL_PROFILE) -> str:
    """Short, human summary for a ``tool_result`` — never a bare "Done"."""
    if _is_summary_view(data) and data["summary"]:
        # The sidecar already summarized it; re-summarizing names its keys.
        return data["summary"]
    if isinstance(data, dict) and data:
        return _summarize_tool_result(data)
    return f"Ran {_tool_label(tool, profile).lower()}"


def _derive_success(data: Any) -> bool:
    """Whether the tool worked, by the same rule the in-process handler uses."""
    if _is_summary_view(data) and isinstance(data.get("success"), bool):
        return data["success"]
    return not tool_result_failed(data)


def _kind_re() -> "re.Pattern":
    # Built from the shared render-map source so the echo-strip pattern stays
    # in sync as render tools are added, without duplicating the map here.
    langs = sorted(set(SSEOutputHandler._RENDER_TOOL_TO_LANG.values()))
    alt = "|".join(re.escape(lang) for lang in langs)
    return re.compile(rf'"kind"\s*:\s*"(?:{alt})"')


def _user_wait(event: Dict[str, Any], answers_confirmations: bool) -> Optional[str]:
    """The prompt or question id the sidecar now waits on the user for, if any."""
    etype = event.get("type")
    if etype == "needs_input":
        return str(event.get("request_id") or "") or None
    if etype == "needs_confirmation" and answers_confirmations:
        return str(event.get("confirm_id") or "") or None
    return None


def _dispatch_one(
    handler: Any,
    event: Dict[str, Any],
    profile: RelayProfile = EMAIL_PROFILE,
    answers_confirmations: bool = False,
) -> bool:
    """Emit one canonical event as a UI event. Returns True if terminal.

    *answers_confirmations* says the sidecar waits on a ``needs_confirmation``
    for an answer over ``tool_decision``; otherwise the prompt is a card only.
    """
    etype = event.get("type")
    if answers_confirmations:
        # Any event after a prompt means the sidecar has stopped waiting on it.
        handler.set_relay_confirmation(None)
    # Likewise for a question: the run is parked until it is answered.
    handler.set_relay_question(None)

    if etype == "needs_confirmation" and answers_confirmations:
        confirm_id = str(event.get("confirm_id") or "")
        if confirm_id:
            handler.set_relay_confirmation(confirm_id)
            # The UI's own approval prompt. No always_scope: an "always" grant
            # would live in the sidecar, where this chat can neither list nor
            # revoke it, so only allow-once and deny are offered.
            request: Dict[str, Any] = {
                "type": "permission_request",
                "tool": str(event.get("action", "")),
                "confirm_id": confirm_id,
                "summary": str(event.get("summary", "")),
            }
            if event.get("risk"):
                request["risk"] = str(event["risk"])
            if event.get("tool_label"):
                request["tool_label"] = str(event["tool_label"])
            # The whole call: the summary shortens code, and a path prompt is
            # worded from the path's kind. The card reads GAIA's folder, risk note
            # and resolved path from args, as in-process, so prompt_context goes
            # back there.
            args = dict(event["args"]) if isinstance(event.get("args"), dict) else {}
            context = event.get("prompt_context")
            if isinstance(context, dict):
                if context.get("working_directory"):
                    args["working_directory"] = str(context["working_directory"])
                if context.get("risk_note"):
                    args[RISK_NOTE_ARG] = str(context["risk_note"])
                if context.get("file_path"):
                    args["file_path"] = str(context["file_path"])
            if args:
                request["args"] = args
                if event.get("args_truncated"):
                    request["args_truncated"] = True
            handler._emit(request)
            if getattr(handler, "background_mode", False) is True:
                # Unattended run (a schedule): nobody can answer, so deny now
                # rather than park the sidecar until it times out.
                handler.resolve_relay_confirmation(False, confirm_id)
            return False

    if etype == "status":
        status: Dict[str, Any] = {
            "type": "status",
            "message": str(event.get("message", "")),
        }
        # The phase is what makes it the live line ("Reading your request").
        for key in ("phase", "words", "chars", "queue"):
            if key in event:
                status[key] = event[key]
        handler._emit(status)

    elif etype == "token":
        delta = event.get("delta", "")
        if delta:
            handler._emit({"type": "chunk", "content": delta})

    elif etype == "reasoning":
        delta = event.get("delta", "")
        if delta:
            handler._emit({"type": "thinking", "content": delta})

    elif etype == "tool_call":
        tool = str(event.get("tool") or "unknown")
        args = event.get("args") or {}
        label = _tool_label(tool, profile)
        # The turn summary and the heartbeat read this state, not the events.
        handler.record_tool_started(tool)
        if isinstance(args, dict):
            handler.record_tool_args(args)
        # The call in plain words; a sidecar older than the field gets it derived here.
        narration = str(
            event.get("narration")
            or derive_narration(tool, args if isinstance(args, dict) else {})
        )
        start: Dict[str, Any] = {
            "type": "tool_start",
            "tool": tool,
            "detail": label,
            "narration": narration,
        }
        start.update({k: event[k] for k in MCP_TOOL_FIELDS if event.get(k)})
        handler._emit(start)
        handler._emit(
            {
                "type": "tool_args",
                "tool": tool,
                "args": args,
                "detail": _format_tool_args(tool, args) or label,
                "narration": narration,
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
        success = _derive_success(data)
        handler.record_tool_result(data, success)
        out: Dict[str, Any] = {
            "type": "tool_result",
            "tool": tool,
            "summary": _derive_summary(tool, data, profile),
            "success": success,
            "data": data if data is not None else {},
        }
        if isinstance(data, dict) and isinstance(data.get("mcp_output"), dict):
            out["mcp_output"] = data["mcp_output"]
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
        # Recorded so the chat list shows the chat as waiting on the user.
        handler.set_relay_question(str(event.get("request_id") or "") or None)
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
        out = {"type": "answer", "content": cleaned}
        if event.get("failed") is True:
            # The sidecar's turn ended on a model failure; the answer says so.
            out["failed"] = True
        # Same top-level fields an in-process answer carries, so the turn's
        # token counts and cost are recorded for a relayed turn too.
        usage = event.get("usage")
        if isinstance(usage, dict):
            out.update({k: usage[k] for k in _ANSWER_USAGE_KEYS if k in usage})
        context = event.get("context")
        if isinstance(context, dict):
            # Contract >= 2.21: the composer's meter, as an in-process turn streams it.
            handler.relay_context = context
            handler._emit({"type": "context_usage", **context})
        elif isinstance(event.get("context_error"), str):
            handler.relay_context_error = event["context_error"]
        performance = event.get("performance")
        problem = None if performance is None else turn_record_problem(performance)
        if performance is not None and problem is None:
            # What summarize_turn reads, so Under the hood measures this turn.
            handler.relay_performance = performance
        elif problem is not None:
            logger.warning(
                "Sidecar's performance record refused because %s; "
                "the turn shows as not measured",
                problem,
            )
        handler._emit(out)
        return True

    elif etype == "error":
        if event.get("source") == _DAEMON_RELAY_SOURCE:
            # The daemon's crash frame is worded for API callers, not chat users.
            logger.warning(
                "%s relay: daemon reported the agent ended mid-run: %s",
                profile.agent_id,
                event.get("detail"),
            )
            content = profile.stream_ended_text()
        else:
            detail = (
                event.get("detail")
                or f"Unknown error from the {profile.display_name} agent."
            )
            content = _augment_error_detail(str(detail), profile)
        handler._emit(
            {
                "type": "agent_error",
                "content": content,
                # Ends the turn, unlike an in-process tool's agent_error.
                "fatal": True,
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


def _full_access_error(
    profile: RelayProfile, enabled: bool, exc: Exception
) -> Dict[str, Any]:
    """The terminal event for a turn stopped because its mode could not be set."""
    action = "turn on" if enabled else "turn off"
    return {
        "type": "agent_error",
        "content": (
            f"Couldn't {action} Full access for the {profile.display_name} "
            f"agent, so this turn was stopped: {exc}. Send the message again; "
            "if it keeps failing, restart the agent from its card."
        ),
        "fatal": True,
    }


class _FullAccessNotSet(SidecarError):
    """The chat's mode could not be set once ``/query`` created the session."""

    def __init__(self, event: Dict[str, Any]):
        super().__init__(event["content"])
        self.event = event


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
    full_access: Optional[bool] = None,
    profile: RelayProfile = EMAIL_PROFILE,
    session_id: Optional[str] = None,
    answers_confirmations: bool = False,
    documents: Optional[List[str]] = None,
    allowed_paths: Optional[List[str]] = None,
    memory: Optional[str] = None,
    changes_turn_id: Optional[str] = None,
    surface: Optional[str] = None,
    reasoning: bool = False,
) -> None:
    """Drive one ``/query`` run and relay it as UI-vocabulary SSE events.

    *changes_turn_id* — the undo-history turn the sidecar records this run's
    file changes under, for a sidecar the caller has established takes it
    (``profiles.changes_supported``).

    *allowed_paths* — the chat's file scope, for a sidecar the caller has
    established takes it (``profiles.scope_supported``); ``None`` leaves the
    sidecar on its own default.

    *memory* — the chat's memory mode (``on`` / ``private`` / ``off``), for a
    sidecar the caller has established takes it (``profiles.memory_supported``).

    *surface* — the front-end this turn comes from, for a sidecar the caller
    has established takes it (``profiles.surface_supported``).

    *reasoning* — ask the sidecar to stream the model's reasoning, for a
    sidecar the caller has established takes it (``profiles.reasoning_supported``).

    *documents* — absolute paths of the chat's attached files, for a sidecar
    the caller has established takes them (``profiles.documents_supported``).

    *answers_confirmations* — the caller has established that this sidecar
    takes ``tool_decision`` answers (``profiles.confirmations_supported``), so
    a ``needs_confirmation`` becomes an Allow once / Deny prompt.

    Runs synchronously on the caller's thread — the UI backend already runs
    the whole per-turn producer off the event loop in a worker/daemon thread
    (``_chat_helpers._run_agent``), so a blocking HTTP read here is safe.

    Every exception this function's dependencies can raise (a dropped
    connection, a read timeout, a malformed SSE line) is caught HERE and
    translated into a terminal SSE event — nothing propagates to the caller.
    This matters: the caller's surrounding retry/reload logic
    (``_classify_chat_exception``) is tuned for local-Lemonade failure
    substrings and would misclassify a sidecar connection error as a
    Lemonade error and show the wrong remedy. Sidecar
    exceptions must never reach it.

    Never calls ``handler.signal_done()`` — the turn-level done sentinel is
    owned by the caller (``_run_agent``'s outer ``finally``), exactly as for
    every other agent branch. Relay-level signalling would push a second
    ``None`` sentinel per turn, violating the queue's exactly-once contract.

    *full_access* is the chat's permission mode, for a sidecar that takes it
    (``profiles.full_access_supported``); ``None`` leaves the sidecar alone.
    It is set on the sidecar session before the run starts, and a turn whose
    mode could not be set is stopped rather than run on a mode the user did
    not pick.
    """
    # A proxy pointed at one agent driven with another's profile would send
    # that agent's body shape to the wrong /v1/<agent> prefix and label its
    # tools with the wrong names. ``profile`` still defaults to email for the
    # existing callers, so check rather than trust it.
    proxy_agent = getattr(proxy, "agent_id", None)
    if proxy_agent is not None and proxy_agent != profile.agent_id:
        raise ValueError(
            f"relay_query got a '{proxy_agent}' proxy with the "
            f"'{profile.agent_id}' profile; pass the profile that matches the "
            "sidecar being relayed."
        )

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
    if documents:
        body["documents"] = list(documents)
    if allowed_paths is not None:
        body["allowed_paths"] = list(allowed_paths)
    if memory is not None:
        body["memory"] = memory
    if changes_turn_id is not None:
        body["changes_turn_id"] = changes_turn_id
    if surface is not None:
        body["surface"] = surface
    if reasoning:
        body["reasoning"] = True

    syncs_mode = full_access is not None and bool(session_id)
    enable_after_start = False
    if syncs_mode:
        try:
            proxy.set_bypass(session_id, full_access)
        except SidecarHTTPError as exc:
            if exc.status_code != 404:
                handler._emit(_full_access_error(profile, full_access, exc))
                return
            # No sidecar session yet. /query creates it in ask, so only
            # "on" has anything left to do.
            enable_after_start = full_access
        except SidecarError as exc:
            handler._emit(_full_access_error(profile, full_access, exc))
            return

    # So a later POST /api/chat/user-input can find where to deliver the
    # answer (#2595) — mirrors active_relay_response's lifetime exactly.
    handler.active_relay_proxy = proxy
    handler.active_relay_run_id = rid
    # Lets a mid-run switch of the chat's mode reach this sidecar session too.
    handler.active_relay_mode_session = session_id if syncs_mode else None

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
    crash_message = profile.stream_ended_text()
    # Gave the chat's model slot up while the sidecar waits on the user.
    stepped_away = False
    asked = False
    try:
        events = proxy.query_stream(
            body, read_timeout=read_timeout, on_response=_register_response
        )
        if enable_after_start:
            # /query has created the sidecar session, so it can take the mode.
            try:
                proxy.set_bypass(session_id, True)
            except SidecarError as exc:
                raise _FullAccessNotSet(_full_access_error(profile, True, exc)) from exc
        for event in events:
            if handler.cancelled.is_set():
                break
            if asked:
                # Any event after a question means the sidecar stopped waiting.
                handler.set_relay_question(None)
                asked = False
            if stepped_away:
                stepped_away = False
                # The user answered or the sidecar's wait ended. A last event
                # needs no model, so the turn ends without waiting in line.
                if event.get("type") not in ("final", "error"):
                    handler.come_back_to_model()
                    if handler.cancelled.is_set():
                        break
            if _dispatch_one(handler, event, profile, answers_confirmations):
                terminated = True
                break
            waiting_on = _user_wait(event, answers_confirmations)
            if waiting_on is not None:
                if event.get("type") == "needs_input":
                    handler.set_relay_question(waiting_on)
                    asked = True
                # The sidecar run stays parked; only this chat's slot is freed.
                stepped_away = handler.step_away_from_model()
    except _FullAccessNotSet as exc:
        crashed = True
        crash_message = exc.event["content"]
        resp = handler.active_relay_response
        if resp is not None:
            resp.close()
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
                # through to profile.stream_ended_text().
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
        if answers_confirmations:
            handler.set_relay_confirmation(None)
        handler.set_relay_question(None)
        handler.active_relay_response = None
        handler.active_relay_proxy = None
        handler.active_relay_run_id = None
        handler.active_relay_mode_session = None

    if handler.cancelled.is_set():
        _best_effort_cancel(proxy, rid)
        if not terminated:
            handler._emit({"type": "status", "message": "Cancelled."})
    elif crashed or not terminated:
        # No terminal event arrived — the sidecar generation is still decoding
        # on the single GPU slot; stop it or later queries death-spiral.
        _best_effort_cancel(proxy, rid)
        handler._emit({"type": "agent_error", "content": crash_message, "fatal": True})


__all__ = [
    "relay_query",
    "RelayProfile",
    "EMAIL_QUERY_VERSION_UPGRADE_MESSAGE",
    "LEMONADE_CONNECTION_HINT",
]
