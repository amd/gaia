# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Linux bugs users reported, re-checked on a real Strix Halo.

Each test runs the installed ``gaia`` from an empty HOME, the way a new Linux
user meets it, and asserts on what the machine shows afterwards: GPU memory
held by GAIA's server process tree (DRM fdinfo), ports, pids, and the requests
a user's own Lemonade received. Every check writes its evidence to
``GAIA_HW_EVIDENCE`` for the CI job summary.

- EU-8: a ``default_model`` chosen before ``gaia init`` replaces Gemma.
- EU-2: closing the last app unloads the models; the idle timeout and
  ``gaia daemon stop`` stop the server.
- EU-3: a restarted server comes back on the same port.
- EU-4: with little memory free, a load shrinks its window or is refused with
  the "Not enough free memory" message, never sent to run out of memory.
- EU-1: with "use my own Lemonade", GAIA sends that server no setup change and
  never starts a server of its own.

Linux with an amdgpu card only. ``GAIA_HW_REQUIRE=1`` (set in CI) turns a
missing prerequisite into a failure instead of a skip.

Run locally from a venv that has GAIA installed:
    pytest tests/hardware/test_linux_halo_user_issues.py -v -s
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from halo_harness import (  # noqa: E402
    GEMMA,
    SMALL_MODEL,
    WORK_ROOT,
    Scenario,
    amdgpu_present,
    evidence,
    gb,
    gpu_memory_of,
    port_listening,
    process_tree,
    sysfs_gpu_used,
    wait_until,
)

pytestmark = [pytest.mark.integration, pytest.mark.real_model]

_MISSING = None
if not sys.platform.startswith("linux"):
    _MISSING = "Linux only"
elif not amdgpu_present():
    _MISSING = "no amdgpu card in /sys/class/drm"
if _MISSING:
    if os.environ.get("GAIA_HW_REQUIRE") == "1":
        raise RuntimeError(f"GAIA_HW_REQUIRE=1 but this machine cannot run: {_MISSING}")
    pytestmark.append(pytest.mark.skip(reason=_MISSING))

#: Lemonade routes that change a server's setup (mirrors lemonade_choice).
SETUP_ROUTES = ("install", "uninstall", "delete", "unload", "cloud/auth")


def _section(title: str) -> None:
    evidence()
    evidence(f"### {title}")
    evidence()


# =============================================================================
# EU-8, EU-2, EU-3: one fresh install, then its whole lifecycle
# =============================================================================


@pytest.fixture(scope="module")
def fresh_install():
    """A new user who chose a small default model, then ran `gaia init`.

    Its Hugging Face cache starts empty too, so "Gemma was not downloaded" is
    read off the disk rather than inferred.
    """
    hf_home = WORK_ROOT / "fresh-install-hf"
    scenario = Scenario("fresh-install", hf_home=hf_home)
    try:
        yield scenario
    finally:
        killed = scenario.cleanup()
        evidence(f"- fresh-install cleanup: force-killed {killed or 'nothing'}")
        if hf_home.exists():
            import shutil

            shutil.rmtree(hf_home, ignore_errors=True)


def test_eu8_chosen_default_model_replaces_gemma(fresh_install):
    s = fresh_install
    _section("EU-8: `default_model` chosen before `gaia init` replaces Gemma")
    s.gaia("config", "set", "default_model", SMALL_MODEL)
    started = time.monotonic()
    init = s.gaia("init", "--profile", "chat", "--gaia-lemonade", "--yes", timeout=1800)
    hub = Path(s.env["HF_HOME"]) / "hub"
    repos = sorted(p.name for p in hub.glob("models--*")) if hub.is_dir() else []
    downloaded = [
        m["id"]
        for m in s.api("GET", "models").json().get("data", [])
        if m.get("downloaded", True)
    ]
    evidence(
        f"- `gaia init --profile chat` exit 0 in {time.monotonic() - started:.0f}s"
    )
    evidence(f"- Hugging Face cache (empty before init): `{repos}`")
    evidence(f"- Lemonade reports downloaded: `{downloaded}`")
    gemma_repos = [r for r in repos if "gemma-4-e4b" in r.lower()]
    assert not gemma_repos, f"init downloaded Gemma anyway: {gemma_repos}"
    assert GEMMA not in downloaded
    assert SMALL_MODEL in downloaded, f"{SMALL_MODEL} was not downloaded"
    evidence(f"- PASS: {SMALL_MODEL} downloaded, no Gemma on disk")
    assert init.returncode == 0


