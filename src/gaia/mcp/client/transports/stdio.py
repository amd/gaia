# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stdio transport for MCP protocol communication via subprocess."""

import json
import os
import shlex
import shutil
import signal
import subprocess
import time
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

from .base import MCPTransport

logger = get_logger(__name__)

# Operator characters shlex isolates into their own token when unquoted. A
# token made only of these was shell syntax; quoted uses stay inside a larger
# token and are inert under shell=False, so they are left alone.
_SHELL_OPERATOR_CHARS = frozenset(";|&<>()")

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
        env: Optional environment variables to merge with system env
        timeout: Request timeout in seconds (default: 30)
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

            # Merge environment if provided
            merged_env = None
            if self.env:
                merged_env = os.environ.copy()
                merged_env.update(self.env)

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
            )

            logger.debug(f"MCP server process started (PID: {self._process.pid})")

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
        try:
            stderr = self._process.stderr.read()
            if stderr:
                return stderr.strip()[-2000:]
        except Exception:
            pass
        return ""

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
        self, method: str, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Send a JSON-RPC request via stdin and read response from stdout.

        Args:
            method: JSON-RPC method name
            params: Optional parameters dictionary

        Returns:
            dict: JSON-RPC response

        Raises:
            RuntimeError: If not connected or process died
            TimeoutError: If request times out
            ValueError: If response is invalid JSON
        """
        if self._process is None:
            raise RuntimeError("Transport not connected")

        # Check if process is still alive
        if self._process.poll() is not None:
            raise self._process_died_error()

        # Build JSON-RPC request
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        self._request_id += 1

        if self.debug:
            logger.debug(f"Sending request: {json.dumps(request, indent=2)}")

        try:
            # Send request
            request_json = json.dumps(request) + "\n"
            self._process.stdin.write(request_json)
            self._process.stdin.flush()

            # Read response
            try:
                response_line = self._process.stdout.readline()
                if not response_line:
                    # Process may have died while we were waiting
                    if self._process.poll() is not None:
                        raise self._process_died_error()
                    raise RuntimeError("Server closed connection")

                response = json.loads(response_line)

                if self.debug:
                    logger.debug(f"Received response: {json.dumps(response, indent=2)}")

                return response

            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON response: {e}")

        except BrokenPipeError:
            # Process likely died - include stderr in the error
            if self._process.poll() is not None:
                raise self._process_died_error()
            raise RuntimeError("Server closed connection unexpectedly")

    def is_connected(self) -> bool:
        """Check if the subprocess is running.

        Returns:
            bool: True if process is alive
        """
        return self._process is not None and self._process.poll() is None
