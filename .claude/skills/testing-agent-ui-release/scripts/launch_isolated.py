#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run a GAIA desktop build against a throwaway profile, never the person's own install.

    python launch_isolated.py fetch v0.25.4-rc3 TEST_ROOT          # download + unpack, no install
    python launch_isolated.py fetch TEST_ROOT --run 123456789      # a build-installers.yml run's artifacts
    python launch_isolated.py start --app TEST_ROOT/app/gaia-desktop.exe --home TEST_ROOT/home-rc3
    python launch_isolated.py start --app ... --home ... --lemonade http://127.0.0.1:59999   # no model server
    python launch_isolated.py start --home ... --app WT/src/gaia/apps/webui/node_modules/electron/dist/electron.exe \
        --electron-app WT/src/gaia/apps/webui --wheel WT/dist/amd_gaia-X.whl   # a source checkout (WT)
    python launch_isolated.py port --home TEST_ROOT/home-rc3       # backend port of that window
    python launch_isolated.py stop --home TEST_ROOT/home-rc3       # stops only what `start` started
    python launch_isolated.py isolation --home TEST_ROOT/home-rc3  # did the real profile change?

`start` points USERPROFILE, HOME, APPDATA and LOCALAPPDATA at HOME, gives the app
its own --user-data-dir, turns off auto-update, opens a DevTools port (CDP) for
fallback driving, and records the PID. An empty HOME is a true first run: setup
installs the backend and runs `gaia init`, which is itself worth testing. Keep
TEST_ROOT short (e.g. C:\\gaia-test): long paths break model downloads on Windows.

Windows only (the packaged-app pass is a Windows pass). Unpacking needs 7-Zip on
PATH or `pip install py7zr`.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = "amd/gaia"
REAL_HOME = Path(os.environ.get("USERPROFILE") or Path.home())
# Files the person's own GAIA writes; a change during the run means a leak (it has happened).
WATCH = [
    REAL_HOME / ".gaia",
    Path(os.environ.get("APPDATA", REAL_HOME / "AppData/Roaming")) / "GAIA",
]


