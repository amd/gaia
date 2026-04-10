# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
SSE Output Handler - Bridges agent console events to Server-Sent Events.

Maps OutputHandler method calls (thinking, tool calls, steps, etc.)
to JSON events that the streaming endpoint sends to the frontend.
"""

import json
import logging
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.agents.base.console import OutputHandler
from gaia.agents.base.tools import get_tool_metadata

logger = logging.getLogger(__name__)

#: Seconds the agent thread waits for a tool-confirm response from the frontend.
TOOL_CONFIRM_TIMEOUT_SECONDS = 60

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


class SSEOutputHandler(OutputHandler):
    """
    OutputHandler that queues agent events as JSON for SSE streaming.

    Each console method call becomes a typed event pushed to a queue.
    The streaming endpoint reads from this queue and yields SSE events.
    """

    def __init__(self):
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
        self._confirm_event: Optional[threading.Event] = None
        self._confirm_result: bool = False
        self._confirm_id: Optional[str] = None
        self._tool_start_time: Optional[float] = None

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
        event = {
            "type": "tool_start",
            "tool": tool_name,
            "detail": _tool_description(tool_name),
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
                event["result_data"] = {
                    "type": "file_list",
                    "files": files[:20],  # Limit to 20 files
                    "total": data.get("count", len(files)),
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

        self._emit(event)

    # === Status Messages ===

    def print_error(self, error_message: str):
        self._emit(
            {
                "type": "agent_error",
                "content": str(error_message) if error_message else "Unknown error",
            }
        )

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
        self._emit(
            {
                "type": "status",
                "status": "working",
                "message": message or "Working",
            }
        )

    def stop_progress(self):
        pass  # No-op for SSE - frontend manages its own spinners

    # === Completion Methods ===

    def print_final_answer(
        self, answer: str, streaming: bool = True
    ):  # pylint: disable=unused-argument
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
        self._emit(
            {
                "type": "answer",
                "content": _fix_double_escaped(answer) if answer else answer,
                "elapsed": self._elapsed(),
                "steps": self._step_count,
                "tools_used": self._tool_count,
            }
        )

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
        timeout: float = TOOL_CONFIRM_TIMEOUT_SECONDS,
    ) -> bool:
        """Block the agent thread until the user approves or denies a tool call.

        Emits a ``permission_request`` SSE event so the frontend can show a modal.
        Waits up to ``timeout`` seconds for ``resolve_tool_confirmation()``
        to be called by the HTTP endpoint.  Returns ``True`` if the user allows,
        ``False`` otherwise.
        """
        confirm_id = str(uuid.uuid4())
        self._confirm_event = threading.Event()
        self._confirm_result = False
        self._confirm_id = confirm_id

        self._emit(
            {
                "type": "permission_request",
                "tool": tool_name,
                "args": tool_args,
                "confirm_id": confirm_id,
                "timeout_seconds": timeout,
            }
        )

        # Poll in short intervals so cancellation is detected promptly.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancelled.is_set():
                self._confirm_id = None
                self._confirm_event = None
                return False
            if self._confirm_event.wait(timeout=0.5):
                break
        else:
            # Timeout reached
            self._emit(
                {
                    "type": "status",
                    "status": "warning",
                    "message": f"Confirmation for '{tool_name}' timed out ({TOOL_CONFIRM_TIMEOUT_SECONDS} s). Execution denied.",
                }
            )
            logger.warning("Tool confirmation timed out for '%s'", tool_name)
            self._confirm_id = None
            self._confirm_event = None
            return False

        result = self._confirm_result
        self._confirm_id = None
        self._confirm_event = None
        return result

    def resolve_tool_confirmation(self, approved: bool) -> bool:
        """Unblock the agent thread waiting in ``confirm_tool_execution()``.

        Called by the ``POST /api/chat/confirm-tool`` HTTP endpoint.  Returns
        ``False`` if there is no pending confirmation request.
        """
        if self._confirm_event is None:
            # No pending confirmation — initialise state anyway so callers can
            # inspect _confirm_result and _confirm_event after the call.
            self._confirm_event = threading.Event()
        self._confirm_result = approved
        self._confirm_event.set()
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


def _summarize_tool_result(data: Dict[str, Any]) -> str:
    """Create a detailed human-readable summary of a tool result."""
    if not isinstance(data, dict):
        return str(data)[:300]

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
        return str(status)

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
