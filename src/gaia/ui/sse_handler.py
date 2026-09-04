# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
SSE Output Handler - Bridges agent console events to Server-Sent Events.

Maps OutputHandler method calls (thinking, tool calls, steps, etc.)
to JSON events that the streaming endpoint sends to the frontend.
"""

import json
import logging
import math
import queue
import re
import socket
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

from gaia.agents.base.console import OutputHandler
from gaia.agents.base.tool_grants import grant_scope
from gaia.agents.base.tools import get_tool_display_label, get_tool_metadata
from gaia.agents.base.turn_metrics import turn_log_path
from gaia.ui.event_narration import DEBUG_CHANNEL, format_count

logger = logging.getLogger(__name__)

_SUMMARY_CHAR_CAP = 300

#: Seconds the agent thread waits for a tool-confirm response from the frontend.
TOOL_CONFIRM_TIMEOUT_SECONDS = 60

#: Sentinel for "the caller did not pass a timeout". Distinct from ``None``,
#: which is a caller explicitly asking to wait for the human indefinitely.
_USE_HANDLER_TIMEOUT = object()

# ``DEBUG_CHANNEL`` marks an event as harness bookkeeping rather than a
# description of the user's work (#2804). The emitter is the only layer that
# knows which it is, so it tags at the source; downstream
# (:mod:`gaia.ui.sse_translation`) decides whether to forward it.

#: ``start_progress`` labels the agent loop uses to say "I am alive" rather than
#: "here is what I am doing". Compared lower-cased and stripped of trailing
#: punctuation. Anything else — a goal, a real narration — passes through.
_HARNESS_PROGRESS_LABELS = frozenset(
    {"thinking", "working", "processing", "generating", "generating response"}
)

# ── Shared LLM output cleaning patterns ─────────────────────────────────
# These regexes are the canonical definitions for filtering LLM noise.
# Other consumers (MCP server, frontend safety nets) should import from here
# rather than duplicating the patterns.

# Regex to detect raw tool-call JSON that LLMs sometimes emit as text content.
# Matches patterns like:
#   {"tool": "search_file", "tool_args": {...}}
#   {"thought": "...", "goal": "...", "tool": "search_file", "tool_args": {...}}
# The leading .* allows optional fields (thought, goal, plan) before "tool".
_TOOL_CALL_JSON_RE = re.compile(
    r'^\s*\{.*["\s]*tool["\s]*:\s*"[^"]+"\s*,\s*["\s]*tool_args["\s]*:\s*\{.*\}\s*\}\s*$',
    re.DOTALL,
)

# Regex for use with re.sub() to strip tool-call JSON from mixed content.
# Unlike _TOOL_CALL_JSON_RE (which matches whole strings), this variant
# matches tool-call JSON embedded anywhere within larger text and uses
# [^}]* for inner args to avoid over-matching past the closing braces.
# Also handles unquoted tool names (malformed JSON from some LLM quantizations).
_TOOL_CALL_JSON_SUB_RE = re.compile(
    r'\s*\{\s*"?tool"?\s*:\s*"[^"]+"\s*,\s*"?tool_args"?\s*:\s*\{'
    r"[^{}]*(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^{}]*)*"
    r"\}\s*\}",
    re.DOTALL,
)

# Regex to remove {"thought": "..."} JSON blocks from LLM output.
_THOUGHT_JSON_SUB_RE = re.compile(r'\s*\{\s*"thought"\s*:\s*"[^"]*"[^}]*\}\s*')

# Regex to detect {"answer": "..."} JSON blocks from LLM output.
# These duplicate the already-streamed text content and should be stripped.
_ANSWER_JSON_RE = re.compile(r'\s*\{\s*"answer"\s*:\s*"', re.DOTALL)

# Regex for use with re.sub() to strip {"answer": "..."} JSON blobs embedded
# in content.  Used in print_final_answer to remove trailing JSON wrappers
# that some models append after their plain-text response.
# Handles escaped quotes (\") inside the answer string value.
_ANSWER_JSON_SUB_RE = re.compile(
    r'\s*\{\s*"answer"\s*:\s*"(?:[^"\\]|\\.)*"\s*\}', re.DOTALL
)

# Regex to remove <think>...</think> tags that some models output.
_THINK_TAG_SUB_RE = re.compile(r"<think>[\s\S]*?</think>")

# Regex to strip RAG/tool result JSON blobs that Qwen3 sometimes leaks into
# its text output. Pattern: {"status": "success", ..., "chunks": [...], ...}
# or {"chunks": [...], "scores": [...]} — these are tool results, not LLM prose.
# We strip them to avoid corrupting the DB-stored assistant message with raw
# JSON that downstream turns will misread as factual content.
# Note: chunks array contains nested objects like [{"text":"...", "score":...}]
# so we use [\s\S]*? with a lookahead to stop at the outer closing brace.
_RAG_RESULT_JSON_SUB_RE = re.compile(
    r'[}\s`]*\{[^{}]*"chunks"\s*:\s*\[[\s\S]*?\][^{}]*\}[}\s`]*',
    re.DOTALL,
)

# Regex to remove trailing unclosed code fences (``` at end of response).
_TRAILING_CODE_FENCE_RE = re.compile(r"\n?```\s*$")


def _strip_balanced_json_blobs(text: str, needle: "re.Pattern") -> str:
    """Remove every top-level ``{...}`` JSON object whose body matches *needle*.

    Brace- and string-aware: it walks the text tracking quote/escape state so a
    ``{`` or ``}`` inside a JSON string never miscounts depth, and it only
    removes *complete* balanced objects. This is why it is safe to run against
    arbitrary LLM prose — an unbalanced or non-matching object is left intact.
    """
    if not text:
        return text
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            out.append(text[i])
            i += 1
            continue
        depth, j, in_str, esc = 0, i, False, False
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        blob = text[i:j]
        if depth == 0 and needle.search(blob):
            i = j  # drop a complete, matching object
            continue
        out.append(blob)
        i = j
    return "".join(out)


def _peel_response_socket(resp: Any) -> Optional[socket.socket]:
    """Dig the live TCP socket out of a ``requests`` streamed response.

    Needed by the email-relay cancel path (#2109): only ``socket.shutdown()``
    can wake a reader thread parked in a blocking ``recv`` — ``resp.close()``
    waits on the buffered reader's lock, which that parked read holds.

    Tries the known attribute chains across urllib3 v1/v2; returns ``None``
    for anything that isn't a real socket-backed response (mocks, already
    -released connections), letting the caller fall back to ``close()``.
    """
    raw = getattr(resp, "raw", None)
    if raw is None:
        return None
    for conn_attr in ("connection", "_connection"):
        sock = getattr(getattr(raw, conn_attr, None), "sock", None)
        if isinstance(sock, socket.socket):
            return sock
    # http.client.HTTPResponse -> BufferedReader -> SocketIO -> socket
    rawio = getattr(getattr(getattr(raw, "_fp", None), "fp", None), "raw", None)
    sock = getattr(rawio, "_sock", None)
    return sock if isinstance(sock, socket.socket) else None


class SSEOutputHandler(OutputHandler):
    """
    OutputHandler that queues agent events as JSON for SSE streaming.

    Each console method call becomes a typed event pushed to a queue.
    The streaming endpoint reads from this queue and yields SSE events.
    """

    blocking_confirmation = True

    #: How long to wait for a decision. A host that can actually deliver one
    #: raises this — a person reading a prompt routinely takes longer than a
    #: minute — but must not set it to ``None``: an unanswerable question with
    #: no bound is a turn that never ends, which reads as a hang rather than as
    #: the refusal it effectively is. See ``gaia_agent.stdio.PermissionState``,
    #: whose backstop deliberately outlasts its client's own bound.
    #:
    #: Class-level so a handler built with ``__new__`` — which callers that only
    #: need the confirmation gate do, to skip the queue setup — still has a
    #: usable value.
    confirm_timeout_seconds: Optional[float] = TOOL_CONFIRM_TIMEOUT_SECONDS

    #: The tool the live prompt is for, so an "always" answer grants the right
    #: one without the responder having to name it.
    _confirm_tool: Optional[str] = None

    #: The arguments of the live prompt, so an "always" answer grants the scope
    #: the user was actually shown rather than the whole tool.
    _confirm_args: Optional[Dict[str, Any]] = None

    def __init__(self, background_mode: bool = False):
        self.event_queue: queue.Queue = queue.Queue()
        self.cancelled = threading.Event()
        self._start_time: Optional[float] = None
        self._step_count = 0
        self._tool_count = 0
        self._last_tool_name: Optional[str] = None
        self._stream_buffer = ""  # Buffer to detect and filter tool-call JSON
        self._in_thinking = False  # True while inside a <think>...</think> block
        self._json_filtered = False  # True after a JSON block was suppressed; used to eat trailing } artifacts
        # Tool confirmation state (blocking until frontend responds)
        self._confirm_lock = threading.Lock()
        self._confirm_event: Optional[threading.Event] = None
        self._confirm_result: bool = False
        self._confirm_id: Optional[str] = None
        self._confirm_tool = None
        self._confirm_args = None
        self._tool_start_time: Optional[float] = None
        # Autonomous loop support
        # background_mode=True: skip blocking user confirmation; immediately deny.
        self.background_mode: bool = background_mode
        # Directive written by set_loop_state tool; read by AgentLoop after the run.
        self.loop_state_directive: Optional[Dict[str, Any]] = None
        # User input request queue (ordered, multi-slot keyed by request_id).
        self._user_input_queue: deque = deque()
        self._user_input_events: Dict[str, threading.Event] = {}
        self._user_input_results: Dict[str, str] = {}
        # Guards the three maps above. resolve_user_input() runs on a request
        # thread while the agent thread is timing out on the same request_id;
        # unsynchronized, that window lets a caller be told "accepted" for an
        # answer the run has already stopped waiting for.
        self._user_input_lock = threading.Lock()
        # Live response for an in-flight email /query relay (#2109), so the
        # cancel path can force a blocked read to error out by closing it from
        # another thread. None outside an active email-relay turn.
        self.active_relay_response: Optional[Any] = None
        # Sealed turn record for the turn in flight, stashed by
        # print_turn_metrics and consumed by the next print_final_answer.
        self._turn_metrics: Optional[Dict[str, Any]] = None

    def _emit(self, event: Dict[str, Any]):
        """Push an event to the queue for SSE delivery."""
        self.event_queue.put(event)

    def _elapsed(self) -> float:
        if self._start_time is None:
            return 0.0
        return round(time.time() - self._start_time, 2)

    # === Core Progress/State Methods ===

    def print_processing_start(self, query: str, max_steps: int, model_id: str = None):
        self._start_time = time.time()
        self._step_count = 0
        self._tool_count = 0
        model_label = model_id or "LLM"
        self._emit(
            {
                "type": "status",
                "status": "working",
                "message": f"Processing with {model_label}...",
                "channel": DEBUG_CHANNEL,
            }
        )

    def print_step_header(self, step_num: int, step_limit: int):
        self._step_count = step_num
        self._emit(
            {
                "type": "step",
                "step": step_num,
                "total": step_limit,
                "status": "started",
                "channel": DEBUG_CHANNEL,
            }
        )

    def print_state_info(self, state_message: str):
        # Suppress internal agent state labels (PLANNING, DIRECT EXECUTION, etc.)
        # — they duplicate the thinking step that immediately follows.
        pass

    def print_thought(self, thought: str):
        self._emit(
            {
                "type": "thinking",
                "content": thought,
            }
        )

    def print_goal(self, goal: str):
        # Goals are less important than thoughts - emit as status
        # so they don't create redundant "thinking" steps in the UI.
        if goal:
            self._emit(
                {
                    "type": "status",
                    "status": "working",
                    "message": goal,
                }
            )

    def print_plan(self, plan: List[Any], current_step: int = None):
        # Convert plan items to strings for JSON serialization
        plan_strs = []
        for step in plan:
            if isinstance(step, dict):
                if "tool" in step:
                    args_str = ""
                    if step.get("tool_args"):
                        args_str = " — " + ", ".join(
                            f"{k}={v!r}" for k, v in step["tool_args"].items()
                        )
                    plan_strs.append(f"{step['tool']}{args_str}")
                else:
                    plan_strs.append(json.dumps(step))
            else:
                plan_strs.append(str(step))

        self._emit(
            {
                "type": "plan",
                "steps": plan_strs,
                "current_step": current_step,
            }
        )

    # === Tool Execution Methods ===

    def print_tool_usage(self, tool_name: str):
        self._tool_count += 1
        self._last_tool_name = tool_name
        self._tool_start_time = time.monotonic()
        # Prefer an explicit display label from the tool registry; fall back
        # to the legacy description map in this module when none is provided.
        detail_label = get_tool_display_label(tool_name) or _tool_description(tool_name)
        event = {
            "type": "tool_start",
            "tool": tool_name,
            "detail": detail_label,
        }
        # Attach MCP server name if this is an MCP tool.
        # _mcp_server is set by MCPTool.to_gaia_format() during registration
        # in MCPClientMixin._register_mcp_tools() (see mcp/client/mcp_client.py).
        meta = get_tool_metadata(tool_name)
        if meta:
            mcp_server = meta.get("_mcp_server")
            if mcp_server:
                event["mcp_server"] = mcp_server
        self._emit(event)

    def print_tool_complete(self):
        self._tool_start_time = None  # Reset in case tool_result was skipped
        self._emit(
            {
                "type": "tool_end",
                "success": True,
            }
        )

    def pretty_print_json(self, data: Dict[str, Any], title: str = None):
        # When title is "Arguments", emit tool args as a detail update
        # so the frontend can show what the tool was called with.
        if title == "Arguments" and isinstance(data, dict):
            detail = _format_tool_args(self._last_tool_name, data)
            self._emit(
                {
                    "type": "tool_args",
                    "tool": self._last_tool_name,
                    "args": data,
                    "detail": detail,
                }
            )
            return

        # Render-map fix (#2109): for tools registered in _RENDER_TOOL_TO_LANG,
        # carry the card payload as result_data — the /query canonical
        # translator forwards result_data verbatim as tool_result.data, so the
        # frontend renders the typed card straight from the event instead of
        # depending on the LLM echoing it into the final answer (the former
        # fence-injection hack, #1000, removed here).
        render_card = self._render_card_payload(data)

        # For tool results, provide a detailed summary
        summary = _summarize_tool_result(data)
        event = {
            "type": "tool_result",
            "title": title,
            "summary": summary,
            "success": (
                data.get("status") != "error" if isinstance(data, dict) else True
            ),
        }
        # String-returning tools (notably email envelopes) are summarized by a
        # hard character cap. Tell downstream classifiers that an unparsable
        # summary may be incomplete instead of making them infer truncation.
        if not isinstance(data, dict) and len(str(data)) > _SUMMARY_CHAR_CAP:
            event["summary_truncated"] = True

        # Attach latency for tool calls (measured from print_tool_usage)
        if self._tool_start_time is not None:
            latency_ms = round((time.monotonic() - self._tool_start_time) * 1000, 1)
            event["latency_ms"] = latency_ms
            self._tool_start_time = None

        # For command execution results, include structured output data
        # so the frontend can render a proper terminal view
        if (
            isinstance(data, dict)
            and "command" in data
            and ("stdout" in data or "stderr" in data)
        ):
            event["command_output"] = {
                "command": data.get("command", ""),
                "stdout": data.get("stdout", ""),
                "stderr": data.get("stderr", ""),
                "return_code": data.get("return_code", 0),
                "cwd": data.get("cwd", ""),
                "duration_seconds": data.get("duration_seconds"),
                "truncated": data.get("output_truncated", False),
            }

        # For file search results, include structured file list
        if isinstance(data, dict) and ("files" in data or "file_list" in data):
            files = data.get("file_list", data.get("files", []))
            if isinstance(files, list):
                # Keep the UI contract honest: "total" should never claim more accessible
                # files than we actually include in the event payload.
                limited_files = files[:20]
                event["result_data"] = {
                    "type": "file_list",
                    "files": limited_files,  # Limit to 20 files
                    "total": len(limited_files),
                }

        # For search results with chunks, include structured chunk data
        # so the frontend can render expandable chunk cards
        if isinstance(data, dict) and "chunks" in data:
            chunks = data.get("chunks", [])
            if isinstance(chunks, list):
                structured_chunks = []
                for c in chunks[:8]:  # Limit to 8 chunks max
                    if isinstance(c, dict):
                        structured_chunks.append(
                            {
                                "id": c.get("chunk_id", 0),
                                "source": (
                                    Path(c["source_file"]).name
                                    if c.get("source_file")
                                    else None
                                ),
                                "sourcePath": c.get("source_file", ""),
                                "page": c.get("page"),
                                "score": (
                                    round(c.get("relevance_score", 0), 2)
                                    if c.get("relevance_score")
                                    else None
                                ),
                                "preview": (c.get("content", "") or "")[:150],
                                "content": (c.get("content", "") or "")[:800],
                            }
                        )
                    else:
                        structured_chunks.append(
                            {
                                "id": len(structured_chunks) + 1,
                                "preview": str(c)[:150],
                                "content": str(c)[:800],
                            }
                        )
                event["result_data"] = {
                    "type": "search_results",
                    "count": len(chunks),
                    "source_files": data.get("source_files", []),
                    "chunks": structured_chunks,
                }

        # The render-map card (if any) is authoritative for a registered tool —
        # set last so it always wins over the generic result_data shapes above.
        if render_card is not None:
            event["result_data"] = render_card

        self._emit(event)

    # === Status Messages ===

    def print_error(self, error_message: str, recoverable: bool = False):
        event: Dict[str, Any] = {
            "type": "agent_error",
            "content": str(error_message) if error_message else "Unknown error",
        }
        # Only set when True: keeps the wire shape unchanged for every
        # existing (fatal) caller, and lets a downstream translator treat a
        # missing/False flag as the pre-#2515 terminal default.
        if recoverable:
            event["recoverable"] = True
        self._emit(event)

    def print_warning(self, warning_message: str):
        self._emit(
            {
                "type": "status",
                "status": "warning",
                "message": warning_message,
            }
        )

    def print_info(self, message: str):
        self._emit(
            {
                "type": "status",
                "status": "info",
                "message": message,
            }
        )

    # === Progress Indicators ===

    def start_progress(self, message: str):
        # Filter redundant "Executing <tool_name>" progress messages -
        # these just echo the tool name which the frontend already shows.
        if message and message.lower().startswith("executing "):
            return
        # Emit as status (not thinking — thinking is reserved for LLM reasoning)
        text = message or "Working"
        event: Dict[str, Any] = {
            "type": "status",
            "status": "working",
            "message": text,
        }
        if text.strip().lower().rstrip(".…") in _HARNESS_PROGRESS_LABELS:
            event["channel"] = DEBUG_CHANNEL
        self._emit(event)

    def stop_progress(self):
        pass  # No-op for SSE - frontend manages its own spinners

    # === Structured-render map (#2109) ===

    # Mapping from tool name to the card "kind" the frontend's render-card
    # registry draws (spec §4.2). Shared with the sidecar's own canonical
    # translator (``gaia_agent_email/sse_translation.py`` duplicates this map
    # to stay dependency-light) — a test in that package
    # (``test_render_tool_to_lang_maps_stay_in_sync``) pins the two dicts
    # equal so the duplication can't silently drift.
    _RENDER_TOOL_TO_LANG: ClassVar[Dict[str, str]] = {
        "pre_scan_inbox": "email_pre_scan",
        # #2765: a generic ``table`` card (no new client code) so the thread
        # view renders straight from tool data instead of model prose.
        "get_thread": "table",
    }

    def _render_card_payload(self, data: Any) -> Optional[Dict[str, Any]]:
        """Return the render-map card payload for the last-called tool, if any.

        ``@tool``-decorated functions return JSON strings (the dispatch in
        ``Agent._execute_tool`` returns them verbatim), so both string and
        dict envelope shapes are accepted here. Returns ``None`` — never
        raises — on any non-matching or malformed payload: a foreign tool, a
        failed (``ok=false``) envelope, or a ``kind`` that doesn't match the
        registered tool.
        """
        tool = self._last_tool_name or ""
        lang = self._RENDER_TOOL_TO_LANG.get(tool)
        if not lang:
            return None
        envelope: Dict[str, Any]
        if isinstance(data, dict):
            envelope = data
        elif isinstance(data, str):
            try:
                parsed = json.loads(data)
            except (ValueError, TypeError):
                return None
            if not isinstance(parsed, dict):
                return None
            envelope = parsed
        else:
            return None
        if not envelope.get("ok"):
            return None
        inner = envelope.get("data")
        if not isinstance(inner, dict) or inner.get("kind") != lang:
            return None
        return inner

    # === Completion Methods ===

    def print_turn_metrics(self, record: Dict[str, Any]):
        """Stash the sealed turn record for the answer event that follows."""
        self._turn_metrics = record

    def print_final_answer(
        self,
        answer: str,
        streaming: bool = True,  # pylint: disable=unused-argument
        total_tokens: Optional[int] = None,
        ttft_seconds: Optional[float] = None,
    ):
        if answer:
            answer = _THINK_TAG_SUB_RE.sub("", answer)
            # Extract answer text from {"thought":..., "answer":...} JSON before
            # the regex cleaners run.  _THOUGHT_JSON_SUB_RE would otherwise strip
            # the entire blob (including the answer value) leaving an empty string.
            answer = _clean_answer_json(answer.strip())
            # Strip any trailing {"answer": "..."} JSON blob that some models
            # append to their plain-text response.
            answer = _ANSWER_JSON_SUB_RE.sub("", answer)
            answer = _RAG_RESULT_JSON_SUB_RE.sub("", answer)
            answer = _TOOL_CALL_JSON_SUB_RE.sub("", answer)
            answer = _THOUGHT_JSON_SUB_RE.sub("", answer)
            answer = answer.strip()
        event: Dict[str, Any] = {
            "type": "answer",
            "content": _fix_double_escaped(answer) if answer else answer,
            "elapsed": self._elapsed(),
            "steps": self._step_count,
            "tools_used": self._tool_count,
        }
        # Omit entirely when no real count exists — never emit a fake zero.
        # `_sum_conversation_tokens` returns 0 both when a real turn generated
        # zero output tokens (never happens for a completed answer) and when
        # no per-step stats were collected at all (the common "no real count"
        # case) — it can't tell the two apart, so treat <= 0 as unavailable,
        # same as the ttft guard below.
        if total_tokens is not None and total_tokens > 0:
            event["tokens"] = total_tokens
        # Same omit-don't-fake rule for ttft: real value from Lemonade's own
        # generation timing, or nothing.
        if (
            ttft_seconds is not None
            and math.isfinite(ttft_seconds)
            and ttft_seconds > 0
        ):
            event["ttft"] = round(ttft_seconds, 3)
        # Dev-mode only. Gated on the same env var that produced the record, so
        # an ordinary turn's payload stays byte-identical to before this existed.
        record, self._turn_metrics = self._turn_metrics, None
        if record is not None and turn_log_path() is not None:
            event["metrics"] = record
        self._emit(event)

    def print_repeated_tool_warning(self):
        self._emit(
            {
                "type": "status",
                "status": "warning",
                "message": "Detected repetitive tool call pattern. Execution paused.",
            }
        )

    def print_completion(self, steps_taken: int, steps_limit: int):
        self._emit(
            {
                "type": "status",
                "status": "complete",
                "message": f"Completed in {steps_taken} steps",
                "steps": steps_taken,
                "elapsed": self._elapsed(),
            }
        )

    def print_step_paused(self, description: str):
        pass  # Not relevant for web UI

    def print_command_executing(self, command: str):
        self._emit(
            {
                "type": "tool_start",
                "tool": "run_shell_command",
                "detail": command,
            }
        )

    def print_agent_selected(self, agent_name: str, language: str, project_type: str):
        self._emit(
            {
                "type": "status",
                "status": "info",
                "message": f"Agent: {agent_name}",
            }
        )

    def print_agent_created(self, agent_id: str) -> None:
        """Notify the frontend that a new agent is available in the registry."""
        self._emit({"type": "agent_created", "agent_id": agent_id})

    # === Optional Methods (with SSE-friendly implementations) ===

    def print_streaming_text(self, text_chunk: str, end_of_stream: bool = False):
        if text_chunk:
            # Buffer text to detect and suppress raw tool-call JSON that
            # LLMs sometimes emit as text content before the tool is invoked.
            self._stream_buffer += text_chunk

            # ── Handle <think>...</think> blocks ──────────────────────
            # Route thinking content to thinking events, keep remainder
            # in buffer for normal tool-call filtering below.
            while "<think>" in self._stream_buffer or self._in_thinking:
                if self._in_thinking:
                    # We're inside a thinking block — look for closing tag
                    close_idx = self._stream_buffer.find("</think>")
                    if close_idx >= 0:
                        thinking_text = self._stream_buffer[:close_idx].strip()
                        if thinking_text:
                            self._emit({"type": "thinking", "content": thinking_text})
                        self._stream_buffer = self._stream_buffer[
                            close_idx + len("</think>") :
                        ]
                        self._in_thinking = False
                        continue  # Check for more <think> blocks
                    else:
                        # Still inside thinking — emit partial and wait
                        if self._stream_buffer.strip():
                            self._emit(
                                {"type": "thinking", "content": self._stream_buffer}
                            )
                        self._stream_buffer = ""
                        return
                else:
                    # Not in thinking — look for opening tag
                    open_idx = self._stream_buffer.find("<think>")
                    if open_idx >= 0:
                        # Emit any text before <think> as regular content,
                        # stripping thought/tool-call JSON artifacts that the
                        # model sometimes outputs before its think block.
                        before = self._stream_buffer[:open_idx]
                        before = _THOUGHT_JSON_SUB_RE.sub("", before)
                        before = _TOOL_CALL_JSON_SUB_RE.sub("", before)
                        if before.strip():
                            self._json_filtered = False
                            self._emit({"type": "chunk", "content": before})
                        else:
                            self._json_filtered = True
                        self._stream_buffer = self._stream_buffer[
                            open_idx + len("<think>") :
                        ]
                        self._in_thinking = True
                        continue
                    else:
                        break  # No more <think> tags

            # If buffer is empty after thinking extraction, nothing left to do
            if not self._stream_buffer:
                return

            stripped = self._stream_buffer.strip()

            # Case 0: Buffer starts with "{" — hold until we can identify the
            # JSON type (tool call vs final answer).  The LLM outputs either
            # {"tool": ..., "tool_args": {...}} or {"thought": ..., "answer": ...}.
            # We MUST see "tool" or "answer" before routing to Case 1/1b.
            # Releasing early (e.g., on "thought") causes partial JSON to leak
            # as text chunks and then get stripped by _THOUGHT_JSON_SUB_RE,
            # producing an empty response.
            # Hold limit: 8 KB for proper JSON objects ({"...}), 30 bytes for
            # curly braces in plain text (e.g. "Use {var} in your code").
            _looks_like_json_obj = bool(re.match(r'^\{\s*"', stripped))
            _hold_limit = 8192 if _looks_like_json_obj else 30
            if (
                stripped.startswith("{")
                and '"tool"' not in stripped
                and '"answer"' not in stripped
                and not end_of_stream
                and len(stripped) < _hold_limit
            ):
                return  # Wait for more tokens

            # Case 1: Buffer starts with "{" and has "tool" — pure JSON accumulation
            if stripped.startswith("{") and '"tool"' in stripped:
                if len(self._stream_buffer) > 2048:
                    self._emit({"type": "chunk", "content": self._stream_buffer})
                    self._stream_buffer = ""
                    self._json_filtered = False
                    return
                if stripped.endswith("}"):
                    if _TOOL_CALL_JSON_RE.match(stripped):
                        logger.debug("Filtered tool-call JSON: %s", stripped[:100])
                        self._stream_buffer = ""
                        self._json_filtered = True
                        return
                    # Also handle compound patterns where "tool"/"tool_args" are
                    # preceded by "thought"/"goal" keys, e.g.:
                    #   {"thought": "...", "goal": "...", "tool": "x", "tool_args": {...}}
                    cleaned = _TOOL_CALL_JSON_SUB_RE.sub("", stripped)
                    cleaned = _THOUGHT_JSON_SUB_RE.sub("", cleaned).strip()
                    if not cleaned:
                        logger.debug(
                            "Filtered compound tool-call JSON: %s", stripped[:100]
                        )
                        self._stream_buffer = ""
                        return
                    self._emit({"type": "chunk", "content": cleaned})
                    self._stream_buffer = ""
                    self._json_filtered = False
                # If end_of_stream, fall through to the flush block below
                # instead of returning (otherwise the buffer is never flushed).
                if not end_of_stream:
                    return

            # Case 1b: Buffer starts with "{" and has "answer" — raw JSON answer
            # The LLM sometimes emits {"answer": "..."} as the entire response.
            # Extract the answer text and emit it so the frontend can stream it.
            elif stripped.startswith("{") and '"answer"' in stripped:
                if stripped.endswith("}"):
                    answer_text = _clean_answer_json(stripped)
                    if answer_text and answer_text != stripped:
                        # Extracted answer text — emit as answer event
                        logger.debug(
                            "Extracted answer from JSON (%d chars): %s",
                            len(answer_text),
                            answer_text[:100],
                        )
                        self._emit({"type": "answer", "content": answer_text})
                    else:
                        logger.debug("Filtered answer JSON: %s", stripped[:100])
                    self._stream_buffer = ""
                    self._json_filtered = True
                    return
                if len(self._stream_buffer) > 4096:
                    # Safety: don't buffer forever
                    self._stream_buffer = ""
                    self._json_filtered = True
                    return
                if not end_of_stream:
                    return

            # Case 2: Buffer has "answer" embedded after normal text
            # e.g., "...some text. {"answer": "duplicated text..."}"
            # Strip the JSON portion, emit only the text before it.
            elif '"answer"' in stripped and '{"answer"' in self._stream_buffer:
                json_idx = self._stream_buffer.find('{"answer"')
                if json_idx >= 0:
                    text_before = self._stream_buffer[:json_idx].rstrip()
                    if text_before:
                        self._emit({"type": "chunk", "content": text_before})
                    # Buffer the JSON part — discard when complete
                    json_part = self._stream_buffer[json_idx:]
                    json_stripped = json_part.strip()
                    if json_stripped.endswith("}"):
                        logger.debug(
                            "Filtered embedded answer JSON: %s", json_stripped[:100]
                        )
                        self._stream_buffer = ""
                        self._json_filtered = True
                    else:
                        self._stream_buffer = json_part  # Keep buffering
                    return

            # Case 3: Buffer has "tool" embedded after normal text (e.g., "I'll help.\n{"tool":...")
            # Suppress the planning text before the JSON (system prompt forbids pre-tool
            # reasoning text) and discard the tool-call JSON itself.
            elif '"tool"' in stripped and '{"tool"' in self._stream_buffer:
                json_idx = self._stream_buffer.find('{"tool"')
                if json_idx > 0:
                    # Suppress text_before — it's pre-tool planning text that the system
                    # prompt explicitly forbids ("NEVER output planning text before a tool call").
                    # The tool will execute and its result will be shown instead.
                    json_part = self._stream_buffer[json_idx:]
                    self._stream_buffer = json_part
                    # Check if the JSON part is complete
                    json_stripped = json_part.strip()
                    if json_stripped.endswith("}"):
                        if _TOOL_CALL_JSON_RE.match(json_stripped):
                            logger.debug(
                                "Filtered embedded tool-call JSON (and preceding planning text): %s",
                                json_stripped[:100],
                            )
                            self._stream_buffer = ""
                            self._json_filtered = True
                            return
                        # JSON didn't match tool-call pattern — emit it as content
                        self._emit({"type": "chunk", "content": json_part})
                        self._stream_buffer = ""
                        self._json_filtered = False
                    return

            # Case 3.5: Buffer contains "chunks" — RAG tool-result JSON leaking
            # into the response stream.  Strip it out and emit the clean text.
            elif '"chunks"' in stripped:
                cleaned = _RAG_RESULT_JSON_SUB_RE.sub("", self._stream_buffer).strip()
                if cleaned:
                    self._emit({"type": "chunk", "content": cleaned})
                self._stream_buffer = ""
                return

            # Not tool-call JSON — emit the buffered content.
            # Suppress bare closing-brace artifacts (e.g. "}" or "}}") that appear
            # immediately after a JSON block was filtered — these are structural
            # remnants of JSON wrappers, not real text content.
            if self._json_filtered and re.match(r"^[\s}]+$", stripped):
                logger.debug("Suppressed JSON artifact: %r", stripped)
                self._stream_buffer = ""
                return
            self._json_filtered = False
            self._emit({"type": "chunk", "content": self._stream_buffer})
            self._stream_buffer = ""

        if end_of_stream and self._stream_buffer:
            # Flush any remaining buffer at end of stream
            stripped = self._stream_buffer.strip()
            is_json_fragment = bool(re.match(r"^[\s}]+$", stripped))
            if (
                not _TOOL_CALL_JSON_RE.match(stripped)
                and not _ANSWER_JSON_RE.search(stripped)
                and not is_json_fragment
            ):
                self._emit({"type": "chunk", "content": self._stream_buffer})
            self._stream_buffer = ""

    # === Tool Confirmation (blocking) ===

    def confirm_tool_execution(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        timeout: Optional[float] = _USE_HANDLER_TIMEOUT,
    ) -> bool:
        """Block the agent thread until the user approves or denies a tool call.

        Emits a ``permission_request`` SSE event so the frontend can show a modal
        and waits for ``resolve_tool_confirmation()``. Returns ``True`` if the
        user allows, ``False`` otherwise.

        *timeout* defaults to ``self.confirm_timeout_seconds``. ``None`` waits
        indefinitely and no host sets it: a prompt the user never saw then
        produces a turn nothing can end, which is worse than either answer. A
        host with a live decision channel gives itself a generous bound instead
        — long enough that reading is never a way to lose the call, finite so
        the turn always ends saying why. Expiry denies; it never approves.

        The wait is interruptible either way — ``cancelled`` breaks the loop, so
        Ctrl+C and a closed stdin both end the run before any bound is reached.
        """
        if timeout is _USE_HANDLER_TIMEOUT:
            timeout = self.confirm_timeout_seconds

        # Bypass and prior "always" grants are checked before anything is
        # emitted: neither has a question to ask, so putting a modal up would be
        # theatre. Checked per call, so toggling bypass mid-run takes effect on
        # the very next gated tool.
        if self.auto_approve_confirmations_enabled():
            self.log_auto_approval(tool_name)
            self._emit(
                {
                    "type": "status",
                    "status": "warning",
                    "message": (
                        f"Bypass permissions is ON — ran '{tool_name}' without "
                        "asking."
                    ),
                }
            )
            return True

        if self.call_is_granted(tool_name, tool_args):
            self._last_denial = None
            logger.info(
                "Confirmation-gated call to '%s' pre-approved for this session "
                "by the user",
                tool_name,
            )
            return True

        # Background mode: immediately deny — no semaphore hold, no waiting.
        if self.background_mode:
            unattended_message = (
                f"'{tool_name}' requires live user approval and cannot run "
                "unattended. Use request_user_input() to notify the user, "
                "then retry in a subsequent turn."
            )
            self._emit(
                {
                    "type": "tool_confirm_denied",
                    "tool": tool_name,
                    "reason": "unattended",
                    "message": unattended_message,
                }
            )
            logger.info(
                "Background mode: immediately denied confirmation for '%s'", tool_name
            )
            self._last_denial = (tool_name, unattended_message)
            return False

        confirm_id = str(uuid.uuid4())
        with self._confirm_lock:
            self._confirm_event = threading.Event()
            self._confirm_result = False
            self._confirm_id = confirm_id
            self._confirm_tool = tool_name
            self._confirm_args = tool_args if isinstance(tool_args, dict) else {}
        self._last_denial = None

        request: Dict[str, Any] = {
            "type": "permission_request",
            "tool": tool_name,
            "args": tool_args,
            "confirm_id": confirm_id,
        }
        # Present only when an "always" answer would create a grant, and it
        # names exactly what that grant covers. A front-end must not offer the
        # choice without it: an unqualified "always" is read as far broader
        # than the invocation-scoped grant actually on offer.
        scope = grant_scope(tool_name, tool_args)
        if scope is not None:
            request["always_scope"] = scope.label
        # Advertised only when it is real. A front-end told "60 s" that then
        # never expires runs a countdown to nothing; one told nothing correctly
        # renders a prompt that waits.
        if timeout is not None:
            request["timeout_seconds"] = timeout
        self._emit(request)

        # Poll in short intervals so cancellation is detected promptly.
        deadline = None if timeout is None else time.monotonic() + timeout
        answered = False
        while deadline is None or time.monotonic() < deadline:
            if self.cancelled.is_set():
                self._clear_pending_confirmation()
                self._last_denial = (
                    tool_name,
                    f"Confirmation for '{tool_name}' was abandoned: the run was "
                    "cancelled before the user answered.",
                )
                return False
            if self._confirm_event.wait(timeout=0.5):
                answered = True
                break

        if not answered:
            self._emit(
                {
                    "type": "status",
                    "status": "warning",
                    "message": f"Confirmation for '{tool_name}' timed out ({timeout:g} s). Execution denied.",
                }
            )
            logger.warning("Tool confirmation timed out for '%s'", tool_name)
            self._clear_pending_confirmation()
            self._last_denial = (
                tool_name,
                f"Confirmation for '{tool_name}' timed out after "
                f"{timeout:g} s with no user response. "
                "Execution denied.",
            )
            return False

        result = self._confirm_result
        self._clear_pending_confirmation()
        if not result:
            self._last_denial = (
                tool_name,
                f"Tool '{tool_name}' was denied by the user.",
            )
        return result

    def _clear_pending_confirmation(self) -> None:
        """Drop the live prompt's slot so a later answer cannot resolve it."""
        with self._confirm_lock:
            self._confirm_id = None
            self._confirm_event = None
            self._confirm_tool = None
            self._confirm_args = None

    def print_policy_alert(
        self,
        tool_name: str,
        decision: str,
        reason: str,
        rule_ids: List[str],
        policy_version: str,
        receipt_id: Optional[str] = None,
    ) -> None:
        """Emit a policy alert event for a governance-blocked tool call."""
        event: Dict[str, Any] = {
            "type": "policy_alert",
            "tool": tool_name,
            "decision": decision,
            "reason": reason,
            "rule_ids": list(rule_ids),
            "policy_version": policy_version,
        }
        if receipt_id is not None:
            event["receipt_id"] = receipt_id
        self._emit(event)

    def resolve_tool_confirmation(
        self,
        approved: bool,
        always: bool = False,
        confirm_id: Optional[str] = None,
    ) -> bool:
        """Unblock the agent thread waiting in ``confirm_tool_execution()``.

        Called by the ``POST /api/chat/confirm-tool`` HTTP endpoint and by the
        TUI's stdio control channel. Returns ``False`` if there is no pending
        confirmation request.

        *always* grants the pending tool for the rest of the session, so this
        prompt and every later one for the same tool are approved — the grant
        the terminal prompt's ``[a]lways for this tool`` already makes.

        *confirm_id*, when given, must match the live prompt. A decision typed
        against a prompt that has since expired or been replaced is dropped
        rather than applied to whatever is on screen now — the difference
        between approving what you read and approving what arrived while you
        were reading.
        """
        with self._confirm_lock:
            if confirm_id is not None and confirm_id != self._confirm_id:
                logger.warning(
                    "Dropped a tool decision for confirm_id %s: the live prompt "
                    "is %s",
                    confirm_id,
                    self._confirm_id,
                )
                return False
            if always and self._confirm_tool:
                self.grant_call_for_session(self._confirm_tool, self._confirm_args)
            if self._confirm_event is None:
                # No pending confirmation — initialise state anyway so callers can
                # inspect _confirm_result and _confirm_event after the call.
                self._confirm_event = threading.Event()
            self._confirm_result = approved or always
            self._confirm_event.set()
        return True

    def close_active_relay_response(self) -> None:
        """Force-close a live email-relay HTTP response, if any (#2109).

        A between-events ``cancelled`` check alone cannot interrupt a relay
        worker thread parked in a blocking socket read — this is the other
        half of that seam: called from the cancel path (both the explicit
        ``/api/chat/cancel`` endpoint and the streaming generator's orphan
        cleanup) right after ``cancelled.set()``.

        The unblocking MUST go through ``socket.shutdown()``, not
        ``resp.close()``: CPython's buffered reader holds its internal lock
        for the whole blocking ``read()``, and ``close()`` waits on that same
        lock — so a cross-thread ``close()`` blocks until the read returns on
        its own (proven by the real-socket test in
        ``tests/unit/test_email_sidecar_proxy.py``). ``shutdown()`` bypasses
        the file-object layer entirely and wakes the parked ``recv``
        immediately; the relay's stream generator then closes the response
        itself on the way out. ``resp.close()`` remains only as the fallback
        when no underlying socket can be found (e.g. mocked responses).
        """
        resp = self.active_relay_response
        if resp is None:
            return
        sock = _peel_response_socket(resp)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
                return  # reader wakes promptly; the stream owner closes resp
            except OSError:
                logger.debug(
                    "email relay: socket shutdown failed; falling back to close",
                    exc_info=True,
                )
        try:
            resp.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup, never fatal
            logger.debug("email relay: failed to close active response", exc_info=True)

    def request_user_input_blocking(
        self,
        message: str,
        choices: Optional[List[str]] = None,
        default_if_no_response: Optional[str] = None,
        timeout_seconds: int = 300,
        continue_if_no_response: bool = True,
        options: Optional[List[Dict[str, Any]]] = None,
        allow_free_text: bool = True,
        sensitive: bool = False,
    ) -> str:
        """Ask the user a question and block until a response arrives or timeout.

        Emits a ``user_input_request`` SSE event.  In background mode the request
        is still emitted (so it appears in the Goals dashboard), but returns
        ``"__NO_RESPONSE__"`` immediately instead of blocking.

        ``choices`` is the flat list of answer strings.  ``options`` is the
        richer form — ``[{"value", "label", "description"}, ...]`` — for surfaces
        that render a labelled picker; a bare yes/no cannot express "Gmail or
        Outlook?".  Both may be supplied; a surface that only understands
        ``choices`` keeps working unchanged.  ``sensitive`` tells the surface the
        answer is a credential, so it masks the typed characters — asking for an
        OAuth client secret with no way to say "this is a secret" is a defect,
        not a styling preference.

        Returns:
            User's response string, the chosen option, the default value on
            timeout, or ``"__NO_RESPONSE__"`` when no default is provided and
            the timeout expires.  Never returns empty string.
        """
        request_id = str(uuid.uuid4())
        timeout_seconds = max(10, timeout_seconds)  # floor: 10 seconds

        # Register BEFORE emitting, mirroring confirm_tool_execution: a fast
        # client can answer before this thread reaches the registration, and
        # resolve_user_input would then reject a perfectly good answer.
        evt: Optional[threading.Event] = None
        if not self.background_mode:
            evt = threading.Event()
            with self._user_input_lock:
                self._user_input_events[request_id] = evt
                self._user_input_queue.append(request_id)

        self._emit(
            {
                "type": "user_input_request",
                "request_id": request_id,
                "message": message,
                "choices": choices or [],
                "options": options or [],
                "allow_free_text": bool(allow_free_text),
                "sensitive": bool(sensitive),
                "timeout_seconds": timeout_seconds,
                "continue_if_no_response": continue_if_no_response,
            }
        )

        if evt is None:
            # Background mode: can't block — no active SSE consumer.  Return the
            # sentinel so the caller can decide whether to proceed or retry.
            return (
                default_if_no_response
                if default_if_no_response is not None
                else "__NO_RESPONSE__"
            )

        # Block until resolved or timeout
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.cancelled.is_set():
                break
            if evt.wait(timeout=0.5):
                break

        # Clean up. Under the lock and as one step, so a resolve landing at the
        # deadline either wins outright or is rejected — never accepted into a
        # slot this thread is about to drop.
        with self._user_input_lock:
            self._user_input_queue = deque(
                rid for rid in self._user_input_queue if rid != request_id
            )
            self._user_input_events.pop(request_id, None)
            response = self._user_input_results.pop(request_id, None)

        if response is not None:
            return response
        if default_if_no_response is not None:
            return default_if_no_response
        if not continue_if_no_response:
            # Signal loop to pause
            if self.loop_state_directive is None:
                self.loop_state_directive = {
                    "directive": "paused",
                    "reason": "User input timed out and continue_if_no_response=False",
                    "wake_in_seconds": 0,
                }
        return "__NO_RESPONSE__"

    def resolve_user_input(self, request_id: str, response: str) -> bool:
        """Unblock a pending ``request_user_input_blocking()`` call.

        Called by ``POST /api/chat/user-input`` and by the email sidecar's
        ``POST /v1/email/query/{run_id}/respond``.  Returns ``False`` if no
        request with that ID is pending, so the caller can answer 409 rather
        than report an accepted answer nothing will read.
        """
        with self._user_input_lock:
            evt = self._user_input_events.get(request_id)
            if evt is None:
                return False
            self._user_input_results[request_id] = response
            evt.set()
        return True

    def signal_done(self):
        """Signal that the agent has finished processing."""
        # Flush any pending thinking content
        if self._in_thinking and self._stream_buffer:
            self._emit({"type": "thinking", "content": self._stream_buffer})
            self._stream_buffer = ""
            self._in_thinking = False

        # Flush any remaining stream buffer before signaling done
        if self._stream_buffer:
            stripped = self._stream_buffer.strip()
            if not _TOOL_CALL_JSON_RE.match(stripped) and not _ANSWER_JSON_RE.search(
                stripped
            ):
                self._emit({"type": "chunk", "content": self._stream_buffer})
            self._stream_buffer = ""
        self._emit(None)  # Sentinel value