def test_eu2_closing_the_last_app_frees_model_memory(fresh_install):
    s = fresh_install
    _section("EU-2: closing the last GAIA app unloads the models")
    pid, port, _ = s.server()
    s.api("POST", "unload")  # Start from nothing loaded, whatever init left.
    idle_tree = s.server_gpu_memory()
    idle_machine = sysfs_gpu_used()
    evidence(f"- GAIA's server pid {pid}, port {port}")
    evidence(
        f"- before the app: server tree GPU memory {gb(idle_tree)}, "
        f"machine {gb(idle_machine)}"
    )

    app = s.spawn([sys.executable, str(HERE / "halo_app.py"), SMALL_MODEL], "app.log")
    line = app.stdout.readline()
    assert line, f"the app exited before it was ready (code {app.poll()}); see app.log"
    ready = json.loads(line)
    assert "client_id" in ready, ready
    loaded = s.loaded()
    held_tree = s.server_gpu_memory()
    held_machine = sysfs_gpu_used()
    evidence(
        f"- app registered as client {ready['client_id']}, replied {ready['reply']!r}"
    )
    evidence(f"- loaded while open: `{loaded}`")
    evidence(
        f"- app open: server tree GPU memory {gb(held_tree)}, "
        f"machine {gb(held_machine)}"
    )
    assert SMALL_MODEL in loaded
    assert held_tree > idle_tree + 200e6, "loading the model used no GPU memory"

    app.stdin.write("quit\n")
    app.stdin.flush()
    assert app.wait(timeout=60) == 0, "the app did not exit cleanly"
    closed_at = time.monotonic()
    _, took = wait_until(lambda: not s.loaded(), 30, "the models to unload")
    deadline = time.monotonic() + 30
    after_tree = s.server_gpu_memory()
    while after_tree > idle_tree + 100e6 and time.monotonic() < deadline:
        time.sleep(1)
        after_tree = s.server_gpu_memory()
    after_machine = sysfs_gpu_used()
    evidence(f"- app closed; models unloaded {took:.1f}s later")
    evidence(
        f"- after close: server tree GPU memory {gb(after_tree)} "
        f"(freed {gb(held_tree - after_tree)}), machine {gb(after_machine)} "
        f"(freed {gb(held_machine - after_machine)})"
    )
    assert after_tree <= idle_tree + 100e6, (
        f"GPU memory still held after the app closed: {gb(after_tree)} "
        f"(was {gb(idle_tree)} before it opened)"
    )
    assert s.server_running(), "unloading must leave the server running"
    evidence(
        f"- PASS: {gb(held_tree - after_tree)} freed within "
        f"{time.monotonic() - closed_at:.0f}s; server still up"
    )


def test_eu2_idle_timeout_stops_the_server(fresh_install):
    s = fresh_install
    _section("EU-2: the server stops once no client is left for the idle timeout")
    pid, port, _ = s.server()
    s.gaia("config", "set", "lemonade_idle_timeout_minutes", "1")
    set_at = time.monotonic()
    wait_until(
        lambda: not psutil.pid_exists(pid), 150, "the idle timeout to stop lemond"
    )
    took = time.monotonic() - set_at
    evidence(
        f"- idle timeout set to 1 minute; lemond pid {pid} exited {took:.0f}s later"
    )
    assert not port_listening(port), f"port {port} still listening"
    assert s.daemon_pid(), "the idle stop took the daemon down with it"
    evidence(f"- PASS: port {port} closed, daemon pid {s.daemon_pid()} still running")
    s.gaia("config", "set", "lemonade_idle_timeout_minutes", "15")


