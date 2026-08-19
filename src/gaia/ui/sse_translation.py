# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Translate the in-process agent-loop SSE vocabulary into the frozen ``/query``
canonical wire contract (#2015 spec, first implemented for email in #2016).

The loop→SSE seam already exists: ``gaia.ui.sse_handler.SSEOutputHandler`` turns
every agent-loop ``console.print_*`` call into a typed JSON event on a queue. But
that handler emits its **own** vocabulary (``status`` / ``step`` / ``thinking`` /
``plan`` / ``tool_start`` / ``tool_args`` / ``tool_result`` / ``tool_end`` /
``chunk`` / ``answer`` / ``permission_request`` / ``user_input_request`` /
``tool_confirm_denied`` / ``agent_error`` / ``policy_alert`` / ``agent_created``),
which is **not** the v2 contract.

This module is the reusable translation layer (spec §6): a **total,
source-exhaustive** mapping from the handler's vocabulary onto the canonical
event types —

    status | token | tool_call | tool_result | needs_confirmation
          | needs_input | final | error

— terminated by exactly one ``final`` or ``error``. It is dependency-light (this
module plus the stdlib-only :mod:`gaia.ui.event_narration`) so it unit-tests
without Lemonade, a mailbox, or a live agent, and so the OpenAPI export stays
cheap.

Per-agent shape is injected, not hardcoded: ``agent_id`` (drives ``respond_url``),
``render_tool_map`` (which tool draws which card), ``action_labels`` +
``summary_renderer`` (how a gated action is described to the human). Defaults are
generic, so an agent that needs none of it constructs with just ``agent_id``.

Design commitments
------------------
- **No source event left unmapped** (spec §6.2). Every top-level type the handler
  emits has an explicit map / fold / drop decision below.
- **Buffer ``tool_start`` + ``tool_args`` into one ``tool_call``** (spec §6.3): the
  handler emits the name first and the arguments separately; the canonical
  ``tool_call`` carries ``{tool, args}`` together.
- **Describe the work, not the harness** (#2804). ``tool_call`` carries an
  additive ``narration`` ("Running command: git status") and ``tool_result`` an
  additive one-line ``preview`` ("18 skills · 21ms"), both from
  :mod:`gaia.ui.event_narration`. Conversely the loop's own bookkeeping —
  ``Step 3/50``, ``Processing with <model>...``, a bare ``Thinking`` — is
  tagged ``channel="debug"`` at the source and dropped here unless the
  translator is constructed with ``debug=True`` (or ``GAIA_SSE_DEBUG_EVENTS``
  is set). Those lines stay available for harness development; they just stop
  being the only thing a user sees during a two-minute turn.
- **Fail loudly, never silently.** A fatal top-level ``agent_error`` and a
  governance ``policy_alert`` map to a terminal ``error`` with an actionable
  ``detail`` — never a placeholder. A **recoverable** ``agent_error`` (the
  source event's ``recoverable`` flag, set by ``agent.py``'s
  ``STATE_ERROR_RECOVERY`` retry path) is explicitly NOT terminal — it folds
  to a ``status`` line so the run continues (#2515). The ``None`` queue
  sentinel is *stream close*, handled by the drain loop, not a wire event.

Spec open questions surfaced in this file:
- **Q2** — ``policy_alert`` maps to ``error`` (status 403). A governance block is
  per-*tool* (the run may continue) whereas canonical ``error`` is terminal; a
  dedicated additive event type may be warranted. See spec §9 Q2.
- **Q3 — RESOLVED (#2469).** ``user_input_request`` maps to the eighth canonical
  type ``needs_input`` (answerable, run continues), NOT to
  ``needs_confirmation`` (terminal approve/deny). See spec §5.1.
- **Q4 (D1)** — ``needs_confirmation`` omits ``confirm_url`` under the stateless
  stop-and-hand-off model (no server-side resume). See spec §5 / §9 Q4.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from gaia.ui.event_narration import (
    DEBUG_CHANNEL,
    derive_narration,
    derive_preview,
)

# HTTP-style status codes for the canonical ``error`` event's ``status`` field.
_ERROR_STATUS_AGENT = 500  # an agent-loop failure
_ERROR_STATUS_POLICY = 403  # a governance BLOCK (forbidden by policy)

#: Opt-in switch for the harness-internal debug channel (``Step 3/50``,
#: ``Processing with <model>...``, bare ``Thinking``). Off by default: those
#: lines describe the agent loop, not the user's work.
_DEBUG_ENV_VAR = "GAIA_SSE_DEBUG_EVENTS"
_DEBUG_ENV_TRUE = frozenset({"1", "true", "yes", "on"})


def _debug_enabled_by_env() -> bool:
    return os.environ.get(_DEBUG_ENV_VAR, "").strip().lower() in _DEBUG_ENV_TRUE


#: Canonical terminal event types — exactly one ends a run (spec §3).
#: ``needs_input`` is deliberately NOT here: the run pauses on it and resumes on
#: the same stream once the answer arrives (spec §5.1).
TERMINAL_TYPES = frozenset({"final", "error"})


class CanonicalTranslator:
    """Stateful translator: in-process handler events → canonical wire events.

    Feed each event dict drained from ``SSEOutputHandler.event_queue`` to
    :meth:`translate`; it returns zero or more canonical events to forward. Call
    :meth:`flush` once when the queue closes (the ``None`` sentinel) to release any
    buffered ``tool_call``.

    One instance per run — it buffers a pending ``tool_call`` and tracks the last
    tool name so ``tool_result`` can carry ``tool`` (the handler's ``tool_result``
    event does not repeat the name).

    Args:
        run_id: The host-minted run handle, echoed on pause events.
        agent_id: Agent this run belongs to. Drives ``respond_url`` — the reason
            this is a parameter and not a constant: a hardcoded ``/v1/email/...``
            silently points every other agent's client at the wrong sidecar.
        render_tool_map: ``tool name -> card key`` for ``tool_result.render``
            (spec §4.2). Must mirror the caller's
            ``SSEOutputHandler._RENDER_TOOL_TO_LANG``.
        action_labels: ``tool name -> human headline`` for confirmation prompts.
        summary_renderer: ``(tool, args) -> str`` override for the whole
            confirmation summary, when an agent wants domain detail (recipients,
            paths) beyond the generic label.
        debug: Forward harness-internal progress (``Step 3/50``, ``Processing
            with <model>...``, bare ``Thinking``) as ``status`` events marked
            ``channel="debug"``. Default ``None`` reads the
            ``GAIA_SSE_DEBUG_EVENTS`` environment variable, which is off unless
            a developer sets it.
    """

    def __init__(
        self,
        run_id: str,
        *,
        agent_id: str,
        render_tool_map: Optional[Dict[str, str]] = None,
        action_labels: Optional[Dict[str, str]] = None,
        summary_renderer: Optional[Callable[[str, Dict[str, Any]], str]] = None,
        debug: Optional[bool] = None,
    ) -> None:
        self._run_id = run_id
        self._agent_id = agent_id
        self._debug = _debug_enabled_by_env() if debug is None else bool(debug)
        self._render_tool_map = dict(render_tool_map or {})
        self._action_labels = dict(action_labels or {})
        self._summary_renderer = summary_renderer
        # Buffered tool_start awaiting its tool_args (spec §6.3). Shape:
        # {"tool": str, "args": dict}. None when nothing is pending.
        self._pending_tool: Optional[Dict[str, Any]] = None
        # Name of the most recently emitted tool_call — carried onto the
        # following tool_result (the source tool_result event omits it).
        self._last_tool: Optional[str] = None
        # Whether a tool_result was seen since the last tool_call, so a bare
        # tool_end can synthesize a minimal tool_result only when one is missing.
        self._result_seen_since_call = False
        # Last user-facing status message, cleared as soon as any other event
        # is emitted — see ``_user_status``.
        self._last_status: Optional[str] = None
        # Latched once a terminal event goes out — see ``translate``.
        self._terminal_emitted = False

    # -- public API --------------------------------------------------------

    def translate(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Map one source event to zero or more canonical events.

        Nothing is emitted after the first terminal event: the contract is
        "exactly one ``final`` or ``error`` ends every turn", and a reader
        stops at it — anything written afterwards sits unread in the pipe and
        gets consumed as the opening events of the NEXT turn. Clamping here
        makes every consumer loop (stdio, HTTP, email) trivially correct.
        """
        if self._terminal_emitted:
            return []
        return self._clamp_terminal(self._track_status(self._translate(event)))

    def _track_status(self, out: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Forget the last status once anything else reaches the wire.

        Keeps ``_user_status``'s de-duplication scoped to *consecutive* status
        events. Re-announcing a phase after a tool ran is real progress and
        must still emit — it is what fills the long silence while a local model
        composes its answer.
        """
        if any(e.get("type") != "status" for e in out):
            self._last_status = None
        return out

    def _translate(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        etype = event.get("type")

        # tool_args merges into the buffered tool_call; every other event first
        # flushes any pending tool_call (an argument-less tool), then maps.
        if etype == "tool_args":
            return self._on_tool_args(event)

        out: List[Dict[str, Any]] = []
        if etype != "tool_start":
            out.extend(self._flush_pending())

        handler = self._DISPATCH.get(etype)
        if handler is None:
            # Unknown/unlisted source type. The contract's no-silent-fallback
            # rule (spec §7) is enforced at the wire's RECEIVING end (unknown
            # canonical type → visible placeholder); here, on the SENDING end, an
            # unmapped SOURCE type means sse_handler.py grew a vocabulary this
            # translator hasn't been taught. Surface it as a status line rather
            # than dropping it silently — and it should be added to _DISPATCH.
            if etype:
                out.append(
                    {
                        "type": "status",
                        "message": f"[unmapped agent event: {etype}]",
                    }
                )
            return out
        out.extend(handler(self, event))
        return out

    def flush(self) -> List[Dict[str, Any]]:
        """Release any buffered ``tool_call`` at stream close."""
        if self._terminal_emitted:
            return []
        return self._clamp_terminal(self._track_status(self._flush_pending()))

    def _clamp_terminal(self, out: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Truncate a batch at its first terminal event and latch the clamp."""
        for i, e in enumerate(out):
            if e.get("type") in TERMINAL_TYPES:
                self._terminal_emitted = True
                return out[: i + 1]
        return out

    # -- tool_call buffering (spec §6.3) -----------------------------------

    def _flush_pending(self) -> List[Dict[str, Any]]:
        if self._pending_tool is None:
            return []
        pending = self._pending_tool
        self._pending_tool = None
        return [self._emit_tool_call(pending["tool"], pending.get("args") or {})]

    def _emit_tool_call(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        self._last_tool = tool
        self._result_seen_since_call = False
        # ``narration`` is additive — ``tool`` and ``args`` keep their shape for
        # clients that predate it (spec §4.1, additive-MINOR).
        return {
            "type": "tool_call",
            "tool": tool,
            "args": args,
            "narration": derive_narration(tool, args),
        }

    def _on_tool_start(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Flush a previous, arg-less pending tool_call before buffering this one.
        out = self._flush_pending()
        self._pending_tool = {"tool": event.get("tool") or "unknown", "args": {}}
        return out

    def _on_tool_args(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        args = event.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        if self._pending_tool is not None:
            tool = self._pending_tool["tool"]
            self._pending_tool = None
            return [self._emit_tool_call(tool, args)]
        # tool_args with no buffered tool_start (defensive) — emit a standalone
        # tool_call keyed on whatever name the args event carries.
        return [self._emit_tool_call(event.get("tool") or "unknown", args)]

    # -- individual maps ---------------------------------------------------

    def _debug_status(self, message: str) -> List[Dict[str, Any]]:
        """Route a harness-internal line to the debug channel, or drop it.

        Not deleted — agent-harness development depends on seeing the step
        counter and the model banner. It just does not belong on the surface a
        user watches to understand what the agent is doing (#2804).
        """
        if not self._debug:
            return []
        return [{"type": "status", "message": message, "channel": DEBUG_CHANNEL}]

    def _user_status(self, message: str) -> List[Dict[str, Any]]:
        """Emit a user-facing status, skipping an immediate repeat.

        The loop re-announces its phase on every step, so a three-step turn
        emitted "Working out how to answer" three times. A consecutive
        duplicate carries no new information — it is the noise half of the
        high-signal/low-noise ask (#2804). A repeat that is *not* consecutive
        still emits: coming back to a phase is real progress.
        """
        if message == self._last_status:
            return []
        self._last_status = message
        return [{"type": "status", "message": message}]

    def _on_status(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Already the canonical shape; keep only ``message`` (drop the
        # progress-only status/steps/elapsed sub-fields).
        message = str(event.get("message", ""))
        if event.get("channel") == DEBUG_CHANNEL:
            return self._debug_status(message)
        return self._user_status(message)

    def _on_step(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        step = event.get("step")
        total = event.get("total")
        msg = f"Step {step}/{total}" if step and total else "Step"
        # The step counter measures the harness, never the work.
        return self._debug_status(msg)

    def _on_thinking(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Reasoning narration folds to status, NOT token (token is answer text the
        # UI commits to the message). See spec §6.2 / Q1.
        content = str(event.get("content", "")).strip()
        if not content:
            return []
        return self._user_status(content)

    def _on_plan(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        steps = event.get("steps") or []
        joined = " → ".join(str(s) for s in steps)
        return self._user_status(f"Plan: {joined}" if joined else "Plan")

    def _on_chunk(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        delta = event.get("content", "")
        if not delta:
            return []
        return [{"type": "token", "delta": delta}]

    def _on_tool_result(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        self._result_seen_since_call = True
        data = event.get("result_data")
        if data is None:
            # The handler's tool_result event is a summary view; carry the
            # available structured bits so the generic result card has content.
            data = {}
            for key in ("summary", "success", "command_output", "latency_ms"):
                if key in event:
                    data[key] = event[key]
        tool = self._last_tool or "unknown"
        canonical: Dict[str, Any] = {
            "type": "tool_result",
            "tool": tool,
            "data": data,
            # Derived from the SOURCE event, not from ``data``: for a render-map
            # tool ``data`` is the card payload and carries neither the summary
            # nor the latency the preview line is made of.
            "preview": derive_preview(tool, event),
        }
        render = self._render_tool_map.get(self._last_tool or "")
        if render:
            canonical["render"] = render
        return [canonical]

    def _on_tool_end(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Redundant terminator: the tool_result already signals completion. Only
        # synthesize a minimal tool_result when the result was skipped, so
        # completion is never lost (spec §6.2).
        if self._result_seen_since_call:
            return []
        self._result_seen_since_call = True
        tool = self._last_tool or "unknown"
        return [
            {
                "type": "tool_result",
                "tool": tool,
                "data": {},
                "preview": derive_preview(tool, event),
            }
        ]

    def _on_answer(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        usage: Dict[str, Any] = {}
        # ``tokens`` and ``ttft`` are already omitted upstream when no real
        # measurement exists (SSEOutputHandler.print_answer), so anything here
        # is genuine — dropping them cost the TUI its tokens/sec readout.
        for src, dst in (
            ("steps", "steps"),
            ("tools_used", "tools_used"),
            ("elapsed", "elapsed"),
            ("tokens", "tokens"),
            ("ttft", "ttft"),
            # Dev-mode per-turn record, passed through verbatim — the client
            # decides what of it to show, so a new field needs no change here.
            ("metrics", "metrics"),
        ):
            if event.get(src) is not None:
                usage[dst] = event[src]
        final: Dict[str, Any] = {
            "type": "final",
            "answer": str(event.get("content", "") or ""),
        }
        if usage:
            final["usage"] = usage
        return [final]

    def _on_agent_error(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        detail = str(event.get("content") or "Unknown agent error")
        if event.get("recoverable"):
            # A per-tool error the agent loop is retrying (agent.py's
            # STATE_ERROR_RECOVERY path, e.g. a bad tool argument) is not
            # terminal — the run continues on this same stream. Fold it to a
            # status line, same pattern as ``_on_tool_confirm_denied``, so
            # the user still SEES the failure without the stream (and the
            # still-retrying agent) being cut out from under it (#2515).
            return [
                {"type": "status", "message": f"Tool call failed, retrying: {detail}"}
            ]
        return [{"type": "error", "detail": detail, "status": _ERROR_STATUS_AGENT}]

    def _on_policy_alert(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # A governance BLOCK is an actionable, must-surface refusal (spec §6.2).
        # Q2: it is per-tool (the run may continue), but canonical error is
        # terminal — the drain loop decides terminal-vs-continue; the mapping
        # itself is error with a structured tail on detail.
        reason = str(event.get("reason") or "blocked by policy")
        tool = event.get("tool")
        rule_ids = event.get("rule_ids") or []
        tail_parts = []
        if tool:
            tail_parts.append(f"tool={tool}")
        if rule_ids:
            tail_parts.append("rules=" + ",".join(str(r) for r in rule_ids))
        detail = reason + (f" ({'; '.join(tail_parts)})" if tail_parts else "")
        return [{"type": "error", "detail": detail, "status": _ERROR_STATUS_POLICY}]

    def _on_permission_request(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        tool = str(event.get("tool") or "action")
        args = event.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        summary = self._render_args_summary(tool, args)
        # confirm_url omitted: stateless stop-and-hand-off (D1, spec §5 / Q4).
        canonical: Dict[str, Any] = {
            "type": "needs_confirmation",
            "run_id": self._run_id,
            "action": tool,
            "summary": summary,
        }
        # The emitter's handle for THIS prompt. A front-end with a live decision
        # channel echoes it back so a late answer cannot resolve the confirmation
        # that replaced the one it was typed for.
        confirm_id = event.get("confirm_id")
        if confirm_id:
            canonical["confirm_id"] = str(confirm_id)
        # What an "always" answer would grant, e.g. ``gh issue list``. Absent
        # means this call has no scope narrow enough to describe, so the
        # front-end must not offer the choice at all — see
        # :mod:`gaia.agents.base.tool_grants`.
        always_scope = event.get("always_scope")
        if always_scope:
            canonical["always_scope"] = str(always_scope)
        return [canonical]

    def _on_user_input_request(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Spec §9 Q3 resolved: a mid-run question is its OWN canonical type, not
        # a flavour of needs_confirmation. The two differ in the only way that
        # matters on the wire — needs_confirmation is a terminal approve/deny
        # (deny-by-default, run over), needs_input is answerable and the run
        # continues on the same stream. Folding them would have made the
        # security-relevant terminal behaviour depend on an optional field.
        question = str(event.get("message") or "Input requested")
        canonical: Dict[str, Any] = {
            "type": "needs_input",
            "run_id": self._run_id,
            "request_id": str(event.get("request_id") or ""),
            "question": question,
            "options": _normalize_options(event),
            "allow_free_text": bool(event.get("allow_free_text", True)),
            "sensitive": bool(event.get("sensitive", False)),
            "respond_url": f"/v1/{self._agent_id}/query/{self._run_id}/respond",
        }
        timeout = event.get("timeout_seconds")
        if isinstance(timeout, (int, float)) and timeout > 0:
            canonical["timeout_seconds"] = int(timeout)
        return [canonical]

    def _on_tool_confirm_denied(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Unattended auto-deny is informational — the run continues and the agent
        # retries. Surface as a status line, not an error (spec §6.2).
        return [{"type": "status", "message": str(event.get("message", ""))}]

    def _on_agent_created(self, _event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Registry-refresh signal with no chat-stream meaning — dropped from the
        # /query stream by contract decision (spec §6.2). It belongs on a host
        # control channel, not this event stream.
        return []

    # -- confirmation summary ----------------------------------------------

    def _render_args_summary(self, tool: str, args: Dict[str, Any]) -> str:
        """Render a human-readable confirmation prompt for a gated tool call.

        Produces a plain sentence a chat user can approve rather than a raw
        ``key=value`` dump. The machine action name is carried on the event's
        ``action`` field, not here. Agents with domain detail worth showing
        (recipients, paths) pass ``summary_renderer``.
        """
        if self._summary_renderer is not None:
            return self._summary_renderer(tool, args)
        label = self._action_labels.get(tool, f"Run {tool!r}")
        detail = render_invocation(args)
        if not detail:
            return f"{label}?"
        return f"{label} with {detail}?"

    # Dispatch table: every top-level source type sse_handler.py emits.
    _DISPATCH = {
        "status": _on_status,
        "step": _on_step,
        "thinking": _on_thinking,
        "plan": _on_plan,
        "chunk": _on_chunk,
        "tool_start": _on_tool_start,
        "tool_result": _on_tool_result,
        "tool_end": _on_tool_end,
        "answer": _on_answer,
        "agent_error": _on_agent_error,
        "policy_alert": _on_policy_alert,
        "permission_request": _on_permission_request,
        "user_input_request": _on_user_input_request,
        "tool_confirm_denied": _on_tool_confirm_denied,
        "agent_created": _on_agent_created,
        # tool_args is handled before dispatch (merges into the pending tool_call).
    }


def _normalize_options(event: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build the canonical ``options`` array for a ``needs_input`` event.

    Prefers the rich ``options`` list (``{value, label, description}``) and falls
    back to the flat ``choices`` strings, so a caller that only passed choices
    still gets a pickable list rather than an unlabelled blob buried in prose.
    """
    out: List[Dict[str, str]] = []
    raw_options = event.get("options")
    if isinstance(raw_options, list):
        for item in raw_options:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or item.get("label") or "").strip()
            if not value:
                continue
            out.append(
                {
                    "value": value,
                    "label": str(item.get("label") or value),
                    "description": str(item.get("description") or ""),
                }
            )
    if out:
        return out
    for choice in event.get("choices") or []:
        text = str(choice).strip()
        if text:
            out.append({"value": text, "label": text, "description": ""})
    return out


def render_labelled_summary(
    tool: str,
    args: Dict[str, Any],
    *,
    labels: Dict[str, str],
    detail_fields: Dict[str, str],
    max_field_chars: int = 120,
) -> str:
    """Build a confirmation sentence from a label plus selected argument fields.

    ``detail_fields`` maps an argument name to a prefix, e.g.
    ``{"to": "to", "subject": "— subject"}`` renders
    ``Send this email to a@b.com — subject "Re: …"?``. Values longer than
    ``max_field_chars`` are elided. Agents pass this (via ``functools.partial``)
    as ``summary_renderer`` rather than reimplementing the shape.
    """
    label = labels.get(tool, f"Run {tool!r}")
    if not isinstance(args, dict) or not args:
        return f"{label}?"
    detail = []
    for field, prefix in detail_fields.items():
        value = args.get(field)
        if not value:
            continue
        text = str(value)
        if len(text) > max_field_chars:
            text = text[:max_field_chars] + "…"
        # A quoted field reads as a title; a bare one reads as an identifier.
        detail.append(
            f'{prefix} "{text}"' if prefix.startswith("—") else f"{prefix} {text}"
        )
    if detail:
        return f"{label} " + " ".join(detail) + "?"
    return f"{label}?"


#: Longest a single argument value may be in a confirmation summary. Past this
#: the value is elided: the prompt has to fit a modal, and a payload nobody can
#: read is not disclosure.
#:
#: Sized for the longest thing a person actually has to read before answering —
#: the body of a comment about to be posted publicly. At 180 a routine triage
#: reply was cut mid-sentence, so "approve this exact command" meant approving
#: text the user could not see. A modal wraps, so the cost of the extra lines is
#: scrolling; the cost of the old cap was unread consent.
INVOCATION_VALUE_CHARS = 600

#: Longest the whole rendered argument clause may be.
INVOCATION_TOTAL_CHARS = 1200


def render_invocation(args: Dict[str, Any]) -> str:
    """Render a tool call's arguments as one compact, readable clause.

    A permission prompt that names only the tool ("Run 'run_shell_command'?")
    hides the one thing the decision turns on, which trains people to approve
    without looking. Every argument name is shown — a hidden key is a hidden
    side effect — and only oversized values are elided.
    """
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for key in sorted(args):
        value = args[key]
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        text = " ".join(text.split())
        if len(text) > INVOCATION_VALUE_CHARS:
            hidden = len(text) - INVOCATION_VALUE_CHARS
            # Never a bare "…". A silent cut reads as the whole value, so the
            # user approves text they were never shown and does not know it.
            text = f"{text[:INVOCATION_VALUE_CHARS]}… [+{hidden:,} more characters not shown]"
        parts.append(f'{key}="{text}"')
    clause = ", ".join(parts)
    if len(clause) > INVOCATION_TOTAL_CHARS:
        clause = clause[:INVOCATION_TOTAL_CHARS] + "…"
    return clause


__all__ = [
    "CanonicalTranslator",
    "TERMINAL_TYPES",
    "render_invocation",
    "render_labelled_summary",
]
