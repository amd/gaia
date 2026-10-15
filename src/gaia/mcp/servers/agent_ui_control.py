# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Turn control for the Agent UI MCP server.

Runs a chat turn in the background, answers its permission prompts, and reads
it back the way the Agent UI renders it, so a test harness can drive a real
task without a browser.

Each turn is tracked by reading the same SSE stream the web UI reads
(``POST /api/chat/send``, or ``GET /api/chat/attach`` for a turn started
elsewhere) and folding its events with the rules ``ChatView.tsx`` uses. Prompt
answers go through ``POST /api/chat/confirm-tool`` -- the endpoint a user's
click posts to -- and only for a prompt this tracker actually saw.

Kept free of the optional ``mcp`` dependency so it is unit-testable on its own.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import math
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests

from gaia.ui.sse_handler import (
    _RAG_RESULT_JSON_SUB_RE,
    _THINK_TAG_SUB_RE,
    _THOUGHT_JSON_SUB_RE,
    _TOOL_CALL_JSON_SUB_RE,
    _TRAILING_CODE_FENCE_RE,
)

# ── Limits ──────────────────────────────────────────────────────────────────

#: Same ceiling as the TUI's ``/control/v1/wait``.
MAX_WAIT_MS = 10 * 60 * 1000
DEFAULT_WAIT_MS = 60_000

#: How long ``start`` waits for the backend to accept or refuse the turn, so a
#: missing session or a busy one is reported by the call that caused it.
START_ACK_TIMEOUT_S = 15.0

#: (connect, read). The backend sends a keepalive every ~5 s, including while
#: a prompt waits, so a read gap this long means the backend is gone.
STREAM_TIMEOUT: Tuple[float, float] = (10.0, 120.0)
REQUEST_TIMEOUT_S = 30.0

ATTACH_GRACE_S = 5.0

#: Prompt ids remembered for the cross-session check.
MAX_REMEMBERED_PROMPTS = 2000

WAIT_CONDITIONS = ("idle", "prompt", "question", "attention", "text")
DECISIONS = ("allow_once", "always", "deny")

_ARG_VALUE_CAP = 500
_ARG_SUMMARY_CAP = 300
_STEP_TEXT_CAP = 500

#: The tool summary ``tool_start`` falls back to in the UI.
_PATH_ACCESS_TOOL = "request_path_access"


def err(detail: str, **extra: Any) -> Dict[str, Any]:
    return {"status": "error", "detail": detail, **extra}


# ── Argument validation ────────────────────────────────────────────────────


def parse_timeout_ms(value: Any) -> Tuple[Optional[int], Optional[str]]:
    """Validate a ``timeout_ms`` argument; return ``(ms, None)`` or ``(None, why)``."""
    if isinstance(value, bool) or value is None:
        return None, f"timeout_ms must be a whole number of ms, got {value!r}."
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            return None, f"timeout_ms must be a whole number of ms, got {value!r}."
        value = int(value)
    if isinstance(value, str):
        if not value.strip().lstrip("-").isdigit():
            return None, f"timeout_ms must be a whole number of ms, got {value!r}."
        value = int(value.strip())
    if not isinstance(value, int):
        return None, f"timeout_ms must be a whole number of ms, got {value!r}."
    if not 0 < value <= MAX_WAIT_MS:
        return None, (
            f"timeout_ms must be between 1 and {MAX_WAIT_MS} (10 minutes), "
            f"got {value}. For a longer turn, call wait_for_turn again."
        )
    return value, None


# ── Event folding (mirrors ChatView.tsx) ───────────────────────────────────


def _cap(text: Any, limit: int = _STEP_TEXT_CAP) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[:limit] + "…"


def _truncate_args(args: Any) -> Any:
    if isinstance(args, dict):
        return {str(k): _truncate_args(v) for k, v in args.items()}
    if isinstance(args, list):
        return [_truncate_args(v) for v in args[:50]]
    if isinstance(args, str):
        return _cap(args, _ARG_VALUE_CAP)
    return args


def summarize_args(args: Any) -> str:
    """One line naming each argument, values clipped -- what a prompt shows."""
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for key, value in args.items():
        shown = value if isinstance(value, str) else json.dumps(value, default=str)
        shown = " ".join(str(shown).split())
        if len(shown) > 80:
            shown = shown[:80] + "…"
        parts.append(f"{key}={shown}")
    return _cap(", ".join(parts), _ARG_SUMMARY_CAP)