def test_eu3_a_relaunched_app_gets_the_same_port(fresh_install):
    s = fresh_install
    _section("EU-3: the server comes back on the same port")
    before = s.last_port()
    assert before, "no last_port.json was recorded"
    app = s.spawn([sys.executable, str(HERE / "halo_app.py"), SMALL_MODEL], "app2.log")
    line = app.stdout.readline()
    assert line, f"the relaunched app failed (code {app.poll()}); see app2.log"
    pid, port, _ = s.server()
    evidence(f"- after the idle stop, relaunching an app started lemond pid {pid}")
    evidence(f"- port before {before}, after {port}")
    app.stdin.write("quit\n")
    app.stdin.flush()
    app.wait(timeout=60)
    assert port == before, f"came back on port {port}, not {before}"
    evidence("- PASS: same port")


def test_eu2_daemon_stop_stops_the_server_and_eu3_port_survives(fresh_install):
    s = fresh_install
    _section("EU-2/EU-3: `gaia daemon stop` stops the server; restart keeps the port")
    pid, port, _ = s.server()
    daemon = s.daemon_pid()
    s.gaia("daemon", "stop", timeout=120)
    wait_until(lambda: not psutil.pid_exists(pid), 30, "lemond to exit")
    evidence(f"- `gaia daemon stop` (daemon pid {daemon}): lemond pid {pid} exited")
    assert not port_listening(port), f"port {port} still listening"
    assert not s.daemon_pid(), "the daemon is still running"

    s.gaia("daemon", "start", timeout=120)
    (new_pid, new_port), took = wait_until(
        s.server_running, 120, "the daemon to bring the server back"
    )
    evidence(
        f"- `gaia daemon start`: lemond pid {new_pid} on port {new_port} "
        f"after {took:.0f}s (was {port})"
    )
    assert new_port == port, f"came back on port {new_port}, not {port}"
    evidence("- PASS: server stopped with the daemon and came back on the same port")


# =============================================================================
# EU-4: the free-memory guard, on the real load path
# =============================================================================


@pytest.fixture(scope="module")
def gemma_install():
    """GAIA's own server with Gemma downloaded (from the shared model cache)."""
    scenario = Scenario("memory-guard")
    try:
        scenario.gaia(
            "init", "--profile", "minimal", "--gaia-lemonade", "--yes", timeout=3600
        )
        yield scenario
    finally:
        killed = scenario.cleanup()
        evidence(f"- memory-guard cleanup: force-killed {killed or 'nothing'}")


def _capped_llm(s: Scenario, free_gb: float) -> subprocess.CompletedProcess:
    return s.gaia(
        "llm",
        "Reply with the single word OK.",
        "--model",
        GEMMA,
        "--no-stream",
        "--max-tokens",
        "16",
        timeout=900,
        check=False,
        extra_env={"GAIA_HW_FREE_GB": str(free_gb), "PYTHONPATH": str(HERE / "memcap")},
    )


def test_eu4_real_free_memory_is_read_on_this_machine(gemma_install):
    s = gemma_install
    _section("EU-4: GAIA reads this machine's free memory")
    _, port, _ = s.server()
    probe = s.python(
        "import json\n"
        "from gaia.llm.lemonade_client import _free_memory_snapshot\n"
        f"free, resident = _free_memory_snapshot('http://localhost:{port}/api/v1')\n"
        "print(json.dumps(None if free is None else "
        "{'free_gb': free.free_gb, 'limit_gb': free.limit_gb}))\n",
    )
    reading = json.loads(probe.stdout.strip().splitlines()[-1])
    evidence(f"- unmodified reading on this Strix Halo: `{reading}`")
    assert (
        reading is not None
    ), "GAIA could not read free memory here, so the guard is off on Linux Strix Halo"


