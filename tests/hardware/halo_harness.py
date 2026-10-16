# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run GAIA the way a Linux user does, and read what it did to the machine.

Every scenario gets its own empty ``HOME`` and drives the ``gaia`` installed
next to this interpreter as a subprocess, so the code under test is the
installed package (a wheel, or the checkout built into the venv), never this
checkout's ``src/`` that the root conftest puts on ``sys.path``.

Evidence -- memory, ports, pids, request logs -- is appended to a Markdown file
(``GAIA_HW_EVIDENCE``) that CI copies into the job summary.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import psutil
import requests

WORK_ROOT = Path(
    os.environ.get("GAIA_HW_WORKDIR") or tempfile.mkdtemp(prefix="gaia-linux-halo-")
)
EVIDENCE = Path(os.environ.get("GAIA_HW_EVIDENCE") or WORK_ROOT / "evidence.md")
ARTIFACTS = Path(os.environ.get("GAIA_HW_ARTIFACTS") or WORK_ROOT / "artifacts")

#: Variables that would point GAIA somewhere other than the scenario's HOME,
#: or at another checkout's source. The root conftest sets several of them.
_SCRUBBED_ENV = (
    "PYTHONPATH",
    "GAIA_HOME",
    "GAIA_CONFIG_FILE",
    "GAIA_CONFIG_DIR",
    "GAIA_DAEMON_HOME",
    "GAIA_MEMORY_DB",
    "GAIA_GATEWAY_FILE",
    "GAIA_CTX_SIZE",
    "GAIA_LEMONADE_EMBEDDED",
    "LEMONADE_BASE_URL",
    "LEMONADE_API_KEY",
)

#: A small built-in model: these bugs are about lifecycle, not model size.
SMALL_MODEL = os.environ.get("GAIA_HW_SMALL_MODEL", "Qwen3-0.6B-GGUF")
#: The model the 16 GB memory guard was written for; GAIA knows its size.
GEMMA = "Gemma-4-E4B-it-GGUF"


def evidence(line: str = "") -> None:
    """Append *line* to the evidence file and echo it to the test log."""
    print(line, flush=True)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVIDENCE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def gb(n_bytes: float) -> str:
    return f"{n_bytes / 1e9:.2f} GB"


def wait_until(
    predicate: Callable[[], object], timeout: float, what: str, interval: float = 1.0
):
    """Poll *predicate* until it is truthy; return its value and the seconds taken."""
    start = time.monotonic()
    while True:
        value = predicate()
        if value:
            return value, time.monotonic() - start
        if time.monotonic() - start > timeout:
            raise AssertionError(f"Timed out after {timeout:.0f}s waiting for {what}.")
        time.sleep(interval)


# --- GPU memory --------------------------------------------------------------


