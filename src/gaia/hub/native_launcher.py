# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Subprocess launcher for native (C++) Agent Hub agents.

Native agents are standalone binaries that speak **JSON-RPC 2.0 over stdio** —
one JSON object per line on the subprocess's stdin (requests) and stdout
(responses), with stderr reserved for diagnostics. This is the same wire
protocol the Electron desktop app's Agent Process Manager uses and that the
C++ ``StdioTransport`` (``cpp/src/mcp_client.cpp``) and the Python MCP
``StdioTransport`` (``gaia.mcp.client.transports.stdio``) already implement.

Until now those binaries could only be driven from Electron, so web-backend
users couldn't start native agents (the registry stubbed them with a
``_noop_factory`` that raised). :class:`NativeAgentLauncher` lifts that
restriction: it spawns ``<binary> --stdio``, performs the ``initialize``
handshake, exchanges JSON-RPC requests, and shuts the process down gracefully —
all from plain Python, no Electron required.

Design notes:

* **One reader thread per process.** stdout is drained continuously into a
  shared mailbox so request/response matching is by ``id`` and a slow or
  chatty agent can't deadlock the pipe. This is also what makes a real
  per-request *timeout* possible on Windows, where ``select`` doesn't work on
  pipes.
* **Fail loudly** (see ``CLAUDE.md``): a missing binary, an unsupported
  platform, a botched handshake, a JSON-RPC error response, or a timeout each
  raise a :class:`NativeAgentError` whose message names *what* failed, *what*
  to do, and *where* to look. The only swallowed error is a best-effort
  ``shutdown`` RPC during :meth:`stop` — its failure is logged, then the
  process is terminated anyway, because the caller's intent is "make it stop".

Schema reference: ``docs/spec/agent-hub-restructure.mdx`` (native/C++ agents).
"""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Mapping, Optional, Union

from gaia.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "NativeAgentError",
    "NativeAgentTimeout",
    "NativeAgentLauncher",
    "current_platform",
]

# Docs URL surfaced in error messages so the operator knows where to look next.
_SPEC_URL = "https://amd-gaia.ai/docs/spec/agent-hub-restructure"

# Flag the binary is launched with; the native agent is expected to enter its
# JSON-RPC-over-stdio loop when it sees this.
_STDIO_FLAG = "--stdio"

# How long to wait for the process to die before escalating to SIGKILL.
_DEFAULT_SHUTDOWN_TIMEOUT = 5.0
# Default per-request wait for a JSON-RPC response.
_DEFAULT_REQUEST_TIMEOUT = 30.0
# Default wait for the initialize handshake to complete after spawn.
_DEFAULT_STARTUP_TIMEOUT = 10.0
# Grace window after spawn to catch an immediate crash (bad binary, load error).
_CRASH_DETECT_DELAY = 0.1
# Cap on retained stderr lines for crash diagnostics.
_STDERR_RING = 200


class NativeAgentError(RuntimeError):
    """Raised when a native agent can't be launched or driven over stdio.

    The message always names three things (per ``CLAUDE.md``): *what* failed,
    *what* the caller should do, and *where* to look. Subclasses
    :class:`RuntimeError` so existing ``except RuntimeError`` callers keep
    working.
    """


class NativeAgentTimeout(NativeAgentError, TimeoutError):
    """Raised when a JSON-RPC request or handshake exceeds its deadline.

    Multiply-inherits :class:`TimeoutError` so callers can catch either the
    GAIA-specific :class:`NativeAgentError` family or the stdlib timeout type.
    """


# ---------------------------------------------------------------------------
# Platform resolution
# ---------------------------------------------------------------------------

# platform.machine() values that map onto our canonical arch suffixes.
_X64_MACHINES = frozenset({"x86_64", "amd64", "x64"})
_ARM64_MACHINES = frozenset({"arm64", "aarch64"})

# platform.system() values that map onto our canonical OS prefixes.
_OS_PREFIX = {"windows": "win", "linux": "linux", "darwin": "darwin"}


def current_platform() -> str:
    """Return the running platform as a hub triple, e.g. ``"win-x64"``.

    Mirrors the triples used by ``requirements.platforms`` and
    ``cpp.binaries`` in ``gaia-agent.yaml`` (see
    :data:`gaia.hub.manifest.VALID_PLATFORMS`).

    Raises:
        NativeAgentError: If the OS or CPU architecture isn't one the hub
            packages binaries for.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    os_prefix = _OS_PREFIX.get(system)
    if os_prefix is None:
        raise NativeAgentError(
            f"Unsupported operating system {platform.system()!r}. Native agents "
            f"ship binaries for Windows, Linux, and macOS only. See {_SPEC_URL}."
        )

    if machine in _X64_MACHINES:
        arch = "x64"
    elif machine in _ARM64_MACHINES:
        arch = "arm64"
    else:
        raise NativeAgentError(
            f"Unsupported CPU architecture {platform.machine()!r}. Native agents "
            f"ship x64 and arm64 binaries only. See {_SPEC_URL}."
        )

    return f"{os_prefix}-{arch}"