def _format_tool_args(  # pylint: disable=unused-argument
    tool_name: str, args: Dict[str, Any]
) -> str:
    """Format tool arguments into a human-readable string."""
    if not args:
        return ""

    parts = []
    for key, value in args.items():
        if value is None or value == "" or value is False:
            continue
        if value is True:
            parts.append(key)
        elif isinstance(value, str) and len(value) > 150:
            parts.append(f"{key}: {value[:150]}...")
        else:
            parts.append(f"{key}: {value}")

    return "\n".join(parts) if len(parts) > 2 else ", ".join(parts)


def _count_summary(data: Dict[str, Any]) -> Optional[str]:
    """``{"status": "success", "skills": [...]}`` -> ``"18 skills"``.

    Returns ``None`` when nothing countable is present, so the caller keeps its
    own wording rather than inventing a number.
    """
    for key, value in data.items():
        if key == "status" or not isinstance(value, list) or not value:
            continue
        return format_count(len(value), key)
    return None


def _summarize_tool_result(data: Dict[str, Any]) -> str:
    """Create a detailed human-readable summary of a tool result."""
    if not isinstance(data, dict):
        return str(data)[:_SUMMARY_CHAR_CAP]

    # Command execution results
    if "command" in data and "stdout" in data:
        stdout = data.get("stdout", "")
        rc = data.get("return_code", 0)
        lines = stdout.strip().split("\n") if stdout.strip() else []
        if rc != 0:
            stderr = data.get("stderr", "")
            return f"Command failed (exit {rc})" + (
                f": {stderr[:150]}" if stderr else ""
            )
        if lines:
            # Show first few lines of output
            preview = "\n".join(lines[:5])
            if len(lines) > 5:
                preview += f"\n... ({len(lines)} lines total)"
            return preview
        return "Command completed (no output)"

    # File search results
    if "files" in data or "file_list" in data:
        files = data.get("file_list", data.get("files", []))
        count = data.get("count", len(files) if isinstance(files, list) else 0)
        display_msg = data.get("display_message", "")
        if isinstance(files, list) and files:
            file_names = []
            for f in files[:5]:
                if isinstance(f, dict):
                    name = f.get("name", f.get("filename", f.get("file_name", "")))
                    # Fallback: extract filename from file_path if name keys are missing
                    if not name and f.get("file_path"):
                        name = f["file_path"].replace("\\", "/").rsplit("/", 1)[-1]
                    directory = f.get("directory", "")
                    if directory and name:
                        file_names.append(f"{name} ({directory})")
                    elif name:
                        file_names.append(name)
                    elif directory:
                        file_names.append(directory)
                else:
                    file_names.append(str(f))
            result = "\n".join(f"  {name}" for name in file_names)
            if count > 5:
                result += f"\n  ... +{count - 5} more"
            return (
                (display_msg + "\n" + result)
                if display_msg
                else f"Found {count} file(s):\n{result}"
            )
        if display_msg:
            return display_msg
        return f"Found {count} file(s)"

    # Search/query results with chunks
    if "chunks" in data:
        chunks = data["chunks"]
        if isinstance(chunks, list):
            scores = data.get("scores", [])
            result = f"Found {len(chunks)} relevant chunk(s)"
            if scores:
                result += f" (best score: {max(scores):.2f})"
            # Show brief preview of top chunk
            if chunks and isinstance(chunks[0], str):
                preview = chunks[0][:120].replace("\n", " ")
                result += f'\n  Top match: "{preview}..."'
            return result

    # Search/query results generic
    if "results" in data:
        results = data["results"]
        if isinstance(results, list):
            return f"Found {len(results)} result(s)"
        return str(results)[:200]

    # Document indexing results
    if "num_chunks" in data or "chunk_count" in data:
        chunks = data.get("num_chunks", data.get("chunk_count", 0))
        filename = data.get("filename", data.get("file_path", ""))
        if filename:
            return f"Indexed {filename} ({chunks} chunks)"
        return f"Indexed document ({chunks} chunks)"

    # File read results
    if "content" in data and "filepath" in data:
        content = data["content"]
        lines = content.split("\n") if isinstance(content, str) else []
        return f"Read {len(lines)} lines from {data.get('filename', data.get('filepath', 'file'))}"

    # list_indexed_documents results — has "documents" list + "count" + "total_chunks"
    if "documents" in data and "count" in data and "total_chunks" in data:
        count = data.get("count", 0)
        if count == 0:
            return "No documents indexed"
        docs = data.get("documents", [])
        names = [d.get("name", "?") for d in docs[:5] if isinstance(d, dict)]
        result = f"{count} document(s) indexed: {', '.join(names)}"
        if count > 5:
            result += f" (+{count - 5} more)"
        return result

    # Status-based results
    if "status" in data:
        status = data["status"]
        msg = data.get("message", data.get("error", data.get("display_message", "")))
        if msg:
            return f"{status}: {str(msg)[:200]}"
        # A bare "success" tells the user nothing. Report what came back
        # instead, when the payload carries a countable collection (#2804).
        return _count_summary(data) or str(status)

    # Generic fallback - show more useful info
    keys = list(data.keys())[:6]
    return f"Result with keys: {', '.join(keys)}"


