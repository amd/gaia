# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon's supervision of the local model server.

GAIA needs a Lemonade Server for every LLM call, and until now every front-end
that found it down printed instructions and stopped. This module is what turns
"it is not running" into "it is running".

**The daemon owns the process.** ``gaia daemon`` starts the server as a child,
supervises it, and tree-kills it on shutdown; the Go TUI and the Agent UI are
HTTP clients that *ask* for it (``POST /daemon/v1/lemonade/start``) and never
spawn anything. One machine-wide supervisor falls out of the daemon already
being single-instance — which is why there is no lock here: the daemon's own
:class:`~gaia.daemon.lock.StartLock` already guarantees one daemon, and a
second locking scheme on top would be redundant state to keep consistent. The
in-process lock below serializes the daemon's own threads, nothing more.

**How the server is launched is not decided here.**
:func:`~gaia.llm.lemonade_launcher.resolve_lemonade` and
:func:`~gaia.llm.lemonade_launcher.build_start_command` own that, and hand back
a ``StartSpec(argv, env)`` this module passes through untouched. That matters
beyond tidiness: the three launch forms do not share an argv shape (the
embeddable ``lemond ./ --port`` takes a working directory and a port the others
do not), so anything here that inspected or assembled argv would break the
moment bundled-lemond resolution lands behind the same function.

**What "supervises" does and does not mean.** The daemon tracks the child, can
report its pid, reaps it on shutdown, and starts a fresh one if the previous
died. There is no watchdog thread and no restart-on-crash: a crash surfaces on
the next request, loudly, rather than being papered over by a silent respawn.

