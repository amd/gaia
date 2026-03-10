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
import time
from typing import Any, Dict, List, Optional

from gaia.agents.base.console import OutputHandler

logger = logging.getLogger(__name__)


class SSEOutputHandler(OutputHandler):
    """
    OutputHandler that queues agent events as JSON for SSE streaming.

    Each console method call becomes a typed event pushed to a queue.
    The streaming endpoint reads from this queue and yields SSE events.
    """

    def __init__(self):
        self.event_queue: queue.Queue = queue.Queue()
        self._start_time: Optional[float] = None
        self._step_count = 0
        self._tool_count = 0
        self._last_tool_name: Optional[str] = None

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
        self._emit(
            {
                "type": "status",
                "status": "started",
                "message": "Processing your request...",
                "model": model_id,
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
        self._emit(
            {
                "type": "status",
                "status": "working",
                "message": state_message,
            }
        )

    def print_thought(self, thought: str):
        self._emit(
            {
                "type": "thinking",
                "content": thought,
            }
        )

    def print_goal(self, goal: str):
        self._emit(
            {
                "type": "thinking",
                "content": goal,
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
        self._emit(
            {
                "type": "tool_start",
                "tool": tool_name,
            }
        )

    def print_tool_complete(self):
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
        self._emit(
            {
                "type": "tool_result",
                "title": title,
                "summary": summary,
                "success": (
                    data.get("status") != "error" if isinstance(data, dict) else True
                ),
            }
        )

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
        self._emit(
            {
                "type": "status",
                "status": "working",
                "message": message,
            }
        )

    def stop_progress(self):
        pass  # No-op for SSE - frontend manages its own spinners

    # === Completion Methods ===

    def print_final_answer(
        self, answer: str, streaming: bool = True
    ):  # pylint: disable=unused-argument
        self._emit(
            {
                "type": "answer",
                "content": answer,
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

    # === Optional Methods (with SSE-friendly implementations) ===

    def print_streaming_text(self, text_chunk: str, end_of_stream: bool = False):
        if text_chunk:
            self._emit(
                {
                    "type": "chunk",
                    "content": text_chunk,
                }
            )

    def signal_done(self):
        """Signal that the agent has finished processing."""
        self._emit(None)  # Sentinel value


def _format_tool_args(tool_name: str, args: Dict[str, Any]) -> str:
    """Format tool arguments into a human-readable string."""
    if not args:
        return ""

    parts = []
    for key, value in args.items():
        if value is None or value == "" or value is False:
            continue
        if value is True:
            parts.append(key)
        elif isinstance(value, str) and len(value) > 100:
            parts.append(f"{key}: {value[:100]}...")
        else:
            parts.append(f"{key}: {value}")

    return ", ".join(parts)


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
            return f"Command failed (exit {rc})" + (f": {stderr[:150]}" if stderr else "")
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
                    name = f.get("name", f.get("filename", ""))
                    directory = f.get("directory", "")
                    if directory:
                        file_names.append(f"{name} ({directory})")
                    else:
                        file_names.append(name)
                else:
                    file_names.append(str(f))
            result = "\n".join(f"  {name}" for name in file_names)
            if count > 5:
                result += f"\n  ... +{count - 5} more"
            return (display_msg + "\n" + result) if display_msg else f"Found {count} file(s):\n{result}"
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
                result += f"\n  Top match: \"{preview}...\""
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
