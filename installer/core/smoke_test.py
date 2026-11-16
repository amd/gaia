# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Check a frozen ``gaia`` the way the desktop app uses it, in a throwaway home.

Usage::

    python installer/core/smoke_test.py installer/core/dist/gaia/gaia[.exe]

Each check runs the executable, never this interpreter, so a module that
resolves only in the build environment fails here instead of on a user's PC.
Lemonade points at a port nothing listens on: no check may load a model.
Exit 0 when every check passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

#: What the Agent UI backend imports to index documents and keep sign-ins.
BUNDLED_MODULES = [
    "faiss",
    "pypdf",
    "pypdfium2",
    "docx",
    "pptx",
    "openpyxl",
    "reportlab",
    "keyring",
    "fastapi",
    "uvicorn",
    "tiktoken_ext.openai_public",
    "gaia.ui.server",
    "gaia.daemon.server",
    "gaia.mcp.servers.agent_ui_mcp",
    # The `gaia mcp` servers, `gaia talk` and the scheduler's notification sink.
    "mcp.server",
    "gaia.mcp.agent_mcp_server",
    "gaia.audio.audio_recorder",
    "gaia.audio.kokoro_tts",
    "plyer",
]
if sys.platform == "win32":
    BUNDLED_MODULES.append("plyer.platforms.win.notification")
#: The hub agents run as their own binaries; the core must not carry them.
NOT_BUNDLED = ["gaia_agent", "gaia_agent_email", "torch", "PyInstaller"]
HEALTH_TIMEOUT_S = 90

#: One MCP ``initialize`` request; a stdio server answers it, then exits at EOF.
MCP_INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "gaia-core-smoke", "version": "1"},
        },
    }
)

#: Run by the frozen gaia: sounddevice and its PortAudio reach `gaia talk`.
#: Linux wheels carry no PortAudio, so there only the module must be bundled.
TALK_AUDIO_CHECK = """
import importlib.util, sys
from gaia.audio import audio_recorder, kokoro_tts
assert importlib.util.find_spec("sounddevice"), "sounddevice is not bundled"
if not sys.platform.startswith("linux"):
    assert audio_recorder.sd is not None, audio_recorder._SD_IMPORT_ERROR
    assert kokoro_tts.sd is not None, kokoro_tts._SD_IMPORT_ERROR
"""


#: Run by the frozen gaia: every command its own parser registers, hidden ones
#: included, with the arguments each one cannot start without.
SUBCOMMAND_LISTING = """
import argparse, json
from gaia.cli import build_parser

def needs(parser):
    out = []
    # "--cron or --at is required": the first of each such group.
    one_of = [
        group._group_actions[0]
        for group in parser._mutually_exclusive_groups
        if group.required
    ]
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        positional = not action.option_strings
        if positional and action.nargs in ("?", "*", argparse.REMAINDER):
            continue
        if not positional and not action.required and action not in one_of:
            continue
        out.append({
            "flag": None if positional else action.option_strings[0],
            "choices": [str(c) for c in action.choices] if action.choices else None,
            "type": getattr(action.type, "__name__", None),
            "count": action.nargs if isinstance(action.nargs, int) else 1,
        })
    return out

def walk(parser, path, found):
    groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    found.append({
        "path": path,
        "needs": needs(parser),
        # A group that insists on a sub-command has nothing to start by itself.
        "bare": not any(group.required for group in groups),
    })
    for group in groups:
        seen = set()
        for name, child in group.choices.items():
            if id(child) not in seen:
                seen.add(id(child))
                walk(child, path + [name], found)

found = []
walk(build_parser(), [], found)
print("SUBCOMMANDS " + json.dumps(found[1:]))
"""

#: Commands the walk lists but never starts, each with why. Everything else is
#: started for real, so a command added to the CLI is covered with no edit here.
NEVER_STARTED = {
    "kill": "ends GAIA and Lemonade processes for the whole machine",
    "uninstall": "removes GAIA's files",
    "init": "installs Lemonade and downloads models; `init --check` runs above",
    "daemon": "started and stopped by the daemon check above",
    "lemonade embedded": "downloads and starts a model server",
    "eval": "runs an eval against whichever backend answers on the default port",
    "test": "runs model smoke tests that need a loaded model",
}

#: Commands that fail the walk today, each with its issue. Strict: once one
#: starts cleanly the walk fails until its line is deleted.
KNOWN_BROKEN = {
    "youtube": "amd/gaia#7337",
    "perf-vis": "amd/gaia#7337",
    "agent init": "amd/gaia#7337",
}

