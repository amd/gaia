# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia-agent --mcp``: the flagship agent as an MCP server over stdio.

Claude Code launches this from the GAIA plugin and hands it self-contained
tasks through ``gaia_task``. The agent is built once, in the background, and
kept: building it takes ~40 s and a turn a few seconds, so a process per task
would spend most of its time starting.

Only the slice of MCP a tool server needs is spoken here (``initialize``,
``ping``, ``tools/list``, ``tools/call``, ``notifications/cancelled``). The
frozen binary does not bundle the ``mcp`` package, and this slice is small
enough that adding it would cost more than it saves.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from typing import Any, BinaryIO, Callable, Dict, Optional

from gaia_agent import __version__ as agent_version

from gaia.logger import get_logger

logger = get_logger(__name__)

#: Newest first. A client asking for one of these gets it back; any other
#: request is answered with the newest, as the protocol's negotiation says.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

TASK_TIMEOUT_S = 300.0
#: How long a task waits for the agent build, which includes loading the model.
BUILD_WAIT_S = 600.0
#: How long a timed-out task gets to reach a step boundary and stop.
STOP_GRACE_S = 30.0
#: How often a waiting task checks whether it was cancelled.
WAIT_POLL_S = 0.25

TIMEOUT_ANSWER = "Stopped: the task ran past its time limit."
CANCEL_ANSWER = "Stopped: Claude cancelled the task."

INSTRUCTIONS = (
    "GAIA is an AI agent running on this PC's own model. Hand it self-contained, "
    "read-mostly work: summarizing files or logs, triage across many files, "
    "extraction and classification, questions about documents, first drafts. "
    "It can read files inside the folder Claude Code was started in, search the "
    "web, and run read-only commands. It cannot edit or delete files, so apply "
    "changes yourself. Check its answer before relying on it."
)