def clean_answer(text: str) -> str:
    """Strip the LLM noise the UI strips before it shows answer text."""
    text = _TOOL_CALL_JSON_SUB_RE.sub("", text or "")
    text = _THOUGHT_JSON_SUB_RE.sub("", text)
    text = _RAG_RESULT_JSON_SUB_RE.sub("", text)
    text = _TRAILING_CODE_FENCE_RE.sub("", text)
    text = _THINK_TAG_SUB_RE.sub("", text)
    return re.sub(r"^[}\s`]+", "", text).strip()


def _format_latency(ms: int) -> str:
    return f"{ms}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


class TurnTracker:
    """One chat turn, rebuilt from its SSE events.

    Every field is guarded by ``cond``; waiters are woken on each event.
    """

    def __init__(
        self,
        session_id: str,
        origin: str,
        on_prompt: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.session_id = session_id
        self.origin = origin
        self.turn_id = uuid.uuid4().hex[:12]
        self.cond = threading.Condition()
        self.started_at = time.monotonic()
        self.ended_at: Optional[float] = None
        self.opened = threading.Event()
        self.live = True
        self.outcome: Optional[str] = None
        self.error: Optional[str] = None
        self.raw_content = ""
        self.final_content: Optional[str] = None
        self.steps: List[Dict[str, Any]] = []
        self.pending_prompt: Optional[Dict[str, Any]] = None
        self.pending_question: Optional[Dict[str, Any]] = None
        self.answered_prompt_ids: set = set()
        self.denials: List[Dict[str, Any]] = []
        self.answer_meta: Dict[str, Any] = {}
        self.done_stats: Optional[Dict[str, Any]] = None
        self.message_id: Optional[int] = None
        self.cancelled = False
        self.stop_requested = False
        self.history_notice: Optional[str] = None
        self.live_status: Optional[str] = None
        self.event_count = 0
        self._tool_since_text = False
        self._on_prompt = on_prompt

    # ── folding ──

    def apply(self, event: Dict[str, Any]) -> None:
        etype = event.get("type", "")
        with self.cond:
            self.event_count += 1
            # The agent emits nothing while it blocks on the user, so any later
            # event means the wait is over -- the rule ChatView applies.
            if etype not in ("permission_request", "needs_input"):
                self.pending_prompt = None
                self.pending_question = None
            self._fold(etype, event)
            self.cond.notify_all()

    def _fold(self, etype: str, event: Dict[str, Any]) -> None:
        if etype == "chunk":
            content = event.get("content") or ""
            if content:
                if self._tool_since_text:
                    if self.raw_content:
                        self.raw_content += "\n\n"
                    self._tool_since_text = False
                self.raw_content += content
        elif etype == "answer":
            if event.get("content"):
                self.raw_content = event["content"]
            self.answer_meta = {
                k: event[k]
                for k in (
                    "elapsed",
                    "steps",
                    "tools_used",
                    "tokens",
                    "ttft",
                    "tok_per_s",
                    "input_tokens",
                    "cached_tokens",
                    "metrics",
                )
                if k in event
            }
        elif etype == "done":
            self._finish_locked(
                "cancelled" if event.get("cancelled") else "completed", None
            )
            self.cancelled = bool(event.get("cancelled"))
            self.message_id = event.get("message_id")
            if event.get("content"):
                self.final_content = event["content"]
            if isinstance(event.get("stats"), dict):
                self.done_stats = event["stats"]
        elif etype == "error":
            self.error = str(event.get("content") or "Unknown error")
            self._finish_locked("error", self.error)
        elif etype == "permission_request":
            self._prompt(event)
        elif etype == "needs_input":
            self.pending_question = {
                "request_id": str(event.get("request_id") or ""),
                "question": _cap(event.get("question"), 2000),
                "options": event.get("options") or [],
                "allow_free_text": bool(event.get("allow_free_text", True)),
                "received_at": time.monotonic(),
                "timeout_seconds": event.get("timeout_seconds"),
            }
        elif etype == "tool_confirm_denied":
            self.denials.append(
                {
                    "tool": event.get("tool"),
                    "reason": event.get("reason"),
                    "message": _cap(event.get("message")),
                }
            )
        else:
            self._fold_step(etype, event)

    def _prompt(self, event: Dict[str, Any]) -> None:
        confirm_id = str(event.get("confirm_id") or "")
        args = event.get("args")
        prompt = {
            "confirm_id": confirm_id,
            "tool": event.get("tool") or "",
            "args": _truncate_args(args) if isinstance(args, dict) else {},
            "args_summary": (
                _cap(event.get("summary"), _ARG_SUMMARY_CAP)
                if event.get("summary")
                else summarize_args(args)
            ),
            "risk": event.get("risk"),
            "always_scope": event.get("always_scope"),
            "timeout_seconds": event.get("timeout_seconds"),
            "received_at": time.monotonic(),
        }
        self.pending_prompt = prompt
        if confirm_id and self._on_prompt is not None:
            self._on_prompt(confirm_id, self.session_id)

    def _last_tool_step(self) -> Optional[Dict[str, Any]]:
        for step in reversed(self.steps):
            if step["type"] == "tool":
                return step
        return None

    def _add_step(self, step: Dict[str, Any]) -> None:
        self.steps.append(step)

    def _fold_step(self, etype: str, event: Dict[str, Any]) -> None:
        last = self.steps[-1] if self.steps else None
        if etype == "tool_end":
            tool = self._last_tool_step()
            if tool:
                tool["active"] = False
                tool["success"] = event.get("success") is not False
        elif etype == "tool_result":
            tool = self._last_tool_step()
            if tool:
                tool["result"] = _cap(
                    event.get("summary") or event.get("title") or "Done"
                )
                tool["active"] = False
                tool["success"] = event.get("success") is not False
                if event.get("latency_ms") is not None:
                    tool["latency_ms"] = event["latency_ms"]
                cmd = event.get("command_output")
                if isinstance(cmd, dict):
                    tool["command"] = {
                        "command": _cap(cmd.get("command")),
                        "return_code": cmd.get("return_code"),
                        "stdout": _cap(cmd.get("stdout")),
                        "stderr": _cap(cmd.get("stderr")),
                    }
        elif etype == "tool_args":
            tool = self._last_tool_step()
            if tool:
                tool["detail"] = _cap(
                    event.get("detail") or json.dumps(event.get("args"), default=str)
                )
        elif etype == "thinking":
            if last and last["type"] == "thinking":
                last["detail"] = _cap(
                    (last.get("detail") or "") + (event.get("content") or ""), 4000
                )
            else:
                self._add_step(
                    {
                        "type": "thinking",
                        "label": "Thinking",
                        "detail": _cap(event.get("content"), 4000),
                        "active": True,
                    }
                )
        elif etype == "status":
            status = event.get("status")
            msg = (event.get("message") or "").strip()
            if status == "history_cut":
                self.history_notice = msg or None
                return
            if event.get("phase") and msg:
                self.live_status = msg
            if msg.lower().startswith("executing ") or msg.startswith("Agent: "):
                return
            if status not in ("working", "warning", "info"):
                return
            if last and last["type"] == "status" and last.get("active"):
                last["label"] = msg or "Working"
                last["detail"] = msg
                return
            if last and last["type"] == "thinking" and last.get("active"):
                last["label"] = msg or "Thinking"
                return
            self._add_step(
                {
                    "type": "status",
                    "label": msg or status or "Working",
                    "active": status == "working",
                }
            )
        elif etype == "step":
            return  # the UI drops step headers too
        elif etype == "tool_start":
            self._add_step(
                {
                    "type": "tool",
                    "label": "Using tool",
                    "tool": event.get("tool"),
                    "detail": _cap(event.get("detail")),
                    "active": True,
                    "success": None,
                }
            )
            self._tool_since_text = True
            self.live_status = None
        elif etype == "plan":
            self._add_step(
                {
                    "type": "plan",
                    "label": "Created plan",
                    "plan": [_cap(s, 200) for s in (event.get("steps") or [])],
                    "active": False,
                    "success": True,
                }
            )
        elif etype == "agent_error":
            self._add_step(
                {
                    "type": "error",
                    "label": "Error",
                    "detail": _cap(event.get("content")),
                    "active": False,
                    "success": False,
                }
            )
        elif etype == "policy_alert":
            tool = event.get("tool") or "unknown tool"
            self._add_step(
                {
                    "type": "policy_alert",
                    "label": f"Policy blocked {tool}",
                    "tool": tool,
                    "detail": _cap(event.get("reason") or event.get("message")),
                    "active": False,
                    "success": False,
                }
            )

    def finish(self, outcome: str, error: Optional[str] = None) -> None:
        with self.cond:
            self._finish_locked(outcome, error)
            self.cond.notify_all()

    def _finish_locked(self, outcome: str, error: Optional[str]) -> None:
        if not self.live:
            return
        self.live = False
        self.outcome = outcome
        if error:
            self.error = error
        self.ended_at = time.monotonic()
        self.pending_prompt = None
        self.pending_question = None
        for step in self.steps:
            if step.get("active"):
                step["active"] = False

    # ── views (call with cond held) ──

    def content(self) -> str:
        if self.final_content is not None:
            return self.final_content
        return clean_answer(self.raw_content)

    def elapsed_s(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.monotonic()
        return round(end - self.started_at, 2)

    def state(self) -> str:
        if not self.live:
            return "idle"
        if self.pending_prompt is not None:
            return "awaiting_permission"
        if self.pending_question is not None:
            return "awaiting_input"
        return "streaming"

    def prompt_view(self) -> Optional[Dict[str, Any]]:
        p = self.pending_prompt
        if p is None:
            return None
        view = {k: v for k, v in p.items() if k != "received_at"}
        timeout = p.get("timeout_seconds")
        if isinstance(timeout, (int, float)):
            view["expires_in_s"] = max(
                0.0, round(p["received_at"] + timeout - time.monotonic(), 1)
            )
        return view

    def question_view(self) -> Optional[Dict[str, Any]]:
        q = self.pending_question
        if q is None:
            return None
        return {k: v for k, v in q.items() if k != "received_at"}

    def current_step(self) -> Optional[Dict[str, Any]]:
        for step in reversed(self.steps):
            if step.get("active"):
                return _step_brief(step)
        return _step_brief(self.steps[-1]) if self.steps else None

    def status(self) -> Dict[str, Any]:
        state = self.state()
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "origin": self.origin,
            "state": state,
            "streaming": state != "idle",
            "outcome": self.outcome,
            "error": self.error,
            "pending_prompt": self.prompt_view(),
            "pending_question": self.question_view(),
            "current_step": self.current_step(),
            "live_status": self.live_status,
            "elapsed_s": self.elapsed_s(),
            "steps_so_far": len(self.steps),
            "answer_chars": len(self.content()),
        }

    def footer(self) -> Dict[str, Any]:
        """The token footer under an answer (MessageBubble ``msg-stats``)."""
        latency_ms = int(round(self.elapsed_s() * 1000)) if not self.live else None
        stats = self.done_stats or {}
        parts: List[str] = []
        if latency_ms:
            parts.append(_format_latency(latency_ms))
        tps = stats.get("tokens_per_second")
        ttft = stats.get("time_to_first_token")
        tin = stats.get("input_tokens")
        tout = stats.get("output_tokens")
        if isinstance(tps, (int, float)) and tps > 0:
            parts.append(f"{tps} tok/s")
        if isinstance(ttft, (int, float)) and ttft > 0:
            parts.append(f"{ttft * 1000:.0f}ms TTFT")
        if isinstance(tout, (int, float)) and tout > 0:
            parts.append(f"{int(tin or 0):,} → {int(tout):,} tokens")
        return {
            "latency_ms": latency_ms,
            "tokens_per_second": tps,
            "time_to_first_token_s": ttft,
            "input_tokens": tin,
            "output_tokens": tout,
            "text": " · ".join(parts),
        }

    def view(self) -> Dict[str, Any]:
        return {
            **self.status(),
            "content": self.content(),
            "steps": [dict(s) for s in self.steps],
            "denials": list(self.denials),
            "footer": self.footer() if not self.live else None,
            "stopped": self.cancelled,
            "history_notice": self.history_notice,
            "message_id": self.message_id,
        }

    def metrics(self) -> Dict[str, Any]:
        a = self.answer_meta
        stats = self.done_stats or {}
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "state": self.state(),
            "outcome": self.outcome,
            "steps": a.get("steps"),
            "tool_calls": a.get("tools_used"),
            "tool_calls_seen": sum(1 for s in self.steps if s["type"] == "tool"),
            "tokens_in": stats.get("input_tokens", a.get("input_tokens")),
            "tokens_out": stats.get("output_tokens"),
            "turn_output_tokens": a.get("tokens"),
            "turn_input_tokens": a.get("input_tokens"),
            "cached_tokens": a.get("cached_tokens"),
            "tokens_per_second": stats.get("tokens_per_second", a.get("tok_per_s")),
            "time_to_first_token_s": stats.get("time_to_first_token", a.get("ttft")),
            "agent_elapsed_s": a.get("elapsed"),
            "wall_s": self.elapsed_s(),
            "denials": len(self.denials),
            "dev_metrics": a.get("metrics"),
        }