# ---------------------------------------------------------------------------
# Per-process bookkeeping
# ---------------------------------------------------------------------------


class _ProcState:
    """Internal per-subprocess state: reader threads, mailbox, id counter."""

    def __init__(self, proc: subprocess.Popen, agent_dir: Path, binary: Path):
        self.proc = proc
        self.agent_dir = agent_dir
        self.binary = binary
        self.server_info: Dict[str, Any] = {}

        self._write_lock = threading.Lock()
        self._next_id = 0

        # Mailbox of id -> response, guarded by _cond. The reader thread fills
        # it; send_rpc drains its own id.
        self._cond = threading.Condition()
        self._responses: Dict[int, Dict[str, Any]] = {}
        self._notifications: Deque[Dict[str, Any]] = deque(maxlen=256)
        self._stdout_closed = False

        # Ring buffer of recent stderr lines for crash diagnostics.
        self._stderr_lines: Deque[str] = deque(maxlen=_STDERR_RING)

        self._reader = threading.Thread(
            target=self._read_stdout, name=f"native-stdout-{proc.pid}", daemon=True
        )
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, name=f"native-stderr-{proc.pid}", daemon=True
        )
        self._reader.start()
        self._stderr_reader.start()

    # -- thread bodies ---------------------------------------------------

    def _read_stdout(self) -> None:
        stream = self.proc.stdout
        if stream is None:  # pragma: no cover - start() always pipes stdout
            return
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    # A native agent must keep stdout clean for JSON-RPC; a
                    # non-JSON line is a protocol bug on its side. Record it as
                    # diagnostics rather than crashing the reader.
                    logger.warning(
                        "native-launcher: non-JSON line on stdout (pid=%s): %s",
                        self.proc.pid,
                        line[:500],
                    )
                    self._stderr_lines.append(f"[stdout non-json] {line[:500]}")
                    continue
                with self._cond:
                    if isinstance(msg, dict) and msg.get("id") is not None:
                        self._responses[msg["id"]] = msg
                    else:
                        self._notifications.append(msg)
                    self._cond.notify_all()
        finally:
            with self._cond:
                self._stdout_closed = True
                self._cond.notify_all()

    def _read_stderr(self) -> None:
        stream = self.proc.stderr
        if stream is None:  # pragma: no cover - start() always pipes stderr
            return
        for line in stream:
            line = line.rstrip("\n")
            if line:
                self._stderr_lines.append(line)

    # -- helpers ---------------------------------------------------------

    def recent_stderr(self) -> str:
        """Return the most recent stderr lines (truncated) for diagnostics."""
        return "\n".join(self._stderr_lines)

    def allocate_id(self) -> int:
        with self._write_lock:
            rid = self._next_id
            self._next_id += 1
            return rid

    def write_line(self, payload: str) -> None:
        stdin = self.proc.stdin
        if stdin is None:  # pragma: no cover - start() always pipes stdin
            raise NativeAgentError("native agent stdin is not available")
        with self._write_lock:
            try:
                stdin.write(payload + "\n")
                stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise NativeAgentError(
                    f"native agent stdin closed (pid={self.proc.pid}): {e}. The "
                    f"process likely crashed; check its stderr."
                ) from e


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------