def test_eu4_tight_memory_shrinks_the_window(gemma_install):
    s = gemma_install
    _section("EU-4: 10.5 GB free (a 16 GB laptop) shrinks Gemma's window")
    pid, _, _ = s.server()
    s.api("POST", "unload")
    result = _capped_llm(s, 10.5)
    loaded = s.loaded()
    evidence(
        f"- `gaia llm --model {GEMMA}` with 10.5 GB free: exit {result.returncode}"
    )
    evidence(f"- loaded: `{loaded}`")
    shrink = [l for l in result.output.splitlines() if "memory is free" in l]
    evidence(f"- GAIA said: `{shrink[-1].strip() if shrink else '(nothing)'}`")
    assert "LLM Response:" in result.output, "the prompt was not answered"
    assert GEMMA in loaded
    assert 32768 <= loaded[GEMMA] < 65536, f"window {loaded[GEMMA]} was not shrunk"
    assert s.server()[0] == pid, "the server restarted (crashed?) during the load"
    evidence(f"- PASS: loaded at {loaded[GEMMA]:,} tokens instead of 65,536, answered")


def test_eu4_too_little_memory_is_refused_with_a_clear_message(gemma_install):
    s = gemma_install
    _section("EU-4: 6 GB free is refused with the clear message, nothing loaded")
    pid, _, _ = s.server()
    s.api("POST", "unload")
    before = sysfs_gpu_used()
    result = _capped_llm(s, 6.0)
    loaded = s.loaded()
    refusal = [l for l in result.output.splitlines() if "Not enough free memory" in l]
    evidence(f"- `gaia llm --model {GEMMA}` with 6 GB free: exit {result.returncode}")
    evidence(f"- GAIA said: `{refusal[0].strip() if refusal else '(nothing)'}`")
    evidence(
        f"- loaded afterwards: `{loaded}`; machine GPU use {gb(before)} -> "
        f"{gb(sysfs_gpu_used())}"
    )
    assert refusal, "no 'Not enough free memory' message"
    assert GEMMA not in loaded, "the refused model was loaded anyway"
    assert s.server()[0] == pid, "the server died"
    evidence("- PASS: refused before loading, with the message; server unharmed")


# =============================================================================
# EU-1: "use my own Lemonade"
# =============================================================================


class RecordingProxy:
    """Forwards to the user's Lemonade and records every request it sees."""

    def __init__(self, upstream_port: int):
        self.requests = []
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _forward(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else None
                parsed = None
                if body:
                    try:
                        parsed = json.loads(body)
                    except ValueError:
                        parsed = "<non-json>"
                proxy.requests.append((self.command, self.path, parsed))
                headers = {
                    k: v
                    for k, v in self.headers.items()
                    if k.lower() not in ("host", "connection", "keep-alive")
                }
                upstream = http.client.HTTPConnection(
                    "localhost", upstream_port, timeout=1800
                )
                upstream.request(self.command, self.path, body=body, headers=headers)
                resp = upstream.getresponse()
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(k, v)
                self.end_headers()
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                upstream.close()

            do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = _forward

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def setup_changes(self):
        bad = []
        for method, path, body in self.requests:
            route = path.split("/api/v1/", 1)[-1].split("?")[0].strip("/")
            if method in ("GET", "HEAD", "OPTIONS"):
                continue
            if any(route == r or route.startswith(r + "/") for r in SETUP_ROUTES):
                bad.append((method, path))
            elif (
                route == "load" and isinstance(body, dict) and body.get("save_options")
            ):
                bad.append((method, path, "save_options"))
        return bad

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _tree_digest(root: Path) -> dict:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix != ".log"
    }


