# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Translate the in-process agent-loop SSE vocabulary into the frozen ``/query``
canonical wire contract (#2016, implementing the #2015 spec).

The loop→SSE seam already exists: ``gaia.ui.sse_handler.SSEOutputHandler`` turns
every agent-loop ``console.print_*`` call into a typed JSON event on a queue. But
that handler emits its **own** vocabulary (``status`` / ``step`` / ``thinking`` /
``plan`` / ``tool_start`` / ``tool_args`` / ``tool_result`` / ``tool_end`` /
``chunk`` / ``answer`` / ``permission_request`` / ``user_input_request`` /
``tool_confirm_denied`` / ``agent_error`` / ``policy_alert`` / ``agent_created``),
which is **not** the v2 contract.

This module is the reusable translation layer (spec §6): a **total,
source-exhaustive** mapping from the handler's vocabulary onto the **seven
canonical event types** —

    status | token | tool_call | tool_result | needs_confirmation | final | error

— terminated by exactly one ``final`` or ``error``. It is dependency-light (stdlib
only) so it unit-tests without Lemonade, Gmail, or ``gaia.ui`` imports, and so the
OpenAPI export stays cheap.

Design commitments
------------------
- **No source event left unmapped** (spec §6.2). Every top-level type the handler
  emits has an explicit map / fold / drop decision below.
- **Buffer ``tool_start`` + ``tool_args`` into one ``tool_call``** (spec §6.3): the
  handler emits the name first and the arguments separately; the canonical
  ``tool_call`` carries ``{tool, args}`` together.
- **Fail loudly, never silently.** ``agent_error`` and a governance
  ``policy_alert`` map to a terminal ``error`` with an actionable ``detail`` — never
  a placeholder. The ``None`` queue sentinel is *stream close*, handled by the
  drain loop, not a wire event.

Spec open questions surfaced in this file (do not block #2016):
- **Q2** — ``policy_alert`` maps to ``error`` (status 403). A governance block is
  per-*tool* (the run may continue) whereas canonical ``error`` is terminal; a
  dedicated additive event type may be warranted. See spec §9 Q2.
- **Q4 (D1)** — ``needs_confirmation`` omits ``confirm_url`` under the stateless
  stop-and-hand-off model (no server-side resume). See spec §5 / §9 Q4.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Mirrors ``gaia.ui.sse_handler.SSEOutputHandler._RENDER_TOOL_TO_LANG`` — the
# tool→card-key map that tells the host which typed ``tool_result.render`` card to
# draw (spec §4.2, replacing the #1000 fence-injection hack). Duplicated (not
# imported) to keep this module free of the ``gaia.ui`` import chain; the email
# agent's only render tool today is the inbox pre-scan.
_RENDER_TOOL_TO_LANG: Dict[str, str] = {
    "pre_scan_inbox": "email_pre_scan",
}

# HTTP-style status codes for the canonical ``error`` event's ``status`` field.
_ERROR_STATUS_AGENT = 500  # an agent-loop failure
_ERROR_STATUS_POLICY = 403  # a governance BLOCK (forbidden by policy)


class CanonicalTranslator:
    """Stateful translator: in-process handler events → canonical wire events.

    Feed each event dict drained from ``SSEOutputHandler.event_queue`` to
    :meth:`translate`; it returns zero or more canonical events to forward. Call
    :meth:`flush` once when the queue closes (the ``None`` sentinel) to release any
    buffered ``tool_call``.

    One instance per run — it buffers a pending ``tool_call`` and tracks the last
    tool name so ``tool_result`` can carry ``tool`` (the handler's ``tool_result``
    event does not repeat the name).
    """

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        # Buffered tool_start awaiting its tool_args (spec §6.3). Shape:
        # {"tool": str, "args": dict}. None when nothing is pending.
        self._pending_tool: Optional[Dict[str, Any]] = None
        # Name of the most recently emitted tool_call — carried onto the
        # following tool_result (the source tool_result event omits it).
        self._last_tool: Optional[str] = None
        # Whether a tool_result was seen since the last tool_call, so a bare
        # tool_end can synthesize a minimal tool_result only when one is missing.
        self._result_seen_since_call = False

    # -- public API --------------------------------------------------------

    def translate(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Map one source event to zero or more canonical events."""
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
        return self._flush_pending()

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
        return {"type": "tool_call", "tool": tool, "args": args}

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

    def _on_status(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Already the canonical shape; keep only ``message`` (drop the
        # progress-only status/steps/elapsed sub-fields).
        return [{"type": "status", "message": str(event.get("message", ""))}]

    def _on_step(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        step = event.get("step")
        total = event.get("total")
        msg = f"Step {step}/{total}" if step and total else "Step"
        return [{"type": "status", "message": msg}]

    def _on_thinking(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Reasoning narration folds to status, NOT token (token is answer text the
        # UI commits to the message). See spec §6.2 / Q1.
        content = str(event.get("content", "")).strip()
        if not content:
            return []
        return [{"type": "status", "message": content}]

    def _on_plan(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        steps = event.get("steps") or []
        joined = " → ".join(str(s) for s in steps)
        return [{"type": "status", "message": f"Plan: {joined}" if joined else "Plan"}]

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
        canonical: Dict[str, Any] = {
            "type": "tool_result",
            "tool": self._last_tool or "unknown",
            "data": data,
        }
        render = _RENDER_TOOL_TO_LANG.get(self._last_tool or "")
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
        return [
            {"type": "tool_result", "tool": self._last_tool or "unknown", "data": {}}
        ]

    def _on_answer(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        usage: Dict[str, Any] = {}
        for src, dst in (
            ("steps", "steps"),
            ("tools_used", "tools_used"),
            ("elapsed", "elapsed"),
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
        summary = _render_args_summary(tool, args)
        # confirm_url omitted: stateless stop-and-hand-off (D1, spec §5 / Q4).
        return [
            {
                "type": "needs_confirmation",
                "run_id": self._run_id,
                "action": tool,
                "summary": summary,
            }
        ]

    def _on_user_input_request(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        message = str(event.get("message") or "Input requested")
        choices = event.get("choices") or []
        summary = message
        if choices:
            summary = f"{message} (choices: {', '.join(str(c) for c in choices)})"
        # Same "pause for the user" primitive as approve/deny (spec §6.2 / Q3).
        return [
            {
                "type": "needs_confirmation",
                "run_id": self._run_id,
                "action": "input",
                "summary": summary,
            }
        ]

    def _on_tool_confirm_denied(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Unattended auto-deny is informational — the run continues and the agent
        # retries. Surface as a status line, not an error (spec §6.2).
        return [{"type": "status", "message": str(event.get("message", ""))}]

    def _on_agent_created(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Registry-refresh signal with no chat-stream meaning — dropped from the
        # /query stream by contract decision (spec §6.2). It belongs on a host
        # control channel, not this event stream.
        return []

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


#: Canonical terminal event types — exactly one ends a run (spec §3).
TERMINAL_TYPES = frozenset({"final", "error"})


def _render_args_summary(tool: str, args: Dict[str, Any]) -> str:
    """Render tool args as the literal text the user would approve.

    Kept deterministic and compact — recipients/subject/body for a send, or a
    ``key=value`` join for any other gated action.
    """
    if not args:
        return f"{tool} (no arguments)"
    if isinstance(args, dict):
        parts = []
        for key, value in args.items():
            text = str(value)
            if len(text) > 200:
                text = text[:200] + "…"
            parts.append(f"{key}={text}")
        return f"{tool}: " + ", ".join(parts)
    return f"{tool}: {args}"


__all__ = ["CanonicalTranslator", "TERMINAL_TYPES"]