TOOLS = [
    {
        "name": "gaia_task",
        "title": "Run a task on local GAIA",
        "description": (
            "Run one self-contained task on the GAIA agent, which uses this PC's "
            "local model, and return its answer. Each call starts fresh: include "
            "everything GAIA needs (file paths, the question, the output format "
            "you want). GAIA can read files in the current project folder, search "
            "the web and run read-only commands. It cannot edit or delete files, "
            "though it may save new ones such as downloads. Tasks run one at a "
            f"time; each stops {int(TASK_TIMEOUT_S)} seconds after it starts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The complete task, written for an assistant "
                    "that has seen nothing of this conversation.",
                }
            },
            "required": ["task"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "openWorldHint": True},
    },
    {
        "name": "gaia_status",
        "title": "Check local GAIA",
        "description": (
            "Report whether GAIA is ready, still starting, or failed to start, "
            "and which local model it runs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
]


class TaskError(Exception):
    """A task that could not run, said in words Claude can pass on."""


class TaskCancelled(TaskError):
    """Claude cancelled the task before it started running."""


def build_agent(model: Optional[str] = None) -> Any:
    """The flagship agent, configured as ``gaia -p`` configures it."""
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig, check_skill_set_selection

    from gaia.print_mode import configure_console

    check_skill_set_selection()
    config: Dict[str, Any] = {
        "silent_mode": True,
        "streaming": False,
        "allowed_paths": [os.getcwd()],
        # Claude's tasks are not the user's chats: nothing is saved to memory,
        # and none of the user's memories reach an answer that goes to Claude.
        "memory_incognito": True,
    }
    if model:
        config["model_id"] = model
    agent = GaiaAgent(config=GaiaAgentConfig(**config))
    # Gated tools are denied: nobody on this wire can answer a confirmation.
    configure_console(agent.console, skip_permissions=False, full_access=False)
    return agent


class AgentHost:
    """Builds the agent once, in the background, and runs one task at a time."""

    def __init__(
        self,
        build: Callable[[], Any],
        task_timeout_s: float = TASK_TIMEOUT_S,
        build_wait_s: float = BUILD_WAIT_S,
        stop_grace_s: float = STOP_GRACE_S,
    ) -> None:
        self._build = build
        self.task_timeout_s = task_timeout_s
        self.build_wait_s = build_wait_s
        self.stop_grace_s = stop_grace_s
        self._agent: Any = None
        self._error: Optional[BaseException] = None
        self._build_seconds: Optional[float] = None
        self._ready = threading.Event()
        self._started = False
        self._start_lock = threading.Lock()
        # Held from a task's start until its worker returns, even past a
        # timeout: two process_query calls must never share one agent.
        self._busy = threading.Lock()
        self._active: Optional[tuple] = None

    def start(self) -> None:
        """Begin building the agent, once."""
        with self._start_lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._run_build, daemon=True, name="gaia-build").start()

    def _run_build(self) -> None:
        started = time.monotonic()
        try:
            self._agent = self._build()
        except Exception as exc:  # reported by gaia_status and every task
            logger.exception("Could not build the GAIA agent")
            self._error = exc
        finally:
            self._build_seconds = round(time.monotonic() - started, 1)
            self._ready.set()

    def status(self) -> Dict[str, Any]:
        if not self._ready.is_set():
            return {
                "state": "starting",
                "message": "GAIA is still loading its agent and model.",
            }
        if self._error is not None:
            return {"state": "failed", "error": _describe(self._error)}
        return {
            "state": "ready",
            "model": getattr(self._agent, "model_id", None),
            "folder": os.getcwd(),
            "startup_s": self._build_seconds,
        }

    def cancel(self, request_id: Any) -> bool:
        """Stop the task *request_id* if it is the one running."""
        active = self._active
        if active is None or active[0] != request_id:
            return False
        active[1].cancel(CANCEL_ANSWER)
        return True

    def run_task(
        self,
        task: str,
        request_id: Any = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> Dict[str, Any]:
        """Run *task* and return the ``gaia -p --output-format json`` record.

        *cancelled* is polled while the task waits for the build or for an
        earlier task, so a cancel sent before it starts still stops it.
        """
        from gaia.agents.base.agent import TurnCancel
        from gaia.print_mode import summarize

        self.start()
        deadline = time.monotonic() + self.build_wait_s
        while not self._ready.wait(timeout=WAIT_POLL_S):
            if cancelled():
                raise TaskCancelled(CANCEL_ANSWER)
            if time.monotonic() >= deadline:
                raise TaskError(
                    f"GAIA is still starting after {int(self.build_wait_s)} "
                    "seconds. Its model may still be loading; try again shortly."
                )
        if self._error is not None:
            raise TaskError(f"GAIA could not start: {_describe(self._error)}")
        deadline = time.monotonic() + self.task_timeout_s
        while not self._busy.acquire(timeout=WAIT_POLL_S):
            if cancelled():
                raise TaskCancelled(CANCEL_ANSWER)
            if time.monotonic() >= deadline:
                raise TaskError(
                    "GAIA is still working on an earlier task. Try again when "
                    "it finishes."
                )
        if cancelled():
            self._busy.release()
            raise TaskCancelled(CANCEL_ANSWER)

        agent = self._agent
        cancel = TurnCancel()
        outcome: Dict[str, Any] = {}

        def _work() -> None:
            try:
                outcome["value"] = agent.process_query(task) or {}
            except Exception as exc:  # becomes the task's error
                logger.exception("GAIA task failed")
                outcome["error"] = exc
            finally:
                self._active = None
                agent._cancel_event = None
                self._busy.release()

        try:
            agent.conversation_history.clear()
            reset = getattr(agent, "_ensure_tool_loader_reset", None)
            if callable(reset):
                reset()
            # Claude wrote this task, not the user: no skill lesson may be
            # learned from it as though the user had asked.
            agent.mark_external_content()
            agent._cancel_event = cancel
            self._active = (request_id, cancel)
            started = time.time()
            worker = threading.Thread(target=_work, daemon=True, name="gaia-task")
            worker.start()
        except BaseException:
            self._active = None
            agent._cancel_event = None
            self._busy.release()
            raise

        worker.join(self.task_timeout_s)
        timed_out = worker.is_alive()
        if timed_out:
            cancel.cancel(TIMEOUT_ANSWER)
            worker.join(self.stop_grace_s)
        duration = time.time() - started

        if "error" in outcome:
            raise TaskError(f"GAIA failed: {_describe(outcome['error'])}")
        record = summarize(
            outcome.get("value") or {}, getattr(agent, "model_id", None), duration
        )
        if timed_out:
            record.update(
                is_error=True,
                status="timeout",
                error=f"The task ran past {int(self.task_timeout_s)} seconds and "
                "was stopped. Split it into smaller tasks.",
            )
        elif cancel.is_set():
            record.update(is_error=True, status="cancelled", error=CANCEL_ANSWER)
        return record

    def shutdown(self) -> None:
        """Let background memory work finish before the process exits."""
        if self._agent is None:
            return
        from gaia.agents.base.memory import drain_memory_extraction

        drain_memory_extraction(self._agent)


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    return text if text else type(exc).__name__


def _task_result(record: Dict[str, Any]) -> Dict[str, Any]:
    if record.get("is_error"):
        answer = record.get("result") or ""
        text = f"GAIA did not finish: {record.get('error') or record.get('status')}"
        if answer:
            text += f"\n\nWhat it had so far:\n{answer}"
    else:
        text = record.get("result") or "(GAIA returned an empty answer.)"
    text += (
        f"\n\n[GAIA · {record.get('model') or 'local model'} · "
        f"{record.get('num_steps', 0)} steps · {record.get('num_tool_calls', 0)} "
        f"tool calls · {record.get('duration_s', 0)} s]"
    )
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": record,
        "isError": bool(record.get("is_error")),
    }