#: Output that means a command could not load its own code. A frozen app has no
#: pip, so a remedy that names it is as dead as the import.
BROKEN_START = (
    "Traceback (most recent call last)",
    "ModuleNotFoundError",
    "No module named",
    "ImportError",
    "Failed to execute script",
    "pip install",
)

#: Longest a command may run before the walk stops it. A server still up by
#: then has imported what it needs.
WALK_TIMEOUT_S = 20


def parse_subcommands(stdout: str) -> list[dict]:
    """The command list :data:`SUBCOMMAND_LISTING` printed."""
    for line in stdout.splitlines():
        if line.startswith("SUBCOMMANDS "):
            return json.loads(line[len("SUBCOMMANDS ") :])
    raise ValueError("the frozen gaia printed no SUBCOMMANDS line")


def never_started(path: list[str]) -> str | None:
    """Why the walk does not start *path*, or None when it does."""
    for depth in range(len(path), 0, -1):
        reason = NEVER_STARTED.get(" ".join(path[:depth]))
        if reason:
            return reason
    return None


def start_args(command: dict) -> list[str]:
    """Arguments that get *command* past its parser: a value for each one it needs."""
    args = list(command["path"])
    for need in command["needs"]:
        if need["choices"]:
            value = need["choices"][0]
        elif need["type"] in ("int", "float"):
            value = "1"
        else:
            value = "smoke-walk"
        if need["flag"]:
            args.append(need["flag"])
        args.extend([value] * need["count"])
    return args


def broken_start(output: str) -> str | None:
    """The first sign in *output* that the command could not load its code."""
    return next((mark for mark in BROKEN_START if mark in output), None)


def mcp_stdio_commands(dead_url: str, repo: Path) -> dict[str, list[str]]:
    """The stdio MCP servers `gaia mcp -h` lists, each pointed at nothing live."""
    return {
        "gaia mcp serve": ["mcp", "serve", "--stdio", "--backend", dead_url],
        "gaia mcp tui": ["mcp", "tui", "--stdio"],
        "gaia mcp code-index": ["mcp", "code-index", "--repo", str(repo)],
    }


def answered_initialize(stdout: str) -> bool:
    """True when *stdout* holds the JSON-RPC answer to :data:`MCP_INITIALIZE`."""
    for line in stdout.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(message, dict)
            and message.get("id") == 1
            and "serverInfo" in (message.get("result") or {})
        ):
            return True
    return False


def serves_app(html: str) -> bool:
    """True when *html* is the built Agent UI, not the backend's placeholder page."""
    return '<div id="root">' in html and 'type="module"' in html


def entry_script(html: str) -> str | None:
    """The server path of the page's module entry script, e.g. ``/assets/index-x.js``."""
    match = re.search(r'<script\b[^>]*type="module"[^>]*\bsrc="([^"]+)"', html)
    if not match:
        return None
    return "/" + match.group(1).removeprefix("./").lstrip("/")


#: What the frozen `gaia api start` says: the flagship is not in this build.
API_REFUSAL = "can't run in this GAIA install"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def throwaway_env(home: Path) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GAIA_", "LEMONADE_", "PYTHON", "VIRTUAL_ENV"))
    }
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(home / "AppData" / "Roaming"),
        LOCALAPPDATA=str(home / "AppData" / "Local"),
        LEMONADE_BASE_URL=f"http://127.0.0.1:{free_port()}",
        PYTHONIOENCODING="utf-8",
    )
    return env


