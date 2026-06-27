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
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from gaia.logger import get_logger
from gaia.ui.email_sidecar import fetch
from gaia.ui.email_sidecar.errors import HealthTimeoutError, SidecarSpawnError

logger = get_logger(__name__)

_HOST = "127.0.0.1"
_RESERVED_PORT = 4001
_VALID_MODES = ("user", "dev")


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


class EmailSidecarManager:
    def __init__(
        self,
        mode: Optional[str] = None,
        *,
        host: str = _HOST,
        lock_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        email_src_dir: Optional[Path] = None,
        health_timeout: float = 30.0,
    ):
        self._mode_override = mode
        self.host = host
        self.lock_path = lock_path
        self.cache_dir = cache_dir
        self.email_src_dir = (
            Path(email_src_dir) if email_src_dir else _default_email_src_dir()
        )
        self.health_timeout = health_timeout
        self._proc: Optional[subprocess.Popen] = None
        self.port: Optional[int] = None
        self.base_url: Optional[str] = None

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

    def start(self) -> str:
        if self.is_running:
            return self.base_url
        self.port = find_free_port(self.host)
        argv, popen_kwargs = self.build_spawn_command(port=self.port)
        logger.info("email sidecar: spawning (%s mode) %s", self.mode, " ".join(argv))
        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            start_new_session = True  # own process group for tree-kill
        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=start_new_session,
                creationflags=creationflags,
                **popen_kwargs,
            )
        except OSError as e:
            raise SidecarSpawnError(
                f"failed to launch the email sidecar ({argv[0]}): {e}"
            ) from e
        self.base_url = f"http://{self.host}:{self.port}"
        try:
            self._wait_for_health()
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
                stderr = (self._proc.stderr.read() or b"").decode("utf-8", "replace")
                raise SidecarSpawnError(
                    f"email sidecar exited early (code {self._proc.returncode}) before "
                    f"becoming healthy. Last stderr:\n{stderr[-2000:]}"
                )
            try:
                r = requests.get(url, timeout=interval * 4)
                if r.status_code == 200 and r.json().get("status") == "ok":
                    logger.info("email sidecar healthy at %s", self.base_url)
                    return
                last_err = f"status={r.status_code} body={r.text[:200]}"
            except requests.exceptions.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
            time.sleep(interval)
        raise HealthTimeoutError(
            f"email sidecar at {self.base_url} did not become healthy within "
            f"{self.health_timeout}s. Last probe error: {last_err}. Check the process "
            "launched and the port is free."
        )

    def shutdown(self, timeout: float = 5.0) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is not None:
            self._proc = None
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
        logger.info("email sidecar: shut down")
