# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EmailSidecarManager — spawn / health / tree-kill the email sidecar (port of
lifecycle.ts), Node-free.

Modes (``GAIA_EMAIL_AGENT_MODE``):
  user (default) — spawn the cached frozen binary (lazy-fetched on first use).
  dev            — spawn ``uvicorn server:app --reload --app-dir <email>/packaging``
                   from source. The file is loaded as the TOP-LEVEL module
                   ``server`` (NOT ``packaging.server:app``, which would resolve
                   to the PyPI ``packaging`` library — the email package's
                   ``packaging/`` dir has no ``__init__.py`` by design).

Both serve the identical ``/v1/email/*`` contract; the mode only swaps which
process answers. The manager binds an ephemeral per-instance port (NEVER 4001)
and tree-kills the whole process group on shutdown (a PyInstaller one-file build
spawns a uvicorn child that a plain kill would orphan).

Threading note: ``start()`` (health-poll, lazy binary fetch) and the proxy's HTTP
calls are **synchronous and blocking**. The Agent UI backend is async, so callers
MUST invoke them off the event loop (``await asyncio.to_thread(...)`` /
``run_in_executor``) — exactly as the existing email agent path already runs agent
work in a worker thread. Calling ``start()`` directly from an async route would
stall the whole UI backend for up to ``health_timeout`` seconds.
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from gaia.logger import get_logger
from gaia.ui.email_sidecar import fetch
from gaia.ui.email_sidecar.errors import (
    HealthTimeoutError,
    SidecarSpawnError,
    VersionMismatchError,
)

logger = get_logger(__name__)

_HOST = "127.0.0.1"
_RESERVED_PORT = 4001
_VALID_MODES = ("user", "dev")
# The sidecar's stable service identifier (server.py /health + api_routes
# HealthResponse). Used to confirm the process answering our ephemeral port is
# actually the email sidecar and not some unrelated server.
_SERVICE_ID = "gaia-agent-email"
# Keep only the most recent N per-port sidecar logs; ephemeral ports mean a new
# file per restart, which would otherwise accumulate without bound.
_MAX_SIDECAR_LOGS = 5


def find_free_port(host: str = _HOST) -> int:
    """Bind to port 0 to get an OS-assigned free port. Never returns 4001."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            port = s.getsockname()[1]
        if port != _RESERVED_PORT:
            return port
    raise SidecarSpawnError(
        "could not find a free ephemeral port for the email sidecar"
    )


def _default_email_src_dir() -> Path:
    # src/gaia/ui/email_sidecar/manager.py -> repo root is parents[4].
    return Path(__file__).resolve().parents[4] / "hub" / "agents" / "python" / "email"


def _major(version: str) -> int:
    """Parse the MAJOR component of a dotted version ('1.3' -> 1)."""
    try:
        return int(str(version).split(".")[0])
    except (ValueError, IndexError) as e:
        raise VersionMismatchError(
            f"cannot parse a MAJOR version from '{version}'"
        ) from e


class EmailSidecarManager:
    def __init__(
        self,
        mode: Optional[str] = None,
        *,
        host: str = _HOST,
        lock_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        email_src_dir: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        health_timeout: float = 30.0,
        expected_api_version: Optional[str] = None,
    ):
        self._mode_override = mode
        self.host = host
        self.lock_path = lock_path
        self.cache_dir = cache_dir
        self.email_src_dir = (
            Path(email_src_dir) if email_src_dir else _default_email_src_dir()
        )
        self.log_dir = Path(log_dir) if log_dir else fetch.default_cache_dir() / "logs"
        self.health_timeout = health_timeout
        self.expected_api_version = expected_api_version
        self._proc: Optional[subprocess.Popen] = None
        self._log_handle = None
        self._log_path: Optional[Path] = None
        self._atexit_registered = False
        # Serializes start()/shutdown() so a concurrent lazy "first email use"
        # from two UI worker threads spawns exactly one sidecar, not two.
        # Reentrant: start() calls shutdown() on its own failure path.
        self._lock = threading.RLock()
        self.port: Optional[int] = None
        self.base_url: Optional[str] = None
        self.api_version: Optional[str] = None
        self.agent_version: Optional[str] = None

    @property
    def mode(self) -> str:
        m = self._mode_override or os.environ.get("GAIA_EMAIL_AGENT_MODE") or "user"
        if m not in _VALID_MODES:
            raise SidecarSpawnError(
                f"GAIA_EMAIL_AGENT_MODE='{m}' is invalid; expected one of "
                f"{_VALID_MODES}. There is no fallback — set it explicitly."
            )
        return m

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def proxy(self, **kwargs):
        """Return an :class:`EmailSidecarProxy` bound to this running sidecar.

        The manager owns the ephemeral port; the proxy is the request surface.
        Raises if the sidecar has not been started yet (no silent unbound proxy).
        """
        from gaia.ui.email_sidecar.errors import SidecarError
        from gaia.ui.email_sidecar.proxy import EmailSidecarProxy

        if not self.base_url or not self.is_running:
            raise SidecarError(
                "email sidecar is not started — call start() before proxy()."
            )
        return EmailSidecarProxy(self.base_url, **kwargs)

    def __enter__(self) -> "EmailSidecarManager":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    def build_spawn_command(self, *, port: int):
        if port == _RESERVED_PORT:
            raise ValueError("port 4001 is reserved and must never be used")
        if self.mode == "user":
            try:
                result = fetch.fetch_binary(
                    out_dir=self.cache_dir, lock_path=self.lock_path
                )
            except Exception as e:  # re-raise with the user-mode remedy
                raise SidecarSpawnError(
                    f"email sidecar binary unavailable in user mode: {e} "
                    "Set GAIA_EMAIL_AGENT_MODE=dev to run from source, or publish "
                    "the email agent so the binary + real SHA exist."
                ) from e
            argv = [str(result.binary_path), "--host", self.host, "--port", str(port)]
            return argv, {}
        # dev mode — load packaging/server.py as the top-level module `server`.
        packaging_dir = self.email_src_dir / "packaging"
        if not packaging_dir.is_dir():
            raise SidecarSpawnError(
                f"dev mode needs the email source at {self.email_src_dir} but it is "
                "missing. Run from a source checkout, or install it: "
                "`uv pip install -e hub/agents/python/email`."
            )
        argv = [
            sys.executable,
            "-m",
            "uvicorn",
            "server:app",
            "--reload",
            "--app-dir",
            str(packaging_dir),
            "--host",
            self.host,
            "--port",
            str(port),
        ]
        return argv, {"cwd": str(self.email_src_dir)}

    def _http_get(self, url: str, timeout: float):
        """Single seam for the readiness/version probes (monkeypatched in tests)."""
        import requests

        return requests.get(url, timeout=timeout)

    def _open_log(self, port: int):
        # Redirect the sidecar's stdout+stderr to a per-port file. A plain PIPE
        # that is never drained deadlocks the child once the OS buffer (~64KB)
        # fills — uvicorn logs an access line per request, so that WOULD happen
        # under normal use. A file has no such ceiling and stays debuggable.
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._prune_old_logs()
        self._log_path = self.log_dir / f"sidecar-{port}.log"
        self._log_handle = open(self._log_path, "wb")
        return self._log_handle

    def _prune_old_logs(self) -> None:
        # Keep the newest _MAX_SIDECAR_LOGS-1 so this run's new file lands within
        # the cap. Best-effort: a failed unlink never blocks a start.
        try:
            existing = sorted(
                self.log_dir.glob("sidecar-*.log"),
                key=lambda p: p.stat().st_mtime,
            )
        except OSError:
            return
        for stale in existing[: -(_MAX_SIDECAR_LOGS - 1) or None]:
            try:
                stale.unlink()
            except OSError:
                pass

    def _read_log_tail(self, limit: int = 2000) -> str:
        if not self._log_path:
            return ""
        try:
            data = self._log_path.read_bytes()
        except OSError:
            return ""
        return data[-limit:].decode("utf-8", "replace")

    def _spawn_process(self, argv, popen_kwargs, port: int) -> None:
        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            start_new_session = True  # own process group for tree-kill
        log_handle = self._open_log(port)
        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=start_new_session,
                creationflags=creationflags,
                **popen_kwargs,
            )
        except OSError as e:
            self._close_log()
            raise SidecarSpawnError(
                f"failed to launch the email sidecar ({argv[0]}): {e}"
            ) from e
        # Reap the sidecar if the backend exits without calling shutdown() — a
        # detached child otherwise survives a crash/Ctrl-C and holds its port +
        # a loaded LLM. atexit covers normal exit + uncaught exceptions.
        if not self._atexit_registered:
            atexit.register(self.shutdown)
            self._atexit_registered = True

    def start(self, max_attempts: int = 3) -> str:
        """Spawn + health-check + version-handshake the sidecar; return base_url.

        Retries only the FAST early-exit failure (e.g. the sidecar lost the
        ephemeral-port race and uvicorn failed to bind), picking a fresh port
        each time. A genuine hang (``HealthTimeoutError``) is NOT retried — that
        would just multiply the wait — and surfaces loudly on the first attempt.
        """
        with self._lock:
            return self._start_locked(max_attempts)

    def _start_locked(self, max_attempts: int) -> str:
        if self.is_running:
            return self.base_url
        last_exit_err: Optional[SidecarSpawnError] = None
        for attempt in range(1, max_attempts + 1):
            self.port = find_free_port(self.host)
            argv, popen_kwargs = self.build_spawn_command(port=self.port)
            logger.info(
                "email sidecar: spawning (%s mode, attempt %d/%d) %s",
                self.mode,
                attempt,
                max_attempts,
                " ".join(argv),
            )
            self._spawn_process(argv, popen_kwargs, self.port)
            self.base_url = f"http://{self.host}:{self.port}"
            try:
                self._wait_for_health()
                break
            except SidecarSpawnError as e:
                # Early exit — could be a port race; reap and retry on a new port.
                self.shutdown()
                last_exit_err = e
                logger.warning(
                    "email sidecar attempt %d/%d exited early; retrying: %s",
                    attempt,
                    max_attempts,
                    e,
                )
                continue
            except Exception:
                self.shutdown()
                raise
        else:
            raise SidecarSpawnError(
                f"email sidecar failed to start after {max_attempts} attempts. "
                f"Last failure: {last_exit_err}"
            )
        try:
            self._check_version()
        except Exception:
            self.shutdown()
            raise
        return self.base_url

    def _wait_for_health(self, interval: float = 0.25) -> None:
        import requests

        deadline = time.monotonic() + self.health_timeout
        url = f"{self.base_url}/health"
        last_err = ""
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise SidecarSpawnError(
                    f"email sidecar exited early (code {self._proc.returncode}) before "
                    f"becoming healthy. Last log output:\n{self._read_log_tail()}"
                )
            try:
                r = self._http_get(url, timeout=interval * 4)
                body = r.json() if r.status_code == 200 else {}
                # Require the service identity, not just status==ok: a foreign
                # process that happened to grab this ephemeral port must not be
                # mistaken for our sidecar.
                if (
                    r.status_code == 200
                    and body.get("status") == "ok"
                    and body.get("service") == _SERVICE_ID
                ):
                    logger.info("email sidecar healthy at %s", self.base_url)
                    return
                if r.status_code == 200 and body.get("service") not in (
                    None,
                    _SERVICE_ID,
                ):
                    last_err = (
                        f"a different service ('{body.get('service')}') is serving "
                        f"{self.base_url} — not the email sidecar"
                    )
                else:
                    last_err = f"status={r.status_code} body={r.text[:200]}"
            except requests.exceptions.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
            time.sleep(interval)
        raise HealthTimeoutError(
            f"email sidecar at {self.base_url} did not become healthy within "
            f"{self.health_timeout}s. Last probe error: {last_err}. Check the process "
            f"launched and the port is free. Sidecar log:\n{self._read_log_tail()}"
        )

    def _check_version(self) -> None:
        """Capture the sidecar's contract version; gate on a MAJOR mismatch.

        Always records ``api_version``/``agent_version`` for diagnostics. When an
        ``expected_api_version`` was provided, a differing MAJOR is a breaking
        contract change and fails loudly (no silent mismatch). The expected
        version is supplied by the caller rather than imported, so core stays
        free of the email wheel.
        """
        try:
            r = self._http_get(f"{self.base_url}/version", timeout=5.0)
            info = r.json()
        except Exception as e:  # noqa: BLE001 - surface as a loud spawn failure
            raise SidecarSpawnError(
                f"email sidecar did not answer /version at {self.base_url}: {e}"
            ) from e
        self.api_version = info.get("apiVersion")
        self.agent_version = info.get("agentVersion")
        logger.info(
            "email sidecar contract: apiVersion=%s agentVersion=%s",
            self.api_version,
            self.agent_version,
        )
        if self.expected_api_version:
            if not self.api_version:
                raise VersionMismatchError(
                    f"email sidecar /version omitted 'apiVersion' but the host pins "
                    f"'{self.expected_api_version}'. Cannot confirm contract "
                    "compatibility — refusing to proceed."
                )
            if _major(self.api_version) != _major(self.expected_api_version):
                raise VersionMismatchError(
                    f"email sidecar apiVersion '{self.api_version}' has a different "
                    f"MAJOR than expected '{self.expected_api_version}'. A major bump "
                    "is a breaking contract change — upgrade the sidecar binary or "
                    "the host to matching versions."
                )

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._shutdown_locked(timeout)

    def _shutdown_locked(self, timeout: float = 5.0) -> None:
        if self._atexit_registered:
            atexit.unregister(self.shutdown)
            self._atexit_registered = False
        proc = self._proc
        if proc is None:
            self._close_log()
            return
        if proc.poll() is not None:
            self._proc = None
            self._close_log()
            return
        pid = proc.pid
        logger.info("email sidecar: tree-killing pid=%s", pid)
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.killpg(os.getpgid(pid), 15)  # SIGTERM to the group
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("email sidecar did not exit in %ss; SIGKILL", timeout)
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(pid), 9)  # SIGKILL
                else:
                    proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
        self._proc = None
        self._close_log()
        logger.info("email sidecar: shut down")


# ---------------------------------------------------------------------------
# Shared process-wide manager
# ---------------------------------------------------------------------------
# The Agent UI has exactly ONE email backend (design: "One backend for the UI").
# Both consumers — the /v1/email REST router and the in-app email chat agent
# (agent_type=email) — share this single manager so they drive the SAME sidecar
# process (one ephemeral port, one lazy spawn, one tree-kill) instead of racing
# to spawn two. The contract MAJOR is pinned here so a breaking sidecar upgrade
# fails loudly (kept in lockstep with the contract SCHEMA_VERSION major).
_EXPECTED_API_MAJOR = "2"
_shared_manager: Optional["EmailSidecarManager"] = None
_shared_manager_lock = threading.Lock()


def get_shared_manager() -> "EmailSidecarManager":
    """Return the process-wide email sidecar manager, creating it once."""
    global _shared_manager
    with _shared_manager_lock:
        if _shared_manager is None:
            _shared_manager = EmailSidecarManager(
                expected_api_version=_EXPECTED_API_MAJOR
            )
        return _shared_manager


def reset_shared_manager() -> None:
    """Shut down and clear the shared manager (test isolation seam)."""
    global _shared_manager
    with _shared_manager_lock:
        if _shared_manager is not None:
            _shared_manager.shutdown()
            _shared_manager = None