class Smoke:
    def __init__(self, exe: Path, home: Path):
        self.exe = exe
        self.home = home
        self.env = throwaway_env(home)
        self.failures: list[str] = []

    def run(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(self.exe), *args],
            env=self.env,
            cwd=str(self.home),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        print(
            f"{'PASS' if ok else 'FAIL'}  {name}"
            + (f"\n      {detail}" if detail and not ok else ""),
            flush=True,
        )
        if not ok:
            self.failures.append(name)

    def version(self) -> None:
        result = self.run("--version")
        self.check(
            "gaia --version",
            result.returncode == 0 and result.stdout.strip() != "",
            result.stderr[-800:],
        )

    def imports(self) -> None:
        code = "import importlib, sys\n" + "".join(
            f"importlib.import_module({m!r})\n" for m in BUNDLED_MODULES
        )
        result = self.run("-c", code)
        self.check(
            "bundled modules import", result.returncode == 0, result.stderr[-1500:]
        )
        for module in NOT_BUNDLED:
            result = self.run("-c", f"import {module}")
            self.check(
                f"{module} is not bundled",
                "ModuleNotFoundError" in result.stderr,
                result.stderr[-400:],
            )

    def mcp_servers(self) -> None:
        repo = self.home / "repo"
        repo.mkdir(exist_ok=True)
        (repo / "hello.py").write_text("print('hello')\n", encoding="utf-8")
        dead_url = f"http://127.0.0.1:{free_port()}"
        for name, args in mcp_stdio_commands(dead_url, repo).items():
            try:
                result = subprocess.run(
                    [str(self.exe), *args],
                    env=self.env,
                    cwd=str(self.home),
                    input=MCP_INITIALIZE + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                    check=False,
                )
                ok = answered_initialize(result.stdout)
                detail = (result.stdout + result.stderr)[-1500:]
            except subprocess.TimeoutExpired as e:
                ok, detail = False, f"no answer within {e.timeout}s"
            self.check(f"{name} answers an MCP initialize", ok, detail)

    def subcommands(self) -> None:
        """Start every command the frozen gaia registers, not only its --help."""
        listing = self.run("-c", SUBCOMMAND_LISTING)
        try:
            commands = parse_subcommands(listing.stdout)
        except ValueError as e:
            self.check("gaia lists its subcommands", False, f"{e}\n{listing.stderr}")
            return
        self.check("gaia lists its subcommands", len(commands) > 20)
        for command in commands:
            name = "gaia " + " ".join(command["path"])
            reason = never_started(command["path"])
            if reason or not command["bare"]:
                helped = self.run(*command["path"], "--help")
                self.check(
                    f"{name} --help ({reason or 'needs a sub-command'})",
                    helped.returncode == 0 and not broken_start(helped.stderr),
                    helped.stderr[-800:],
                )
                continue
            output = self._start(start_args(command))
            mark = broken_start(output)
            issue = KNOWN_BROKEN.get(" ".join(command["path"]))
            if issue:
                self.check(
                    f"{name} still fails, as {issue} says",
                    mark is not None,
                    f"It starts cleanly now: delete its KNOWN_BROKEN line.\n"
                    f"{output[-800:]}",
                )
                continue
            self.check(f"{name} starts", mark is None, f"{mark}\n{output[-1500:]}")

    def _start(self, args: list[str]) -> str:
        """Run one command with nothing to answer it; return what it printed."""
        log = self.home / "walk.log"
        with log.open("wb") as out:
            proc = subprocess.Popen(
                [str(self.exe), *args],
                env=self.env,
                cwd=str(self.home),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
            )
            try:
                proc.wait(timeout=WALK_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self._end_tree(proc.pid)
                proc.wait(timeout=30)
        return log.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _end_tree(pid: int) -> None:
        """End a command still running, and whatever it started."""
        import psutil

        try:
            parent = psutil.Process(pid)
            tree = [*parent.children(recursive=True), parent]
        except psutil.NoSuchProcess:
            return
        for proc in tree:
            # One that already exited must not spare the rest of the tree.
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                continue

    def talk_audio(self) -> None:
        result = self.run("-c", TALK_AUDIO_CHECK)
        self.check(
            "gaia talk's audio I/O is bundled",
            result.returncode == 0,
            result.stderr[-1500:],
        )

    def relaunch_forms(self) -> None:
        result = self.run("-m", "pip", "install", "x")
        self.check(
            "-m refuses modules that are not GAIA's",
            result.returncode == 2,
            result.stderr[-400:],
        )
        result = self.run("-m", "gaia.cli", "--version")
        self.check(
            "-m gaia.cli runs the CLI", result.returncode == 0, result.stderr[-800:]
        )
        # The one-line installers give it the gaia-mcp name too.
        mcp = self.exe.with_name("gaia-mcp" + self.exe.suffix)
        shutil.copy2(self.exe, mcp)
        try:
            result = subprocess.run(
                [str(mcp), "--help"],
                env=self.env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        finally:
            mcp.unlink()
        self.check(
            "runs as gaia-mcp under that name",
            result.returncode == 0 and "MCP Bridge" in result.stdout,
            (result.stdout + result.stderr)[-800:],
        )

    def init_check(self) -> None:
        result = self.run("init", "--check", "--profile", "gaia", "--json", timeout=180)
        try:
            json.loads(result.stdout.strip().splitlines()[-1])
            parsed = True
        except (ValueError, IndexError):
            parsed = False
        self.check(
            "gaia init --check --json answers",
            parsed and "Traceback" not in result.stderr,
            (result.stdout + result.stderr)[-1500:],
        )

    def api_start(self) -> None:
        try:
            result = self.run(
                "api",
                "start",
                "--port",
                str(free_port()),
                "--no-lemonade-check",
                timeout=60,
            )
            ok = (
                result.returncode == 1
                and API_REFUSAL in result.stderr
                and "Traceback" not in result.stderr
            )
            detail = (result.stdout + result.stderr)[-1500:]
        except subprocess.TimeoutExpired:
            ok, detail = False, "it started serving instead of refusing"
        self.check("gaia api start refuses with its reason", ok, detail)

    def daemon(self) -> None:
        start = self.run("daemon", "start", timeout=120)
        self.check(
            "gaia daemon start",
            start.returncode == 0,
            (start.stdout + start.stderr)[-1500:],
        )
        status = self.run("daemon", "status", timeout=60)
        self.check(
            "gaia daemon status",
            status.returncode == 0,
            (status.stdout + status.stderr)[-800:],
        )
        stop = self.run("daemon", "stop", timeout=150)
        self.check(
            "gaia daemon stop", stop.returncode == 0, (stop.stdout + stop.stderr)[-800:]
        )
        self._kill_own_daemon()

    def _kill_own_daemon(self) -> None:
        """End the daemon this home started when its own stop did not."""
        record = self.home / ".gaia" / "host" / "instance.json"
        try:
            pid = int(json.loads(record.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError):
            return
        import psutil

        try:
            proc = psutil.Process(pid)
            if Path(proc.exe()).resolve() != self.exe:
                return
            print(f"      ending this run's daemon, pid {pid}", flush=True)
            proc.kill()
        except psutil.Error:
            return

    def backend(self) -> None:
        port = free_port()
        log = (self.home / "backend.log").open("wb")
        proc = subprocess.Popen(
            [str(self.exe), "chat", "--ui", "--ui-port", str(port)],
            env=self.env,
            cwd=str(self.home),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.close()
        healthy = False
        page = ""
        script_ok = False
        script_detail = "no module entry script in the page"
        deadline = time.monotonic() + HEALTH_TIMEOUT_S
        try:
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/health", timeout=2
                    ) as r:
                        healthy = r.status == 200
                except (urllib.error.URLError, OSError):
                    pass
                if healthy:
                    break
                time.sleep(0.5)
            if healthy:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/", timeout=10
                    ) as r:
                        page = r.read().decode("utf-8", errors="replace")
                except (urllib.error.URLError, OSError) as e:
                    page = f"GET / failed: {e}"
            script = entry_script(page)
            if script:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}{script}", timeout=10
                    ) as r:
                        script_ok = r.status == 200 and "javascript" in (
                            r.headers.get("Content-Type", "")
                        )
                        script_detail = (
                            f"GET {script} -> {r.status} "
                            f"{r.headers.get('Content-Type', '')}"
                        )
                except (urllib.error.URLError, OSError) as e:
                    script_detail = f"GET {script} failed: {e}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        tail = (self.home / "backend.log").read_text(
            encoding="utf-8", errors="replace"
        )[-1500:]
        self.check("gaia chat --ui answers /api/health", healthy, tail)
        self.check(
            "gaia chat --ui serves the Agent UI at /",
            serves_app(page),
            f"GET / returned:\n{page[:600]}",
        )
        self.check("the Agent UI's entry script loads", script_ok, script_detail)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("exe", type=Path)
    args = parser.parse_args(argv)
    # A failing check echoes GAIA's output, emoji included, to a cp1252 console.
    sys.stdout.reconfigure(errors="replace")
    exe = args.exe.resolve()
    if not exe.is_file():
        print(f"smoke: {exe} does not exist", file=sys.stderr)
        return 1
    home = Path(tempfile.mkdtemp(prefix="gaia-core-smoke-"))
    smoke = Smoke(exe, home)
    try:
        smoke.version()
        smoke.imports()
        smoke.relaunch_forms()
        smoke.mcp_servers()
        smoke.talk_audio()
        smoke.init_check()
        # The backend starts a daemon, which the daemon checks then stop.
        smoke.backend()
        smoke.daemon()
        smoke.api_start()
        # Last: it starts every other command, some of which leave a daemon up.
        smoke.subcommands()
    finally:
        smoke._kill_own_daemon()
        shutil.rmtree(home, ignore_errors=True)
    if smoke.failures:
        print(
            f"\nsmoke: {len(smoke.failures)} check(s) failed: {', '.join(smoke.failures)}"
        )
        return 1
    print("\nsmoke: every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