def sh(*cmd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def cmd_fetch(a) -> None:
    if not a.tag and not a.run:
        sys.exit("Give a release tag, or --run with a build-installers.yml run id.")
    root = Path(a.root).resolve()
    name = a.tag or f"run{a.run}"
    dl, app = root / f"dl-{name}", root / f"app-{name}"
    dl.mkdir(parents=True, exist_ok=True)
    try:
        if a.tag:
            sh(
                "gh",
                "release",
                "download",
                a.tag,
                "-R",
                REPO,
                "-D",
                str(dl),
                "--skip-existing",
                "-p",
                "gaia-agent-ui-*-x64-setup.exe",
                "-p",
                "amd_gaia-*.whl",
            )
        else:
            sh(
                "gh",
                "run",
                "download",
                str(a.run),
                "-R",
                REPO,
                "-D",
                str(dl),
                "-n",
                "windows-installer",
                "-n",
                "gaia-wheel",
            )
    except subprocess.CalledProcessError as e:
        sys.exit(f"Download failed: {e.stderr.strip()}")
    setup = next(dl.rglob("gaia-agent-ui-*-x64-setup.exe"), None)
    if not setup:
        sys.exit(f"No gaia-agent-ui-*-x64-setup.exe in {name}'s downloads ({dl}).")
    if (app / "gaia-desktop.exe").exists():
        print(f"Already unpacked: {app / 'gaia-desktop.exe'}")
        return
    unpack(setup, app)
    exe = app / "gaia-desktop.exe"
    if not exe.exists():
        sys.exit(f"Unpacked {setup.name} but found no gaia-desktop.exe in {app}.")
    wheel = next(dl.rglob("amd_gaia-*.whl"), None)
    print(f"app   {exe}\nwheel {wheel or '-'}")


def unpack(setup: Path, dest: Path) -> None:
    """The NSIS installer carries the app as an inner 7z archive; extract it without installing."""
    seven = (
        shutil.which("7z")
        or shutil.which("7z.exe")
        or next(
            (
                p
                for p in (
                    Path(os.environ.get("ProgramFiles", "C:/Program Files"))
                    / "7-Zip/7z.exe",
                )
                if p.exists()
            ),
            None,
        )
    )
    if seven:
        tmp = dest.with_name(dest.name + "-nsis")
        sh(str(seven), "x", "-y", f"-o{tmp}", str(setup))
        inner = next(tmp.rglob("app-64.7z"), None)
        if not inner:
            sys.exit(f"No app-64.7z inside {setup.name}; the installer layout changed.")
        sh(str(seven), "x", "-y", f"-o{dest}", str(inner))
        shutil.rmtree(tmp, ignore_errors=True)
        return
    try:
        import py7zr
    except ImportError:
        sys.exit("Unpacking needs 7-Zip on PATH or `python -m pip install py7zr`.")
    blob = setup.read_bytes()
    for m in re.finditer(re.escape(b"7z\xbc\xaf\x27\x1c"), blob):
        try:
            with py7zr.SevenZipFile(io.BytesIO(blob[m.start() :])) as z:
                if any(n.endswith("gaia-desktop.exe") for n in z.getnames()):
                    z.extractall(dest)
                    return
        except Exception:  # noqa: BLE001 - most signature hits are not archive starts
            continue
    sys.exit(f"Found no app archive inside {setup.name}.")


DATA_SUFFIXES = (".db", ".db-wal", ".json", ".sqlite", ".md")
SKIP_DIRS = {
    "venv",
    "bin",
    "cache",
    "browser",
    "logs",
    "node_modules",
    "Cache",
    "Code Cache",
}


def snapshot() -> dict:
    """mtime and size of every data file under the real profile; memory and chats live deep in it."""
    files = {}
    for root in WATCH:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.endswith(DATA_SUFFIXES):
                    p = os.path.join(dirpath, name)
                    try:
                        s = os.stat(p)
                    except OSError:
                        continue
                    files[p] = [s.st_mtime, s.st_size]
    return files


def state_path(home: Path) -> Path:
    return home / "isolated-run.json"


def cmd_start(a) -> None:
    home = Path(a.home).resolve()
    exe = Path(a.app).resolve()
    if not exe.exists():
        sys.exit(
            f"No app at {exe}. `fetch` unpacks one; the installed app is "
            f"%LOCALAPPDATA%\\Programs\\gaia-desktop\\gaia-desktop.exe."
        )
    if home.resolve() == REAL_HOME.resolve():
        sys.exit("--home is the real profile. Pick an empty folder.")
    st = state_path(home)
    if st.exists():
        old = json.loads(st.read_text(encoding="utf-8"))
        if pid_alive(old["pid"]):
            sys.exit(
                f"Already running from this home (pid {old['pid']}). `stop` it first."
            )
    if port_open(a.cdp):
        sys.exit(
            f"DevTools port {a.cdp} is already in use, probably by another run; "
            "pass a free one with --cdp."
        )
    for sub in ("AppData/Roaming", "AppData/Local", "userdata"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    env = dict(
        os.environ,
        USERPROFILE=str(home),
        HOME=str(home),
        APPDATA=str(home / "AppData/Roaming"),
        LOCALAPPDATA=str(home / "AppData/Local"),
        GAIA_DISABLE_UPDATE="1",
        PYTHONUTF8="1",
    )
    # Any of these inherited would point the test back at the real profile.
    for var in (
        "GAIA_HOME",
        "GAIA_CONFIG_DIR",
        "GAIA_HOME_OVERRIDE",
        "GAIA_MEMORY_DB",
        "GAIA_DAEMON_HOME",
    ):
        env.pop(var, None)
    if a.lemonade:
        env["LEMONADE_BASE_URL"] = a.lemonade
    if a.wheel:
        env["GAIA_LOCAL_WHEEL"] = str(Path(a.wheel).resolve())
    if a.skip_init:
        env["GAIA_SKIP_GAIA_INIT"] = "1"
    log = open(home / "app.log", "ab")
    app_dir = [str(Path(a.electron_app).resolve())] if a.electron_app else []
    args = [
        str(exe),
        *app_dir,
        f"--user-data-dir={home / 'userdata'}",
        f"--remote-debugging-port={a.cdp}",
    ]
    proc = subprocess.Popen(
        args,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    st.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "app": str(exe),
                "cdp": a.cdp,
                "started": time.time(),
                "lemonade": a.lemonade,
                "real_profile": snapshot(),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"pid {proc.pid}  cdp {a.cdp}  log {home / 'app.log'}")
    print(
        "First run installs the backend; `port` reports it once the window has loaded."
    )


def backend_port(cdp: int) -> int | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{cdp}/json/list", timeout=2
        ) as r:
            pages = json.loads(r.read().decode("utf-8"))
    except OSError:
        return None
    for page in pages:
        m = re.search(r"[?&]api=([^&#]+)", page.get("url", ""))
        if m:
            port = re.search(r"(?:%3A|:)(\d+)", urllib.request.unquote(m.group(1)))
            if port:
                return int(port.group(1))
    return None


def cmd_port(a) -> None:
    st = json.loads(state_path(Path(a.home)).read_text(encoding="utf-8"))
    deadline = time.time() + a.wait
    port = None
    while time.time() < deadline:
        port = port or backend_port(st["cdp"])
        if port and backend_up(port):
            print(port)
            return
        time.sleep(2)
    home = Path(a.home)
    if not port:
        sys.exit(
            f"The window has not loaded the UI after {a.wait}s (CDP {st['cdp']}). "
            f"First-run setup may still be going: look at the window, and {home / '.gaia/electron-install.log'}."
        )
    sys.exit(
        f"The UI expects its backend on {port}, which is not answering after {a.wait}s. "
        f"Read {home / '.gaia/electron-main.log'}; a stuck 'Starting GAIA...' is a finding if it never clears."
    )


def backend_up(port: int) -> bool:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/health", headers={"X-Gaia-UI": "1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def image_name(pid: int) -> str | None:
    """The executable name running as *pid*, or None when nothing is."""
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not out.startswith('"'):
        return None
    return out.split('","')[0].strip('"')


def pid_alive(pid: int) -> bool:
    return image_name(pid) is not None


def cmd_stop(a) -> None:
    home = Path(a.home)
    st = json.loads(state_path(home).read_text(encoding="utf-8"))
    running = image_name(st["pid"])
    if running and running.lower() != Path(st["app"]).name.lower():
        # The PID was reused by something else after the app exited.
        print(f"{st['pid']} is now {running}, not the app; left alone")
    elif running:
        # /T takes the backend and agent children with it; only this run's tree.
        subprocess.run(
            ["taskkill", "/PID", str(st["pid"]), "/T", "/F"], capture_output=True
        )
        print(f"stopped {st['pid']}")
    else:
        print(f"{st['pid']} already exited")
    cmd_isolation(a)


def cmd_isolation(a) -> None:
    home = Path(a.home)
    st = json.loads(state_path(home).read_text(encoding="utf-8"))
    own = home / ".gaia" / "chat" / "gaia_chat.db"
    print(
        f"Test home holds its own chat database: {'yes' if own.exists() else 'NO'}"
        + (
            ""
            if own.exists()
            else " (did the app ever start, or did it use another home?)"
        )
    )
    before, after = st["real_profile"], snapshot()
    changed = [p for p, t in after.items() if before.get(p) != t]
    if not changed:
        print("Real profile untouched.")
        return
    print(
        "Real-profile files changed since start (a leak if this run wrote them; "
        "another session may have):"
    )
    for p in changed:
        print(f"  {p}")
    sys.exit(1)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.platform != "win32":
        sys.exit("launch_isolated.py is Windows only.")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("fetch")
    sp.add_argument("tag", nargs="?", help="Release tag, e.g. v0.25.4-rc3")
    sp.add_argument("root")
    sp.add_argument(
        "--run",
        type=int,
        help="build-installers.yml run id, for a build with no release",
    )
    sp.set_defaults(fn=cmd_fetch)
    sp = sub.add_parser("start")
    sp.add_argument("--app", required=True)
    sp.add_argument("--home", required=True)
    sp.add_argument(
        "--cdp",
        type=int,
        default=9333,
        help="DevTools port for fallback driving (default 9333)",
    )
    sp.add_argument(
        "--lemonade",
        help="LEMONADE_BASE_URL for the run; a dead port tests the no-server path",
    )
    sp.add_argument(
        "--wheel", help="GAIA_LOCAL_WHEEL: install this wheel instead of the release's"
    )
    sp.add_argument(
        "--electron-app",
        help="App folder for a stock electron.exe (a source checkout's webui)",
    )
    sp.add_argument(
        "--skip-init",
        action="store_true",
        help="GAIA_SKIP_GAIA_INIT=1: skip model setup",
    )
    sp.set_defaults(fn=cmd_start)
    sp = sub.add_parser("port")
    sp.add_argument("--home", required=True)
    sp.add_argument("--wait", type=int, default=120)
    sp.set_defaults(fn=cmd_port)
    for name, fn in (("stop", cmd_stop), ("isolation", cmd_isolation)):
        sp = sub.add_parser(name)
        sp.add_argument("--home", required=True)
        sp.set_defaults(fn=fn)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