class NativeAgentLauncher:
    """Spawn and drive native (C++) agents over JSON-RPC 2.0 stdio.

    A single launcher can manage multiple concurrent native-agent processes;
    each :class:`subprocess.Popen` returned by :meth:`start` is the handle the
    caller passes back to :meth:`send_rpc` and :meth:`stop`.

    Example::

        launcher = NativeAgentLauncher()
        proc = launcher.start(agent_dir, "bin/my-agent")     # spawns + handshake
        tools = launcher.send_rpc(proc, "tools/list")        # {"tools": [...]}
        launcher.stop(proc)                                  # graceful shutdown

    Or resolve the platform binary straight from a parsed manifest::

        proc = launcher.start_from_manifest(manifest)

    Args:
        startup_timeout: Seconds to wait for the ``initialize`` handshake.
        request_timeout: Default seconds to wait for a JSON-RPC response.
        shutdown_timeout: Seconds to wait for graceful exit before SIGKILL.
        client_name: ``clientInfo.name`` advertised during the handshake.
        debug: Emit verbose request/response logs.
    """

    def __init__(
        self,
        *,
        startup_timeout: float = _DEFAULT_STARTUP_TIMEOUT,
        request_timeout: float = _DEFAULT_REQUEST_TIMEOUT,
        shutdown_timeout: float = _DEFAULT_SHUTDOWN_TIMEOUT,
        client_name: str = "gaia-hub",
        debug: bool = False,
    ):
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.shutdown_timeout = shutdown_timeout
        self.client_name = client_name
        self.debug = debug
        self._states: Dict[int, _ProcState] = {}
        self._states_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Binary resolution
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_binary(
        binaries: Mapping[str, str],
        agent_dir: Union[str, Path],
        *,
        platform_triple: Optional[str] = None,
    ) -> Path:
        """Resolve the binary for the current platform from a ``cpp.binaries`` map.

        Args:
            binaries: The manifest's ``cpp.binaries`` mapping (platform triple
                -> binary path relative to *agent_dir*).
            agent_dir: Directory the agent package is installed in.
            platform_triple: Override the detected platform (mainly for tests).

        Returns:
            Absolute path to the binary for this platform.

        Raises:
            NativeAgentError: If no binary is declared for this platform
                (wrong-platform) or the declared binary file is missing.
        """
        triple = platform_triple or current_platform()
        rel = binaries.get(triple)
        if not rel:
            available = ", ".join(sorted(binaries)) or "(none)"
            raise NativeAgentError(
                f"No native binary for platform {triple!r}. This agent ships "
                f"binaries for: {available}. Build/download a {triple} binary or "
                f"run on a supported platform. See {_SPEC_URL}."
            )
        binary = (Path(agent_dir) / rel).resolve()
        if not binary.exists():
            raise NativeAgentError(
                f"Native binary not found: {binary} (declared for {triple!r}). "
                f"Ensure the agent package was built/installed for this platform. "
                f"See {_SPEC_URL}."
            )
        return binary

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        agent_dir: Union[str, Path],
        binary_name: str,
        *,
        extra_args: Optional[list] = None,
        env: Optional[Mapping[str, str]] = None,
        handshake: bool = True,
    ) -> subprocess.Popen:
        """Spawn ``<binary> --stdio`` and (optionally) run the handshake.

        Args:
            agent_dir: Directory the agent lives in; *binary_name* is resolved
                relative to it (an absolute *binary_name* is used as-is).
            binary_name: Path to the executable (relative to *agent_dir* or
                absolute).
            extra_args: Extra CLI args appended after ``--stdio``.
            env: Extra environment variables merged over ``os.environ``.
            handshake: When True (default), send ``initialize`` and store the
                server's reply; raise if it fails.

        Returns:
            The running :class:`subprocess.Popen`. Pass it to :meth:`send_rpc`
            and :meth:`stop`.

        Raises:
            NativeAgentError: If the binary is missing, the process dies on
                startup, or the handshake fails.
        """
        agent_dir = Path(agent_dir)
        binary_path = Path(binary_name)
        if not binary_path.is_absolute():
            binary_path = (agent_dir / binary_path).resolve()

        if not binary_path.exists():
            raise NativeAgentError(
                f"Native agent binary not found: {binary_path}. Check the "
                f"manifest's cpp.binaries path and that the package was installed "
                f"for this platform ({current_platform()}). See {_SPEC_URL}."
            )
        if binary_path.is_dir():
            raise NativeAgentError(
                f"Native agent binary path is a directory, not an executable: "
                f"{binary_path}. See {_SPEC_URL}."
            )

        cmd = [str(binary_path), _STDIO_FLAG]
        if extra_args:
            cmd.extend(str(a) for a in extra_args)

        merged_env = None
        if env:
            merged_env = os.environ.copy()
            merged_env.update(env)

        if self.debug:
            logger.debug("native-launcher: spawning %s", cmd)

        try:
            proc = subprocess.Popen(  # noqa: S603 - binary path is operator-controlled
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                cwd=str(agent_dir) if agent_dir.exists() else None,
                env=merged_env,
                # close_fds=False lets Python use posix_spawn instead of
                # fork+exec; fork() in a process holding background native
                # threads can SIGSEGV the child on macOS (see mcp stdio
                # transport for the same rationale).
                close_fds=False,
            )
        except OSError as e:
            raise NativeAgentError(
                f"Failed to spawn native agent {binary_path}: {e}. Verify the "
                f"file is executable. See {_SPEC_URL}."
            ) from e

        # Catch an immediate crash (bad binary, missing shared lib, load error).
        time.sleep(_CRASH_DETECT_DELAY)
        if proc.poll() is not None:
            stderr = self._drain_dead_stderr(proc)
            code = self._format_exit_code(proc.returncode)
            msg = (
                f"Native agent {binary_path.name} exited immediately "
                f"(exit code {code}) before the JSON-RPC handshake."
            )
            if stderr:
                msg += f"\nstderr:\n{stderr}"
            raise NativeAgentError(msg)

        state = _ProcState(proc, agent_dir, binary_path)
        with self._states_lock:
            self._states[proc.pid] = state

        if handshake:
            try:
                state.server_info = self._initialize(state)
            except NativeAgentError:
                # Handshake failed — don't leak the process.
                self._force_stop(state)
                raise

        logger.info("native-launcher: started %s (pid=%s)", binary_path.name, proc.pid)
        return proc

    def start_from_manifest(
        self,
        manifest: Any,
        *,
        agent_dir: Optional[Union[str, Path]] = None,
        platform_triple: Optional[str] = None,
        **kwargs: Any,
    ) -> subprocess.Popen:
        """Resolve the platform binary from a parsed manifest, then :meth:`start`.

        Args:
            manifest: A :class:`gaia.hub.manifest.AgentManifest` with a populated
                ``cpp.binaries`` map.
            agent_dir: Package directory. Defaults to the manifest's
                ``source_path`` parent.
            platform_triple: Override the detected platform (mainly for tests).
            **kwargs: Forwarded to :meth:`start`.

        Raises:
            NativeAgentError: If the manifest isn't a runnable C++ agent or the
                binary can't be resolved.
        """
        cpp = getattr(manifest, "cpp", None)
        if cpp is None or not getattr(cpp, "binaries", None):
            raise NativeAgentError(
                f"Manifest {getattr(manifest, 'id', '?')!r} has no cpp.binaries; "
                f"it is not a native agent. See {_SPEC_URL}."
            )

        if agent_dir is None:
            source_path = getattr(manifest, "source_path", None)
            if source_path is None:
                raise NativeAgentError(
                    "Cannot resolve agent_dir: manifest has no source_path. Pass "
                    "agent_dir= explicitly to start_from_manifest()."
                )
            agent_dir = Path(source_path).parent

        binary = self.resolve_binary(
            cpp.binaries, agent_dir, platform_triple=platform_triple
        )
        return self.start(agent_dir, binary, **kwargs)

    def stop(self, proc: subprocess.Popen, *, graceful: bool = True) -> int:
        """Stop a native agent: graceful ``shutdown`` RPC, then terminate/kill.

        Best-effort and idempotent — calling it on an already-dead process just
        reaps it. The ``shutdown`` RPC's failure is logged, not raised: the
        caller's intent is "make it stop", so we proceed to terminate regardless.

        Args:
            proc: The handle returned by :meth:`start`.
            graceful: When True (default), try the ``shutdown`` RPC and let the
                process exit on its own before escalating to terminate/kill.

        Returns:
            The process exit code.
        """
        state = self._states.get(proc.pid)

        if proc.poll() is not None:
            self._discard(proc.pid)
            return proc.returncode

        if graceful and state is not None:
            try:
                self.send_rpc(proc, "shutdown", timeout=min(self.shutdown_timeout, 5.0))
            except NativeAgentError as e:
                # Agent may not implement shutdown, or may exit before replying.
                logger.debug(
                    "native-launcher: shutdown RPC for pid=%s did not complete "
                    "cleanly (%s); terminating",
                    proc.pid,
                    e,
                )
            # Close stdin to signal EOF, then give it a moment to exit.
            self._close_stdin(proc)
            try:
                proc.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                pass

        if proc.poll() is None:
            self._force_stop(state if state is not None else proc)
        else:
            self._discard(proc.pid)
        return proc.returncode

    # ------------------------------------------------------------------
    # RPC
    # ------------------------------------------------------------------

    def send_rpc(
        self,
        proc: subprocess.Popen,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send a JSON-RPC 2.0 request and return its ``result``.

        Handles framing (one JSON object per line), id allocation and matching,
        and error propagation.

        Args:
            proc: The handle returned by :meth:`start`.
            method: JSON-RPC method (e.g. ``"tools/list"``, ``"ping"``).
            params: Optional params object.
            timeout: Per-request override of the launcher default.

        Returns:
            The ``result`` field of the response (``{}`` if the agent omits it).

        Raises:
            NativeAgentError: If the process isn't tracked/alive or the agent
                returns a JSON-RPC error.
            NativeAgentTimeout: If no matching response arrives in time.
        """
        state = self._states.get(proc.pid)
        if state is None:
            raise NativeAgentError(
                f"Process pid={proc.pid} is not managed by this launcher. Start "
                f"it with NativeAgentLauncher.start() first."
            )

        if proc.poll() is not None:
            raise self._dead_process_error(state)

        wait = self.request_timeout if timeout is None else timeout
        rid = state.allocate_id()
        request = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params or {},
        }

        if self.debug:
            logger.debug("native-launcher: -> %s", json.dumps(request))

        state.write_line(json.dumps(request))

        deadline = time.monotonic() + wait
        with state._cond:  # noqa: SLF001 - internal coordination
            while rid not in state._responses:  # noqa: SLF001
                if state._stdout_closed:  # noqa: SLF001
                    raise self._dead_process_error(state)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise NativeAgentTimeout(
                        f"Native agent did not answer {method!r} within {wait:g}s "
                        f"(pid={proc.pid}). The agent may be hung or slow; raise "
                        f"the request_timeout or check its stderr."
                    )
                state._cond.wait(remaining)  # noqa: SLF001
            response = state._responses.pop(rid)  # noqa: SLF001

        if self.debug:
            logger.debug("native-launcher: <- %s", json.dumps(response))

        if "error" in response and response["error"] is not None:
            err = response["error"]
            code = err.get("code", "?") if isinstance(err, dict) else "?"
            message = err.get("message", err) if isinstance(err, dict) else err
            raise NativeAgentError(
                f"Native agent returned JSON-RPC error for {method!r} "
                f"(code {code}): {message}"
            )

        result = response.get("result")
        return result if isinstance(result, dict) else {"result": result}

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def ping(self, proc: subprocess.Popen, *, timeout: Optional[float] = None) -> bool:
        """Return True if the agent answers a ``ping`` RPC.

        Raises:
            NativeAgentError / NativeAgentTimeout: If the process is dead or
                unresponsive.
        """
        self.send_rpc(proc, "ping", timeout=timeout or 5.0)
        return True

    def is_alive(self, proc: subprocess.Popen) -> bool:
        """Return True if the subprocess is still running."""
        return proc.poll() is None

    def server_info(self, proc: subprocess.Popen) -> Dict[str, Any]:
        """Return the ``initialize`` result captured during :meth:`start`."""
        state = self._states.get(proc.pid)
        return dict(state.server_info) if state else {}

    def recent_stderr(self, proc: subprocess.Popen) -> str:
        """Return recent stderr lines from the agent (diagnostics)."""
        state = self._states.get(proc.pid)
        return state.recent_stderr() if state else ""

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _initialize(self, state: _ProcState) -> Dict[str, Any]:
        params = {
            "protocolVersion": "2.0",
            "clientInfo": {"name": self.client_name, "version": "1.0.0"},
            "capabilities": {},
        }
        try:
            result = self.send_rpc(
                state.proc, "initialize", params, timeout=self.startup_timeout
            )
        except NativeAgentTimeout as e:
            raise NativeAgentError(
                f"Native agent handshake timed out: {e}. The binary may not "
                f"speak the JSON-RPC stdio protocol (expected a reply to "
                f"'initialize' on stdout). See {_SPEC_URL}."
            ) from e
        except NativeAgentError as e:
            raise NativeAgentError(
                f"Native agent handshake failed: {e}. Confirm the binary "
                f"implements the 'initialize' method. See {_SPEC_URL}."
            ) from e
        return result

    def _close_stdin(self, proc: subprocess.Popen) -> None:
        if proc.stdin is not None and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass

    def _force_stop(self, state_or_proc: Union[_ProcState, subprocess.Popen]) -> None:
        proc = (
            state_or_proc.proc
            if isinstance(state_or_proc, _ProcState)
            else state_or_proc
        )
        self._close_stdin(proc)
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "native-launcher: pid=%s ignored terminate, sending SIGKILL",
                    proc.pid,
                )
                proc.kill()
                try:
                    proc.wait(timeout=self.shutdown_timeout)
                except subprocess.TimeoutExpired:
                    logger.error("native-launcher: pid=%s survived SIGKILL", proc.pid)
            except OSError:
                pass
        self._discard(proc.pid)

    def _discard(self, pid: int) -> None:
        with self._states_lock:
            self._states.pop(pid, None)

    def _dead_process_error(self, state: _ProcState) -> NativeAgentError:
        code = self._format_exit_code(state.proc.returncode)
        stderr = state.recent_stderr()
        msg = f"Native agent process died (exit code {code}, pid={state.proc.pid})"
        if stderr:
            msg += f"\nstderr:\n{stderr[-2000:]}"
        return NativeAgentError(msg)

    @staticmethod
    def _drain_dead_stderr(proc: subprocess.Popen) -> str:
        if proc.stderr is None:
            return ""
        try:
            return (proc.stderr.read() or "").strip()[-2000:]
        except OSError:
            return ""

    @staticmethod
    def _format_exit_code(exit_code: Optional[int]) -> str:
        """Render an exit code, naming the signal for negative (Unix) codes."""
        if exit_code is None:
            return "None"
        text = str(exit_code)
        if exit_code < 0:
            try:
                text += f" ({signal.Signals(-exit_code).name})"
            except ValueError:
                pass
        return text
