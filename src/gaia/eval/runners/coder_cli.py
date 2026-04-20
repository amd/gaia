# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""CLI-driven runner for ``gaia-coder`` (§10.2).

The runner drives ``gaia-coder`` through its **CLI**, not its Python
API — this ensures eval exercises the same surface a real EM hits,
catching CLI-level regressions (argparse bugs, stdout formatting, exit
codes, zombie processes). See ``docs/plans/coder-agent.mdx`` §10.2.

Note on the base class: the §10.2 prose says "imports the existing
``gaia.eval.runner`` base class"; in practice the existing
:class:`gaia.eval.runner.AgentEvalRunner` is tightly coupled to the
Agent UI MCP scenario format and is not a generic base. Rather than
retrofit inheritance that would drag in scenario-YAML loading we do
not need, :class:`CoderCLIRunner` is a standalone class that lives in
the same ``gaia.eval`` package and follows the same conventions (JSON
output, explicit timeouts, structured results). A future refactor can
extract a shared ``Runner`` Protocol when a second CLI-driven agent
lands.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from gaia.coder.cli import ARTIFACT_FILENAMES
from gaia.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Default binary path + timeouts.
# ---------------------------------------------------------------------------

# Resolve ``gaia-coder`` from PATH by default; callers override for tests
# (e.g. "invoke the freshly-installed worktree copy").
_DEFAULT_CODER_BINARY = "gaia-coder"

# How long we wait for the daemon process to exit after ``gaia-coder
# stop`` returns success. The daemon prints ``exiting`` and then the
# Popen object needs a beat to reap.
_PROCESS_REAP_TIMEOUT_S = 5.0


# ---------------------------------------------------------------------------
# Result dataclasses.
# ---------------------------------------------------------------------------


@dataclass
class AgentHandle:
    """Opaque handle returned by :meth:`CoderCLIRunner.spawn_agent`.

    Holds the live :class:`subprocess.Popen` plus metadata the other
    runner methods need (sandbox path, tier). Callers should treat the
    attributes as read-only.
    """

    process: subprocess.Popen
    sandbox: Path
    capability_tier: int
    no_network_writes: bool
    pid: int = field(init=False)

    def __post_init__(self) -> None:
        self.pid = self.process.pid


@dataclass
class TaskResult:
    """Outcome of one ``ask + wait`` round-trip."""

    task_id: str
    completed: bool
    wait_returncode: int
    elapsed_s: float
    timed_out: bool = False
    artifact_dir: Optional[Path] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "completed": self.completed,
            "wait_returncode": self.wait_returncode,
            "elapsed_s": round(self.elapsed_s, 3),
            "timed_out": self.timed_out,
            "artifact_dir": (str(self.artifact_dir) if self.artifact_dir else None),
        }


# ---------------------------------------------------------------------------
# Runner.
# ---------------------------------------------------------------------------


