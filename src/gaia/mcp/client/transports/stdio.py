# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stdio transport for MCP protocol communication via subprocess."""

import collections
import json
import os
import queue
import shlex
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from gaia.env import child_env
from gaia.logger import get_logger
from gaia.tool_cancellation import ToolCancelled, tool_cancelled
from gaia.utils.terminal import windows_hidden_popen_kwargs

from .base import MCPTransport

logger = get_logger(__name__)

# Operator characters shlex isolates into their own token when unquoted. A
# token made only of these was shell syntax; quoted uses stay inside a larger
# token and are inert under shell=False, so they are left alone.
_SHELL_OPERATOR_CHARS = frozenset(";|&<>()")

# How often a blocked read wakes to check for cancellation.
_POLL_INTERVAL_S = 0.25

# Lines of server stderr kept for crash diagnostics.
_STDERR_TAIL_LINES = 200

# A tool call may run as long as the agent's own per-tool limit
# (DEFAULT_TOOL_TIMEOUT in gaia.agents.base.agent).
TOOL_CALL_TIMEOUT_S = 180

# JSON-RPC "method not found": the reply to a request this client can't serve.
_METHOD_NOT_FOUND = -32601

_EOF = object()

_FIX_HINT = (
    'Split the program from its arguments instead: {"command": "npx", '
    '"args": ["-y", "@scope/server"]}. See docs/spec/mcp-client.mdx.'
)


