# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
SSE Output Handler for API streaming.

Converts agent output into Server-Sent Events format for API clients.
"""

import json
import time
from collections import deque
from typing import Any, Dict, List

from gaia.agents.base.console import OutputHandler


class SSEOutputHandler(OutputHandler):
    """
    Output handler for Server-Sent Events (SSE) streaming to API clients.

    Formats agent outputs as SSE-compatible JSON chunks that can be
    streamed to API clients (e.g., VSCode extension).

    Each output is converted to a dictionary and added to a queue
    that can be consumed by the API server.

    Args:
        debug_mode: If True, include verbose event details. If False, only stream
                   clean, user-friendly status updates.
    """

    def __init__(self, debug_mode: bool = False):
        """Initialize the SSE output handler.

        Args:
            debug_mode: Enable verbose event streaming for debugging
        """
        self.queue = deque()
        self.streaming_buffer = ""  # Maintain compatibility
        self.debug_mode = debug_mode
        self.current_step = 0
        self.total_steps = 0

    def _add_event(self, event_type: str, data: Dict[str, Any]):
        """
        Add an event to the output queue.

        Args:
            event_type: Type of event (thinking, tool_call, etc.)
            data: Event data to send
        """
        self.queue.append({"type": event_type, "data": data, "timestamp": time.time()})

    def should_stream_as_content(self, event_type: str) -> bool:
        """
        Determine if an event should be streamed as content to the client.

        In normal mode: Only stream key status updates and final answers
        In debug mode: Stream all events

        Args:
            event_type: Type of event

        Returns:
            True if event should be streamed as content
        """
        if self.debug_mode:
            # Debug mode: stream everything
            return True

        # Normal mode: only stream these event types
        # Note: Errors are excluded - they're internal agent messages
        streamable_events = {
            "processing_start",
            "step_header",
            "state",
            "tool_usage",
            "file_preview_start",
            "file_preview_complete",
            "final_answer",
            "warning",  # Keep warnings for important user feedback
            "success",  # Success messages for completed operations
            "diff",  # Code diff notifications
            "completion",
            "checklist",  # Checklist progress from orchestrator
            "checklist_reasoning",  # Checklist reasoning (debug info)
            "message",  # Generic messages from print() calls
            "agent_selected",  # Agent routing selection notification
        }
        return event_type in streamable_events

    # === Core Progress/State Methods (Required) ===

    def print_processing_start(self, query: str, max_steps: int):
        """Print processing start message."""
        self.total_steps = max_steps
        self._add_event("processing_start", {"query": query, "max_steps": max_steps})

    def print_step_header(self, step_num: int, step_limit: int):
        """Print step header."""
        self.current_step = step_num
        self.total_steps = step_limit
        self._add_event("step_header", {"step": step_num, "step_limit": step_limit})

    def print_state_info(self, state_message: str):
        """Print current execution state."""
        self._add_event("state", {"message": state_message})

    def print_thought(self, thought: str):
        """Print agent's reasoning/thought."""
        self._add_event("thought", {"message": thought})

    def print_goal(self, goal: str):
        """Print agent's current goal."""
        self._add_event("goal", {"message": goal})

    def print_plan(self, plan: List[Any], current_step: int = None):
        """Print agent's plan with optional current step highlight."""
        self._add_event("plan", {"plan": plan, "current_step": current_step})

    # === Tool Execution Methods (Required) ===

    def print_tool_usage(self, tool_name: str):
        """Print tool being called."""
        self._add_event("tool_usage", {"tool_name": tool_name})

    def print_tool_complete(self):
        """Print tool completion."""
        self._add_event("tool_complete", {})

    def pretty_print_json(self, data: Dict[str, Any], title: str = None):
        """Print JSON data (tool args/results)."""
        self._add_event("json", {"data": data, "title": title})

    # === Status Messages (Required) ===

    def print_error(self, error_message: str):
        """Print error message."""
        self._add_event("error", {"message": error_message})

    def print_warning(self, warning_message: str):
        """Print warning message."""
        self._add_event("warning", {"message": warning_message})

    def print_info(self, message: str):
        """Print informational message."""
        self._add_event("info", {"message": message})

    def print_success(self, message: str):
        """Print success message."""
        self._add_event("success", {"message": message})

    def print_diff(self, diff: str, filename: str):
        """Print code diff."""
        self._add_event("diff", {"diff": diff, "filename": filename})

    # === Progress Indicators (Required) ===

    def start_progress(self, message: str):
        """Start progress indicator."""
        self._add_event("progress_start", {"message": message})

    def stop_progress(self):
        """Stop progress indicator."""
        self._add_event("progress_stop", {})

    # === Completion Methods (Required) ===

    def print_final_answer(self, answer: str):
        """Print final answer/result."""
        self._add_event("final_answer", {"answer": answer})

    def print_repeated_tool_warning(self):
        """Print warning about repeated tool calls (loop detection)."""
        self._add_event(
            "warning",
            {"message": "Repeated tool call detected - possible infinite loop"},
        )

    def print_completion(self, steps_taken: int, steps_limit: int):
        """Print completion summary."""
        self._add_event(
            "completion", {"steps_taken": steps_taken, "steps_limit": steps_limit}
        )

    # === File Preview Methods (Required for Code Agent) ===

    def start_file_preview(
        self, filename: str, max_lines: int = None, title_prefix: str = ""
    ):
        """Start file preview display."""
        self._add_event(
            "file_preview_start",
            {
                "filename": filename,
                "max_lines": max_lines,
                "title_prefix": title_prefix,
            },
        )

    def update_file_preview(self, content_chunk: str):
        """Update file preview with content."""
        self._add_event("file_preview_update", {"content": content_chunk})

    def stop_file_preview(self):
        """Stop file preview display."""
        self._add_event("file_preview_complete", {})

    def print_step_paused(self, description: str):
        """Print step paused message."""
        self._add_event("step_paused", {"description": description})

    def print_command_executing(self, command: str):
        """Print command executing message."""
        self._add_event("command_executing", {"command": command})

    def print_agent_selected(self, agent_name: str, language: str, project_type: str):
        """Print agent selected message."""
        self._add_event(
            "agent_selected",
            {
                "agent_name": agent_name,
                "language": language,
                "project_type": project_type,
            },
        )

    def print(self, *args, **_kwargs):
        """
        Handle generic print() calls - queue as message event.

        This method captures print() calls from agent code and queues them
        as SSE events so they can be streamed to the client.

        Args:
            *args: Values to print (will be joined with spaces)
            **_kwargs: Ignored (for compatibility with built-in print)
        """
        # Join args with spaces, converting to strings
        message = " ".join(str(arg) for arg in args)
        if message.strip():
            self._add_event("message", {"text": message})

    # === Checklist Methods (Required for Code Agent Orchestration) ===

    def print_checklist(self, items: List[Any], current_idx: int) -> None:
        """Print checklist items with current progress."""
        self._add_event(
            "checklist",
            {
                "items": [str(item) for item in items],
                "current_index": current_idx,
            },
        )

    def print_checklist_reasoning(self, reasoning: str) -> None:
        """Print checklist reasoning/planning."""
        self._add_event("checklist_reasoning", {"reasoning": reasoning})

    def get_events(self) -> List[Dict[str, Any]]:
        """
        Get all queued events and clear the queue.

        Returns:
            List of event dictionaries
        """
        events = list(self.queue)
        self.queue.clear()
        return events

    def has_events(self) -> bool:
        """Check if there are any queued events."""
        return len(self.queue) > 0

    def format_event_as_content(self, event: Dict[str, Any]) -> str:
        """
        Format an event as clean content text for streaming.

        Sends clean, minimal text that is OpenAI-compatible.
        The VSCode extension will add formatting (emojis, separators) for display.

        Args:
            event: Event dictionary with type, data, and timestamp

        Returns:
            Clean content string suitable for any OpenAI-compatible client
        """
        event_type = event.get("type", "message")
        data = event.get("data", {})

        if self.debug_mode:
            # Debug mode: Include event type and data in compact format
            return f"[{event_type}] {json.dumps(data, separators=(',', ':'))}\n"

        # Normal mode: Clean, minimal status messages (no emojis/separators here)
        # The VSCode extension will add formatting for better UX

        if event_type == "processing_start":
            return "Processing request...\n"

        elif event_type == "step_header":
            # Don't emit step headers in normal mode - too verbose
            # Debug mode will show them via the debug format above
            return ""

        elif event_type == "state":
            message = data.get("message", "")
            return f"{message}\n"

        elif event_type == "tool_usage":
            tool_name = data.get("tool_name", "unknown")
            return f"Using tool: {tool_name}\n"

        elif event_type == "file_preview_start":
            filename = data.get("filename", "unknown")
            return f"Previewing file: {filename}\n"

        elif event_type == "file_preview_update":
            # Skip content chunks to avoid clutter
            return ""

        elif event_type == "file_preview_complete":
            return "File preview complete\n"

        elif event_type == "final_answer":
            # Final answer is the actual response - send as-is
            answer = data.get("answer", "")
            return f"{answer}\n"

        elif event_type == "error":
            message = data.get("message", "An error occurred")
            return f"Error: {message}\n"

        elif event_type == "warning":
            message = data.get("message", "")
            return f"Warning: {message}\n"

        elif event_type == "success":
            message = data.get("message", "")
            return f"{message}\n"

        elif event_type == "diff":
            filename = data.get("filename", "unknown")
            return f"Modified file: {filename}\n"

        elif event_type == "completion":
            steps_taken = data.get("steps_taken", 0)
            return f"Completed in {steps_taken} steps\n"

        elif event_type == "checklist":
            items = data.get("items", [])
            current_idx = data.get("current_index", 0)
            return f"Progress: step {current_idx + 1} of {len(items)}\n"

        elif event_type == "checklist_reasoning":
            # Skip reasoning in non-debug mode (too verbose)
            return ""

        elif event_type == "message":
            text = data.get("text", "")
            return f"{text}\n" if text else ""

        elif event_type == "agent_selected":
            agent_name = data.get("agent_name", "unknown")
            language = data.get("language", "")
            project_type = data.get("project_type", "")
            return f"Agent: {agent_name} ({language}/{project_type})\n"

        # For other events in normal mode, don't stream them
        return ""