def _parse_size(text: str) -> int:
    value, _, unit = text.strip().partition(" ")
    scale = {"": 1, "B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}[unit]
    return int(value) * scale


def gpu_memory_of(pids: Iterable[int]) -> int:
    """Bytes of GPU memory (VRAM + GTT) the processes hold, from DRM fdinfo.

    Per-process, so another workload on this shared machine cannot move it.
    Each DRM client is counted once even when several fds share it.
    """
    seen = set()
    total = 0
    for pid in pids:
        fdinfo = Path(f"/proc/{pid}/fdinfo")
        try:
            entries = list(fdinfo.iterdir())
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        for entry in entries:
            try:
                text = entry.read_text()
            except OSError:
                continue
            if "drm-driver:" not in text:
                continue
            fields = {}
            for line in text.splitlines():
                key, sep, value = line.partition(":")
                if sep:
                    fields[key.strip()] = value.strip()
            client = (fields.get("drm-pdev"), fields.get("drm-client-id"))
            if client in seen:
                continue
            seen.add(client)
            for region in ("vram", "gtt"):
                for prefix in ("drm-resident-", "drm-memory-", "drm-total-"):
                    raw = fields.get(prefix + region)
                    if raw:
                        total += _parse_size(raw)
                        break
    return total


def sysfs_gpu_used() -> int:
    """Machine-wide VRAM + GTT in use on every amdgpu card, in bytes."""
    total = 0
    for dev in Path("/sys/class/drm").glob("card[0-9]*/device"):
        if not (dev / "mem_info_vram_used").is_file():
            continue
        for name in ("mem_info_vram_used", "mem_info_gtt_used"):
            total += int((dev / name).read_text())
    return total


def amdgpu_present() -> bool:
    return any(
        (dev / "mem_info_vram_used").is_file()
        for dev in Path("/sys/class/drm").glob("card[0-9]*/device")
    )


def process_tree(pid: int) -> List[int]:
    try:
        root = psutil.Process(pid)
        return [pid] + [c.pid for c in root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def port_listening(port: int) -> bool:
    for conn in psutil.net_connections(kind="tcp"):
        if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
            return True
    return False


# --- A user's machine, in a directory -----------------------------------------


class Scenario:
    """One fresh user: an empty HOME, the installed ``gaia``, and its servers."""

    def __init__(self, name: str, hf_home: Optional[Path] = None):
        self.name = name
        self.root = WORK_ROOT / name
        if self.root.exists():
            raise RuntimeError(
                f"{self.root} already exists, so this scenario would not start cold."
            )
        self.home = self.root / "home"
        self.gaia_home = self.home / ".gaia"
        self.home.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
        env.update(
            HOME=str(self.home),
            GAIA_HOME=str(self.gaia_home),
            NO_PROXY="localhost,127.0.0.1,::1",
            PYTHONUNBUFFERED="1",
        )
        hf = hf_home or os.environ.get("HF_HOME")
        if hf:
            env["HF_HOME"] = str(hf)
        env.pop("HF_HUB_CACHE", None)
        self.env = env
        bindir = Path(sys.executable).parent
        self.gaia_bin = bindir / "gaia"
        if not self.gaia_bin.exists():
            raise RuntimeError(
                f"No `gaia` next to {sys.executable}. Install GAIA into this "
                "interpreter's environment first."
            )

    # -- running things -------------------------------------------------------

    def run(
        self,
        argv: List[str],
        timeout: float = 600,
        check: bool = True,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        env = {**self.env, **(extra_env or {})}
        print(f"$ {' '.join(argv)}", flush=True)
        result = subprocess.run(
            argv,
            env=env,
            cwd=str(self.home),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        print(output, flush=True)
        if check and result.returncode != 0:
            raise AssertionError(
                f"`{' '.join(argv)}` exited {result.returncode}:\n{output[-4000:]}"
            )
        result.output = output
        return result

    def gaia(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return self.run([str(self.gaia_bin), *args], **kwargs)

    def python(self, code: str, **kwargs) -> subprocess.CompletedProcess:
        return self.run([sys.executable, "-c", code], **kwargs)

    def spawn(
        self, argv: List[str], log_name: str, extra_env: Optional[dict] = None
    ) -> subprocess.Popen:
        log = open(self.root / log_name, "ab")  # noqa: SIM115 - closed with the process
        return subprocess.Popen(
            argv,
            env={**self.env, **(extra_env or {})},
            cwd=str(self.home),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )

    # -- GAIA's embedded server -----------------------------------------------

    @property
    def lemonade_dir(self) -> Path:
        return self.gaia_home / "lemonade"

    def state(self) -> Optional[dict]:
        path = self.lemonade_dir / "state.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def last_port(self) -> Optional[int]:
        path = self.lemonade_dir / "last_port.json"
        if not path.is_file():
            return None
        return int(json.loads(path.read_text(encoding="utf-8"))["port"])

    def server(self) -> Tuple[int, int, str]:
        """``(pid, port, api_key)`` of GAIA's running server; fails if none."""
        state = self.state()
        if not state or not psutil.pid_exists(int(state["pid"])):
            raise AssertionError(
                f"GAIA's Lemonade Server is not running (state: {state})."
            )
        return int(state["pid"]), int(state["port"]), state["api_key"]

    def server_running(self) -> Optional[Tuple[int, int]]:
        state = self.state()
        if not state or not psutil.pid_exists(int(state["pid"])):
            return None
        try:
            r = requests.get(
                f"http://localhost:{state['port']}/api/v1/health",
                headers={"Authorization": f"Bearer {state['api_key']}"},
                timeout=5,
            )
        except requests.RequestException:
            return None
        return (int(state["pid"]), int(state["port"])) if r.ok else None

    def api(self, method: str, route: str, **kwargs) -> requests.Response:
        _, port, key = self.server()
        return requests.request(
            method,
            f"http://localhost:{port}/api/v1/{route}",
            headers={"Authorization": f"Bearer {key}"},
            timeout=kwargs.pop("timeout", 60),
            **kwargs,
        )

    def loaded(self) -> Dict[str, int]:
        """``{model: ctx_size}`` for every model GAIA's server has loaded."""
        r = self.api("GET", "health")
        r.raise_for_status()
        out = {}
        for entry in r.json().get("all_models_loaded") or []:
            name = entry.get("model_name") or entry.get("id")
            ctx = (entry.get("recipe_options") or {}).get("ctx_size") or 0
            out[name] = int(ctx)
        return out

    def server_gpu_memory(self) -> int:
        pid, _, _ = self.server()
        return gpu_memory_of(process_tree(pid))

    def daemon_pid(self) -> Optional[int]:
        for path in (self.gaia_home / "host").glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            pid = data.get("pid") if isinstance(data, dict) else None
            if isinstance(pid, int) and psutil.pid_exists(pid):
                return pid
        return None

    # -- leaving the machine clean -------------------------------------------

    def own_processes(self) -> List[psutil.Process]:
        """Every process started from this scenario's directory."""
        marker = str(self.root)
        found = []
        for proc in psutil.process_iter(["pid", "cmdline", "cwd", "environ"]):
            if proc.pid == os.getpid():
                continue
            info = proc.info
            cmd = " ".join(info.get("cmdline") or [])
            env_home = (info.get("environ") or {}).get("HOME", "")
            if (
                marker in cmd
                or (info.get("cwd") or "").startswith(marker)
                or env_home.startswith(marker)
            ):
                found.append(proc)
        return found

    def cleanup(self) -> List[str]:
        """Stop everything this scenario started, keep its logs, delete the rest.

        Returns what had to be killed by force; empty means GAIA shut down on
        its own.
        """
        for argv in (("daemon", "stop"), ("lemonade", "embedded", "stop")):
            try:
                self.gaia(*argv, timeout=120, check=False)
            except subprocess.TimeoutExpired:
                print(f"gaia {' '.join(argv)} timed out during cleanup", flush=True)
        killed = []
        leftovers = self.own_processes()
        for proc in leftovers:
            try:
                killed.append(f"{proc.pid} {' '.join(proc.cmdline())[:160]}")
                proc.send_signal(signal.SIGTERM)
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(leftovers, timeout=15)
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
        dest = ARTIFACTS / self.name
        dest.mkdir(parents=True, exist_ok=True)
        for pattern in ("*.log", "**/*.log", "**/state.json", "**/last_port.json"):
            for path in self.root.glob(pattern):
                if path.is_file() and "models--" not in str(path):
                    target = dest / path.relative_to(self.root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
        shutil.rmtree(self.root, ignore_errors=True)
        return killed
