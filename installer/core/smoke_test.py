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
]
#: The hub agents run as their own binaries; the core must not carry them.
NOT_BUNDLED = ["gaia_agent", "gaia_agent_email", "torch", "PyInstaller"]
HEALTH_TIMEOUT_S = 90


def serves_app(html: str) -> bool:
    """True when *html* is the built Agent UI, not the backend's placeholder page."""
    return '<div id="root">' in html and 'type="module"' in html


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("exe", type=Path)
    args = parser.parse_args(argv)
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
        smoke.init_check()
        # The backend starts a daemon, which the daemon checks then stop.
        smoke.backend()
        smoke.daemon()
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
