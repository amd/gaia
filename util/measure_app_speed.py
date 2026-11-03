# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Time the Agent UI's setup and first chat from a clean state, on real hardware.

The hardware half of the binaries-only speed baseline (docs/plans/binaries-only.mdx,
"Speed baseline"). Each run starts from an empty throwaway profile: its own home,
AppData, uv cache, managed Python, Hugging Face cache and GAIA's own Lemonade, so
nothing this PC already has installed or cached reaches the numbers. Per run:

  M2  the backend install, by the release's own backend-installer.cjs, per stage
  M3  ``gaia init --profile gaia``, per step and per model
  M4  backend cold start: ``gaia chat --ui`` until ``/api/health`` answers
  M5  agent cold start: the daemon starts the ``gaia`` sidecar until it answers
  M6  first chat: first streamed text and the finished answer, then a second
      message on the warm model

M4-M6 start with every GAIA process and GAIA's Lemonade stopped, as after a reboot.

Windows only for now. It refuses to start while anything else is running a model
or a test on this PC, because that would slow every number it takes.

    python util/measure_app_speed.py --release v0.25.4-rc3 --runs 3 \\
        --out installer/headless/baselines/binaries-only-hardware-v0.25.4-rc3.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = "src/gaia/apps/webui/services/backend-installer.cjs"
UI_HEADERS = {"X-Gaia-UI": "1", "Content-Type": "application/json"}
CHAT_PROMPT = "Reply with exactly one word: ready"
WARM_PROMPT = "Reply with exactly one word: again"

# Prefixes of variables that point a process at this PC's own GAIA, Lemonade,
# Python or caches. None of them exist on a clean PC.
_DROPPED_ENV_PREFIXES = (
    "GAIA_",
    "LEMONADE_",
    "HF_",
    "HUGGINGFACE",
    "UV_",
    "VIRTUAL_ENV",
    "PYTHON",
    "CONDA",
    "PIP_",
    "NODE_OPTIONS",
)

# Processes that, running outside the throwaway profile, would compete for the
# GPU, CPU or disk while a run is timed. An idle Lemonade Server is not one: a
# model it has loaded shows up as its own llama-server, flm or sd-server child.
_BUSY_NAMES = ("llama-server", "flm", "sd-server", "whisper-server", "koko")
_BUSY_MODULES = ("pytest", "gaia.ui.server", "gaia.eval")
_BUSY_SCRIPTS = ("pytest", "pytest.exe", "serve_fixtures.py")
# Above this average CPU use something unlisted is running; wait it out.
MAX_IDLE_CPU_PERCENT = 25.0

_NODE_DRIVER = r"""
const installer = require(process.argv[2]);
const emit = (o) => process.stdout.write("@@M " + JSON.stringify({ t: Date.now(), ...o }) + "\n");
emit({ start: true });
installer
  .ensureBackend({
    version: process.argv[3],
    skipGaiaInit: true,
    isPackaged: false,
    onProgress: (stage, percent, message) => emit({ stage, percent, message }),
  })
  .then((bin) => emit({ done: true, bin }))
  .catch((err) => {
    emit({ error: String((err && err.message) || err), stage: err && err.stage });
    process.exit(1);
  });
"""


class MeasureError(RuntimeError):
    """A measurement could not be taken; the message says which and why."""


# ── Pure helpers (unit-tested) ───────────────────────────────────────────────