def _error_result(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


class McpServer:
    """JSON-RPC over newline-delimited stdio, dispatching to an :class:`AgentHost`."""

    def __init__(self, host: AgentHost, reader: BinaryIO, writer: BinaryIO) -> None:
        self.host = host
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()
        self._cancelled: set = set()
        self._calls: list = []

    def serve(self) -> int:
        """Answer requests until stdin closes."""
        for raw in self._reader:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self._handle_line(line)
        # A closed stdin is the host shutting down; it kills us if it tires of
        # waiting, and each task already ends at its own time limit.
        for call in self._calls:
            call.join()
        self.host.shutdown()
        return 0

    def _send(self, message: Dict[str, Any]) -> None:
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            self._writer.write(data)
            self._writer.flush()

    def _reply(self, request_id: Any, result: Dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _fail(self, request_id: Any, code: int, message: str) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    def _handle_line(self, line: str) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self._fail(None, -32700, "Parse error: not valid JSON.")
            return
        if not isinstance(message, dict):
            self._fail(None, -32600, "Invalid request: batches are not supported.")
            return
        method = message.get("method")
        request_id = message.get("id")
        is_request = "id" in message
        params = message.get("params") or {}
        if not isinstance(params, dict):
            if is_request:
                self._fail(request_id, -32602, "Invalid params: expected an object.")
            return

        if method == "notifications/cancelled":
            target = params.get("requestId")
            self._cancelled.add(target)
            self.host.cancel(target)
            return
        if not is_request:
            return
        if method == "initialize":
            self.host.start()
            requested = params.get("protocolVersion")
            self._reply(
                request_id,
                {
                    "protocolVersion": (
                        requested
                        if requested in PROTOCOL_VERSIONS
                        else PROTOCOL_VERSIONS[0]
                    ),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "gaia", "version": agent_version},
                    "instructions": INSTRUCTIONS,
                },
            )
        elif method == "ping":
            self._reply(request_id, {})
        elif method == "tools/list":
            self._reply(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            call = threading.Thread(
                target=self._call_tool,
                args=(request_id, params),
                daemon=True,
                name="gaia-tool-call",
            )
            self._calls = [c for c in self._calls if c.is_alive()] + [call]
            call.start()
        else:
            self._fail(request_id, -32601, f"Method not found: {method}")

    def _call_tool(self, request_id: Any, params: Dict[str, Any]) -> None:
        try:
            self._run_tool(request_id, params)
        except Exception as exc:  # a call must always get its reply
            logger.exception("tools/call %r failed", params.get("name"))
            self._fail(request_id, -32603, f"Internal error: {_describe(exc)}")

    def _run_tool(self, request_id: Any, params: Dict[str, Any]) -> None:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            self._fail(
                request_id, -32602, "Invalid params: arguments must be an object."
            )
            return
        if name == "gaia_status":
            status = self.host.status()
            self._reply(
                request_id,
                {
                    "content": [{"type": "text", "text": json.dumps(status)}],
                    "structuredContent": status,
                    "isError": status["state"] == "failed",
                },
            )
            return
        if name != "gaia_task":
            self._fail(request_id, -32602, f"Unknown tool: {name}")
            return
        task = arguments.get("task")
        if not isinstance(task, str) or not task.strip():
            self._reply(request_id, _error_result("gaia_task needs a non-empty task."))
            return
        try:
            result = _task_result(
                self.host.run_task(
                    task.strip(),
                    request_id,
                    cancelled=lambda: request_id in self._cancelled,
                )
            )
        except TaskError as exc:
            result = _error_result(str(exc))
        # A cancelled request gets no response, as the protocol asks.
        if request_id in self._cancelled:
            self._cancelled.discard(request_id)
        else:
            self._reply(request_id, result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaia-agent --mcp",
        description="Serve the GAIA agent to an MCP host (Claude Code) over stdio.",
    )
    parser.add_argument("--model", default=None, help="model id override")
    parser.add_argument(
        "--task-timeout",
        type=float,
        default=TASK_TIMEOUT_S,
        help=f"seconds before a task is stopped (default: {int(TASK_TIMEOUT_S)})",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="DEBUG-level logging to the log file instead of errors only.",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    """Serve MCP on the real stdout, with everything else sent to stderr."""
    from gaia_agent.stdio import _configure_logging

    args = build_parser().parse_args(argv)
    # stdout carries only JSON-RPC frames. Keep a private handle on it, then
    # point fd 1 at stderr so a stray print anywhere cannot corrupt the wire.
    sys.stdout.flush()
    wire = os.fdopen(os.dup(1), "wb")
    # Logs go to the agent's log file, as on the stdio wire: a host that reads
    # stderr slowly would otherwise stall the agent on a full pipe.
    _configure_logging(sys.__stdout__, dev=args.dev)
    os.dup2(2, 1)
    host = AgentHost(lambda: build_agent(args.model), task_timeout_s=args.task_timeout)
    return McpServer(host, sys.stdin.buffer, wire).serve()
