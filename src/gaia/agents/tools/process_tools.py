# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ProcessToolsMixin — run long commands in the background and check on them.

``run_shell_command`` holds the turn until its command exits. These tools start
a command (a dev server, a build, a test suite, a download, an export) and
return at once, so the agent can keep working and look in on it later — in
this turn or a later one, since the processes belong to the agent, not to the
turn that started them.

Every launch goes through ``ShellToolsMixin._prepare_shell_line``, the gate
``run_shell_command`` itself uses: the same allowlist, confirmation tiers,
skill grants, path checks, rate limit and full-access audit. There is no
separate background policy to drift.
"""

from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.agents.base.checks import attach_check, check_from_command
from gaia.agents.base.verification import NOT_EXECUTED
from gaia.agents.tools.background_process import (
    EXITED,
    MAX_RUNNING,
    RUNNING,
    STOPPED,
    BackgroundProcess,
    ProcessLimitError,
    ProcessStartError,
    ProcessTable,
    complete_utf8,
    started_at_iso,
    terminal_view,
)
from gaia.logger import get_logger

logger = get_logger(__name__)

#: How long ``start_process`` watches a new process before returning, so a
#: command that fails at once (a typo, a missing script) says so immediately.
STARTUP_WATCH_SECONDS = 2.0
#: ``wait_for_process`` bounds, matching ``wait_for_condition``'s.
WAIT_DEFAULT_SECONDS = 120
WAIT_MAX_SECONDS = 600
#: Most new output one status shows; the rest is paged with read_process_output.
STATUS_OUTPUT_CHARS = 4000
DEFAULT_TAIL_LINES = 20
MAX_TAIL_LINES = 200
#: One read_process_output page.
PAGE_BYTES = 8000
#: How much of the end of the log a pattern wait rescans for a match that
#: straddles two reads.
_PATTERN_OVERLAP = 4096
_PATTERN_SCAN_BYTES = 256 * 1024

#: Tools that only look at or stop what the agent itself started.
PROCESS_STATE_TOOLS = frozenset(
    {"process_status", "read_process_output", "wait_for_process", "stop_process"}
)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _tail(data: bytes, count: int) -> tuple:
    """The last *count* lines of *data* as a terminal shows them, and whether any were cut."""
    lines = terminal_view(_decode(complete_utf8(data))).rstrip("\n").split("\n")
    text = "\n".join(lines[-count:])
    cut = len(lines) > count or len(text) > STATUS_OUTPUT_CHARS
    return text[-STATUS_OUTPUT_CHARS:], cut


class ProcessToolsMixin:
    """
    Mixin providing background-process tools.

    Tools provided:
    - start_process: launch a command line in the background, return at once
    - process_status: state, exit code, runtime and new output; all processes
      when no id is given
    - read_process_output: page through the full output
    - wait_for_process: wait (bounded, ends on Stop) for exit or an output line
    - stop_process: kill the process and everything it spawned

    Requires ``ShellToolsMixin`` on the same host: launching reuses its policy
    gate, and waiting reuses its Stop handling. Call
    :meth:`close_background_processes` from the host's teardown.
    """

    # -- host plumbing ------------------------------------------------------

    def _process_table(self) -> ProcessTable:
        table = getattr(self, "_background_processes", None)
        if table is None:
            scratch = getattr(self, "scratch_dir", None)
            log_dir = (
                Path(scratch) / "processes"
                if scratch
                else Path(tempfile.mkdtemp(prefix="gaia-processes-"))
            )
            table = ProcessTable(log_dir, owns_log_dir=True)
            self._background_processes = table
        return table

    def close_background_processes(self) -> None:
        """Stop every background process and delete its logs. Idempotent."""
        table = getattr(self, "_background_processes", None)
        if table is not None:
            table.close()
            self._background_processes = None

    def take_background_process_notices(self) -> List[str]:
        """One line per process that finished since the model last looked.

        Read by the agent loop at each step boundary — the only point a running
        turn can be told something it did not ask for. Each finish is reported
        once, and never for a process whose end the model already saw in a
        tool result.
        """
        table = getattr(self, "_background_processes", None)
        if table is None:
            return []
        notices = []
        for process in table.take_unreported_finishes():
            if process.state == EXITED:
                how = f"exited with code {process.exit_code}"
            elif process.state == STOPPED:
                how = "was stopped"
            else:
                how = f"failed ({process.error})"
            notices.append(
                f"Background process {process.process_id} ({process.command}) "
                f"{how} after {process.runtime_seconds:g}s. "
                f"process_status('{process.process_id}') shows its output."
            )
        return notices

    def _require_shell_host(self) -> None:
        if not callable(getattr(self, "_prepare_shell_line", None)):
            raise TypeError(
                f"{type(self).__name__} cannot run background processes: it has "
                "no ShellToolsMixin, whose policy gate every launch must pass. "
                "Add ShellToolsMixin to the agent's bases."
            )

    def _unknown_process(self, process_id: str) -> Dict[str, Any]:
        known = [p.process_id for p in self._process_table().all()]
        return {
            "status": "error",
            "error": (
                f"No background process '{process_id}'. "
                + (
                    f"Known ids: {', '.join(known)}."
                    if known
                    else "None has been started in this session."
                )
            ),
            "has_errors": True,
            "hint": "Call process_status() with no id to list them.",
        }

    # -- result shapes ------------------------------------------------------

    def _process_report(
        self, process: BackgroundProcess, tail_lines: int = DEFAULT_TAIL_LINES
    ) -> Dict[str, Any]:
        """State plus the output the model has not seen yet; marks it seen."""
        # Before anything else: a finish read here guarantees the state below
        # is final, so end_reported is never set on a report saying "running".
        finished = process.finished
        log = process.log
        total = log.total_bytes
        start = max(process.seen_offset, log.earliest_offset)
        report: Dict[str, Any] = {
            "status": "success",
            "process_id": process.process_id,
            "command": process.command,
            "cwd": process.plan.cwd,
            "state": process.state,
            "pid": process.pid,
            "exit_code": process.exit_code,
            "runtime_seconds": process.runtime_seconds,
            "started_at": started_at_iso(process),
            "output_bytes": total,
            "log_file": str(log.path),
            "has_errors": finished and process.has_errors,
        }
        if process.name:
            report["name"] = process.name
        if process.error:
            report["error"] = process.error
        if process.stop_error:
            report["error"] = process.stop_error
            report["has_errors"] = True
        if log.problem or log.discarded_bytes:
            report["output_problem"] = {
                "discarded_bytes": log.discarded_bytes,
                "reason": log.problem,
            }
        if finished:
            report["steps"] = [
                {"command": s.command, "return_code": s.return_code}
                for s in process.steps
            ]
            process.end_reported = True

        unseen = total - start
        if unseen > 0:
            # Never re-show bytes already seen; only the newest are shown.
            window_start = max(start, total - STATUS_OUTPUT_CHARS * 2)
            text, cut = _tail(log.read(window_start, total - window_start), tail_lines)
            report["new_output"] = text
            if cut or window_start > start:
                report["new_output_truncated"] = {
                    "unseen_bytes": unseen,
                    "unseen_from_offset": start,
                    "hint": (
                        "Only the newest lines are shown; read_process_output "
                        "from unseen_from_offset pages through the rest."
                    ),
                }
        else:
            report["new_output"] = ""
            if total:
                window_start = max(log.earliest_offset, total - STATUS_OUTPUT_CHARS * 2)
                report["last_lines"], _ = _tail(
                    log.read(window_start, total - window_start), tail_lines
                )
        process.seen_offset = total

        check = None
        if finished and process.state in (EXITED, STOPPED):
            tail_text = _decode(
                log.read(max(log.earliest_offset, total - 20_000), 20_000)
            )
            check = check_from_command(
                process.command,
                process.segments,
                process.exit_code if process.state == EXITED else None,
                tail_text,
                "",
            )
        return attach_check(report, check)

    # -- registration -------------------------------------------------------

    def register_process_tools(self) -> None:
        """Register the background-process tools."""
        from gaia.agents.base.tools import tool

        @tool(display_label="Starting background process")
        def start_process(
            command: str, working_directory: Optional[str] = None, name: str = ""
        ) -> Dict[str, Any]:
            """Start a long-running command in the background and return at once with its id — a dev server, build, test suite, download or export. Same allowlist and approval as run_shell_command. Check it later with process_status or wait_for_process; it keeps running across turns until it exits or stop_process.

            Args:
                command: Command line, same syntax and rules as run_shell_command
                working_directory: Directory to run it in; defaults to the shell session's
                name: Optional short label, e.g. "dev server"
            """
            return self._start_process(command, working_directory, name)

        @tool(atomic=True, display_label="Checking background process")
        def process_status(
            process_id: str = "", tail_lines: int = DEFAULT_TAIL_LINES
        ) -> Dict[str, Any]:
            """State, exit code, runtime and new output of a background process; with no id, lists every process this session started.

            Args:
                process_id: Id from start_process, e.g. "p1"; omit to list all
                tail_lines: Most lines of output to show (default 20, max 200)
            """
            if not (process_id or "").strip():
                return self._list_processes()
            process = self._process_table().get(process_id)
            if process is None:
                return self._unknown_process(process_id)
            try:
                lines = int(tail_lines)
            except (TypeError, ValueError):
                return {
                    "status": "error",
                    "error": f"tail_lines must be a whole number, got {tail_lines!r}.",
                    "has_errors": True,
                }
            return self._process_report(process, min(max(lines, 1), MAX_TAIL_LINES))

        @tool(atomic=True, display_label="Reading process output")
        def read_process_output(
            process_id: str, offset: int = 0, limit: int = PAGE_BYTES
        ) -> Dict[str, Any]:
            """Page through a background process's full output by byte offset; a negative offset counts back from the end.

            Args:
                process_id: Id from start_process
                offset: Byte offset to start at; -4000 reads the last 4000 bytes
                limit: Bytes to return, at most 8000
            """
            return self._read_output(process_id, offset, limit)

        @tool(
            atomic=True,
            display_label="Waiting for background process",
            # Outlast the longest wait the tool itself permits.
            timeout=WAIT_MAX_SECONDS + 60,
        )
        def wait_for_process(
            process_id: str, timeout: int = WAIT_DEFAULT_SECONDS, pattern: str = ""
        ) -> Dict[str, Any]:
            """Wait for a background process to exit, or until its output matches a regex (e.g. "Listening on"); 120s by default, 600s at most. Still running at the deadline is reported, not an error.

            Args:
                process_id: Id from start_process
                timeout: Seconds to wait, 1-600
                pattern: Regex to wait for in the output; empty waits for exit
            """
            return self._wait_for_process(process_id, timeout, pattern)

        @tool(atomic=True, display_label="Stopping background process")
        def stop_process(process_id: str) -> Dict[str, Any]:
            """Stop a background process and everything it started, and report its final output.

            Args:
                process_id: Id from start_process
            """
            process = self._process_table().get(process_id)
            if process is None:
                return self._unknown_process(process_id)
            was_running = not process.finished
            process.stop()
            report = self._process_report(process)
            report["stopped"] = was_running
            if not was_running:
                report["message"] = (
                    f"{process_id} had already finished; anything it left "
                    "running was stopped."
                )
            return report

    # -- tool bodies --------------------------------------------------------

    def _start_process(
        self, command: str, working_directory: Optional[str], name: str
    ) -> Dict[str, Any]:
        self._require_shell_host()
        bypass = self.full_access_active()
        if self._nothing_could_have_confirmed():
            return {**unattended_refusal(), "command": command}
        table = self._process_table()
        if len(table.running()) >= MAX_RUNNING:
            return {
                **NOT_EXECUTED,
                "status": "error",
                "error": (
                    f"{MAX_RUNNING} background processes are already running, "
                    "the most one session may have. Nothing was started."
                ),
                "has_errors": True,
                "running": [
                    {"process_id": p.process_id, "command": p.command}
                    for p in table.running()
                ],
                "hint": "Stop one with stop_process, or wait for one to finish.",
            }

        error, plan = self._prepare_shell_line(command, working_directory, bypass)
        if error is not None:
            return {**error, "command": command}
        if self._cancel_requested():
            return {
                **NOT_EXECUTED,
                "status": "error",
                "error": f"Stopped before starting, so nothing was run: {command}",
                "command": command,
                "has_errors": True,
                "cancelled": True,
            }

        try:
            process = table.start(command, plan, (name or "").strip())
        except (ProcessLimitError, ProcessStartError) as exc:
            return {
                **NOT_EXECUTED,
                "status": "error",
                "error": str(exc),
                "command": command,
                "has_errors": True,
            }
        if not bypass:
            self._record_command_execution()
        logger.info(
            "Started background process %s: %s (cwd %s)",
            process.process_id,
            command,
            plan.cwd,
        )

        process.wait(STARTUP_WATCH_SECONDS)
        if process.finished and not process.spawned:
            # Nothing ever reached a process: a missing program, or a
            # containment failure. Said plainly, as run_shell_command does.
            report = self._process_report(process)
            return {
                **NOT_EXECUTED,
                "status": "error",
                "error": (
                    process.error
                    or report.get("new_output")
                    or f"Could not start: {command}"
                ),
                "command": command,
                "process_id": process.process_id,
                "has_errors": True,
            }
        report = self._process_report(process)
        if process.state == RUNNING:
            report["hint"] = (
                f"Running in the background as {process.process_id}. Carry on "
                f"with other work; check it with process_status('{process.process_id}') "
                f"or wait_for_process('{process.process_id}')."
            )
        return report

    def _list_processes(self) -> Dict[str, Any]:
        processes = self._process_table().all()
        return {
            "status": "success",
            "processes": [
                {
                    "process_id": p.process_id,
                    "command": p.command,
                    **({"name": p.name} if p.name else {}),
                    "state": p.state,
                    "exit_code": p.exit_code,
                    "runtime_seconds": p.runtime_seconds,
                    "unread_output_bytes": max(p.log.total_bytes - p.seen_offset, 0),
                }
                for p in processes
            ],
            "running": sum(1 for p in processes if p.state == RUNNING),
            "max_running": MAX_RUNNING,
            "has_errors": False,
        }

    def _read_output(self, process_id: str, offset: Any, limit: Any) -> Dict[str, Any]:
        process = self._process_table().get(process_id)
        if process is None:
            return self._unknown_process(process_id)
        try:
            offset = int(offset)
            limit = int(limit)
        except (TypeError, ValueError):
            return {
                "status": "error",
                "error": f"offset and limit must be whole numbers, got {offset!r}, {limit!r}.",
                "has_errors": True,
            }
        if not 1 <= limit <= PAGE_BYTES:
            return {
                "status": "error",
                "error": f"limit must be 1-{PAGE_BYTES} bytes, got {limit}.",
                "has_errors": True,
            }
        log = process.log
        total = log.total_bytes
        earliest = log.earliest_offset
        requested = offset
        if offset < 0:
            offset = max(total + offset, 0)
        if offset > total:
            return {
                "status": "error",
                "error": f"offset {requested} is past the end of the output ({total} bytes).",
                "has_errors": True,
            }
        start = max(offset, earliest)
        data = complete_utf8(log.read(start, limit))
        end = start + len(data)
        page: Dict[str, Any] = {
            "status": "success",
            "process_id": process.process_id,
            "state": process.state,
            "content": _decode(data),
            "offset": start,
            "next_offset": end if end < total else None,
            "total_bytes": total,
            "offset_unit": "bytes",
            "has_errors": False,
        }
        if start > offset:
            page["skipped_bytes"] = start - offset
            page["note"] = (
                f"The first {earliest} bytes were discarded to keep the log "
                "under its size cap; this page starts at the oldest kept byte."
            )
        process.seen_offset = max(process.seen_offset, end)
        return page

    def _wait_for_process(
        self, process_id: str, timeout: Any, pattern: str
    ) -> Dict[str, Any]:
        process = self._process_table().get(process_id)
        if process is None:
            return self._unknown_process(process_id)
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            return {
                "status": "error",
                "error": f"timeout must be whole seconds, got {timeout!r}.",
                "has_errors": True,
            }
        if not 0 < timeout <= WAIT_MAX_SECONDS:
            return {
                "status": "error",
                "error": (
                    f"timeout must be between 1 and {WAIT_MAX_SECONDS} seconds, got "
                    f"{timeout}. Wait again afterwards if it is still worth waiting for."
                ),
                "has_errors": True,
            }
        matcher = None
        if pattern:
            try:
                matcher = re.compile(pattern, re.MULTILINE)
            except re.error as exc:
                return {
                    "status": "error",
                    "error": f"pattern is not a valid regex: {exc}",
                    "has_errors": True,
                }

        log = process.log
        start = time.monotonic()
        deadline = start + timeout
        scanned = max(log.earliest_offset, log.total_bytes - _PATTERN_SCAN_BYTES)
        matched: Optional[str] = None
        cancelled = False
        while True:
            # Before the scan: output written before the exit is then scanned.
            done = process.finished
            if matcher is not None:
                total = log.total_bytes
                if total > scanned:
                    window_start = max(scanned - _PATTERN_OVERLAP, log.earliest_offset)
                    view = terminal_view(
                        _decode(log.read(window_start, total - window_start))
                    )
                    found = matcher.search(view)
                    if found:
                        line_start = view.rfind("\n", 0, found.start()) + 1
                        line_end = view.find("\n", found.end())
                        matched = view[
                            line_start : line_end if line_end >= 0 else None
                        ].strip()
                        break
                    scanned = total
            if done:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._sleep_unless_cancelled(min(0.25, remaining)):
                cancelled = True
                break

        report = self._process_report(process)
        report["waited_seconds"] = round(time.monotonic() - start, 1)
        if matched is not None:
            report["matched"] = matched
        elif cancelled:
            report.update(
                {
                    "status": "error",
                    "error": (
                        f"The wait was stopped; {process_id} itself is still "
                        f"{process.state}."
                    ),
                    "cancelled": True,
                    "has_errors": True,
                }
            )
        elif not process.finished:
            report["still_running"] = True
            report["message"] = (
                f"{process_id} is still running after {timeout}s"
                + (f" and its output has not matched {pattern!r}" if pattern else "")
                + ". Wait again, do other work and check back, or stop it."
            )
        elif matcher is not None:
            report["message"] = (
                f"{process_id} finished without printing a match for {pattern!r}."
            )
        return report


def unattended_refusal() -> Dict[str, Any]:
    """Why nothing starts in the background when no person approved it.

    A blanket pre-approval admits only the read-only list, and in the
    foreground even those are cut off at their class timeout. A background
    process has no timeout, so it waits for a person or full access.
    """
    return {
        **NOT_EXECUTED,
        "status": "error",
        "error": (
            "Background processes run only when a person approves them or full "
            "access is on; this run approves prompts automatically or has no "
            "one to ask, so nothing was started."
        ),
        "has_errors": True,
        "hint": "Use run_shell_command, which ends at its timeout, or run interactively.",
    }


#: Every tool this mixin registers, for bundles and gates that name them.
PROCESS_TOOL_NAMES = ("start_process",) + tuple(sorted(PROCESS_STATE_TOOLS))