def _tool_description(tool_name: str) -> str:
    """Return a human-readable description for known agent tools."""
    descriptions = {
        "query_documents": "Searching indexed documents for relevant content",
        "query_specific_file": "Searching a specific document for relevant content",
        "search_indexed_chunks": "Searching document chunks by keyword",
        "search_documents": "Searching indexed documents for relevant content",
        "search_file": "Searching for files matching a pattern",
        "read_file": "Reading file contents",
        "list_directory": "Listing directory contents",
        "run_shell_command": "Executing a shell command",
        "write_file": "Writing to a file",
        "create_file": "Creating a new file",
        "get_file_preview": "Previewing file contents",
        "index_document": "Indexing a document for retrieval",
        "evaluate_retrieval": "Evaluating document retrieval quality",
    }
    return descriptions.get(tool_name, "")


def _clean_answer_json(text: str) -> str:
    """Strip {"answer": "..."} JSON wrapping from LLM output.

    LLMs sometimes wrap their entire response in a JSON envelope like
    ``{"answer": "the actual text..."}``.  This function detects that
    pattern and extracts only the answer content.  It handles both
    valid JSON (with escaped newlines) and the common case where the
    JSON string contains literal newlines (making it invalid JSON).
    """
    if not text:
        return text
    stripped = text.strip()
    # Quick check: must start with { and contain "answer"
    if not (
        stripped.startswith("{") and '"answer"' in stripped and stripped.endswith("}")
    ):
        return text
    # Try proper JSON parse first
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict) and "answer" in parsed:
            return parsed["answer"]
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: manual extraction for JSON with literal newlines
    m = re.match(r'^\s*\{\s*"answer"\s*:\s*"', stripped)
    if m:
        content_start = m.end()
        # Walk backwards from end, skipping whitespace + closing } + "
        end = len(stripped) - 1
        while end > content_start and stripped[end] in " \t\n\r}":
            end -= 1
        if end > content_start and stripped[end] == '"':
            end -= 1  # skip trailing quote
        extracted = stripped[content_start : end + 1]
        # Unescape any JSON escape sequences
        extracted = extracted.replace("\\n", "\n")
        extracted = extracted.replace("\\t", "\t")
        extracted = extracted.replace('\\"', '"')
        return extracted
    return text


def _fix_double_escaped(text: str) -> str:
    """Fix double-escaped newlines/tabs from LLM output.

    Some models output literal '\\n' (two chars) instead of actual newlines,
    which breaks markdown rendering. Only unescape when there are significantly
    more literal \\n sequences than real newlines.
    """
    if not text:
        return text
    literal_count = text.count("\\n")
    real_count = text.count("\n")
    if literal_count > 2 and literal_count > real_count * 2:
        text = text.replace("\\n", "\n")
        text = text.replace("\\t", "\t")
        text = text.replace('\\"', '"')
    return text