@pytest.fixture(scope="module")
def users_lemonade():
    """A Lemonade the user runs: its own directory, port and config, no API key."""
    scenario = Scenario("users-lemonade")
    proc = proxy = None
    try:
        dist = (
            scenario.python(
                "from pathlib import Path\n"
                "from gaia.llm.lemonade_embedded import EmbeddedLemonade\n"
                f"m = EmbeddedLemonade(home=Path({str(scenario.root / 'server')!r}))\n"
                "m.install()\n"
                "print(m.daemon_path)\n",
                timeout=600,
            )
            .stdout.strip()
            .splitlines()[-1]
        )
        cache = scenario.root / "server" / "cache"
        config = scenario.root / "server" / "config"
        cache.mkdir(parents=True, exist_ok=True)
        config.mkdir(parents=True, exist_ok=True)
        port = _free_port()
        log = open(scenario.root / "users-lemond.log", "ab")  # noqa: SIM115
        env = {k: v for k, v in scenario.env.items() if k != "LEMONADE_API_KEY"}
        proc = subprocess.Popen(
            [dist, str(cache), str(config), "--port", str(port)],
            env=env,
            cwd=str(Path(dist).parent),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

        def healthy():
            try:
                return requests.get(
                    f"http://localhost:{port}/api/v1/health", timeout=5
                ).ok
            except requests.RequestException:
                return False

        wait_until(healthy, 120, "the user's Lemonade to answer")
        proxy = RecordingProxy(port)
        yield scenario, proc, proxy, config
    finally:
        if proxy:
            proxy.close()
        if proc:
            for child in process_tree(proc.pid)[1:]:
                try:
                    psutil.Process(child).terminate()
                except psutil.NoSuchProcess:
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        killed = scenario.cleanup()
        evidence(f"- users-lemonade cleanup: force-killed {killed or 'nothing'}")


def test_eu1_users_lemonade_is_used_and_never_reconfigured(users_lemonade):
    scenario, server, proxy, config_dir = users_lemonade
    _section("EU-1: GAIA uses the user's own Lemonade and never changes its setup")
    url = f"http://127.0.0.1:{proxy.port}"
    user = Scenario("eu1-gaia-user")
    try:
        user.gaia("config", "set", "default_model", SMALL_MODEL)
        config_before = _tree_digest(config_dir)
        user.gaia(
            "init", "--profile", "chat", "--lemonade-url", url, "--yes", timeout=1800
        )
        chat = user.gaia(
            "llm",
            "Reply with the single word OK.",
            "--no-stream",
            "--max-tokens",
            "16",
            timeout=600,
            check=False,
        )
        config_after = _tree_digest(config_dir)
        changes = proxy.setup_changes()
        routes = {}
        for method, path, _ in proxy.requests:
            key = f"{method} {path.split('?')[0]}"
            routes[key] = routes.get(key, 0) + 1
        own = [
            p
            for p in psutil.process_iter(["name", "cmdline"])
            if "lemond" in (p.info["name"] or "")
            and str(user.root) in " ".join(p.info["cmdline"] or [])
        ]
        evidence(f"- user's lemond pid {server.pid}, GAIA pointed at {url}")
        evidence(f"- requests the user's server received ({len(proxy.requests)}):")
        evidence("  ```")
        for key, count in sorted(routes.items()):
            evidence(f"  {count:4d}  {key}")
        evidence("  ```")
        evidence(f"- setup changes: `{changes}`")
        evidence(f"- server config files before {config_before}, after {config_after}")
        evidence(
            f"- GAIA's own server: dist dir exists={(user.lemonade_dir / 'dist').exists()}, "
            f"state.json exists={(user.lemonade_dir / 'state.json').exists()}, "
            f"lemond processes under its HOME={[p.pid for p in own]}"
        )
        assert "LLM Response:" in chat.output, "chat through the user's server failed"
        assert any(
            "chat/completions" in p for _, p, _ in proxy.requests
        ), "the chat never reached the user's server"
        assert not changes, f"GAIA changed the user's server: {changes}"
        assert config_after == config_before, "the user's server config changed"
        assert not (
            user.lemonade_dir / "dist"
        ).exists(), "GAIA installed its own server"
        assert not (user.lemonade_dir / "state.json").exists(), "GAIA started its own"
        assert not own, f"GAIA started lemond: {[p.pid for p in own]}"
        evidence(
            "- PASS: chat answered through the user's server; zero setup writes; "
            "GAIA started no server"
        )
    finally:
        killed = user.cleanup()
        evidence(f"- eu1 GAIA cleanup: force-killed {killed or 'nothing'}")
