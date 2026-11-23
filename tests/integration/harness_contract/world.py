# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One GAIA install in a temp home, with the real daemon and the real agents.

Nothing here tells GAIA where its model server is. The fake Lemonade is on a
port the OS picked and holds a key, and the only record of either is the one
GAIA's own embedded server writes: ``~/.gaia/lemonade/state.json``. Every
process the world starts inherits an environment with no ``LEMONADE_*`` and no
``GAIA_*`` address or key in it, so an agent that answers found the server the
way an installed one has to.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import psutil
import requests

REPO = Path(__file__).resolve().parents[3]
FAKE_SERVER = REPO / "installer" / "headless" / "fake_model_server.py"
#: Lemonade's standard port: where a harness that never found GAIA's server looks.
STANDARD_PORT = 13305

#: The model GAIA is set to use, and one it remembers from the last chat. Both
#: are in the fake's catalog and neither is the built-in fallback, so a request
#: naming anything else did not read the saved choice.
SAVED_MODEL = "Qwen3.5-4B-GGUF"
LAST_MODEL = "Qwen3-VL-4B-Instruct-GGUF"
#: Listed by the fake, never downloaded.
ABSENT_MODEL = "Qwen3.5-2B-GGUF"
EMBEDDER = "user.embeddinggemma-300m-GGUF"

_SCRUBBED = re.compile(
    r"^(GAIA_|LEMONADE_|PYTHONPATH$|VIRTUAL_ENV_DISABLE)|^(all|http|https|no)_proxy$",
    re.IGNORECASE,
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def gaiacpp_binary() -> Optional[Path]:
    """The gaiacpp this run tests: ``GAIA_TEST_GAIACPP``, else this checkout's build."""
    named = os.environ.get("GAIA_TEST_GAIACPP", "").strip()
    if named:
        path = Path(named)
        if not path.is_file():
            raise FileNotFoundError(
                f"GAIA_TEST_GAIACPP names {path}, which is not a file. Build gaiacpp "
                "(cmake --build cpp/build --target gaiacpp) and point it there."
            )
        return path.resolve()
    name = "gaiacpp.exe" if sys.platform == "win32" else "gaiacpp"
    for found in sorted((REPO / "cpp" / "build").glob(f"**/{name}")):
        if found.is_file():
            return found.resolve()
    return None


class Canary:
    """Listens on Lemonade's standard port and counts who knocks."""

    def __init__(self) -> None:
        self.sockets: List[socket.socket] = []
        self.listening = False
        self._count = 0
        self._lock = threading.Lock()
        for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
                if sys.platform != "win32":
                    # Past connections in TIME_WAIT must not read as "in use".
                    # (On Windows this option would take the port from a live server.)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, STANDARD_PORT))
                sock.listen(16)
            except OSError:
                # No IPv6 loopback is fine; the IPv4 one is the port's owner.
                continue
            self.listening = self.listening or family == socket.AF_INET
            self.sockets.append(sock)
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock: socket.socket) -> None:
        # Hang up at once, so a caller that looked here fails fast.
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            conn.close()
            with self._lock:
                self._count += 1

    def connections(self) -> int:
        """Connections made since the last call."""
        with self._lock:
            count, self._count = self._count, 0
        return count

    def close(self) -> None:
        for sock in self.sockets:
            sock.close()


