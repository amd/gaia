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
        # Fold goal into status rather than separate event
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
                    plan_strs.append(f"Use {step['tool']}")
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
        # Summarize tool results for the frontend (don't send raw data)
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


def _summarize_tool_result(data: Dict[str, Any]) -> str:
    """Create a brief human-readable summary of a tool result."""
    if not isinstance(data, dict):
        return str(data)[:200]

    # Command execution results
    if "command" in data and "stdout" in data:
        stdout = data.get("stdout", "")
        rc = data.get("return_code", 0)
        lines = stdout.strip().split("\n") if stdout.strip() else []
        if rc != 0:
            return f"Command failed (exit {rc})"
        if lines:
            return f"{len(lines)} line(s) of output"
        return "Command completed"

    # Search/query results
    if "results" in data:
        results = data["results"]
        if isinstance(results, list):
            return f"Found {len(results)} result(s)"
        return str(results)[:100]

    # File read results
    if "content" in data and "filepath" in data:
        content = data["content"]
        lines = content.split("\n") if isinstance(content, str) else []
        return f"Read {len(lines)} lines from {data.get('filename', 'file')}"

    # Status-based results
    if "status" in data:
        status = data["status"]
        msg = data.get("message", data.get("error", ""))
        if msg:
            return f"{status}: {str(msg)[:100]}"
        return str(status)

    # RAG results
    if "chunks" in data:
        chunks = data["chunks"]
        if isinstance(chunks, list):
            return f"Found {len(chunks)} relevant chunk(s)"

    # Generic fallback
    keys = list(data.keys())[:4]
    return f"Result with keys: {', '.join(keys)}"
