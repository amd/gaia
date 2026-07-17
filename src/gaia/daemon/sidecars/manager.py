# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AgentSidecarManager — spawn / health / tree-kill a sidecar agent process,
generalized from ``EmailSidecarManager`` (Node-free) to be spec-driven so the
daemon can supervise more than one kind of sidecar (issue #2142, T1).

Modes (``spec.mode_env_var``):
  user (default) — spawn the verified frozen binary: a Hub-installed one when
                   present (#2095), else lazy-fetched via binaries.lock.json.
  dev            — spawn ``uvicorn <spec.dev_module> --reload --app-dir
                   <spec.dev_src_dir>/<spec.dev_app_dir>`` from source. Loaded
                   as a TOP-LEVEL module (NOT ``packaging.server:app``, which
                   would resolve to the PyPI ``packaging`` library — the
                   email package's ``packaging/`` dir has no ``__init__.py``
                   by design).

Both serve the identical sidecar contract; the mode only swaps which process
answers. The manager binds an ephemeral per-instance port (NEVER the daemon's
reserved port) and tree-kills the whole process group on shutdown (a
PyInstaller one-file build spawns a uvicorn child that a plain kill would
orphan).

Threading note: ``start()`` (health-poll, lazy binary fetch) is **synchronous
and blocking**. Callers MUST invoke it off the event loop (``await
asyncio.to_thread(...)`` / ``run_in_executor``). Calling ``start()`` directly
from an async route would stall the caller for up to ``health_timeout``
seconds.

Layering: this module never imports ``gaia.ui``. Constructing a request proxy
against the running sidecar is UI-side work — the UI's daemon client owns it.
"""

from __future__ import annotations

import atexit
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from gaia.daemon.constants import RESERVED_PORT
from gaia.daemon.sidecars import fetch
from gaia.daemon.sidecars.errors import (
    HealthTimeoutError,
    SidecarSpawnError,
    VersionMismatchError,
)
from gaia.daemon.sidecars.spec import AgentSidecarSpec
from gaia.logger import get_logger

logger = get_logger(__name__)

_HOST = "127.0.0.1"
_VALID_MODES = ("user", "dev")
# Keep only the most recent N per-port sidecar logs; ephemeral ports mean a new
# file per restart, which would otherwise accumulate without bound.
_MAX_SIDECAR_LOGS = 5


def find_free_port(host: str = _HOST) -> int:
    """Bind to port 0 to get an OS-assigned free port. Never the daemon's reserved port."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            port = s.getsockname()[1]
        if port != RESERVED_PORT:
            return port
    raise SidecarSpawnError("could not find a free ephemeral port for the sidecar")


def _major(version: str) -> int:
    """Parse the MAJOR component of a dotted version ('1.3' -> 1)."""
    try:
        return int(str(version).split(".")[0])
    except (ValueError, IndexError) as e:
        raise VersionMismatchError(
            f"cannot parse a MAJOR version from '{version}'"
        ) from e


class AgentSidecarManager:
    def __init__(
        self,
        spec: AgentSidecarSpec,
        mode: Optional[str] = None,
        *,
        host: str = _HOST,
        lock_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        health_timeout: Optional[float] = None,
        expected_api_version: Optional[str] = None,
    ):
        self.spec = spec
        self._mode_override = mode
        self.host = host
        self.lock_path = lock_path
        self.cache_dir = cache_dir
        self.log_dir = (
            Path(log_dir)
            if log_dir
            else fetch.default_cache_dir(spec.cache_dir_name) / "logs"
        )
        self.health_timeout = (
            health_timeout if health_timeout is not None else spec.health_timeout
        )
        self.expected_api_version = expected_api_version
        self._proc: Optional[subprocess.Popen] = None
        self._log_handle = None
        self._log_path: Optional[Path] = None
        self._atexit_registered = False
        # Serializes start()/shutdown() so a concurrent lazy "first use" from two
        # callers spawns exactly one sidecar, not two. Reentrant: start() calls
        # shutdown() on its own failure path.
        self._lock = threading.RLock()
        self.port: Optional[int] = None
        self.base_url: Optional[str] = None
        self.api_version: Optional[str] = None
        self.agent_version: Optional[str] = None
        self.started_at: Optional[float] = None
        # The argv the live process was spawned with — recorded so the daemon's
        # crash-reap ledger can later confirm a pid's identity before killing it.
        self.spawn_argv: Optional[list] = None
        # Lifecycle hooks (set by the registry). on_process_spawned fires the
        # moment Popen returns — before the health wait — so the crash-reap
        # ledger has an entry for the whole vulnerable window. on_process_reaped
        # fires only once the leader is confirmed exited.
        self.on_process_spawned: Optional[Callable] = None
        self.on_process_reaped: Optional[Callable] = None
        # The mode actually used at spawn time, captured once (NOT the live
        # `mode` property, which re-reads env on every access). A registry
        # comparing "is a re-ensure asking for a different mode than what's
        # already running" must compare against this, not a moving target.
        self.resolved_mode: Optional[str] = None
        # Per-session caller-auth token handed to the sidecar on spawn (#1706).
        # Generated once per manager instance and passed over the private env
        # channel; the proxy replays it as a bearer token on every request.
        self.auth_token: str = secrets.token_urlsafe(32)

    @property
    def mode(self) -> str:
        m = self._mode_override or os.environ.get(self.spec.mode_env_var) or "user"
        if m not in _VALID_MODES:
            raise SidecarSpawnError(
                f"{self.spec.mode_env_var}='{m}' is invalid; expected one of "
                f"{_VALID_MODES}. There is no fallback — set it explicitly."
            )
        return m

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc is not None else None

    def __enter__(self) -> "AgentSidecarManager":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    def build_spawn_command(self, *, port: int):
        if port == RESERVED_PORT:
            raise ValueError(f"port {RESERVED_PORT} is reserved and must never be used")
        if self.mode == "user":
            try:
                result = fetch.fetch_binary(
                    out_dir=self.cache_dir,
                    lock_path=self.lock_path,
                    agent_dir_name=self.spec.cache_dir_name,
                )
            except Exception as e:  # re-raise with the user-mode remedy
                raise SidecarSpawnError(
                    f"{self.spec.display_name} sidecar binary unavailable in user "
                    f"mode: {e} Set {self.spec.mode_env_var}=dev to run from "
                    "source, or publish the agent so the binary + real SHA exist."
                ) from e
            argv = [str(result.binary_path), "--host", self.host, "--port", str(port)]
            return argv, {}
        # dev mode — load packaging/server.py as the top-level module `server`.
        if self.spec.dev_src_dir is None:
            raise SidecarSpawnError(
                f"dev mode needs a source dir but {self.spec.agent_id}'s spec has "
                "none configured."
            )
        app_dir = self.spec.dev_src_dir / self.spec.dev_app_dir
        if not app_dir.is_dir():
            raise SidecarSpawnError(
                f"dev mode needs the {self.spec.agent_id} source at {app_dir} but "
                "it is missing. Run from a source checkout, or install it: "
                "`uv pip install -e hub/agents/python/email`."
            )
        argv = [
            sys.executable,
            "-m",
            "uvicorn",
            self.spec.dev_module,
            "--reload",
            "--app-dir",
            str(app_dir),
            "--host",
            self.host,
            "--port",
            str(port),
        ]
        return argv, {"cwd": str(self.spec.dev_src_dir)}

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
        # Hand the sidecar its per-session caller-auth token over the private env
        # channel (#1706). Merge over the inherited env / any cwd already in
        # popen_kwargs — never clobber them.
        spawn_env = {**os.environ, **(popen_kwargs.pop("env", None) or {})}
        spawn_env[self.spec.token_env_var] = self.auth_token
        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=start_new_session,
                creationflags=creationflags,
                env=spawn_env,
                **popen_kwargs,
            )
        except OSError as e:
            self._close_log()
            raise SidecarSpawnError(
                f"failed to launch the {self.spec.agent_id} sidecar ({argv[0]}): {e}"
            ) from e
        self.spawn_argv = list(argv)
        self.started_at = time.time()
        if self.on_process_spawned is not None:
            self.on_process_spawned(self._proc.pid, port, list(argv))
        # Reap the sidecar if the owner exits without calling shutdown() — a
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
        # Captured once per spawn attempt sequence — NOT re-read from the live
        # (env-driven) `mode` property afterward.
        self.resolved_mode = self.mode
        last_exit_err: Optional[SidecarSpawnError] = None
        for attempt in range(1, max_attempts + 1):
            self.port = find_free_port(self.host)
            argv, popen_kwargs = self.build_spawn_command(port=self.port)
            logger.info(
                "%s sidecar: spawning (%s mode, attempt %d/%d) %s",
                self.spec.agent_id,
                self.resolved_mode,
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
                    "%s sidecar attempt %d/%d exited early; retrying: %s",
                    self.spec.agent_id,
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
                f"{self.spec.agent_id} sidecar failed to start after "
                f"{max_attempts} attempts. Last failure: {last_exit_err}"
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
                    f"{self.spec.agent_id} sidecar exited early (code "
                    f"{self._proc.returncode}) before becoming healthy. Last log "
                    f"output:\n{self._read_log_tail()}"
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
                    and body.get("service") == self.spec.service_id
                ):
                    logger.info(
                        "%s sidecar healthy at %s", self.spec.agent_id, self.base_url
                    )
                    return
                if r.status_code == 200 and body.get("service") not in (
                    None,
                    self.spec.service_id,
                ):
                    last_err = (
                        f"a different service ('{body.get('service')}') is serving "
                        f"{self.base_url} — not the {self.spec.agent_id} sidecar"
                    )
                else:
                    last_err = f"status={r.status_code} body={r.text[:200]}"
            except requests.exceptions.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
            time.sleep(interval)
        raise HealthTimeoutError(
            f"{self.spec.agent_id} sidecar at {self.base_url} did not become "
            f"healthy within {self.health_timeout}s. Last probe error: {last_err}. "
            "Check the process launched and the port is free. Sidecar log:\n"
            f"{self._read_log_tail()}"
        )

    def _check_version(self) -> None:
        """Capture the sidecar's contract version; gate on a MAJOR mismatch.

        Always records ``api_version``/``agent_version`` for diagnostics. When an
        ``expected_api_version`` was provided, a differing MAJOR is a breaking
        contract change and fails loudly (no silent mismatch).
        """
        try:
            r = self._http_get(f"{self.base_url}/version", timeout=5.0)
            info = r.json()
        except Exception as e:  # noqa: BLE001 - surface as a loud spawn failure
            raise SidecarSpawnError(
                f"{self.spec.agent_id} sidecar did not answer /version at "
                f"{self.base_url}: {e}"
            ) from e
        self.api_version = info.get("apiVersion")
        self.agent_version = info.get("agentVersion")
        logger.info(
            "%s sidecar contract: apiVersion=%s agentVersion=%s",
            self.spec.agent_id,
            self.api_version,
            self.agent_version,
        )
        if self.expected_api_version:
            if not self.api_version:
                raise VersionMismatchError(
                    f"{self.spec.agent_id} sidecar /version omitted 'apiVersion' "
                    f"but the host pins '{self.expected_api_version}'. Cannot "
                    "confirm contract compatibility — refusing to proceed."
                )
            if _major(self.api_version) != _major(self.expected_api_version):
                raise VersionMismatchError(
                    f"{self.spec.agent_id} sidecar apiVersion '{self.api_version}' "
                    f"has a different MAJOR than expected "
                    f"'{self.expected_api_version}'. A major bump is a breaking "
                    "contract change — upgrade the sidecar binary or the host to "
                    "matching versions."
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
            self._fire_reaped()
            return
        pid = proc.pid
        logger.info("%s sidecar: tree-killing pid=%s", self.spec.agent_id, pid)
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
            logger.warning(
                "%s sidecar did not exit in %ss; SIGKILL", self.spec.agent_id, timeout
            )
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
        leader_gone = proc.poll() is not None
        self._proc = None
        self._close_log()
        if leader_gone:
            self._fire_reaped()
        logger.info("%s sidecar: shut down", self.spec.agent_id)

    def _fire_reaped(self) -> None:
        if self.on_process_reaped is not None:
            self.on_process_reaped()