class World:
    """A temp home, a fake Lemonade recorded as GAIA's own, and what runs in it."""

    def __init__(self, root: Path, src: Path = REPO, source_agent: bool = True) -> None:
        self.root = root
        #: Run the Python agent from this checkout's source. Off for a world that
        #: only ever runs gaiacpp, which then starts as it does on a user's PC.
        self.source_agent = source_agent
        #: The checkout whose daemon, backend and Python agent run; this one unless
        #: a run is proving the suite fails on another.
        self.src = src
        self.home = root / "home"
        self.gaia_home = self.home / ".gaia"
        self.logs = root / "logs"
        self.work = root / "work"
        self.outside = root / "outside"
        self.tmp = root / "tmp"
        for folder in (self.gaia_home, self.logs, self.work, self.outside, self.tmp):
            folder.mkdir(parents=True, exist_ok=True)
        self.request_log = self.logs / "fake-requests.jsonl"
        self.script = self.logs / "fake-script.json"
        self.api_key = secrets.token_hex(16)
        self.fake: Optional[subprocess.Popen] = None
        self.fake_port = 0
        self.backend: Optional[subprocess.Popen] = None
        self.backend_port = 0
        self._children: List[subprocess.Popen] = []
        #: Set by the suite: the agent this world's settings choose, and the
        #: listener on Lemonade's standard port.
        self.harness = ""
        self.canary: Optional[Canary] = None
        #: A program to run as gaiacpp in place of the one under test.
        self.gaiacpp: Optional[Path] = None

    # ── environment ──
    def env(self, **extra: str) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not _SCRUBBED.match(k)}
        env.update(
            HOME=str(self.home),
            USERPROFILE=str(self.home),
            APPDATA=str(self.home / "AppData" / "Roaming"),
            LOCALAPPDATA=str(self.home / "AppData" / "Local"),
            XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_CACHE_HOME=str(self.home / ".cache"),
            HF_HOME=str(self.home / ".cache" / "huggingface"),
            PYTHONUTF8="1",
            PYTHONIOENCODING="utf-8",
            PYTHONPATH=str(self.src / "src"),
            GAIA_MEMORY_DISABLED="1",
            # A temp folder of this world's own: the agents keep every shared
            # temp folder out of reach, and `outside` must not be inside one.
            TMPDIR=str(self.tmp),
            TEMP=str(self.tmp),
            TMP=str(self.tmp),
        )
        if self.source_agent:
            # The Python agent from this checkout, not a downloaded build.
            env["GAIA_GAIA_AGENT_MODE"] = "dev"
        binary = self.gaiacpp or gaiacpp_binary()
        if binary is not None:
            env["GAIA_GAIACPP_PATH"] = str(binary)
        env.update(extra)
        return env

    # ── GAIA's settings ──
    def configure(self, **settings: Any) -> None:
        """Merge *settings* into ``~/.gaia/config.json``; None removes a key."""
        path = self.gaia_home / "config.json"
        current = json.loads(path.read_text("utf-8")) if path.is_file() else {}
        for key, value in settings.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")

    def config(self) -> Dict[str, Any]:
        path = self.gaia_home / "config.json"
        return json.loads(path.read_text("utf-8")) if path.is_file() else {}

    def mark_agent_installed(self) -> None:
        """Record the GAIA agent as installed, as setup does on a real PC.

        The Agent UI only offers an agent setup installed. The agent that then
        runs is still the real one: this checkout's Python source (dev mode) or
        the built gaiacpp.
        """
        folder = self.gaia_home / "agents" / "gaia"
        folder.mkdir(parents=True, exist_ok=True)
        record = {
            "id": "gaia",
            "version": "0.0.0",
            "language": "python",
            "installed_at": "2026-01-01T00:00:00+00:00",
            "artifact_sha256": "",
            "artifact_kind": "binary",
            "executable": "gaia-agent.exe" if sys.platform == "win32" else "gaia-agent",
        }
        (folder / ".installed").write_text(json.dumps(record), encoding="utf-8")

    def mark_model_server_installed(self) -> None:
        """Lay out GAIA's Lemonade as unpacked, so setup adopts the running one.

        First-run setup downloads Lemonade when ``dist/<version>/lemond`` is
        missing. With it there and the recorded server answering, setup takes
        that server as GAIA's own and never runs this file: the suite's fake
        keeps serving, and no real Lemonade is fetched or started.
        """
        from gaia.version import LEMONADE_VERSION

        folder = self.gaia_home / "lemonade" / "dist" / LEMONADE_VERSION
        folder.mkdir(parents=True, exist_ok=True)
        lemond = folder / ("lemond.exe" if sys.platform == "win32" else "lemond")
        lemond.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        lemond.chmod(0o755)

    # ── the model server ──
    @property
    def state_file(self) -> Path:
        return self.gaia_home / "lemonade" / "state.json"

    def start_model_server(
        self,
        port: Optional[int] = None,
        models: Optional[List[str]] = None,
        **fake_env: str,
    ) -> int:
        """Start the fake and record it the way GAIA's embedded Lemonade does.

        *fake_env* is the fake's own ``FAKE_LEMONADE_*`` settings.
        """
        self.fake_port = port or free_port()
        argv = [
            sys.executable,
            str(FAKE_SERVER),
            "--catalog",
            "--port",
            str(self.fake_port),
            "--api-key",
            self.api_key,
            "--log",
            str(self.request_log),
            "--script",
            str(self.script),
        ]
        for model in models if models is not None else [SAVED_MODEL, LAST_MODEL]:
            argv += ["--model", model]
        log = (self.logs / "fake.log").open("ab")
        self.fake = subprocess.Popen(
            argv, env=self.env(**fake_env), cwd=self.root, stdout=log, stderr=log
        )
        self._wait(
            lambda: self._fake_answers(),
            "the fake model server",
            self.logs / "fake.log",
            self.fake,
        )
        self.record_model_server()
        return self.fake_port

    def _fake_answers(self) -> bool:
        try:
            reply = requests.get(
                f"http://127.0.0.1:{self.fake_port}/api/v1/health",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=2,
            )
        except requests.RequestException:
            return False
        return reply.ok

    def record_model_server(self) -> None:
        """Write state.json as ``EmbeddedLemonade._write_state`` does after a start.

        ``pid`` is the serving process, so the liveness checks both harnesses
        make (a live pid; health answering with the recorded key) are true
        because the server is really there, and false once it is stopped.
        """
        from gaia.version import LEMONADE_VERSION

        assert self.fake is not None
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "pid": self.fake.pid,
            "port": self.fake_port,
            "api_key": self.api_key,
            "version": LEMONADE_VERSION,
        }
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        (self.state_file.parent / "last_port.json").write_text(
            json.dumps({"port": self.fake_port}), encoding="utf-8"
        )

    def stop_model_server(self) -> None:
        if self.fake is not None:
            _kill(self.fake)
            self.fake = None

    def requests(self) -> List[Dict[str, Any]]:
        if not self.request_log.is_file():
            return []
        lines = self.request_log.read_text("utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def chats(self, since: int = 0) -> List[Dict[str, Any]]:
        return [
            r
            for r in self.requests()[since:]
            if r["method"] == "POST" and r["path"].endswith("/chat/completions")
        ]

    def pulls(self, since: int = 0) -> List[Dict[str, Any]]:
        return [r for r in self.requests()[since:] if r["path"].endswith("/pull")]

    def play(self, rules: List[Dict[str, Any]]) -> None:
        """Script the model's replies; see fake_model_server.py --script."""
        self.script.write_text(json.dumps(rules), encoding="utf-8")

    # ── the Agent UI backend ──
    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.backend_port}"

    def start_backend(self) -> None:
        self.backend_port = free_port()
        argv = [
            sys.executable,
            "-m",
            "gaia.ui.server",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.backend_port),
        ]
        log_path = self.logs / "backend.log"
        log = log_path.open("ab")
        # From the checkout: the source-run Python agent is found from where its
        # caller runs (git's top level), as a developer's `gaia chat --ui` is.
        self.backend = subprocess.Popen(
            argv, env=self.env(), cwd=self.src, stdout=log, stderr=log
        )
        self._wait(
            lambda: _ok(f"{self.url}/api/health"),
            "the Agent UI backend",
            log_path,
            self.backend,
        )

    def api(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        token = (self.gaia_home / "agent-ui-token").read_text("utf-8").strip()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Gaia-UI": "1",
            # What the desktop app sends: it holds the request to the GAIA agent.
            "X-Gaia-Client": "agent-ui",
        }
        headers.update(kwargs.pop("headers", {}))
        kwargs.setdefault("timeout", 60)
        return requests.request(method, f"{self.url}{path}", headers=headers, **kwargs)

    # ── running things ──
    def run(
        self,
        argv: List[str],
        timeout: float = 120,
        cwd: Optional[Path] = None,
        **env: str,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            env=self.env(**env),
            cwd=cwd or self.work,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )

    def spawn(
        self, argv: List[str], cwd: Optional[Path] = None, **env: str
    ) -> subprocess.Popen:
        child = subprocess.Popen(
            argv,
            env=self.env(**env),
            cwd=cwd or self.work,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=(self.logs / "child.log").open("ab"),
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._children.append(child)
        return child

    def _wait(self, ready, what: str, log: Path, proc: subprocess.Popen) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"{what} exited {proc.returncode}:\n{tail(log)}")
            if ready():
                return
            time.sleep(0.1)
        raise RuntimeError(f"{what} was not ready within 90 s:\n{tail(log)}")

    # ── teardown ──
    def agent_program(self) -> Optional[str]:
        """Which program the daemon is running as the GAIA agent, by its command line."""
        ledger = _read_json(self.gaia_home / "host" / "sidecars.json")
        for entry in ledger if isinstance(ledger, list) else []:
            if entry.get("agent_id") != "gaia" or not psutil.pid_exists(entry["pid"]):
                continue
            program = Path((entry.get("argv") or [""])[0]).name.lower()
            return "gaiacpp" if program.startswith("gaiacpp") else "gaiapy"
        return None

    def daemon_pids(self) -> List[int]:
        host = self.gaia_home / "host"
        pids: List[int] = []
        instance = _read_json(host / "instance.json")
        if isinstance(instance, dict) and isinstance(instance.get("pid"), int):
            pids.append(instance["pid"])
        ledger = _read_json(host / "sidecars.json")
        for entry in ledger if isinstance(ledger, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("pid"), int):
                pids.append(entry["pid"])
        return [pid for pid in pids if psutil.pid_exists(pid)]

    def describe(self) -> str:
        """The logs a failed test needs, newest lines last."""
        logs = [
            self.logs / "backend.log",
            self.gaia_home / "host" / "daemon.log",
            self.logs / "child.log",
            self.logs / "fake.log",
        ]
        logs += sorted((self.gaia_home / "agents" / "gaia" / "logs").glob("*.log"))[-2:]
        return "\n".join(f"--- {p}\n{tail(p, 15)}" for p in logs if p.is_file())

    def stop(self) -> None:
        """Stop everything this world started, the daemon and its agents included."""
        for child in self._children:
            _kill(child)
        if self.backend is not None:
            _kill(self.backend)
        pids = self.daemon_pids()
        if pids:
            # As a user would; the kill below is for one that does not listen.
            subprocess.run(
                [sys.executable, "-m", "gaia.cli", "daemon", "stop"],
                env=self.env(),
                cwd=self.root,
                capture_output=True,
                timeout=60,
                check=False,
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and any(map(psutil.pid_exists, pids)):
                time.sleep(0.1)
            for pid in pids:
                _kill_pid(pid)
        self.stop_model_server()


def _ok(url: str) -> bool:
    try:
        return requests.get(url, timeout=2).ok
    except requests.RequestException:
        return False


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def tail(path: Path, lines: int = 40) -> str:
    if not path.is_file():
        return f"({path} was never written)"
    return "\n".join(path.read_text("utf-8", errors="replace").splitlines()[-lines:])


def _kill_pid(pid: int) -> None:
    try:
        proc = psutil.Process(pid)
        for child in proc.children(recursive=True):
            child.kill()
        proc.kill()
    except psutil.NoSuchProcess:
        pass


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        _kill_pid(proc.pid)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"pid {proc.pid} survived being killed") from e
    for stream in (proc.stdin, proc.stdout):
        if stream is not None:
            stream.close()


def sse_events(response: requests.Response) -> Iterator[Dict[str, Any]]:
    """The JSON payload of each ``data:`` line of a streamed reply."""
    for line in response.iter_lines(decode_unicode=True):
        if line and line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                yield json.loads(payload)