class CoderCLIRunner:
    """Drive ``gaia-coder`` via its CLI for eval scenarios.

    Parameters
    ----------
    coder_binary:
        Name or path of the ``gaia-coder`` executable. Defaults to
        ``"gaia-coder"`` (resolved via ``PATH``). For testing you can
        pass ``[sys.executable, "-m", "gaia.coder.cli"]``-style args
        via :paramref:`coder_argv_prefix`.
    coder_argv_prefix:
        Optional alternative to ``coder_binary`` — a list of arguments
        used as the command prefix. When set, takes precedence over
        ``coder_binary``. Used in tests to invoke the in-tree module
        without a shim script.
    env:
        Optional environment dict passed to every subprocess. When
        ``None`` the parent environment is inherited.
    """

    def __init__(
        self,
        coder_binary: str = _DEFAULT_CODER_BINARY,
        coder_argv_prefix: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self._coder_binary = coder_binary
        self._coder_argv_prefix = list(coder_argv_prefix) if coder_argv_prefix else None
        self._env = env

    # ---- command construction ---------------------------------------

    def _cmd(self, *args: str) -> list[str]:
        """Return the argv list for invoking ``gaia-coder <args>``."""
        if self._coder_argv_prefix is not None:
            return [*self._coder_argv_prefix, *args]
        return [self._coder_binary, *args]

    def _env_dict(self) -> Optional[dict[str, str]]:
        return dict(self._env) if self._env is not None else None

    # ---- daemon lifecycle ------------------------------------------

    def spawn_agent(
        self,
        sandbox: Path,
        tier: int,
        *,
        no_network_writes: bool = True,
        stdout_path: Optional[Path] = None,
        stderr_path: Optional[Path] = None,
        startup_timeout_s: float = 10.0,
    ) -> AgentHandle:
        """Start ``gaia-coder daemon`` as a child process.

        The daemon writes ``$SANDBOX/.eval-artifacts/.daemon.pid`` when
        ready. We poll for that file with :paramref:`startup_timeout_s`
        so callers can be confident the daemon is accepting tasks when
        this method returns.

        The :paramref:`no_network_writes` flag defaults to ``True`` —
        the eval harness MUST forbid network writes (§10.2), and making
        that the default rules out the "accidentally opened a real PR
        during eval" footgun. Callers opt OUT deliberately for non-eval
        flows.
        """
        sandbox = Path(sandbox).resolve()
        if not sandbox.is_dir():
            raise ValueError(
                f"CoderCLIRunner.spawn_agent: sandbox must exist as a "
                f"directory: {sandbox}"
            )

        args = [
            "daemon",
            "--sandbox",
            str(sandbox),
            "--capability-tier",
            str(tier),
        ]
        if no_network_writes:
            args.append("--no-network-writes")

        stdout = (
            open(stdout_path, "w", encoding="utf-8")  # noqa: SIM115
            if stdout_path
            else subprocess.DEVNULL
        )
        stderr = (
            open(stderr_path, "w", encoding="utf-8")  # noqa: SIM115
            if stderr_path
            else subprocess.DEVNULL
        )

        cmd = self._cmd(*args)
        log.info(
            "Spawning daemon: %s (sandbox=%s, tier=%s, no_net=%s)",
            " ".join(cmd),
            sandbox,
            tier,
            no_network_writes,
        )
        process = subprocess.Popen(
            cmd,
            stdout=stdout,
            stderr=stderr,
            env=self._env_dict(),
            # Detach from controlling tty so SIGINT on the runner
            # does not stampede into the daemon (the runner hands
            # termination explicitly via ``stop_agent``).
            start_new_session=True,
        )

        pid_file = sandbox / ".eval-artifacts" / ".daemon.pid"
        deadline = time.monotonic() + startup_timeout_s
        while time.monotonic() < deadline:
            if pid_file.exists():
                return AgentHandle(
                    process=process,
                    sandbox=sandbox,
                    capability_tier=tier,
                    no_network_writes=no_network_writes,
                )
            # If the daemon exited early, fail fast with context.
            if process.poll() is not None:
                raise RuntimeError(
                    f"gaia-coder daemon exited before writing pid file "
                    f"(returncode={process.returncode}); "
                    f"check {stderr_path or 'daemon stderr'} for details"
                )
            time.sleep(0.05)
        # Timeout waiting for pid file — kill process and raise.
        self._kill_process(process)
        raise TimeoutError(
            f"gaia-coder daemon did not write pid file at {pid_file} "
            f"within {startup_timeout_s}s"
        )

    def stop_agent(
        self, handle: AgentHandle, *, timeout_s: float = _PROCESS_REAP_TIMEOUT_S
    ) -> int:
        """Stop the daemon via ``gaia-coder stop`` and reap the process.

        Returns the daemon process's returncode (``0`` if it exited
        cleanly via SIGTERM, or whatever status it reported on its
        own). SIGKILL-fallback is used if ``stop`` leaves the pid live.
        """
        stop_result = subprocess.run(
            self._cmd(
                "stop", "--sandbox", str(handle.sandbox), "--wait-s", str(timeout_s)
            ),
            env=self._env_dict(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s + 5.0,
        )
        log.debug(
            "`gaia-coder stop` returned %d (stdout=%r, stderr=%r)",
            stop_result.returncode,
            stop_result.stdout.strip(),
            stop_result.stderr.strip(),
        )

        try:
            handle.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            # The daemon ignored SIGTERM — SIGKILL it.
            self._kill_process(handle.process)
        return handle.process.returncode

    @staticmethod
    def _kill_process(process: subprocess.Popen) -> None:
        """Best-effort SIGKILL fallback for zombie daemons."""
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            with contextlib.suppress(Exception):
                process.kill()
        with contextlib.suppress(Exception):
            process.wait(timeout=2.0)

    # ---- task lifecycle --------------------------------------------

    def send_task(self, handle: AgentHandle, task_md_path: Path) -> str:
        """Pipe a task body into ``gaia-coder ask -`` and return task_id.

        The task body is read from :paramref:`task_md_path` and piped
        to stdin. The returned task_id is the one printed by the coder
        CLI (either the front-matter ``id`` or a generated UUID-ish
        string — see ``gaia.coder.cli._parse_task_id_from_body``).
        """
        task_md_path = Path(task_md_path)
        if not task_md_path.is_file():
            raise ValueError(
                f"CoderCLIRunner.send_task: task file not found: {task_md_path}"
            )

        body = task_md_path.read_text(encoding="utf-8")
        cmd = self._cmd("ask", "-", "--sandbox", str(handle.sandbox))
        log.debug("Sending task %s via %s", task_md_path, " ".join(cmd))
        result = subprocess.run(
            cmd,
            input=body,
            env=self._env_dict(),
            capture_output=True,
            text=True,
            check=False,
            timeout=30.0,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gaia-coder ask failed (rc={result.returncode}): "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        # First non-empty stdout line is the task_id.
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        raise RuntimeError(
            f"gaia-coder ask produced no task_id on stdout: {result.stdout!r}"
        )

    def wait_for_completion(
        self,
        handle: AgentHandle,
        task_id: str,
        *,
        timeout_min: float = 20.0,
    ) -> TaskResult:
        """Block until the task completes or ``timeout_min`` elapses.

        ``timeout_min`` accepts floats — pass e.g. ``0.05`` (3 s) in
        tests to exercise the timeout path without wall-clock pain.
        """
        cmd = self._cmd(
            "wait",
            "--task-id",
            task_id,
            "--timeout-min",
            str(timeout_min),
            "--sandbox",
            str(handle.sandbox),
        )
        log.debug("Waiting for task %s via %s", task_id, " ".join(cmd))
        start = time.monotonic()
        # ``wait --timeout-min`` handles its own timeout; we give the
        # subprocess call a generous wall-clock cushion on top so the
        # test doesn't hang forever if the CLI itself deadlocks.
        hard_timeout_s = max(timeout_min * 60 + 60, 15)
        result = subprocess.run(
            cmd,
            env=self._env_dict(),
            capture_output=True,
            text=True,
            check=False,
            timeout=hard_timeout_s,
        )
        elapsed = time.monotonic() - start
        timed_out = result.returncode == 124
        completed = result.returncode == 0
        artifact_dir: Optional[Path] = None
        if completed:
            # `wait` prints the artifact dir path on success.
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped:
                    artifact_dir = Path(stripped)
                    break
            if artifact_dir is None:
                # Fall back to the known layout if stdout was empty.
                artifact_dir = handle.sandbox / ".eval-artifacts" / task_id
        return TaskResult(
            task_id=task_id,
            completed=completed,
            wait_returncode=result.returncode,
            elapsed_s=elapsed,
            timed_out=timed_out,
            artifact_dir=artifact_dir,
        )

    # ---- artifact collection ---------------------------------------

    def collect_artifacts(self, sandbox: Path, task_id: str) -> dict[str, Path]:
        """Return ``{filename: Path}`` for the six eval artifacts.

        Missing files are included in the dict with the would-be path
        so the scorer can report "missing artifact X" instead of a
        KeyError. A strict-mode caller can check ``all(p.exists() for p
        in returned.values())``.
        """
        task_dir = Path(sandbox).resolve() / ".eval-artifacts" / task_id
        return {name: task_dir / name for name in ARTIFACT_FILENAMES}

    # ---- high-level convenience ------------------------------------

    def run_one(
        self,
        sandbox: Path,
        task_md_path: Path,
        *,
        tier: int = 0,
        no_network_writes: bool = True,
        timeout_min: float = 20.0,
        daemon_stdout: Optional[Path] = None,
        daemon_stderr: Optional[Path] = None,
    ) -> tuple[TaskResult, dict[str, Path]]:
        """Spawn → ask → wait → collect → stop in a single call.

        Returns ``(result, artifacts)``. Always tears the daemon down
        via ``try/finally`` so a failing task cannot leak zombie
        daemons into the test environment.
        """
        handle = self.spawn_agent(
            sandbox,
            tier,
            no_network_writes=no_network_writes,
            stdout_path=daemon_stdout,
            stderr_path=daemon_stderr,
        )
        try:
            task_id = self.send_task(handle, task_md_path)
            result = self.wait_for_completion(handle, task_id, timeout_min=timeout_min)
            artifacts = self.collect_artifacts(sandbox, task_id)
        finally:
            self.stop_agent(handle)
        return result, artifacts


# ---------------------------------------------------------------------------
# Fallback helper used by tests — returns a CoderCLIRunner that invokes
# the in-tree module via ``python -m gaia.coder.cli``. This avoids a
# PATH lookup (so tests don't depend on ``gaia-coder`` being installed)
# and guarantees we're running the worktree copy of the code.
# ---------------------------------------------------------------------------


def in_tree_runner(
    env: Optional[dict[str, str]] = None,
    python_exe: Optional[str] = None,
) -> CoderCLIRunner:
    """Return a :class:`CoderCLIRunner` that runs the in-tree CLI.

    Prefers ``python -m gaia.coder.cli`` so tests exercise the exact
    source on disk rather than whatever ``gaia-coder`` shim happens to
    be on the caller's PATH.
    """
    exe = python_exe or sys.executable
    if not exe:
        # Fall back to first python on PATH — unusual on POSIX but
        # happens in embedded test environments.
        exe = shutil.which("python3") or shutil.which("python") or "python"
    return CoderCLIRunner(
        coder_argv_prefix=[exe, "-m", "gaia.coder.cli"],
        env=env,
    )