def _split_command_string(command: str) -> List[str]:
    """Split a single-string server command into an argv list.

    Args:
        command: Full command line, e.g. ``"npx -y @scope/server"``.

    Returns:
        List[str]: argv tokens, program first, with ``~`` and environment
        variables expanded the way the shell used to expand them.

    Raises:
        ValueError: If the string is blank, unparseable, or carries shell
            syntax that would no longer be interpreted.
    """
    # Windows: double the backslashes so POSIX quoting rules apply without
    # eating path separators. Matches what CommandLineToArgvW would produce.
    raw = command.replace("\\", "\\\\") if os.name == "nt" else command

    lexer = shlex.shlex(raw, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise ValueError(
            f"MCP server command could not be parsed ({exc}): {command!r}. "
            f"Check for unbalanced quotes. {_FIX_HINT}"
        ) from exc

    if not tokens:
        raise ValueError(
            f"MCP server command is empty: {command!r}. Set 'command' to the "
            f'program to run, e.g. "npx". {_FIX_HINT}'
        )

    for token in tokens:
        # A backtick never forms its own token, so quoting state is already
        # lost here — reject it anywhere in the string rather than guess.
        if set(token) <= _SHELL_OPERATOR_CHARS or "`" in token:
            raise ValueError(
                f"MCP server command contains shell syntax ({token!r}): "
                f"{command!r}. GAIA launches MCP servers without a shell, so this "
                f"would be passed through as literal text rather than interpreted. "
                f"{_FIX_HINT}"
            )

    # The shell used to expand these; do it here so existing configs using
    # ~ or $VAR/%VAR% keep resolving instead of reaching the child literally.
    return [os.path.expandvars(os.path.expanduser(token)) for token in tokens]


class StdioTransport(MCPTransport):
    """Stdio-based transport using subprocess for MCP servers.

    This transport launches MCP servers as subprocesses and communicates
    via stdin/stdout using JSON-RPC messages.

    Servers are always launched with ``shell=False``. Two input shapes are
    accepted:

    1. Legacy: a single command string (e.g. ``"npx -y server"``), tokenized
       here into an argv list rather than handed to a shell.
    2. Modern: ``command`` plus an ``args`` list (matches the Anthropic format).

    Args:
        command: Base command to start the MCP server (e.g., "npx" or "python")
        args: Optional list of arguments (e.g., ["-y", "@modelcontextprotocol/server-github"])
        env: Extra variables for the server, overlaid on the child environment
        timeout: Seconds to wait for each reply (default: 30). A server that
            stays silent that long raises ``TimeoutError``.
        debug: Enable debug logging (default: False)
    """

    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        debug: bool = False,
    ):
        self.command = command
        self.args = args
        self.env = env
        self.timeout = timeout
        self.debug = debug
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        # One request in flight at a time: replies are read off a shared pipe.
        self._request_lock = threading.Lock()
        self._stdout_lines: Optional[queue.Queue] = None
        self._stderr_tail: Optional[collections.deque] = None
        self._stderr_reader: Optional[threading.Thread] = None

    @property
    def label(self) -> str:
        """The program and the package or script it runs, for errors.

        Never the full command line: arguments can carry tokens.
        """
        tokens = [self.command] + list(self.args) if self.args else self.command.split()
        if not tokens:
            return "?"
        names = [os.path.basename(tokens[0])]
        target = next(
            (t for t in tokens[1:] if not t.startswith("-") and "=" not in t), None
        )
        if target is not None:
            # ``@scope/server`` is a package name; a path is shown by its file.
            if not target.startswith("@"):
                target = os.path.basename(target.rstrip("/\\")) or target
            names.append(target)
        return " ".join(names)[:120]

    def _start_readers(self) -> None:
        """Drain stdout into a queue and stderr into a tail buffer.

        Reads on a pipe can't time out, so they happen on daemon threads and
        ``send_request`` waits on the queue instead. Draining stderr also stops
        a chatty server from filling the pipe and blocking mid-write.
        """
        self._stdout_lines = queue.Queue()
        self._stderr_tail = collections.deque(maxlen=_STDERR_TAIL_LINES)
        threading.Thread(
            target=self._pump_stdout,
            args=(self._process.stdout, self._stdout_lines),
            name=f"mcp-stdout:{self._process.pid}",
            daemon=True,
        ).start()
        self._stderr_reader = threading.Thread(
            target=self._pump_stderr,
            args=(self._process.stderr, self._stderr_tail),
            name=f"mcp-stderr:{self._process.pid}",
            daemon=True,
        )
        self._stderr_reader.start()

    @staticmethod
    def _pump_stdout(stream, lines: queue.Queue) -> None:
        try:
            while True:
                line = stream.readline()
                if not isinstance(line, str) or not line:
                    break
                lines.put(line)
        except (OSError, ValueError) as e:
            logger.debug("MCP server stdout closed: %s", e)
        finally:
            lines.put(_EOF)

    @staticmethod
    def _pump_stderr(stream, tail: collections.deque) -> None:
        try:
            while True:
                line = stream.readline()
                if not isinstance(line, str) or not line:
                    return
                tail.append(line)
        except (OSError, ValueError) as e:
            logger.debug("MCP server stderr closed: %s", e)

    def _build_argv(self) -> List[str]:
        """Resolve this transport's command into an argv list for ``shell=False``.

        Returns:
            List[str]: Program path (resolved via PATH when possible) plus args.

        Raises:
            ValueError: If a single-string command cannot be tokenized safely.
        """
        if self.args:
            program, extra = self.command, list(self.args)
        else:
            tokens = _split_command_string(self.command)
            program, extra = tokens[0], tokens[1:]

        # Resolve via PATH (handles Windows .cmd/.bat extensions). Keep this —
        # Popen needs the .cmd suffix to launch npx-style shims with shell=False.
        resolved = shutil.which(program)
        return [resolved or program] + extra

    def connect(self) -> bool:
        """Launch the MCP server subprocess.

        Returns:
            bool: True if process started successfully

        Raises:
            ValueError: If the configured command cannot be tokenized safely.
            RuntimeError: If the process dies immediately after launch.
        """
        if self._process is not None:
            logger.warning("Transport already connected")
            return True

        # Built outside the try so a bad command surfaces as an actionable
        # ValueError instead of a bare "failed to start" False.
        argv = self._build_argv()

        try:
            if self.debug:
                logger.debug(f"Starting MCP server with command: {argv}")

            # Never None: an inherited environment would carry GAIA's own
            # credentials into the server.
            merged_env = child_env(self.env)

            self._process = subprocess.Popen(
                argv,
                shell=False,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=merged_env,
                # close_fds=False lets Python use posix_spawn instead of
                # fork+exec.  fork() in a process that has background C
                # threads (e.g. after loading native extensions via
                # LemonadeManager) crashes the child with SIGSEGV on macOS.
                close_fds=False,
                **windows_hidden_popen_kwargs(),
            )

            logger.debug(f"MCP server process started (PID: {self._process.pid})")

            self._start_readers()

            # Brief pause to catch immediate crashes (e.g. missing binary, SIGSEGV on load)
            time.sleep(0.1)
            if self._process.poll() is not None:
                error = self._process_died_error()
                self._process = None
                raise error

            return True

        except RuntimeError:
            # Propagate crash diagnostics (from early death detection) to caller
            raise
        except Exception as e:
            logger.error(f"Failed to start MCP server: {e}")
            self._process = None
            return False

    def disconnect(self) -> None:
        """Terminate the MCP server subprocess."""
        if self._process is None:
            return

        try:
            logger.debug(f"Terminating MCP server process (PID: {self._process.pid})")

            # If process already died, capture stderr for diagnostics
            if self._process.poll() is not None:
                stderr = self._read_stderr()
                if stderr:
                    logger.debug(f"Server stderr before disconnect:\n{stderr}")

            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Process did not terminate, killing...")
                self._process.kill()
                self._process.wait()

            logger.debug("MCP server process terminated")

        except Exception as e:
            logger.error(f"Error during disconnect: {e}")

        finally:
            self._process = None

    @staticmethod
    def _format_exit_code(exit_code: int) -> str:
        """Format an exit code with signal name if applicable.

        Negative exit codes on Unix indicate termination by signal.
        E.g. -11 means SIGSEGV, -9 means SIGKILL.

        Args:
            exit_code: Process exit code

        Returns:
            str: Formatted exit code, e.g. "-11 (SIGSEGV)" or "1"
        """
        if exit_code is None:
            return "None"
        text = str(exit_code)
        if exit_code < 0:
            try:
                sig = signal.Signals(-exit_code)
                text += f" ({sig.name})"
            except ValueError:
                pass
        return text

    def _read_stderr(self) -> str:
        """Read available stderr from the process without blocking.

        Only reads when the process has exited (poll() is not None) to
        prevent blocking on a live process's stderr pipe.

        Returns:
            str: Stderr content (truncated to last 2000 chars), or empty string
        """
        if self._process is None or self._process.stderr is None:
            return ""
        # Only read stderr from a dead process to avoid blocking
        if self._process.poll() is None:
            return ""
        if self._stderr_tail is not None:
            # The process is gone, so its stderr reader reaches EOF promptly.
            if self._stderr_reader is not None:
                self._stderr_reader.join(timeout=1)
            stderr = "".join(self._stderr_tail)
            return stderr.strip()[-2000:] if stderr else ""
        try:
            stderr = self._process.stderr.read()
        except (OSError, ValueError) as e:
            logger.debug("Could not read the MCP server's stderr: %s", e)
            return ""
        return stderr.strip()[-2000:] if stderr else ""

    def _process_died_error(self) -> RuntimeError:
        """Build a RuntimeError with exit code, signal name, and stderr from a dead process."""
        exit_code = self._process.returncode if self._process else None
        stderr = self._read_stderr()
        formatted_code = self._format_exit_code(exit_code)
        msg = f"MCP server process died (exit code: {formatted_code})"
        if stderr:
            msg += f"\nServer stderr:\n{stderr}"
        if exit_code is not None and exit_code < 0:
            msg += (
                "\nHint: The server crashed with a signal. "
                "Try: uvx cache clean && uvx <server-command>"
            )
        return RuntimeError(msg)

    def send_request(
        self,
        method: str,
        params: Dict[str, Any] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send a JSON-RPC request via stdin and wait for its reply.

        Notifications and replies to earlier, timed-out requests are skipped;
        a request from the server gets a "method not found" error back so it
        doesn't wait on us. The wait ends early once the agent abandons the
        tool call (``tool_cancelled()``), and a request given up on is
        cancelled on the server with ``notifications/cancelled``.

        Args:
            method: JSON-RPC method name
            params: Optional parameters dictionary
            timeout: Seconds to wait for the reply. Defaults to
                ``self.timeout``, or ``TOOL_CALL_TIMEOUT_S`` for ``tools/call``
                when that is longer.

        Returns:
            dict: JSON-RPC response

        Raises:
            RuntimeError: If not connected, the process died, or the server
                rejected the request without naming it
            TimeoutError: If the server doesn't reply in time
            ToolCancelled: If the agent stopped waiting for this tool call
            ValueError: If the server writes something other than JSON-RPC
        """
        # One handle for the whole request: disconnect() may clear the attribute.
        proc = self._process
        if proc is None:
            raise RuntimeError("Transport not connected")

        # Check if process is still alive
        if proc.poll() is not None:
            raise self._process_died_error()

        if timeout is not None:
            wait_s = timeout
        elif method == "tools/call":
            wait_s = max(self.timeout, TOOL_CALL_TIMEOUT_S)
        else:
            wait_s = self.timeout
        # One deadline covers waiting for the lock and for the reply.
        deadline = time.monotonic() + wait_s

        if not self._request_lock.acquire(timeout=max(wait_s, 0)):
            raise TimeoutError(
                f"MCP server '{self.label}' was still busy with another request "
                f"after {wait_s:g}s, so '{method}' was never sent."
            )
        try:
            request_id = self._request_id
            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }

            if self.debug:
                logger.debug(f"Sending request: {json.dumps(request, indent=2)}")

            self._write(proc, request)
            try:
                response = self._await_reply(proc, request_id, method, wait_s, deadline)
            except (TimeoutError, ToolCancelled) as e:
                self._cancel_on_server(proc, request_id, str(e))
                raise

            if self.debug:
                logger.debug(f"Received response: {json.dumps(response, indent=2)}")

            return response
        finally:
            self._request_lock.release()

    def _write(self, proc: subprocess.Popen, message: Dict[str, Any]) -> None:
        try:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as e:
            # Process likely died - include stderr in the error
            if proc.poll() is not None:
                raise self._process_died_error() from e
            raise RuntimeError(
                f"MCP server '{self.label}' closed its input unexpectedly"
            ) from e

    def _cancel_on_server(
        self, proc: subprocess.Popen, request_id: int, reason: str
    ) -> None:
        """Tell the server to drop a request nobody is waiting for any more."""
        try:
            self._write(
                proc,
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": request_id, "reason": reason[:200]},
                },
            )
        except RuntimeError as e:
            # The caller is already raising the reason it gave up.
            logger.warning(
                "Could not cancel request %s on MCP server '%s': %s",
                request_id,
                self.label,
                e,
            )

    def _await_reply(
        self,
        proc: subprocess.Popen,
        request_id: int,
        method: str,
        wait_s: float,
        deadline: float,
    ) -> Dict[str, Any]:
        while True:
            if tool_cancelled():
                raise ToolCancelled(
                    f"Stopped waiting for MCP server '{self.label}' to answer "
                    f"'{method}': the tool call was cancelled."
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"MCP server '{self.label}' did not answer '{method}' within "
                    f"{wait_s:g}s. It may be hung or overloaded; check that it "
                    "starts and answers when run on its own from a terminal, "
                    "then reconnect it."
                )
            try:
                line = self._stdout_lines.get(timeout=min(remaining, _POLL_INTERVAL_S))
            except queue.Empty:
                # A child still holding the pipe open hides EOF; poll as well.
                if proc.poll() is not None and self._stdout_lines.empty():
                    raise self._closed_error(proc, method)
                continue

            if line is _EOF:
                raise self._closed_error(proc, method)
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"MCP server '{self.label}' wrote non-JSON to stdout while "
                    f"answering '{method}': {line[:200]!r}. Servers must log to "
                    "stderr, never stdout."
                ) from e
            if not isinstance(message, dict):
                raise ValueError(
                    f"MCP server '{self.label}' sent a JSON-RPC message that is "
                    f"not an object: {line[:200]!r}"
                )

            if "method" in message:
                self._handle_server_message(proc, message)
                continue
            if message.get("id") is None and "error" in message:
                # A parse error has no id to match; it answers what we just sent.
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else error
                raise RuntimeError(
                    f"MCP server '{self.label}' rejected '{method}': {detail}"
                )
            if message.get("id") != request_id:
                logger.debug(
                    "MCP server '%s': dropping reply to id %r while waiting on %r",
                    self.label,
                    message.get("id"),
                    request_id,
                )
                continue
            return message

    def _handle_server_message(
        self, proc: subprocess.Popen, message: Dict[str, Any]
    ) -> None:
        """Answer a server request with an error; log a notification."""
        if "id" not in message:
            logger.debug(
                "MCP server '%s' notification: %s", self.label, message["method"]
            )
            return
        self._write(
            proc,
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {
                    "code": _METHOD_NOT_FOUND,
                    "message": "GAIA's MCP client does not support "
                    f"'{message['method']}'",
                },
            },
        )

    def _closed_error(self, proc: subprocess.Popen, method: str) -> RuntimeError:
        """The error for a server whose stdout reached EOF mid-request."""
        if self._process is not proc:
            return RuntimeError(
                f"MCP server '{self.label}' was disconnected while answering "
                f"'{method}'."
            )
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            return RuntimeError(
                f"MCP server '{self.label}' closed its output but is still "
                "running; reconnect it."
            )
        return self._process_died_error()

    def is_connected(self) -> bool:
        """Check if the subprocess is running.

        Returns:
            bool: True if process is alive
        """
        return self._process is not None and self._process.poll() is None