def clean_env(
    base: Dict[str, str], profile: Path, path_dirs: Iterable[str]
) -> Dict[str, str]:
    """The environment of a first-run user whose profile is *profile*.

    Drops everything that points at this PC's own installs, moves every
    per-user folder under *profile*, and keeps uv from using a Python it finds
    on this PC (a clean PC has none, so uv downloads its own, as users' does).
    """
    env = {
        k: v for k, v in base.items() if not k.upper().startswith(_DROPPED_ENV_PREFIXES)
    }
    home = profile / "home"
    env.update(
        {
            "USERPROFILE": str(home),
            "HOME": str(home),
            "HOMEDRIVE": home.drive,
            "HOMEPATH": str(home)[len(home.drive) :],
            "APPDATA": str(home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "TEMP": str(profile / "temp"),
            "TMP": str(profile / "temp"),
            "PATH": os.pathsep.join(path_dirs),
            "UV_PYTHON_PREFERENCE": "only-managed",
            "UV_NO_CONFIG": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def stage_durations(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Seconds per backend-install stage from the node driver's ``@@M`` events.

    A stage runs from its first event to the next stage's first event; the last
    one ends at ``done``.
    """
    start = next((e["t"] for e in events if e.get("start")), None)
    done = next((e["t"] for e in events if e.get("done")), None)
    if start is None or done is None:
        raise MeasureError(
            f"backend install reported no start or no finish: {events[-5:]}"
        )
    order: List[Tuple[str, int]] = []
    for e in events:
        stage = e.get("stage")
        if stage and (not order or order[-1][0] != stage):
            if any(s == stage for s, _ in order):
                continue
            order.append((stage, e["t"]))
    stages = {}
    for i, (stage, t) in enumerate(order):
        end = order[i + 1][1] if i + 1 < len(order) else done
        stages[stage] = round((end - t) / 1000, 1)
    return {"total": round((done - start) / 1000, 1), "stages": stages}


def init_breakdown(
    events: List[Tuple[float, Dict[str, Any]]], exit_at: float
) -> Dict[str, Any]:
    """Seconds per ``gaia init`` step and per model from timed progress events.

    *events* are ``(seconds since start, event)``; a step runs until the next
    step event, the last one until the process exited at *exit_at*.
    """
    steps = [(t, e) for t, e in events if e.get("event") == "step"]
    if not steps:
        raise MeasureError(
            "gaia init printed no step events; is --progress-json supported?"
        )
    out_steps = []
    for i, (t, e) in enumerate(steps):
        end = steps[i + 1][0] if i + 1 < len(steps) else exit_at
        out_steps.append({"step": e.get("message", ""), "seconds": round(end - t, 1)})
    sizes: Dict[str, Optional[int]] = {}
    for _, e in events:
        if e.get("event") == "plan":
            for m in e.get("models") or []:
                sizes[m.get("id")] = m.get("bytes_total")
    models = []
    prev = None
    for t, e in events:
        if e.get("event") == "plan":
            prev = t
        elif e.get("event") == "model_done" and prev is not None:
            models.append(
                {
                    "model": e.get("model"),
                    "bytes": sizes.get(e.get("model")),
                    "downloaded": bool(e.get("downloaded")),
                    "seconds": round(t - prev, 1),
                }
            )
            prev = t
    return {"total": round(exit_at, 1), "steps": out_steps, "models": models}


def parse_sse(lines: Iterable[str]) -> Iterable[Dict[str, Any]]:
    """The JSON events of an SSE stream, one per ``data:`` line."""
    for raw in lines:
        line = raw.strip()
        if line.startswith("data:"):
            body = line[len("data:") :].strip()
            if body and body != "[DONE]":
                yield json.loads(body)


def summarize(samples: List[float]) -> Dict[str, Any]:
    return {
        "median": round(statistics.median(samples), 1),
        "min": round(min(samples), 1),
        "max": round(max(samples), 1),
        "samples": [round(s, 1) for s in samples],
    }


# ── Machine checks ───────────────────────────────────────────────────────────


def is_busy(name: str, argv: List[str]) -> bool:
    """Whether a process runs a model, a test, an eval or an Agent UI backend.

    Judged by what it runs, not by text anywhere in its command line: a shell
    whose command mentions pytest is not running it.
    """
    if any(n in name.lower() for n in _BUSY_NAMES):
        return True
    args = [a.lower() for a in argv]
    names = [Path(a).name for a in args]
    if "-m" in args[:-1]:
        module = args[args.index("-m") + 1]
        if module in _BUSY_MODULES or module.startswith("gaia.eval"):
            return True
    if any(n in _BUSY_SCRIPTS for n in names[:2]):
        return True
    for i, n in enumerate(names[:2]):
        if n in ("gaia", "gaia.exe") and args[i + 1 : i + 2] == ["eval"]:
            return True
    return False


def busy_processes(profile_root: Optional[Path]) -> List[str]:
    """What else is running a model or a test here, outside *profile_root*."""
    found = []
    root = str(profile_root).lower() if profile_root else None
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = p.info["name"] or ""
            argv = list(p.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if root and root in " ".join(argv).lower():
            continue
        if is_busy(name, argv):
            found.append(f"pid {p.info['pid']} {name}: {' '.join(argv)[:160]}")
    return found


def cpu_load_problem(seconds: float = 5.0) -> Optional[str]:
    """Why the CPU is too busy to time anything, or None when it is quiet."""
    procs = list(psutil.process_iter(["pid", "name"]))
    for p in procs:
        try:
            p.cpu_percent(None)  # the first reading only sets the baseline
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    load = psutil.cpu_percent(interval=seconds)
    if load <= MAX_IDLE_CPU_PERCENT:
        return None
    usage = []
    for p in procs:
        try:
            usage.append((p.cpu_percent(None), p.info["name"], p.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    top = sorted(usage, reverse=True)[:5]
    names = ", ".join(f"{n} pid {pid} ({c:.0f}%)" for c, n, pid in top)
    return f"CPU is {load:.0f}% busy over {seconds:.0f} s; the busiest: {names}"


def ensure_idle(profile_root: Optional[Path]) -> None:
    """Refuse to time anything while this PC is doing other work."""
    busy = busy_processes(profile_root)
    if busy:
        raise MeasureError(
            "this PC is busy, which would slow every number; wait for these:\n  "
            + "\n  ".join(busy)
        )
    problem = cpu_load_problem()
    if problem:
        raise MeasureError(f"this PC is busy, which would slow every number: {problem}")


def wait_for_idle(profile_root: Path, timeout: float = 1800, poll: float = 30) -> None:
    """Between runs: wait for a short burst of other work to pass, up to *timeout*."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            ensure_idle(profile_root)
            return
        except MeasureError as e:
            if time.monotonic() >= deadline:
                raise MeasureError(f"still busy after {timeout:.0f} s: {e}") from e
            print(f"  waiting for the PC to go quiet: {e}", flush=True)
            time.sleep(poll)


def kill_profile_processes(profile_root: Path) -> List[str]:
    """Stop every process started from *profile_root*; return what was stopped."""
    root = str(profile_root).lower()
    # This script, and the venv launcher that started it, may name the folder too.
    me = psutil.Process()
    spared = {me.pid, *(p.pid for p in me.parents())}
    victims = []
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            text = " ".join([p.info["exe"] or "", *(p.info["cmdline"] or [])]).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if root in text and p.pid not in spared:
            victims.append(p)
    for p in victims:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(victims, timeout=30)
    return [f"{p.pid}" for p in victims]


def machine_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "os": platform.platform(),
        "cpu": platform.processor(),
        "logical_cpus": psutil.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 1e9, 1),
    }
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor).Name; "
                "(Get-CimInstance Win32_VideoController).Name",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout.split("\n")
        names = [line.strip() for line in out if line.strip()]
        info["cpu"], info["gpus"] = names[0], names[1:]
    except (OSError, subprocess.SubprocessError) as e:
        raise MeasureError(f"could not read the CPU and GPU names: {e}") from e
    return info


#: Longest throwaway home allowed. A user's is about C:\Users\<name>; a much
#: longer one pushes files deep in the venv past Windows' 260-character limit,
#: and the backend then fails on start for a reason no user has.
MAX_HOME_CHARS = 60


def check_work_path(work: Path) -> None:
    home = work / "run-1" / "home"
    if len(str(home)) > MAX_HOME_CHARS:
        raise MeasureError(
            f"--work is too long: the throwaway home would be {home} "
            f"({len(str(home))} characters, limit {MAX_HOME_CHARS}). Pick a "
            "shorter folder, e.g. C:\\gs."
        )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── One run ──────────────────────────────────────────────────────────────────


class Run:
    def __init__(
        self, profile: Path, release: str, app_version: str, path_dirs: List[str]
    ):
        self.profile = profile
        self.release = release
        self.app_version = app_version
        for sub in ("home", "temp", "installer/services"):
            (profile / sub).mkdir(parents=True, exist_ok=True)
        (profile / "home" / "AppData" / "Roaming").mkdir(parents=True, exist_ok=True)
        (profile / "home" / "AppData" / "Local").mkdir(parents=True, exist_ok=True)
        self.env = clean_env(dict(os.environ), profile, path_dirs)
        self.gaia_home = profile / "home" / ".gaia"
        self.gaia = self.gaia_home / "venv" / "Scripts" / "gaia.exe"
        self.python = self.gaia_home / "venv" / "Scripts" / "python.exe"
        self.backend: Optional[subprocess.Popen] = None
        self.port = 0

    def _gaia(self, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [str(self.gaia), *args],
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return result

    # M2
    def install_backend(self) -> Dict[str, Any]:
        installer = self.profile / "installer" / "services" / "backend-installer.cjs"
        source = subprocess.run(
            ["git", "show", f"{self.release}:{INSTALLER_PATH}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        installer.write_bytes(source)
        (self.profile / "installer" / "package.json").write_text(
            json.dumps({"version": self.app_version}), encoding="utf-8"
        )
        driver = self.profile / "installer" / "measure-driver.cjs"
        driver.write_text(_NODE_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            ["node", str(driver), str(installer), self.app_version],
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check=False,
        )
        events = [
            json.loads(line[4:])
            for line in proc.stdout.splitlines()
            if line.startswith("@@M ")
        ]
        if proc.returncode != 0:
            raise MeasureError(
                f"backend install failed (exit {proc.returncode}): "
                f"{[e for e in events if 'error' in e]}\n{proc.stderr[-2000:]}"
            )
        return stage_durations(events)

    # M3
    def gaia_init(self) -> Dict[str, Any]:
        args = [
            str(self.gaia),
            "init",
            "--profile",
            "gaia",
            "--yes",
            "--skip-webui-build",
            "--progress-json",
            "--gaia-lemonade",
        ]
        start = time.monotonic()
        proc = subprocess.Popen(
            args,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        events: List[Tuple[float, Dict[str, Any]]] = []
        tail: List[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            now = time.monotonic() - start
            tail = (tail + [line.rstrip()])[-40:]
            text = line.strip()
            if text.startswith("{"):
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and "event" in event:
                    events.append((now, event))
        code = proc.wait()
        exit_at = time.monotonic() - start
        if code != 0:
            raise MeasureError(f"gaia init failed (exit {code}):\n" + "\n".join(tail))
        return init_breakdown(events, exit_at)

    def stop_all(self) -> None:
        """Back to after-a-reboot: no backend, daemon, agent or GAIA Lemonade."""
        if self.backend is not None:
            self.backend.kill()
            self.backend.wait(timeout=60)
            self.backend = None
        for args in (("daemon", "stop"), ("lemonade", "embedded", "stop")):
            result = self._gaia(*args)
            if result.returncode != 0:
                raise MeasureError(
                    f"`gaia {' '.join(args)}` failed: {result.stdout}{result.stderr}"
                )
        kill_profile_processes(self.profile)

    # M4
    def start_backend(self) -> float:
        self.port = free_port()
        log = self.profile / f"backend-{self.port}.log"
        # The child keeps its own handle; ours would stop the profile's removal.
        with open(log, "w", encoding="utf-8") as out:
            start = time.monotonic()
            self.backend = subprocess.Popen(
                [str(self.gaia), "chat", "--ui", "--ui-port", str(self.port)],
                env=self.env,
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        deadline = start + 600
        while time.monotonic() < deadline:
            if self.backend.poll() is not None:
                raise MeasureError(
                    f"the backend exited ({self.backend.returncode}); see {log}"
                )
            try:
                health = self._get("/api/health", timeout=2)
                if health.get("service") == "gaia-agent-ui":
                    return time.monotonic() - start
            except (
                urllib.error.URLError,
                ConnectionError,
                TimeoutError,
                json.JSONDecodeError,
            ):
                pass
            time.sleep(0.1)
        raise MeasureError(
            f"the backend did not answer /api/health within 600 s; see {log}"
        )

    # M5
    def start_agent(self) -> Dict[str, Any]:
        started = self._gaia("daemon", "start")
        if started.returncode != 0:
            raise MeasureError(
                f"`gaia daemon start` failed: {started.stdout}{started.stderr}"
            )
        agents = self._gaia("daemon", "agents")
        if agents.returncode != 0:
            raise MeasureError(
                f"`gaia daemon agents` failed: {agents.stdout}{agents.stderr}"
            )
        was_running = any(
            line.strip().startswith("gaia: running")
            for line in agents.stdout.splitlines()
        )
        if was_running:
            stopped = self._gaia("daemon", "stop-agent", "gaia")
            if stopped.returncode != 0:
                raise MeasureError(
                    f"`gaia daemon stop-agent gaia` failed: {stopped.stdout}{stopped.stderr}"
                )
        code = (
            "import json, time\n"
            "from gaia.ui.email_sidecar import daemon_client\n"
            "t = time.perf_counter()\n"
            "daemon_client.acquire_handle('gaia')\n"
            "print(json.dumps({'seconds': time.perf_counter() - t}))\n"
        )
        result = subprocess.run(
            [str(self.python), "-c", code],
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            raise MeasureError(
                f"starting the gaia agent failed: {result.stderr[-2000:]}"
            )
        seconds = json.loads(result.stdout.strip().splitlines()[-1])["seconds"]
        return {"seconds": round(seconds, 1), "backend_had_started_it": was_running}

    # M6
    def chat(self, session_id: str, message: str) -> Dict[str, Any]:
        body = json.dumps(
            {"session_id": session_id, "message": message, "stream": True}
        )
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/chat/send",
            data=body.encode(),
            headers=UI_HEADERS,
            method="POST",
        )
        start = time.monotonic()
        first = None
        done = None
        content = ""
        kinds: List[str] = []
        with urllib.request.urlopen(req, timeout=900) as resp:
            lines = (raw.decode("utf-8", "replace") for raw in resp)
            for event in parse_sse(lines):
                kind = event.get("type", "")
                kinds.append(kind)
                if kind == "error":
                    raise MeasureError(f"the chat reported an error: {event}")
                if (
                    first is None
                    and kind in ("chunk", "answer")
                    and event.get("content")
                ):
                    first = time.monotonic() - start
                if kind == "done":
                    done = time.monotonic() - start
                    content = str(event.get("content") or "")
                    break
        if first is None or done is None or not content.strip():
            raise MeasureError(f"the chat gave no answer; events were {kinds[-20:]}")
        return {
            "first_text": round(first, 1),
            "answer": round(done, 1),
            "reply": content[:200],
        }

    def first_chat(self) -> Dict[str, Any]:
        session = self._post("/api/sessions", {})
        cold = self.chat(session["id"], CHAT_PROMPT)
        warm = self.chat(session["id"], WARM_PROMPT)
        return {"cold": cold, "warm": warm}

    def _get(self, path: str, timeout: float = 30) -> Dict[str, Any]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", headers=UI_HEADERS
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(),
            headers=UI_HEADERS,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())


def measure_once(run: Run) -> Dict[str, Any]:
    result: Dict[str, Any] = {"backend_install": run.install_backend()}
    print(f"  M2 backend install: {result['backend_install']['total']} s", flush=True)
    result["gaia_init"] = run.gaia_init()
    print(f"  M3 gaia init: {result['gaia_init']['total']} s", flush=True)
    run.stop_all()
    result["backend_cold_start"] = round(run.start_backend(), 1)
    print(f"  M4 backend cold start: {result['backend_cold_start']} s", flush=True)
    result["agent_cold_start"] = run.start_agent()
    print(
        f"  M5 agent cold start: {result['agent_cold_start']['seconds']} s", flush=True
    )
    result["first_chat"] = run.first_chat()
    print(f"  M6 first chat: {result['first_chat']['cold']['answer']} s", flush=True)
    return result


def summary(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    def col(get) -> Dict[str, Any]:
        return summarize([get(r) for r in runs])

    out = {
        "M2_backend_install": col(lambda r: r["backend_install"]["total"]),
        "M3_gaia_init": col(lambda r: r["gaia_init"]["total"]),
        "M4_backend_cold_start": col(lambda r: r["backend_cold_start"]),
        "M5_agent_cold_start": col(lambda r: r["agent_cold_start"]["seconds"]),
        "M6_first_chat_first_text": col(
            lambda r: r["first_chat"]["cold"]["first_text"]
        ),
        "M6_first_chat_answer": col(lambda r: r["first_chat"]["cold"]["answer"]),
        "M6_warm_chat_answer": col(lambda r: r["first_chat"]["warm"]["answer"]),
    }
    stages = sorted({s for r in runs for s in r["backend_install"]["stages"]})
    out["M2_stages"] = {
        s: col(lambda r, s=s: r["backend_install"]["stages"].get(s, 0.0))
        for s in stages
    }
    return out


def tool_dirs() -> List[str]:
    """PATH for the throwaway profile: Windows itself, node, uv and git only."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    dirs = [
        str(Path(system_root) / "System32"),
        system_root,
        str(Path(system_root) / "System32" / "Wbem"),
        str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0"),
    ]
    for tool in ("node", "uv", "git"):
        found = shutil.which(tool)
        if not found:
            raise MeasureError(
                f"`{tool}` is not on PATH; this script needs it to drive the install"
            )
        dirs.append(str(Path(found).parent))
    return list(dict.fromkeys(dirs))


def app_version_of(release: str) -> str:
    """The version a release's app is built as, and pins its backend to.

    Read from the tag, not the tagged package.json: a release candidate is
    stamped ``0.25.4-rc.3`` only at build time, so its source still says
    ``0.25.4``, which PyPI does not have until the final ships.
    """
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)(?:-rc(\d+))?", release)
    if not match:
        raise MeasureError(
            f"'{release}' is not a release tag like v0.25.4 or v0.25.4-rc3"
        )
    base, rc = match.groups()
    return f"{base}-rc.{rc}" if rc else base


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--release", help="release tag whose app to measure, e.g. v0.25.4-rc3"
    )
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", type=Path, help="JSON file to write")
    ap.add_argument(
        "--work", type=Path, default=None, help="folder for the throwaway profiles"
    )
    ap.add_argument(
        "--keep", action="store_true", help="keep each profile after its run"
    )
    ap.add_argument(
        "--check-idle",
        action="store_true",
        help="only report whether this PC is quiet enough to measure (exit 0) or not (exit 1)",
    )
    args = ap.parse_args(argv)

    if sys.platform != "win32":
        raise MeasureError("measure_app_speed.py runs on Windows only for now")
    ensure_idle(None)
    if args.check_idle:
        print("idle")
        return 0
    if not args.release or not args.out:
        ap.error("--release and --out are required to measure")
    app_version = app_version_of(args.release)
    path_dirs = tool_dirs()
    work = (args.work or Path(tempfile.mkdtemp(prefix="gs-"))).resolve()
    check_work_path(work)
    record: Dict[str, Any] = {
        "about": "Binaries-only speed baseline, hardware half. Written by "
        "util/measure_app_speed.py; never hand-edit a number. Method: "
        "docs/plans/binaries-only.mdx, section 'Speed baseline'.",
        "release": args.release,
        "app_version": app_version,
        "measured_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "script_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "machine": machine_info(),
        "tools": {
            t: subprocess.run(
                [t, "--version"], capture_output=True, text=True, check=True
            ).stdout.strip()
            for t in ("node", "uv")
        },
        "runs": [],
    }
    for i in range(1, args.runs + 1):
        profile = work / f"run-{i}"
        print(f"Run {i}/{args.runs} in {profile}", flush=True)
        run = Run(profile, args.release, app_version, path_dirs)
        try:
            wait_for_idle(work)
            record["runs"].append(measure_once(run))
        except BaseException:
            kill_profile_processes(profile)
            print(
                f"Run {i} failed; its profile is kept for the logs: {profile}",
                file=sys.stderr,
            )
            raise
        kill_profile_processes(profile)
        if not args.keep:
            shutil.rmtree(profile)
    record["summary"] = summary(record["runs"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MeasureError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