def _step_brief(step: Dict[str, Any]) -> Dict[str, Any]:
    return {
        k: step.get(k)
        for k in ("type", "label", "tool", "detail", "active", "success")
        if step.get(k) is not None
    }


def iter_sse_events(lines: Iterable[str]) -> Iterable[Dict[str, Any]]:
    """Decode ``data:`` lines into events; comments and keepalives are skipped."""
    for line in lines:
        if not line or not line.startswith("data: "):
            continue
        data = line[6:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


# ── Registry: one live turn per session ─────────────────────────────────────


class TurnRegistry:
    """Tracks this MCP server's turns and every prompt id it has been shown."""

    def __init__(self, backend_url: str, headers: Dict[str, str]) -> None:
        self.backend_url = backend_url
        self.headers = dict(headers)
        self._lock = threading.Lock()
        self._turns: Dict[str, TurnTracker] = {}
        self._prompt_owner: Dict[str, str] = {}

    # ── HTTP ──

    def _url(self, path: str) -> str:
        return f"{self.backend_url}/api{path}"

    def _detail(self, exc: Exception) -> str:
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "Cannot connect to GAIA backend. Is it running?"
        if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
            resp = exc.response
            try:
                body = resp.json()
                detail = body.get("detail") if isinstance(body, dict) else None
            except ValueError:
                detail = None
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail)
            text = str(detail or (resp.text or "")[:200] or f"HTTP {resp.status_code}")
            return text.replace(self.backend_url, "")
        return str(exc)[:200].replace(self.backend_url, "")

    def _call(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            r = requests.request(
                method.upper(),
                self._url(path),
                headers=self.headers,
                timeout=REQUEST_TIMEOUT_S,
                **kwargs,
            )
            r.raise_for_status()
            body = r.json()
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            return err(self._detail(e), http_status=code)
        except (requests.exceptions.RequestException, ValueError) as e:
            return err(self._detail(e))
        return body if isinstance(body, dict) else {"result": body}

    # ── turns ──

    def _remember_prompt(self, confirm_id: str, session_id: str) -> None:
        with self._lock:
            self._prompt_owner[confirm_id] = session_id
            while len(self._prompt_owner) > MAX_REMEMBERED_PROMPTS:
                self._prompt_owner.pop(next(iter(self._prompt_owner)))

    def get(self, session_id: str) -> Optional[TurnTracker]:
        with self._lock:
            return self._turns.get(session_id)

    def _new_tracker(self, session_id: str, origin: str) -> TurnTracker:
        return TurnTracker(session_id, origin, on_prompt=self._remember_prompt)

    def _consume(self, tracker: TurnTracker, method: str, path: str, **kw) -> None:
        try:
            resp = requests.request(
                method,
                self._url(path),
                headers=self.headers,
                stream=True,
                timeout=STREAM_TIMEOUT,
                **kw,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            tracker.finish("error", self._detail(e))
            tracker.opened.set()
            return
        tracker.opened.set()
        try:
            for event in iter_sse_events(resp.iter_lines(decode_unicode=True)):
                tracker.apply(event)
                if not tracker.live:
                    break
        except requests.exceptions.RequestException as e:
            tracker.finish("disconnected", f"Lost the turn's event stream: {e}")
        finally:
            resp.close()
        # The UI synthesizes a done here too; say so instead of passing it off
        # as a completed turn.
        tracker.finish(
            "disconnected",
            "The event stream closed before the turn reported done. "
            "Read get_messages for what the backend saved.",
        )

    def _spawn(self, tracker: TurnTracker, method: str, path: str, **kw) -> None:
        thread = threading.Thread(
            target=self._consume,
            args=(tracker, method, path),
            kwargs=kw,
            name=f"agent-ui-turn-{tracker.session_id[:8]}",
            daemon=True,
        )
        thread.start()

    def start(self, session_id: str, message: str) -> Dict[str, Any]:
        if not isinstance(session_id, str) or not session_id.strip():
            return err("session_id is required — create one with create_session.")
        if not isinstance(message, str) or not message.strip():
            return err("message is empty — pass the text to send.")
        with self._lock:
            current = self._turns.get(session_id)
            if current is not None and current.live:
                return err(
                    "A turn is already running in this session. Wait for it "
                    "with wait_for_turn(until='idle') or stop it with stop_turn.",
                    turn_id=current.turn_id,
                )
            tracker = self._new_tracker(session_id, "mcp")
            self._turns[session_id] = tracker
        self._spawn(
            tracker,
            "POST",
            "/chat/send",
            json={"session_id": session_id, "message": message, "stream": True},
        )
        if not tracker.opened.wait(START_ACK_TIMEOUT_S):
            return {
                "session_id": session_id,
                "turn_id": tracker.turn_id,
                "accepted": None,
                "detail": "The backend has not answered yet; poll turn_status.",
            }
        with tracker.cond:
            if tracker.outcome == "error" and tracker.event_count == 0:
                return err(tracker.error or "The backend refused the turn.")
            return {
                "session_id": session_id,
                "turn_id": tracker.turn_id,
                "accepted": True,
                "state": tracker.state(),
            }

    def _backend_running(self, session_id: str) -> Tuple[Optional[bool], str]:
        body = self._call("GET", "/chat/active")
        if body.get("status") == "error":
            return None, body["detail"]
        return session_id in (body.get("session_ids") or []), ""

    def ensure(self, session_id: str) -> Tuple[Optional[TurnTracker], str]:
        """The session's live turn, attaching to one started elsewhere.

        Returns ``(tracker_or_None, error)``. A finished tracker is returned
        when nothing newer is running, so its result stays readable.
        """
        current = self.get(session_id)
        if current is not None and current.live:
            return current, ""
        # The backend still lists a run for a moment after its done event;
        # attaching then would replay the turn we just watched end.
        if (
            current is not None
            and current.outcome in ("completed", "cancelled")
            and time.monotonic() - (current.ended_at or 0) < ATTACH_GRACE_S
        ):
            return current, ""
        running, problem = self._backend_running(session_id)
        if running is None:
            return None, problem
        if not running:
            return current, ""
        with self._lock:
            again = self._turns.get(session_id)
            if again is not None and again.live:
                return again, ""
            tracker = self._new_tracker(session_id, "attached")
            self._turns[session_id] = tracker
        self._spawn(tracker, "GET", "/chat/attach", params={"session_id": session_id})
        tracker.opened.wait(START_ACK_TIMEOUT_S)
        return tracker, ""

    # ── reads ──

    def status(self, session_id: str) -> Dict[str, Any]:
        tracker, problem = self.ensure(session_id)
        if problem:
            return err(problem)
        if tracker is None:
            return {
                "session_id": session_id,
                "state": "idle",
                "streaming": False,
                "turn_id": None,
                "detail": "No turn has run in this session since this MCP "
                "server started.",
            }
        with tracker.cond:
            return tracker.status()

    def _finished_or_live(self, session_id: str) -> Tuple[Optional[TurnTracker], str]:
        tracker = self.get(session_id)
        if tracker is None:
            return None, (
                "No turn tracked for this session. Start one with start_message, "
                "or call turn_status to attach to one running in the browser."
            )
        return tracker, ""

    def view(self, session_id: str) -> Dict[str, Any]:
        tracker, problem = self._finished_or_live(session_id)
        if tracker is None:
            return err(problem)
        with tracker.cond:
            return tracker.view()

    def metrics(self, session_id: str) -> Dict[str, Any]:
        tracker, problem = self._finished_or_live(session_id)
        if tracker is None:
            return err(problem)
        with tracker.cond:
            return tracker.metrics()

    def wait(
        self,
        session_id: str,
        until: str = "idle",
        text: str = "",
        timeout_ms: Any = DEFAULT_WAIT_MS,
    ) -> Dict[str, Any]:
        ms, problem = parse_timeout_ms(timeout_ms)
        if problem:
            return err(problem)
        if until not in WAIT_CONDITIONS:
            return err(
                f"Unknown condition {until!r}. Use one of: "
                f"{', '.join(WAIT_CONDITIONS)}."
            )
        if until == "text" and not (isinstance(text, str) and text):
            return err("until='text' needs the text to wait for in `text`.")
        tracker, problem = self.ensure(session_id)
        if problem:
            return err(problem)
        if tracker is None:
            if until in ("idle", "attention"):
                return {
                    "matched": True,
                    "until": until,
                    "elapsed_ms": 0,
                    "state": "idle",
                }
            return err("No turn is running in this session, so it cannot reach that.")

        def matched() -> bool:
            if until == "idle":
                return not tracker.live
            if until == "prompt":
                return tracker.pending_prompt is not None
            if until == "question":
                return tracker.pending_question is not None
            if until == "attention":
                return (
                    not tracker.live
                    or tracker.pending_prompt is not None
                    or tracker.pending_question is not None
                )
            return text in tracker.content()

        start = time.monotonic()
        deadline = start + ms / 1000.0
        with tracker.cond:
            while True:
                if matched():
                    return {
                        "matched": True,
                        "until": until,
                        "elapsed_ms": int((time.monotonic() - start) * 1000),
                        **tracker.status(),
                    }
                if not tracker.live:
                    return err(
                        f"The turn ended ({tracker.outcome}) before {until!r} was "
                        "reached.",
                        matched=False,
                        ended=True,
                        **tracker.status(),
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return err(
                        f"Timed out after {ms} ms waiting for {until!r}"
                        + (f" {text!r}" if until == "text" else "")
                        + f"; the turn is {tracker.state()}.",
                        matched=False,
                        timed_out=True,
                        **tracker.status(),
                    )
                tracker.cond.wait(min(remaining, 1.0))

    # ── controls ──

    def answer_prompt(
        self,
        session_id: str,
        confirm_id: str,
        decision: str,
        always_scope: str = "",
    ) -> Dict[str, Any]:
        """Answer a permission prompt this server saw, exactly as a click would.

        Refused locally -- before the backend is called -- when the id is
        missing, belongs to another session, was already answered, has been
        replaced or has expired, or when "always" names a scope other than the
        one the prompt offered.
        """
        if decision not in DECISIONS:
            return err(
                f"decision must be one of {', '.join(DECISIONS)}, got {decision!r}."
            )
        if not isinstance(confirm_id, str) or not confirm_id.strip():
            return err(
                "confirm_id is required: pass the pending_prompt.confirm_id from "
                "turn_status. A prompt is never answered blind."
            )
        with self._lock:
            owner = self._prompt_owner.get(confirm_id)
        if owner is not None and owner != session_id:
            return err(
                "That prompt belongs to a different session; answer it in the "
                "session that asked.",
                refused="wrong_session",
            )
        tracker = self.get(session_id)
        if tracker is None or owner is None:
            return err(
                "This server has not seen that prompt in this session. Read it "
                "with turn_status first.",
                refused="unknown_prompt",
            )
        with tracker.cond:
            if confirm_id in tracker.answered_prompt_ids:
                return err(
                    "That prompt was already answered.", refused="already_answered"
                )
            pending = tracker.pending_prompt
            if pending is None or pending["confirm_id"] != confirm_id:
                return err(
                    "That prompt is no longer waiting for an answer.",
                    refused="stale",
                    pending_prompt=tracker.prompt_view(),
                )
            timeout = pending.get("timeout_seconds")
            if isinstance(timeout, (int, float)) and (
                time.monotonic() > pending["received_at"] + timeout
            ):
                return err("That prompt has expired.", refused="expired")
            if decision == "always":
                offered = pending.get("always_scope")
                if not offered:
                    return err(
                        "This prompt does not offer 'always'; answer allow_once or deny.",
                        refused="always_not_offered",
                    )
                if always_scope != offered:
                    return err(
                        f"'always' must name the scope the prompt offered "
                        f"({offered!r}); got {always_scope!r}.",
                        refused="scope_mismatch",
                    )
            # Claimed before the POST so a concurrent second answer is refused.
            tracker.answered_prompt_ids.add(confirm_id)
        body = self._call(
            "POST",
            "/chat/confirm-tool",
            json={
                "session_id": session_id,
                "approved": decision != "deny",
                "always": decision == "always",
                "confirm_id": confirm_id,
            },
        )
        if body.get("status") == "error":
            if body.get("http_status") in (404, 409):
                body["refused"] = "backend_rejected"
            else:
                # Never delivered (backend down, 5xx): the prompt may still wait.
                with tracker.cond:
                    tracker.answered_prompt_ids.discard(confirm_id)
            return body
        with tracker.cond:
            if (
                tracker.pending_prompt
                and tracker.pending_prompt["confirm_id"] == confirm_id
            ):
                tracker.pending_prompt = None
            tracker.cond.notify_all()
        return {
            "status": "ok",
            "session_id": session_id,
            "confirm_id": confirm_id,
            "decision": decision,
            "granted": body.get("granted"),
        }

    def answer_question(
        self, session_id: str, request_id: str, value: str
    ) -> Dict[str, Any]:
        """Answer a mid-run question this server saw (``POST /chat/user-input``)."""
        if not isinstance(request_id, str) or not request_id.strip():
            return err("request_id is required: pass pending_question.request_id.")
        if not isinstance(value, str):
            return err("value must be a string.")
        tracker = self.get(session_id)
        if tracker is None:
            return err("No turn tracked for this session.", refused="unknown_question")
        with tracker.cond:
            q = tracker.pending_question
            if q is None or q["request_id"] != request_id:
                return err(
                    "That question is no longer waiting for an answer.",
                    refused="stale",
                )
        body = self._call(
            "POST",
            "/chat/user-input",
            json={"session_id": session_id, "request_id": request_id, "value": value},
        )
        if body.get("status") == "error":
            return body
        with tracker.cond:
            if (
                tracker.pending_question
                and tracker.pending_question["request_id"] == request_id
            ):
                tracker.pending_question = None
            tracker.cond.notify_all()
        return {"status": "ok", "session_id": session_id, "request_id": request_id}

    def stop(self, session_id: str) -> Dict[str, Any]:
        """Press Stop: the backend ends the run at its next check."""
        body = self._call("POST", "/chat/cancel", json={"session_id": session_id})
        if body.get("status") == "error":
            if body.get("http_status") == 404:
                return err("No turn is running in this session.")
            return body
        tracker = self.get(session_id)
        if tracker is not None:
            with tracker.cond:
                tracker.stop_requested = True
        return {
            "status": "ok",
            "session_id": session_id,
            "stop_requested": True,
            "detail": "Wait with wait_for_turn(until='idle'); turn_view then "
            "reports stopped=true if the stop ended the run.",
        }


# ── HTTP transport hardening ───────────────────────────────────────────────

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

#: Bearer token a non-loopback bind requires. Read from the environment only,
#: so it never sits in argv, where other local users can read it.
REMOTE_TOKEN_ENV = "GAIA_AGENT_UI_MCP_TOKEN"
MIN_TOKEN_CHARS = 32


class RemoteBindError(ValueError):
    """A non-loopback bind without the opt-in and token it requires."""


def is_loopback_host(host: str) -> bool:
    host = (host or "").strip().strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def remote_token(host: str, allow_remote: bool, token: Optional[str]) -> Optional[str]:
    """The token a bind to *host* requires, or None for loopback.

    This server can approve tool calls, so anything reachable from the
    network must be both explicitly opted into and authenticated.
    """
    if is_loopback_host(host):
        return None
    if not allow_remote:
        raise RemoteBindError(
            f"Refusing to listen on {host!r}: it is reachable from the network, "
            "and this server can approve tool calls. Bind to localhost, or pass "
            f"--allow-remote with a token of at least {MIN_TOKEN_CHARS} "
            f"characters in {REMOTE_TOKEN_ENV}."
        )
    token = (token or "").strip()
    if len(token) < MIN_TOKEN_CHARS:
        raise RemoteBindError(
            f"--allow-remote needs a bearer token of at least {MIN_TOKEN_CHARS} "
            f"characters in {REMOTE_TOKEN_ENV} (it is missing or too short). "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return token


class BearerTokenMiddleware:
    """ASGI guard for a remote bind: no browser origin, and the right token.

    The token is compared in constant time and never echoed: the refusal
    bodies are fixed strings.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode("utf-8")

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if b"origin" in headers:
            await _plain(send, 403, b"Browser requests are refused.")
            return
        given = headers.get(b"authorization", b"")
        if not given or not hmac.compare_digest(given, self._expected):
            await _plain(send, 401, b"Missing or invalid bearer token.", bearer=True)
            return
        await self.app(scope, receive, send)


async def _plain(send, status: int, body: bytes, bearer: bool = False) -> None:
    headers = [
        (b"content-type", b"text/plain"),
        (b"content-length", str(len(body)).encode()),
    ]
    if bearer:
        headers.append((b"www-authenticate", b"Bearer"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def transport_security_settings(host: str):
    """Loopback transport settings that refuse every browser-originated request.

    MCP clients send no ``Origin``; browsers always do on a cross-origin
    ``POST``. With no allowed origins, any page -- including one served from
    another localhost port -- is refused, so a site the user visits cannot
    answer a prompt. ``Host`` is pinned to loopback against DNS rebinding.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[],
    )


def remote_app(mcp, host: str, token: str):
    """The streamable-HTTP app for an opted-in remote bind, token-guarded.

    Host pinning is off because LAN clients address the machine by its own
    name or IP; the bearer token and the Origin refusal carry the guard.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
        host=host,
    )
    return BearerTokenMiddleware(app, token)
