# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Break GAIA's own Lemonade Server on purpose, on an isolated home, and watch.

Every fault here lands on a server this harness started under a temp
``GAIA_HOME`` (and a temp user profile, so even ``~/.gaia/gaia.log`` is the
harness's own). It never stops, restarts or kills any other Lemonade: every
kill first checks the process's executable lives under the harness's root.

Used by ``tests/integration/test_lemonade_restart_live.py`` and runnable on its
own::

    python util/lemonade_restart_harness.py repro           # every scenario
    python util/lemonade_restart_harness.py repro session_job_closed
    python util/lemonade_restart_harness.py --code <main export> repro
    python util/lemonade_restart_harness.py stress --sessions 16

Each run writes ``results.json`` and keeps the isolated home's logs under
``--out`` (default: a temp dir, printed at the end).

The lemond binaries and backends are copied from an existing GAIA install
(``~/.gaia/lemonade`` unless ``--source-home`` says otherwise), or downloaded
when there is none. Models are read from the real Hugging Face cache; nothing
is ever written there except by a download the scenario asked for.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

#: What GAIA prints while it waits for its server (lemonade_client.py).
RECONNECTING = "Reconnecting to the model server"
#: The original failure.
NOT_REACHABLE = "not reachable"
#: The bound the client waits for GAIA's server (lemonade_client.py).
RECONNECT_BOUND = 60.0
#: A small embedding model: it runs on the CPU, so the isolated server never
#: competes for the GPU the user's own sessions use.
EMBED_MODEL = "nomic-embed-text-v2-moe-GGUF"
#: The default local chat model, already in the Hugging Face cache here; the
#: isolated home runs it on the CPU (llamacpp_backend), off the shared GPU.
LOCAL_MODEL = "Gemma-4-E4B-it-GGUF"
#: The stub cloud model, as Lemonade lists it once :class:`StubCloud` is
#: registered as the ``amd`` provider (the incident's provider).
CLOUD_MODEL = "amd.Stub-Flash"

CREATE_SUSPENDED = 0x00000004

#: Whether the session jobs the harness makes allow breakaway, as the TUI's do
#: since #5264. ``--old-tui-job`` makes them as the TUI did before.
TUI_JOB_BREAKAWAY = True


#: Whose code the daemon and clients run: this checkout, or another tree
#: (``--code``) laid out the same way -- e.g. ``main`` before a fix.
CODE_ROOT = Path(os.environ.get("GAIA_HARNESS_CODE", str(REPO_ROOT)))


def _python_path() -> str:
    """The code under test first, so children run it and not an install."""
    parts = [
        CODE_ROOT / "src",
        CODE_ROOT / "hub" / "agents" / "gaia" / "python",
        CODE_ROOT / "hub" / "agents" / "chat" / "python",
    ]
    return os.pathsep.join(str(p) for p in parts)


def _real_hf_cache() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


# -- Windows job objects, as the TUI makes them -------------------------------


class Job:
    """A KILL_ON_JOB_CLOSE job object, built the way ``procgroup_windows.go`` is.

    A process created by a process already in a job joins that job, so closing
    the handle kills everything the child went on to spawn.
    """

    def __init__(self, breakaway_ok: Optional[bool] = None):
        if breakaway_ok is None:
            breakaway_ok = TUI_JOB_BREAKAWAY
        import ctypes
        from ctypes import wintypes

        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.CreateJobObjectW.restype = wintypes.HANDLE
        self._k32.OpenProcess.restype = wintypes.HANDLE
        self.handle = self._k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000 | (  # KILL_ON_JOB_CLOSE
            0x800 if breakaway_ok else 0  # BREAKAWAY_OK
        )
        if not self._k32.SetInformationJobObject(
            self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

    def spawn(self, argv: List[str], **kwargs) -> subprocess.Popen:
        """Start *argv* suspended, put it in the job, then let it run."""
        import ctypes

        import psutil

        flags = kwargs.pop("creationflags", 0) | CREATE_SUSPENDED
        proc = subprocess.Popen(argv, creationflags=flags, **kwargs)
        h = self._k32.OpenProcess(0x0100 | 0x0001, False, proc.pid)
        try:
            if not self._k32.AssignProcessToJobObject(self.handle, h):
                proc.kill()
                raise OSError(
                    ctypes.get_last_error(), "AssignProcessToJobObject failed"
                )
        finally:
            self._k32.CloseHandle(h)
        psutil.Process(proc.pid).resume()
        return proc

    def close(self) -> None:
        """Close the handle: Windows kills every process still in the job."""
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


class StubCloud:
    """A local OpenAI-compatible upstream that answers every chat with "OK".

    Registered as Lemonade's ``amd`` cloud provider, it lets a scenario run
    real cloud turns through the isolated lemond with no account, no network
    and no GPU.
    """

    MODEL = "Stub-Flash"
    #: A second model, so a session can start on one and ``/model`` to the other.
    OTHER = "Stub-Pro"

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        model = self.MODEL
        self.calls = 0
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, body: dict) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(
                    {
                        "object": "list",
                        "data": [
                            {"id": m, "object": "model"} for m in (model, stub.OTHER)
                        ],
                    }
                )

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(length) or b"{}")
                stub.calls += 1
                msg = {"role": "assistant", "content": "OK"}
                if req.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for chunk in (
                        {"choices": [{"index": 0, "delta": msg}]},
                        {
                            "choices": [
                                {"index": 0, "delta": {}, "finish_reason": "stop"}
                            ]
                        },
                    ):
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                self._send(
                    {
                        "id": "stub",
                        "object": "chat.completion",
                        "model": req.get("model", model),
                        "choices": [
                            {"index": 0, "message": msg, "finish_reason": "stop"}
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


# -- the isolated install -------------------------------------------------------


@dataclass
class TurnResult:
    """One client request against the isolated server."""

    scenario: str
    ok: bool
    elapsed: float
    reconnecting: bool = False
    detail: str = ""
    port_before: Optional[int] = None
    port_after: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class IsolatedGaia:
    """A GAIA home, user profile, daemon and lemond nobody else uses.

    Args:
        root: Empty directory to build it in.
        source_home: An existing GAIA home to copy lemond and its backends from.
        idle_minutes: ``lemonade_idle_timeout_minutes`` for this home.
        python: Interpreter for the daemon and the clients.
    """

    def __init__(
        self,
        root: Path,
        source_home: Optional[Path] = None,
        idle_minutes: int = 15,
        python: str = sys.executable,
    ):
        self.root = Path(root).resolve()
        self.profile = self.root / "profile"
        self.home = self.profile / ".gaia"
        self.source_home = source_home
        self.idle_minutes = idle_minutes
        self.python = python
        self.events: List[Dict[str, Any]] = []
        self._t0 = time.monotonic()
        self.cloud = StubCloud()
        self.cloud_ready = False

    # -- environment ---------------------------------------------------------

    def env(self, **extra: str) -> Dict[str, str]:
        """The environment every isolated process runs with."""
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("LEMONADE_", "GAIA_"))
        }
        env.update(
            {
                "GAIA_HOME": str(self.home),
                "USERPROFILE": str(self.profile),
                "HOME": str(self.profile),
                "HF_HUB_CACHE": str(_real_hf_cache()),
                "PYTHONPATH": _python_path(),
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
                "GAIA_SKIP_ONBOARDING": "1",
                # The user's real cloud keys are never read: the stub cloud's
                # key reaches lemond through its own environment instead.
                "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
                "LEMONADE_AMD_API_KEY": "harness-stub-key",
            }
        )
        env.update(extra)
        return env

    def note(self, what: str, **data: Any) -> None:
        """Timestamp *what* on the harness's own timeline."""
        entry = {"t": round(time.monotonic() - self._t0, 3), "event": what, **data}
        self.events.append(entry)
        print(f"[{entry['t']:8.2f}s] {what} {data if data else ''}", flush=True)

    # -- provisioning ----------------------------------------------------------

    def provision(self) -> None:
        """Lay out the home: lemond, its backends, and a config."""
        from gaia.version import LEMONADE_VERSION

        lemonade = self.home / "lemonade"
        dist = lemonade / "dist" / LEMONADE_VERSION
        src = (self.source_home or Path.home() / ".gaia") / "lemonade"
        if (src / "dist" / LEMONADE_VERSION).is_dir():
            shutil.copytree(src / "dist" / LEMONADE_VERSION, dist)
            if (src / "cache" / "bin").is_dir():
                shutil.copytree(src / "cache" / "bin", lemonade / "cache" / "bin")
        else:
            self._run_py(
                "from gaia.llm.lemonade_embedded import EmbeddedLemonade; "
                "EmbeddedLemonade().install()",
                timeout=600,
            )
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.json").write_text(
            json.dumps(
                {
                    "lemonade_idle_timeout_minutes": self.idle_minutes,
                    "llamacpp_backend": "cpu",
                }
            ),
            encoding="utf-8",
        )
        self.note("provisioned", home=str(self.home))

    def set_config(self, **values: Any) -> None:
        """Change this home's ``config.json``; the daemon reads it live."""
        path = self.home / "config.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg.update(values)
        path.write_text(json.dumps(cfg), encoding="utf-8")

    def is_lemond_installed(self) -> bool:
        from gaia.version import LEMONADE_VERSION

        exe = "lemond.exe" if sys.platform == "win32" else "lemond"
        return (self.home / "lemonade" / "dist" / LEMONADE_VERSION / exe).is_file()

    # -- reading state ---------------------------------------------------------

    def state(self) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(
                (self.home / "lemonade" / "state.json").read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ValueError):
            return None

    def instance(self) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(
                (self.home / "host" / "instance.json").read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ValueError):
            return None

    def health(self, timeout: float = 2.0) -> Optional[dict]:
        st = self.state()
        if not st:
            return None
        req = urllib.request.Request(
            f"http://127.0.0.1:{st['port']}/api/v1/health",
            headers={"Authorization": f"Bearer {st['api_key']}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def wait_healthy(self, timeout: float = 60.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.health() is not None:
                return True
            time.sleep(0.25)
        return False

    # -- processes ---------------------------------------------------------------

    def owns(self, pid: int) -> bool:
        """Whether *pid* runs an executable from this harness's root, or is a
        Python whose environment names this harness's GAIA_HOME."""
        import psutil

        try:
            proc = psutil.Process(pid)
            exe = Path(proc.exe()).resolve()
        except (psutil.Error, OSError):
            return False
        if self.root in exe.parents:
            return True
        try:
            return proc.environ().get("GAIA_HOME") == str(self.home)
        except psutil.Error:
            return False

    def lemond_pids(self) -> List[int]:
        import psutil

        pids = []
        for proc in psutil.process_iter(["pid", "name"]):
            if (proc.info["name"] or "").lower().startswith("lemond") and self.owns(
                proc.info["pid"]
            ):
                pids.append(proc.info["pid"])
        return pids

    def daemon_pid(self) -> Optional[int]:
        inst = self.instance()
        return int(inst["pid"]) if inst else None

    def alive(self, pid: Optional[int]) -> bool:
        import psutil

        return bool(pid) and psutil.pid_exists(pid)

    def kill_lemond(self) -> Optional[int]:
        """Hard-kill the isolated lemond and its backends. Refuses any other."""
        st = self.state()
        if not st:
            return None
        pid = int(st["pid"])
        if not self.owns(pid):
            raise RuntimeError(f"pid {pid} is not this harness's lemond; not killing")
        subprocess.run(
            (
                ["taskkill", "/PID", str(pid), "/T", "/F"]
                if sys.platform == "win32"
                else ["kill", "-9", str(pid)]
            ),
            capture_output=True,
            check=False,
        )
        self.note("killed lemond", pid=pid, port=st["port"])
        return pid

    def hang_restart(self, unresponsive_for: Optional[float]) -> Dict[str, Any]:
        """Replace the server with one that is up but not answering.

        Holding the start lock, kill lemond and spawn a fresh one SUSPENDED --
        it exists, is GAIA's by every identity check, and answers nothing, as a
        server still loading does. After *unresponsive_for* seconds it is
        resumed; None leaves it hung for good.
        """
        import psutil

        from gaia.llm.lemonade_embedded import EmbeddedLemonade, child_env

        os.environ["GAIA_HOME"] = str(self.home)
        embedded = EmbeddedLemonade(home=self.home)
        inst = self.instance() or {}
        daemon_pid = inst.get("pid")
        started = psutil.Process(daemon_pid).create_time() if daemon_pid else None
        with embedded._instance_lock():  # pylint: disable=protected-access
            old = self.state() or {}
            self.kill_lemond()
            time.sleep(0.5)
            port = int(old.get("port") or 0) or None
            api_key = secrets.token_urlsafe(32)
            env = child_env(
                {
                    "LEMONADE_API_KEY": api_key,
                    "LEMONADE_AMD_API_KEY": self.env()["LEMONADE_AMD_API_KEY"],
                }
            )
            argv = [
                str(embedded.daemon_path),
                str(embedded.cache_dir),
                str(embedded.config_dir),
                "--port",
                str(port),
            ]
            suspended = (
                {"creationflags": CREATE_SUSPENDED | 0x08000000}
                if sys.platform == "win32"
                else {"start_new_session": True}
            )
            with open(embedded.log_path, "ab") as log:
                proc = subprocess.Popen(
                    argv,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    cwd=str(embedded.dist_dir),
                    **suspended,
                )
            if sys.platform != "win32":
                psutil.Process(proc.pid).suspend()  # before it binds the port
            embedded._write_state(  # pylint: disable=protected-access
                {
                    "pid": proc.pid,
                    "port": port,
                    "api_key": api_key,
                    "version": embedded.version,
                    "owner_pid": daemon_pid,
                    "owner_started": started,
                }
            )
        self.note("hung restart", pid=proc.pid, port=port, for_s=unresponsive_for)
        if unresponsive_for is not None:

            def _resume() -> None:
                time.sleep(unresponsive_for)
                psutil.Process(proc.pid).resume()
                self.note("resumed lemond", pid=proc.pid)

            threading.Thread(target=_resume, daemon=True).start()
        return {"pid": proc.pid, "port": port}

    def start_users_lemond(self) -> Dict[str, Any]:
        """Start a lemond GAIA does not own, as a user's own server: its own
        cache and config, no API key, no GAIA state file. Returns its port."""
        from gaia.llm.lemonade_embedded import EmbeddedLemonade, _free_port

        embedded = EmbeddedLemonade(home=self.home)
        mine = self.root / "users-lemonade"
        (mine / "cache").mkdir(parents=True, exist_ok=True)
        (mine / "config").mkdir(parents=True, exist_ok=True)
        port = _free_port()
        env = {k: v for k, v in self.env().items() if not k.startswith("LEMONADE_")}
        with open(mine / "lemond.log", "ab") as log:
            proc = subprocess.Popen(
                [
                    str(embedded.daemon_path),
                    str(mine / "cache"),
                    str(mine / "config"),
                    "--port",
                    str(port),
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(embedded.dist_dir),
            )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/v1/health", timeout=2
                ):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.25)
        else:
            raise RuntimeError(f"the user's stand-in lemond never answered on {port}")
        self.note("user's own lemond up", pid=proc.pid, port=port)
        return {"pid": proc.pid, "port": port, "proc": proc}

    # -- daemon ------------------------------------------------------------------

    def _run_py(self, code: str, timeout: float = 120, **env: str) -> str:
        r = subprocess.run(
            [self.python, "-c", code],
            env=self.env(**env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(f"child failed ({r.returncode}):\n{r.stderr[-4000:]}")
        return r.stdout

    def start_daemon(self) -> Dict[str, Any]:
        """Start the isolated daemon and have it bring lemond up."""
        out = self._run_py(
            textwrap.dedent("""
                import json
                from gaia.daemon.client import start_or_attach, ensure_lemonade
                inst = start_or_attach()
                served = ensure_lemonade()
                print(json.dumps({"daemon_pid": inst.pid, **served}))
                """),
            timeout=180,
        )
        info = json.loads(out.strip().splitlines()[-1])
        self.note("daemon up", **info)
        if not self.cloud_ready:
            self.connect_cloud()
            self.cloud_ready = True
        return info

    def connect_cloud(self) -> str:
        """Register :class:`StubCloud` as the ``amd`` provider on the isolated
        server, the way ``gaia gateway install`` registers the real gateway.

        Returns the cloud model id GAIA sees (``amd.<id>``).
        """
        res = self.run_client(
            textwrap.dedent(f"""
                from gaia.llm.gateway import GatewayManager
                client._follow_gaia_lemonade()
                GatewayManager(client).install({self.cloud.base_url!r},
                                               allow_insecure_http=True)
                ids = [m["id"] for m in client.list_models(show_all=True)["data"]
                       if m["id"].startswith("amd.")]
                emit(ok=bool(ids), ids=ids)
                """),
            timeout=180,
        )
        if not res.get("ok"):
            raise RuntimeError(f"stub cloud provider did not register: {res}")
        self.note("cloud provider registered", ids=res["ids"])
        return CLOUD_MODEL if CLOUD_MODEL in res["ids"] else res["ids"][0]

    def stop_daemon(self) -> None:
        """Shut the isolated daemon down; it stops its lemond with it."""
        inst = self.instance()
        if inst and self.owns(int(inst["pid"])):
            self._run_py(
                "from gaia.daemon.client import attach, request_shutdown, "
                "wait_until_gone\ni = attach()\n"
                "i and request_shutdown(i) and wait_until_gone(i, 30)",
                timeout=60,
            )
        self.note("daemon stopped", lemond_left=self.lemond_pids())

    def stop_everything(self) -> None:
        """Shut the isolated daemon down, then reap anything it left."""
        import psutil

        inst = self.instance()
        if inst and self.owns(int(inst["pid"])):
            with contextlib.suppress(Exception):
                self._run_py(
                    "from gaia.daemon.client import attach, request_shutdown, "
                    "wait_until_gone\ni = attach()\n"
                    "i and request_shutdown(i) and wait_until_gone(i, 30)",
                    timeout=60,
                )
        for proc in psutil.process_iter(["pid"]):
            pid = proc.info["pid"]
            if pid != os.getpid() and self.owns(pid):
                with contextlib.suppress(psutil.Error):
                    proc.kill()
        self.cloud.close()
        self.note("stopped everything")

    # -- clients -------------------------------------------------------------------

    def client_script(self, body: str, setup: str = "") -> List[str]:
        """argv for a client process: *setup*, then wait for "go", then *body*.

        Both see ``client`` (a LemonadeClient built at start, so it holds
        whatever port was recorded then), ``emit(**kw)`` (prints one JSON line)
        and ``seen`` (the states ``model_load_listener`` reported). The process
        says ``@@READY`` once *setup* is done, so a fault can land after the
        session exists and before its request, as it does for a real session.
        """
        prelude = textwrap.dedent("""
            import json, os, sys, time
            if os.environ.get("HARNESS_NO_LEASE"):
                # A process that never leases, as an older GAIA does not.
                import gaia.llm.lemonade_service as _svc
                _svc._AUTO_LEASED = True
            from gaia.llm.lemonade_client import LemonadeClient
            seen = []
            def emit(**kw):
                print("@@" + json.dumps(kw), flush=True)
            client = LemonadeClient(verbose=False)
            client.model_load_listener = lambda m, s: seen.append(s)
            """)
        handshake = 'print("@@READY", flush=True)\nsys.stdin.readline()\n'
        code = prelude + textwrap.dedent(setup) + handshake + textwrap.dedent(body)
        return [self.python, "-c", code]

    def run_client(
        self,
        body: str,
        timeout: float = 180.0,
        job: Optional[Job] = None,
        setup: str = "",
        inject: Optional[Any] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Run a client to completion; return its last ``emit`` and its log.

        *inject* is called once the client is ready and before it sends its
        request.
        """
        argv = self.client_script(body, setup)
        kwargs = dict(
            env=self.env(**(env or {})),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        proc = job.spawn(argv, **kwargs) if job else subprocess.Popen(argv, **kwargs)
        lines: List[str] = []
        ready = threading.Event()

        def _read() -> None:
            for line in proc.stdout:
                if line.startswith("@@READY"):
                    ready.set()
                elif line.startswith("@@"):
                    lines.append(line[2:])
            ready.set()

        errs: List[str] = []
        reader = threading.Thread(target=_read, daemon=True)
        err_reader = threading.Thread(
            target=lambda: errs.extend(proc.stderr), daemon=True
        )
        reader.start()
        err_reader.start()
        ready.wait(timeout)
        if inject is not None:
            inject()
        with contextlib.suppress(OSError):
            proc.stdin.write("go\n")
            proc.stdin.flush()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        reader.join(5)
        err_reader.join(5)
        result = json.loads(lines[-1]) if lines else {}
        result.setdefault("ok", False)
        result["stderr_tail"] = "".join(errs)[-3000:]
        result["returncode"] = proc.returncode
        return result

    def collect_logs(self, out: Path) -> None:
        """Copy every log the isolated processes wrote to *out*."""
        out.mkdir(parents=True, exist_ok=True)
        for src in (
            self.home / "lemonade" / "lemond.log",
            self.home / "host" / "daemon.log",
            self.home / "gaia.log",
        ):
            if src.is_file():
                shutil.copy2(src, out / src.name)
        (out / "timeline.json").write_text(
            json.dumps(self.events, indent=2), encoding="utf-8"
        )


@contextlib.contextmanager
def isolated(root: Optional[Path] = None, **kwargs: Any) -> Iterator[IsolatedGaia]:
    """An :class:`IsolatedGaia` that is always torn down."""
    made = root is None
    root = Path(root or tempfile.mkdtemp(prefix="gaia-restart-"))
    gaia = IsolatedGaia(root, **kwargs)
    os.environ["GAIA_HOME"] = str(gaia.home)
    try:
        gaia.provision()
        yield gaia
    finally:
        gaia.stop_everything()
        if made:
            shutil.rmtree(root, ignore_errors=True)


# -- scenarios ----------------------------------------------------------------------

#: A session that is the first to need the daemon, so it spawns it.
ENSURE_AND_WAIT = """
from gaia.daemon.client import start_or_attach, ensure_lemonade
inst = start_or_attach()
served = ensure_lemonade()
emit(ok=True, daemon_pid=inst.pid, lemond_pid=served.get("pid"), port=served["port"])
sys.stdin.readline()   # stay alive until the harness closes the job
"""

#: What a fresh session sends first: a cloud turn through chat_completions,
#: the path every agent turn takes.
FIRST_REQUEST = f"""
t0 = time.monotonic()
port_before = client.port
try:
    client.chat_completions(model={CLOUD_MODEL!r},
        messages=[{{"role": "user", "content": "Reply with OK."}}], max_tokens=4)
    emit(ok=True, elapsed=time.monotonic() - t0, port=client.port,
         port_before=port_before, seen=seen)
except Exception as e:
    emit(ok=False, elapsed=time.monotonic() - t0, error=f"{{type(e).__name__}}: {{e}}",
         port=client.port, port_before=port_before, seen=seen)
"""

#: A local turn on the CPU embedder, the first thing an agent's RAG and memory do.
EMBED_REQUEST = FIRST_REQUEST.replace(
    f"""client.chat_completions(model={CLOUD_MODEL!r},
        messages=[{{"role": "user", "content": "Reply with OK."}}], max_tokens=4)""",
    f"client.embeddings(['ping'], model={EMBED_MODEL!r})",
)

#: The TUI's ``/model <id>`` as the stdio agent runs it (``run_model_command``
#: -> ``switch_model``), on a live client like the agent's, without building
#: the whole agent. On main this is ``_lemonade_url`` + ``_lemonade_models``.
MODEL_SWITCH_SETUP = """
from types import SimpleNamespace
from gaia.llm.providers.lemonade import LemonadeProvider
from gaia_agent import stdio
provider = LemonadeProvider(model=None)
provider._backend.model_load_listener = client.model_load_listener
chat = SimpleNamespace(llm_client=provider, config=SimpleNamespace(
    base_url=provider._backend.base_url, use_claude=False, model_name=None))
agent = SimpleNamespace(chat=chat, config=None)
port_before = provider._backend.port
"""

MODEL_SWITCH = """
t0 = time.monotonic()
statuses = []
try:
    if hasattr(stdio, "_reachable_lemonade_models"):
        base_url, available = stdio._reachable_lemonade_models(
            agent, TARGET, statuses.append)
    else:
        base_url = stdio._lemonade_url(agent)
        available = stdio._lemonade_models(base_url)
    emit(ok=TARGET in available, elapsed=time.monotonic() - t0,
         error="" if TARGET in available else f"{TARGET} not listed",
         port_before=port_before, port=provider._backend.port, statuses=statuses)
except Exception as e:
    emit(ok=False, elapsed=time.monotonic() - t0, error=str(e),
         port_before=port_before, port=provider._backend.port, statuses=statuses)
"""


def _model_switch(target: str = CLOUD_MODEL) -> str:
    return f"TARGET = {target!r}\n" + MODEL_SWITCH


def _turn(name: str, res: Dict[str, Any], **extra: Any) -> TurnResult:
    statuses = res.get("statuses") or []
    reconnecting = any(RECONNECTING in m for m in statuses) or (
        "reconnecting" in (res.get("seen") or [])
    )
    if not res.get("ok"):
        extra = {**extra, "stderr_tail": res.get("stderr_tail", "")[-1500:]}
    return TurnResult(
        scenario=name,
        ok=bool(res.get("ok")),
        elapsed=round(float(res.get("elapsed") or 0.0), 2),
        reconnecting=reconnecting,
        detail=(res.get("error") or "")[:600],
        port_before=res.get("port_before"),
        port_after=res.get("port"),
        extra=extra,
    )


def scenario_first_request_during_restart(g: IsolatedGaia) -> TurnResult:
    """A session's first request while its server restarts (up, not answering)."""
    res = g.run_client(FIRST_REQUEST, inject=lambda: g.hang_restart(6.0))
    return _turn("first_request_during_restart", res)


def scenario_model_switch_during_restart(g: IsolatedGaia) -> TurnResult:
    """/model to a cloud model while the server restarts."""
    res = g.run_client(
        _model_switch(), setup=MODEL_SWITCH_SETUP, inject=lambda: g.hang_restart(6.0)
    )
    return _turn("model_switch_during_restart", res)


def scenario_killed_then_model_switch(g: IsolatedGaia) -> TurnResult:
    """/model right after the server died."""
    res = g.run_client(_model_switch(), setup=MODEL_SWITCH_SETUP, inject=g.kill_lemond)
    return _turn("killed_then_model_switch", res)


def scenario_idle_stop_then_request(g: IsolatedGaia) -> TurnResult:
    """The idle timeout stops the server; a session built while it ran then
    sends /model. The session holds no lease, as a client that never
    registered (or whose lease lapsed) does not."""

    stopped = []
    minutes = 1
    g.set_config(lemonade_idle_timeout_minutes=minutes)

    def _wait_for_idle_stop() -> None:
        deadline = time.monotonic() + minutes * 60 + 120
        while time.monotonic() < deadline and g.state() is not None:
            time.sleep(2)
        stopped.append(g.state() is None)
        g.note("idle stop observed" if stopped[0] else "idle stop NOT seen")

    res = g.run_client(
        _model_switch(),
        setup=MODEL_SWITCH_SETUP,
        inject=_wait_for_idle_stop,
        timeout=minutes * 60 + 400,
        env={"HARNESS_NO_LEASE": "1"},
    )
    g.set_config(lemonade_idle_timeout_minutes=g.idle_minutes)
    return _turn(
        "idle_stop_then_request", res, idle_stopped=bool(stopped and stopped[0])
    )


def scenario_stale_port_client(g: IsolatedGaia) -> TurnResult:
    """A client built while no server was recorded, used once one is."""
    g.kill_lemond()
    setup = """
from gaia.llm.lemonade_embedded import EmbeddedLemonade
EmbeddedLemonade().status()    # clears the dead record
client = LemonadeClient(verbose=False)
client.model_load_listener = lambda m, s: seen.append(s)
"""
    res = g.run_client(
        FIRST_REQUEST,
        setup=setup,
        inject=lambda: g.run_client(
            "from gaia.daemon.client import ensure_lemonade\n"
            "ensure_lemonade(); emit(ok=True)"
        ),
    )
    return _turn("stale_port_client", res)


def scenario_never_comes_back(g: IsolatedGaia) -> TurnResult:
    """A server that never answers again: fail within the bound, say why."""
    res = g.run_client(
        FIRST_REQUEST,
        inject=lambda: g.hang_restart(None),
        timeout=RECONNECT_BOUND + 150,
    )
    turn = _turn("never_comes_back", res)
    turn.extra["names_daemon_status"] = "gaia daemon status" in (res.get("error") or "")
    turn.extra["within_bound"] = turn.elapsed <= RECONNECT_BOUND + 85
    g.kill_lemond()
    return turn


class _JobSession:
    """A client in *job* that is the first to need the daemon, so it spawns it."""

    def __init__(self, g: IsolatedGaia, job: Job):
        argv = g.client_script(ENSURE_AND_WAIT)
        self.proc = job.spawn(
            argv,
            env=g.env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.proc.stdin.write("go\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + 180
        line = ""
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if line.startswith("@@") and not line.startswith("@@READY"):
                break
            if not line and self.proc.poll() is not None:
                raise RuntimeError(f"session died: {self.proc.stderr.read()[-3000:]}")
        self.info = json.loads(line[2:])


def scenario_session_job_closed(g: IsolatedGaia) -> TurnResult:
    """The incident: session 1's agent spawned the daemon inside the TUI's
    kill-on-close job; session 2 is using the server when session 1 closes,
    then sends /model to a cloud model."""
    if sys.platform != "win32":
        return TurnResult("session_job_closed", True, 0.0, detail="Windows only")
    g.stop_daemon()
    first = Job()
    s1 = _JobSession(g, first)
    g.note("session 1 spawned the daemon", **s1.info)

    def _close_session_1() -> None:
        first.close()
        time.sleep(2.0)
        g.note(
            "session 1 closed",
            daemon_alive=g.alive(s1.info["daemon_pid"]),
            lemond_alive=g.alive(s1.info["lemond_pid"]),
        )

    res = g.run_client(
        _model_switch(),
        setup=MODEL_SWITCH_SETUP + "\nprovider._backend.list_models()\n",
        inject=_close_session_1,
        job=Job(),
    )
    return _turn(
        "session_job_closed",
        res,
        daemon_survived=g.alive(s1.info["daemon_pid"]),
        lemond_survived=g.alive(s1.info["lemond_pid"]),
    )


def _not_gaias(name: str, g: IsolatedGaia, base_url: str, inject) -> TurnResult:
    """A request to a server that is not GAIA's, broken by *inject*: it fails
    with that server's own error, is never waited on, and nothing restarts it."""
    gaia_before = (g.state() or {}).get("pid")
    ensures_before = _ensure_count(g)
    res = g.run_client(
        FIRST_REQUEST, inject=inject, env={"LEMONADE_BASE_URL": base_url}
    )
    turn = _turn(name, res)
    turn.extra.update(
        follows_gaia=False,
        gaia_server_untouched=(g.state() or {}).get("pid") == gaia_before,
        daemon_asked=_ensure_count(g) - ensures_before,
    )
    return turn


def _ensure_count(g: IsolatedGaia) -> int:
    """How many times the isolated daemon was asked to ensure a server."""
    log = g.home / "gaia.log"
    if not log.is_file():
        return 0
    text = log.read_text(encoding="utf-8", errors="replace")
    return text.count("Asking the GAIA daemon to start GAIA's Lemonade Server")


def scenario_users_own_server_is_never_waited_on(g: IsolatedGaia) -> TurnResult:
    """The user's own lemond dies mid-session: GAIA neither waits nor restarts."""
    users = g.start_users_lemond()

    def _kill_it() -> None:
        users["proc"].kill()
        users["proc"].wait(10)
        g.note("killed the user's own lemond", pid=users["pid"])

    turn = _not_gaias(
        "users_own_server_is_never_waited_on",
        g,
        f"http://localhost:{users['port']}",
        _kill_it,
    )
    turn.extra["users_server_restarted"] = _listening(users["port"])
    return turn


def scenario_remote_server_is_never_waited_on(g: IsolatedGaia) -> TurnResult:
    """An unreachable remote LEMONADE_BASE_URL: same, with nothing to kill."""
    return _not_gaias(
        "remote_server_is_never_waited_on",
        g,
        "http://10.255.255.1:13305",
        lambda: None,
    )


def _listening(port: int) -> bool:
    import psutil

    return any(
        c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port
        for c in psutil.net_connections(kind="tcp")
    )


SCENARIOS = {
    "first_request_during_restart": scenario_first_request_during_restart,
    "model_switch_during_restart": scenario_model_switch_during_restart,
    "killed_then_model_switch": scenario_killed_then_model_switch,
    "stale_port_client": scenario_stale_port_client,
    "never_comes_back": scenario_never_comes_back,
    "session_job_closed": scenario_session_job_closed,
    "users_own_server_is_never_waited_on": scenario_users_own_server_is_never_waited_on,
    "remote_server_is_never_waited_on": scenario_remote_server_is_never_waited_on,
    "idle_stop_then_request": scenario_idle_stop_then_request,
}


def run_repro(args) -> List[TurnResult]:
    results = []
    with isolated(
        Path(args.root) if args.root else None,
        source_home=Path(args.source_home) if args.source_home else None,
        idle_minutes=args.idle_minutes,
    ) as g:
        for name in args.scenarios or list(SCENARIOS):
            g.start_daemon()
            if not g.wait_healthy():
                raise RuntimeError("isolated server never became healthy")
            g.note("scenario", name=name)
            turn = SCENARIOS[name](g)
            g.note("result", **{k: v for k, v in asdict(turn).items() if k != "extra"})
            results.append(turn)
            g.wait_healthy(10)
        if args.out:
            g.collect_logs(Path(args.out) / "logs")
    return results


# -- stress -----------------------------------------------------------------------------


def run_stress(args) -> Dict[str, Any]:
    """N concurrent sessions while the isolated server is killed and restarted
    at random intervals. Each session runs mixed local and cloud turns."""
    rng = random.Random(args.seed)
    body = f"""
import random
rng = random.Random({args.seed} + __import__('os').getpid())
turns = []
for i in range({args.turns}):
    kind = rng.choice(["local", "cloud", "cloud", "embed"])
    t0 = time.monotonic()
    before = seen.count("reconnecting")
    try:
        if kind == "local":
            client.chat_completions(model={LOCAL_MODEL!r},
                messages=[{{"role": "user", "content": "Say OK."}}], max_tokens=4)
        elif kind == "cloud":
            client.chat_completions(model={CLOUD_MODEL!r},
                messages=[{{"role": "user", "content": "Say OK."}}], max_tokens=4)
        else:
            client.embeddings(["ping %d" % i], model={EMBED_MODEL!r})
        turns.append(dict(kind=kind, ok=True, elapsed=time.monotonic() - t0,
                          reconnecting=seen.count("reconnecting") > before))
    except Exception as e:
        import traceback
        turns.append(dict(kind=kind, ok=False, elapsed=time.monotonic() - t0,
                          error=f"{{type(e).__name__}}: {{e}}"[:300],
                          where=traceback.format_exc()[-1800:],
                          reconnecting=seen.count("reconnecting") > before))
    time.sleep(rng.uniform(0.5, 3.0))
emit(ok=all(t["ok"] for t in turns), turns=turns)
"""
    with isolated(
        Path(args.root) if args.root else None,
        source_home=Path(args.source_home) if args.source_home else None,
    ) as g:
        g.start_daemon()
        g.wait_healthy()
        stop = threading.Event()
        faults: List[Dict[str, Any]] = []

        def _chaos() -> None:
            # The first fault lands while every session is under way.
            gap = rng.uniform(5.0, 15.0)
            while not stop.wait(gap):
                gap = rng.uniform(args.min_gap, args.max_gap)
                kind = rng.choice(args.faults.split(","))
                t = time.monotonic()
                if kind == "kill":
                    g.kill_lemond()
                else:
                    g.hang_restart(unresponsive_for=rng.uniform(1.0, 12.0))
                faults.append({"kind": kind, "t": t})

        chaos = threading.Thread(target=_chaos, daemon=True)
        chaos.start()
        results: List[Dict[str, Any]] = [None] * args.sessions  # type: ignore

        def _session(i: int) -> None:
            results[i] = g.run_client(body, timeout=args.turns * 150)

        threads = [
            threading.Thread(target=_session, args=(i,)) for i in range(args.sessions)
        ]
        for t in threads:
            t.start()
            time.sleep(rng.uniform(0.0, 1.0))
        for t in threads:
            t.join()
        stop.set()
        chaos.join(timeout=5)
        turns = [t for r in results for t in (r.get("turns") or [])]
        failed = [t for t in turns if not t["ok"]]
        summary = {
            "sessions": args.sessions,
            "turns": len(turns),
            "failed_turns": len(failed),
            "not_reachable": sum(NOT_REACHABLE in t.get("error", "") for t in failed),
            "reconnecting_turns": sum(t["reconnecting"] for t in turns),
            "faults": len(faults),
            "max_elapsed": max((t["elapsed"] for t in turns), default=0.0),
            "over_bound": sum(t["elapsed"] > RECONNECT_BOUND + 20 for t in turns),
            "recovery_elapsed": sorted(
                round(t["elapsed"], 2) for t in turns if t["reconnecting"]
            ),
            "by_kind": {
                kind: {
                    "turns": sum(t["kind"] == kind for t in turns),
                    "failed": sum(t["kind"] == kind and not t["ok"] for t in turns),
                }
                for kind in ("local", "cloud", "embed")
            },
            "failure_causes": dict(
                collections.Counter(
                    f"{t['kind']}: {t.get('error', '')[:90]}" for t in failed
                ).most_common()
            ),
            "failures": failed,
            "sessions_without_output": sum(not r.get("turns") for r in results),
        }
        if args.out:
            g.collect_logs(Path(args.out) / "logs")
        return summary


# -- the real TUI ------------------------------------------------------------------


class Tui:
    """One ``gaia-tui`` session under the isolated home, driven over its
    loopback control API. Its console is hidden: Bubble Tea needs one, nobody
    needs to see it."""

    def __init__(self, g: IsolatedGaia, exe: Path, n: int, shim_dir: Path):
        self.g = g
        self.dir = g.root / f"tui-{n}"
        self.dir.mkdir(parents=True, exist_ok=True)
        env = g.env(
            GAIA_TUI_HOME=str(self.dir),
            GAIA_MEMORY_DB=str(self.dir / "memory.db"),
            GAIA_AGENT_LOG=str(self.dir / "agent.log"),
            PATH=str(shim_dir) + os.pathsep + os.environ.get("PATH", ""),
        )
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0  # SW_HIDE
        self.proc = subprocess.Popen(
            [str(exe), "--control-port", "0"],
            env=env,
            cwd=str(self.dir),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            startupinfo=startup,
        )
        self._conn = None
        self._hdrs: Dict[str, str] = {}

    def _connect(self, timeout: float = 60.0) -> None:
        import http.client

        control = self.dir / "control.json"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"gaia-tui exited with {self.proc.returncode}")
            try:
                info = json.loads(control.read_text(encoding="utf-8"))
                break
            except (FileNotFoundError, ValueError):
                time.sleep(0.2)
        else:
            raise RuntimeError(f"no {control} after {timeout:.0f}s")
        self._conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=300)
        self._hdrs = {
            "Authorization": "Bearer " + info["token"],
            "Content-Type": "application/json",
        }

    def call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        if self._conn is None:
            self._connect()
        self._conn.request(
            method,
            "/control/v1/" + path,
            body=json.dumps(body) if body is not None else None,
            headers=self._hdrs,
        )
        resp = self._conn.getresponse()
        data = resp.read()
        try:
            return json.loads(data)
        except ValueError:
            return data.decode("utf-8", "replace")

    def screen(self) -> str:
        return self.call("GET", "screen?format=plain").get("screen", "")

    def wait_state(self, state: dict, timeout_s: float) -> bool:
        res = self.call(
            "POST", "wait", {"state": state, "timeout_ms": int(timeout_s * 1000)}
        )
        return bool(isinstance(res, dict) and res.get("ok", res.get("matched")))

    def close(self) -> None:
        if self.proc.poll() is None:
            with contextlib.suppress(Exception):
                self.call("POST", "keys", {"keys": ["ctrl+c"]})
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)


def _tui_session(
    g: IsolatedGaia,
    exe: Path,
    n: int,
    shim_dir: Path,
    target: str,
    previous: Optional["Tui"] = None,
    close_previous_after: float = 0.0,
):
    """One session's ``/model``. With *previous*, that session is still open
    and closes *close_previous_after* seconds into this one's switch -- the
    incident's shape -- and this session is returned open, for the next."""
    import psutil

    daemon_before = g.daemon_pid()
    lemond_before = (g.state() or {}).get("pid")
    t = Tui(g, exe, n, shim_dir)
    result: Dict[str, Any] = {"session": n, "ok": False}
    try:
        t.call("POST", "resize", {"cols": 140, "rows": 45})
        started = time.monotonic()
        t.wait_state({"view": "chat"}, 300)
        result["ready_s"] = round(time.monotonic() - started, 1)
        if "chat" not in json.dumps(t.call("GET", "status")):
            result["error"] = "never reached the chat view:\n" + t.screen()[-1500:]
            return result
        t.call("POST", "text", {"text": f"/model {target}"})
        t.call("POST", "keys", {"keys": ["enter"]})
        closer = None
        if previous is not None:
            closer = threading.Timer(close_previous_after, previous.close)
            closer.start()
            result["closed_previous_after_s"] = round(close_previous_after, 1)
        t0 = time.monotonic()
        seen_reconnecting = False
        text = ""
        while time.monotonic() - t0 < RECONNECT_BOUND + 120:
            text = t.screen()
            seen_reconnecting |= RECONNECTING in text
            if "Switched to" in text or NOT_REACHABLE in text or "refused" in text:
                break
            time.sleep(0.5)
        result.update(
            elapsed=round(time.monotonic() - t0, 2),
            ok="Switched to" in text and NOT_REACHABLE not in text,
            not_reachable=NOT_REACHABLE in text,
            reconnecting=seen_reconnecting,
        )
        if not result["ok"]:
            result["screen"] = text[-2000:]
        if closer is not None:
            closer.join()  # what follows is measured after the previous closed
    except Exception as e:  # noqa: BLE001 - recorded per session, never hidden
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        if previous is None:
            t.close()
        time.sleep(1.0)
        result["daemon_pid_before"] = daemon_before
        result["daemon_pid_after_close"] = g.daemon_pid()
        result["daemon_alive_after_close"] = g.alive(g.daemon_pid())
        result["lemond_pid_before"] = lemond_before
        st = g.state() or {}
        result["lemond_alive_after_close"] = bool(st) and psutil.pid_exists(st["pid"])
    result["tui"] = t
    return result


def run_tui(args) -> Dict[str, Any]:
    """N back-to-back TUI sessions, each switching to a cloud model."""
    exe = Path(args.tui_exe).resolve()
    with isolated(
        Path(args.root) if args.root else None,
        source_home=Path(args.source_home) if args.source_home else None,
    ) as g:
        g.start_daemon()  # registers the stub cloud provider
        flash = CLOUD_MODEL
        other = flash.replace(StubCloud.MODEL, StubCloud.OTHER)
        g.set_config(default_model=flash, last_model=flash, last_provider="amd")
        # Session 1's agent is the first GAIA process to need the daemon, as
        # in the incident, so it is the one that starts it.
        g.stop_daemon()
        shim_dir = g.root / "bin"
        shim_dir.mkdir(exist_ok=True)
        (shim_dir / "gaia-agent.cmd").write_text(
            f'@"{g.python}" -m gaia_agent.stdio %*\r\n', encoding="utf-8"
        )
        rng = random.Random(args.seed)
        sessions = []
        previous = None
        for n in range(args.sessions):
            res = _tui_session(
                g,
                exe,
                n,
                shim_dir,
                other if n % 2 == 0 else flash,
                previous=previous if args.overlap else None,
                close_previous_after=rng.uniform(0.0, 12.0),
            )
            tui = res.pop("tui")
            previous = tui if args.overlap else None
            g.note("tui session", **{k: v for k, v in res.items() if k != "screen"})
            sessions.append(res)
            if not args.overlap:
                time.sleep(args.gap)
        if previous is not None:
            previous.close()
        if args.out:
            g.collect_logs(Path(args.out) / "logs")
            for n in range(args.sessions):
                log = g.root / f"tui-{n}" / "agent.log"
                if log.is_file():
                    shutil.copy2(log, Path(args.out) / "logs" / f"agent-{n}.log")
    return {
        "sessions": len(sessions),
        "ok": sum(s["ok"] for s in sessions),
        "not_reachable": sum(bool(s.get("not_reachable")) for s in sessions),
        "reconnecting": sum(bool(s.get("reconnecting")) for s in sessions),
        "daemon_killed_by_a_close": sum(
            not s["daemon_alive_after_close"] for s in sessions
        ),
        "max_switch_s": max((s.get("elapsed", 0) for s in sessions), default=0),
        "detail": sessions,
    }


# -- an Agent UI backend under its own GAIA_HOME (#5159) ---------------------------


def _daemon_clients(g: IsolatedGaia) -> List[Dict[str, Any]]:
    """The leases the isolated daemon holds."""
    inst = g.instance()
    if not inst:
        return []
    req = urllib.request.Request(
        f"http://127.0.0.1:{inst['port']}/daemon/v1/lemonade/clients",
        headers={"Authorization": f"Bearer {inst['token']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8")).get("clients", [])
    except (urllib.error.URLError, OSError, ValueError):
        return []


def run_ui_lease(args) -> Dict[str, Any]:
    """An Agent UI backend under its own GAIA_HOME, pointed at the isolated
    home's server: it leases that server, so a short idle timeout never stops
    it while the UI runs; a restart is followed across homes; and once the UI
    is gone the server does stop."""
    import psutil

    from gaia.llm.lemonade_embedded import _free_port

    result: Dict[str, Any] = {}
    with isolated(
        Path(args.root) if args.root else None,
        source_home=Path(args.source_home) if args.source_home else None,
    ) as g:
        served = g.start_daemon()
        g.set_config(lemonade_idle_timeout_minutes=1)
        ui_home = g.root / "ui-home"
        ui_home.mkdir()
        port = _free_port()
        ui_env = g.env(
            GAIA_HOME=str(ui_home),
            LEMONADE_BASE_URL=served["base_url"],
            GAIA_MEMORY_DB=str(ui_home / "memory.db"),
        )
        ui = subprocess.Popen(
            [
                g.python,
                "-m",
                "gaia.ui.server",
                "--port",
                str(port),
                "--host",
                "127.0.0.1",
            ],
            env=ui_env,
            stdout=open(g.root / "ui.log", "ab"),  # noqa: SIM115 - lives with ui
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/health", timeout=3
                    ):
                        break
                except (urllib.error.URLError, OSError):
                    time.sleep(1)
            g.note("agent ui up", pid=ui.pid, port=port)
            deadline = time.monotonic() + 90
            ui_pids = {ui.pid} | {c.pid for c in psutil.Process(ui.pid).children(True)}
            leased = []
            while time.monotonic() < deadline and not leased:
                leased = [
                    c for c in _daemon_clients(g) if set(c.get("pids", [])) & ui_pids
                ]
                time.sleep(2)
            result["ui_leased_the_owning_daemon"] = bool(leased)
            result["lease"] = leased
            g.note("ui lease", leased=leased)

            pid_before = (g.state() or {}).get("pid")
            g.note("holding past the 1 min idle timeout with the UI open")
            time.sleep(150)
            st = g.state() or {}
            result["server_kept_while_ui_open"] = (
                st.get("pid") == pid_before and g.health() is not None
            )
            g.note("after hold", kept=result["server_kept_while_ui_open"])

            # A client under the UI's home rides a restart of the other home's
            # server: it follows that daemon, not its own.
            res = g.run_client(
                FIRST_REQUEST,
                inject=lambda: g.hang_restart(5.0),
                env={
                    "GAIA_HOME": str(ui_home),
                    "LEMONADE_BASE_URL": served["base_url"],
                },
            )
            result["cross_home_turn_during_restart"] = asdict(
                _turn("cross_home_turn_during_restart", res)
            )
            g.note("cross-home turn", ok=res.get("ok"), error=res.get("error"))
        finally:
            ui.terminate()
            try:
                ui.wait(30)
            except subprocess.TimeoutExpired:
                ui.kill()
        g.note("agent ui closed")
        deadline = time.monotonic() + 200
        while time.monotonic() < deadline and g.state() is not None:
            time.sleep(2)
        result["server_stopped_after_ui_closed"] = g.state() is None
        g.note("after ui closed", stopped=result["server_stopped_after_ui_closed"])
        if args.out:
            g.collect_logs(Path(args.out) / "logs")
            shutil.copy2(g.root / "ui.log", Path(args.out) / "logs" / "ui.log")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", help="build the isolated home here (kept)")
    parser.add_argument("--out", help="write results.json and logs here")
    parser.add_argument("--source-home", help="GAIA home to copy lemond from")
    parser.add_argument(
        "--old-tui-job",
        action="store_true",
        help="make session jobs as the TUI did before #5264 (no breakaway)",
    )
    parser.add_argument(
        "--code",
        help="tree whose src/ and hub/agents/*/python the daemon and clients "
        "run (default: this checkout), e.g. an export of main",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    repro = sub.add_parser("repro")
    repro.add_argument("scenarios", nargs="*", choices=[*SCENARIOS, []])
    repro.add_argument("--idle-minutes", type=int, default=15)
    stress = sub.add_parser("stress")
    stress.add_argument("--sessions", type=int, default=16)
    stress.add_argument("--turns", type=int, default=6)
    stress.add_argument("--seed", type=int, default=5264)
    stress.add_argument("--min-gap", type=float, default=8.0)
    stress.add_argument("--max-gap", type=float, default=25.0)
    stress.add_argument(
        "--faults",
        default="kill",
        help="comma list of kill,hang. hang is for single clients: under many, "
        "a process created suspended can fail the daemon's identity check "
        "before Windows maps its environment, so the daemon starts a second "
        "lemond on the same port -- a fault of this harness, not of GAIA",
    )
    sub.add_parser("ui-lease", help="an Agent UI under its own GAIA_HOME (#5159)")
    tui = sub.add_parser("tui", help="back-to-back real gaia-tui sessions")
    tui.add_argument("--tui-exe", required=True, help="a gaia-tui built from tui/")
    tui.add_argument("--sessions", type=int, default=16)
    tui.add_argument("--gap", type=float, default=3.0)
    tui.add_argument(
        "--overlap",
        action="store_true",
        help="close each session while the next one is switching models",
    )
    tui.add_argument("--seed", type=int, default=5264)
    args = parser.parse_args(argv)
    global CODE_ROOT, TUI_JOB_BREAKAWAY  # pylint: disable=global-statement
    if args.code:
        CODE_ROOT = Path(args.code).resolve()
    TUI_JOB_BREAKAWAY = not args.old_tui_job

    out = Path(args.out or tempfile.mkdtemp(prefix="gaia-restart-out-"))
    args.out = str(out)
    if args.cmd == "repro":
        data: Any = [asdict(t) for t in run_repro(args)]
    elif args.cmd == "tui":
        data = run_tui(args)
    elif args.cmd == "ui-lease":
        data = run_ui_lease(args)
    else:
        data = run_stress(args)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(json.dumps(data, indent=2))
    print(f"\nResults and logs: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