**It never kills what it did not start.** A Lemonade the user launched from the
tray, or one another GAIA left behind, is attached to and left alone — including
at daemon shutdown.
"""

from __future__ import annotations

import ipaddress
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from gaia.llm.lemonade_launcher import (
    build_start_command,
    describe_start_hint,
    render_command,
    resolve_lemonade,
)
from gaia.logger import get_logger

log = get_logger(__name__)

# How long a freshly started server gets to answer /health. A cold start on a
# laptop that has to page the runtime in measures in tens of seconds; the
# ceiling is generous because the alternative to waiting is a wrong error.
DEFAULT_START_TIMEOUT_S = 120.0

_PROBE_INTERVAL_S = 0.5

# A port that is already accepting connections gets this long to start
# answering /health before we call it a stranger. Covers a Lemonade that
# something else spawned moments ago and is still binding its routes.
_OCCUPIED_PORT_GRACE_S = 20.0

# Tree-kill grace before escalating to SIGKILL, matching the sidecar manager.
_SHUTDOWN_TIMEOUT_S = 5.0

# The health probe's own (connect, read) budget. It must NOT inherit
# LemonadeClient's scalar default (900s, correct for generation): a socket that
# ACCEPTS and then never answers — a Lemonade mid-model-load, or the stranger on
# the port that ``_start_locked`` exists to detect — would otherwise turn "poll
# every half second for 20s" into a single 15-minute call holding the lock.
# Mirrors readiness.PROBE_CONNECT_TIMEOUT / PROBE_READ_TIMEOUT.
_PROBE_TIMEOUT = (2.0, 5.0)


class LemonadeStartError(RuntimeError):
    """The local model server could not be started, and the message says why."""


@dataclass
class LemonadeState:
    """The outcome of an :meth:`LemonadeSupervisor.ensure_running` call."""

    base_url: str
    # False when the server was already up — the daemon started nothing.
    started: bool
    # True when THIS daemon owns the process and will reap it on shutdown.
    owned: bool
    pid: Optional[int]
    waited_seconds: float


def _is_loopback(host: str) -> bool:
    """Whether *host* names this machine, so a server started here would be used."""
    host = (host or "").strip().strip("[]").lower()
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LemonadeSupervisor:
    """Starts, tracks and reaps the local model server for one machine."""

    def __init__(self, base_url: Optional[str] = None, log_dir: Optional[Path] = None):
        self._base_url = base_url
        self._log_dir = log_dir
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._log_handle = None

    # -- introspection -----------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the child this daemon started is still alive."""
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self.is_running else None

    def log_path(self) -> Path:
        """Where a supervised server's output goes, for failure messages to name."""
        if self._log_dir is not None:
            return Path(self._log_dir) / "lemonade.log"
        from gaia.config import GAIA_CONFIG_DIR

        return Path(GAIA_CONFIG_DIR) / "logs" / "lemonade.log"

    # -- the verb ----------------------------------------------------------

    def ensure_running(
        self,
        ctx_size: Optional[int] = None,
        timeout: float = DEFAULT_START_TIMEOUT_S,
    ) -> LemonadeState:
        """Make sure a Lemonade Server is answering, starting one if it is not.

        Args:
            ctx_size: the context window a started server must come up with. A
                server started without one answers ``/health`` and then fails
                every long request, which reads as an agent bug rather than a
                launch bug — so callers pass their profile's window
                (``lemonade_client.profile_ctx_size``).
            timeout: how long a freshly started server gets to answer.

        Raises:
            LemonadeStartError: it is down and could not be started. The message
                names what failed, what to do, and where to look.
        """
        client = self._client()
        target = client.base_url

        # Fast path: one probe, and nothing else runs when the server is up.
        # This sits in front of every agent construction and every CLI call, so
        # anything added here is latency paid by users who never needed it.
        if _probe(client):
            return LemonadeState(
                base_url=target,
                started=False,
                owned=self.is_running,
                pid=self.pid,
                waited_seconds=0.0,
            )

        if not _is_loopback(urlparse(target).hostname or ""):
            raise self._remote_error(target)
        self._require_supervisable_port(client)

        started_at = time.monotonic()
        with self._lock:
            # Re-probe under the lock: another daemon thread may have started
            # the very server we were about to start.
            if _probe(client):
                return LemonadeState(
                    base_url=target,
                    started=False,
                    owned=self.is_running,
                    pid=self.pid,
                    waited_seconds=time.monotonic() - started_at,
                )
            started = self._start_locked(client, ctx_size, timeout)

        return LemonadeState(
            base_url=target,
            started=started,
            owned=self.is_running,
            pid=self.pid,
            waited_seconds=time.monotonic() - started_at,
        )

    def shutdown(self, timeout: float = _SHUTDOWN_TIMEOUT_S) -> None:
        """Tree-kill the server THIS daemon started. A no-op for anything else.

        Tree-kill rather than a plain kill because Lemonade spawns
        ``llama-server`` children — killing only the leader orphans them, and an
        orphan still holds the port and the GPU memory the next start needs.
        """
        with self._lock:
            proc, self._proc = self._proc, None
            if proc is None or proc.poll() is not None:
                self._close_log()
                return
            log.info("lemonade: tree-killing supervised server pid=%s", proc.pid)
            _tree_kill(proc, timeout)
            self._close_log()

    # -- internals ---------------------------------------------------------

    def _client(self):
        from gaia.llm.lemonade_client import LemonadeClient

        if self._base_url:
            return LemonadeClient(
                base_url=self._base_url, keep_alive=True, verbose=False
            )
        return LemonadeClient(keep_alive=True, verbose=False)

    def _start_locked(self, client, ctx_size: Optional[int], timeout: float) -> bool:
        """Spawn and wait. Caller holds the lock and has re-probed."""
        host, port, target = client.host, client.port, client.base_url

        # Something is listening but not answering /health. It may be a server
        # that started a second ago, so give it a grace window — but if it never
        # answers it is a stranger, and evicting it is not ours to do.
        if _port_is_open(host, port):
            log.info(
                "lemonade: %s:%s accepts connections but does not answer health yet; "
                "waiting up to %.0fs",
                host,
                port,
                _OCCUPIED_PORT_GRACE_S,
            )
            if _wait_for_health(client, _OCCUPIED_PORT_GRACE_S):
                return False
            raise self._occupied_port_error(host, port)

        tooling = resolve_lemonade()
        if not tooling.found:
            raise self._not_installed_error(target)

        # argv and env come from the launcher and are passed through untouched:
        # the three launch forms do not share a shape.
        spec = build_start_command(tooling, ctx_size)
        log.info(
            "lemonade: starting supervised server: %s (ctx_size=%s)",
            render_command(spec),
            ctx_size,
        )

        try:
            proc = self._spawn(spec)
        except OSError as e:
            raise LemonadeStartError(
                f"GAIA could not launch the local model server "
                f"(`{' '.join(spec.argv)}`): {e}\n"
                "To fix: re-run `gaia init` to repair the install, or set "
                "LEMONADE_SERVER_PATH to a working server executable.\n"
                f"Where to look: {self.log_path()}"
            ) from e

        if _wait_for_health(client, timeout, proc=proc):
            log.info("lemonade: server is up at %s (pid=%s)", target, proc.pid)
            return True

        exit_code = proc.poll()
        _tree_kill(proc, _SHUTDOWN_TIMEOUT_S)
        self._proc = None
        self._close_log()
        raise self._start_failed_error(target, spec, timeout, exit_code)

    def _spawn(self, spec) -> subprocess.Popen:
        """Launch the server in its own process group, output to the log file.

        Its own group so :func:`_tree_kill` can take the ``llama-server``
        children with it. NOT detached-to-outlive-us: the daemon owns this
        process and reaps it, so a stopped daemon never leaves a server behind.
        """
        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._close_log()
        self._log_handle = open(path, "a", encoding="utf-8", errors="replace")
        self._log_handle.write(f"\n=== GAIA daemon start: {' '.join(spec.argv)} ===\n")
        self._log_handle.flush()

        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            ) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            start_new_session = True

        try:
            # Merge — never replace — the parent environment; the child loses
            # PATH/LOCALAPPDATA otherwise and LemonadeServer.exe cannot start.
            self._proc = subprocess.Popen(
                spec.argv,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env={**os.environ, **spec.env},
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except OSError:
            self._close_log()
            raise
        return self._proc

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            finally:
                self._log_handle = None

    # -- the actionable failures ------------------------------------------

    def _not_installed_error(self, base_url: str) -> LemonadeStartError:
        return LemonadeStartError(
            f"The local model server is not running at {base_url}, and GAIA "
            "could not find one to start.\n"
            f"To fix: {describe_start_hint().instruction}\n"
            "See https://amd-gaia.ai/docs/guides/install"
        )

    def _occupied_port_error(self, host: str, port: int) -> LemonadeStartError:
        return LemonadeStartError(
            f"Port {port} on {host} is held by a process that does not answer "
            "Lemonade's health endpoint, so GAIA will not start a server there "
            "and will not evict what is already running.\n"
            "To fix: stop whatever holds that port, or point GAIA at the right "
            "one with LEMONADE_BASE_URL.\n"
            f"To see what holds it: `netstat -ano | findstr :{port}` on "
            f"Windows, `lsof -i :{port}` elsewhere."
        )

    def _require_supervisable_port(self, client) -> None:
        """Refuse to start a server that would not be the one we then poll.

        ``build_start_command`` takes no port — the launch forms bind Lemonade's
        own default. So a caller pointed at a DIFFERENT local port would have us
        spawn a server on 13305, poll the other port until the deadline, and
        then tree-kill a perfectly healthy server while reporting "it did not
        answer". Refusing up front says what is actually wrong.
        """
        from gaia.llm.lemonade_client import DEFAULT_PORT

        if client.port == DEFAULT_PORT:
            return
        raise LemonadeStartError(
            f"The local model server is not running at {client.base_url}, and "
            f"GAIA can only start one on its default port ({DEFAULT_PORT}) — "
            f"the launcher has no port option, so a server started here would "
            f"not be the one on port {client.port}.\n"
            f"To fix: start the server on port {client.port} yourself, or unset "
            "LEMONADE_BASE_URL so GAIA manages the default one.\n"
            "See https://amd-gaia.ai/docs/reference/troubleshooting"
        )

    def _remote_error(self, base_url: str) -> LemonadeStartError:
        return LemonadeStartError(
            f"The model server at {base_url} is not reachable, and GAIA cannot "
            "start it: that URL names another machine, so a server started here "
            "would never be used.\n"
            "To fix: start it on that host, or point LEMONADE_BASE_URL at a "
            "reachable server.\n"
            "See https://amd-gaia.ai/docs/reference/troubleshooting"
        )

    def _start_failed_error(
        self, base_url: str, spec, timeout: float, exit_code: Optional[int]
    ) -> LemonadeStartError:
        # Rendered through the launcher, not " ".join(argv): a modern install
        # carries ctx_size in the ENV, so a user copying a bare argv line would
        # get a server at the wrong context window — health-green and failing
        # every long request.
        cmd = render_command(spec)
        if exit_code:
            what = (
                f"GAIA started the local model server (`{cmd}`) but it exited "
                f"with code {exit_code} without answering at {base_url}."
            )
        else:
            what = (
                f"GAIA started the local model server (`{cmd}`) but it did not "
                f"answer at {base_url} within {timeout:.0f}s."
            )
        tail = _log_tail(self.log_path())
        detail = f"\nLast log lines:\n{tail}" if tail else ""
        return LemonadeStartError(
            f"{what}\n"
            "To fix: run that command yourself to see the failure directly, or "
            "re-run `gaia init` to repair the install.\n"
            f"Where to look: {self.log_path()}{detail}"
        )


# ---------------------------------------------------------------------------
# Free functions — probing and process control
# ---------------------------------------------------------------------------


def _probe(client) -> bool:
    """Whether *client*'s server answers its health endpoint right now.

    Any successful response counts. Version and model checks belong to the
    callers that need them (``LemonadeManager``, the sidecar's ``/init``); this
    only answers "is a Lemonade there".
    """
    try:
        client.health_check(timeout=_PROBE_TIMEOUT)
        return True
    except Exception as e:  # noqa: BLE001 — any failure means "not answering"
        log.debug("lemonade: health probe failed at %s: %s", client.base_url, e)
        return False


def _port_is_open(host: str, port: int, timeout: float = 0.5) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_health(
    client, timeout: float, proc: Optional[subprocess.Popen] = None
) -> bool:
    """Poll ``/health`` until it answers or *timeout* elapses.

    A *proc* that exits NON-zero ends the wait immediately — the server is dead
    and burning the remaining budget only delays the error. A zero exit keeps
    polling: ``systemctl --user start lemond`` and the macOS ``open`` launcher
    both hand off and return success while the server is still binding.
    """
    deadline = time.monotonic() + timeout
    while True:
        if _probe(client):
            return True
        if proc is not None:
            code = proc.poll()
            if code:
                return False
            if code == 0:
                proc = None  # Handed off; only the deadline bounds us now.
        if time.monotonic() >= deadline:
            return False
        time.sleep(_PROBE_INTERVAL_S)


def _tree_kill(proc: subprocess.Popen, timeout: float) -> None:
    """Kill *proc* and its children, escalating if it does not go quietly.

    Mirrors ``AgentSidecarManager._shutdown_locked`` — one shape for reaping a
    supervised child, so the two cannot drift.
    """
    pid = proc.pid
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(os.getpgid(pid), 15)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("lemonade: pid %s did not exit in %ss; SIGKILL", pid, timeout)
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(pid), 9)
            else:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # It survived SIGKILL. Say so: it still holds the port and the GPU
            # memory the next start needs, and a silent exit here would make
            # that failure look like a fresh mystery.
            log.error(
                "lemonade: pid %s survived SIGKILL and may still hold the "
                "model-server port; clear it with `gaia kill`",
                pid,
            )


def _log_tail(path: Path, lines: int = 15) -> str:
    """The last few log lines, for embedding in a failure message."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join([ln for ln in content.splitlines() if ln.strip()][-lines:])
