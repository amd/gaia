# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the native (C++) agent subprocess launcher (#1092).

The launcher drives real subprocesses over JSON-RPC stdio, so these tests use a
*fake* native agent — a small Python script that speaks the same wire protocol
a C++ binary would. No real C++ binary is required. The fake is wrapped in a
platform-appropriate launcher shim (``.bat`` on Windows, an executable ``sh``
script elsewhere) so the launcher can exec it exactly as it would a binary:
``<shim> --stdio``.
"""

import os
import platform
import re
import signal
import stat
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

from gaia.hub.native_launcher import (
    NativeAgentError,
    NativeAgentLauncher,
    NativeAgentTimeout,
    current_platform,
)

# ---------------------------------------------------------------------------
# Fake native agent
# ---------------------------------------------------------------------------

# A standalone JSON-RPC-over-stdio agent. Behaviour is selected by the
# GAIA_FAKE_MODE env var so a single impl covers every scenario.
_FAKE_AGENT_IMPL = textwrap.dedent("""
    import json, os, sys, time

    mode = os.environ.get("GAIA_FAKE_MODE", "normal")

    if mode == "crash":
        sys.stderr.write("boom: simulated startup crash\\n")
        sys.stderr.flush()
        sys.exit(3)

    if mode == "crash_orphan_stderr":
        import subprocess
        time.sleep(0.5)  # past the spawn grace window, so the handshake sees it
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=sys.stderr,
        )
        sys.stderr.write(f"boom: crash with orphaned stderr holder={holder.pid}\\n")
        sys.stderr.flush()
        sys.exit(4)

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if mode == "silent":
            continue  # read but never reply -> exercises timeout

        if method == "initialize":
            if mode == "bad_handshake":
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32601, "message": "no initialize here"}})
            else:
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "serverInfo": {"name": "fake-native", "version": "0.0.1"},
                    "protocolVersion": "2.0"}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                {"name": "echo", "description": "echo back its params"}]}})
        elif method == "echo":
            send({"jsonrpc": "2.0", "id": rid, "result": {"echo": params}})
        elif method == "boom":
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32000, "message": "kaboom"}})
        elif method == "slow":
            time.sleep(2.0)
            send({"jsonrpc": "2.0", "id": rid, "result": {"slow": True}})
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": rid, "result": {"ok": True}})
            sys.exit(0)
        else:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": "unknown method"}})
    """)


def _write_fake_agent(agent_dir):
    """Create the fake agent impl + an executable shim. Return the shim name."""
    impl = agent_dir / "fake_agent_impl.py"
    impl.write_text(_FAKE_AGENT_IMPL, encoding="utf-8")

    py = sys.executable
    if platform.system() == "Windows":
        shim = agent_dir / "fake_agent.bat"
        shim.write_text(f'@"{py}" "{impl}" %*\r\n', encoding="utf-8")
    else:
        shim = agent_dir / "fake_agent"
        shim.write_text(f'#!/bin/sh\nexec "{py}" "{impl}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim.name


@pytest.fixture
def fake_agent(tmp_path):
    """Return (agent_dir, binary_name) for a normal-mode fake agent."""
    binary = _write_fake_agent(tmp_path)
    return tmp_path, binary


@pytest.fixture
def launcher():
    inst = NativeAgentLauncher(
        startup_timeout=10.0, request_timeout=10.0, shutdown_timeout=5.0
    )
    yield inst


def _start(launcher, fake_agent, mode="normal", **kwargs):
    agent_dir, binary = fake_agent
    env = {"GAIA_FAKE_MODE": mode}
    return launcher.start(agent_dir, binary, env=env, **kwargs)


# ---------------------------------------------------------------------------
# Platform resolution
# ---------------------------------------------------------------------------


def test_current_platform_matches_known_triple():
    triple = current_platform()
    assert triple in {
        "win-x64",
        "win-arm64",
        "linux-x64",
        "linux-arm64",
        "darwin-x64",
        "darwin-arm64",
    }


def test_current_platform_unsupported_os(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Plan9")
    with pytest.raises(NativeAgentError, match="Unsupported operating system"):
        current_platform()


def test_current_platform_unsupported_arch(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "mips")
    with pytest.raises(NativeAgentError, match="Unsupported CPU architecture"):
        current_platform()


def test_resolve_binary_wrong_platform(tmp_path):
    # A binaries map that omits the current platform -> wrong-platform error.
    binaries = {"some-other-plat": "bin/agent"}
    with pytest.raises(NativeAgentError, match="No native binary for platform"):
        NativeAgentLauncher.resolve_binary(
            binaries, tmp_path, platform_triple="win-x64"
        )
    # Same path through real detection (map can't satisfy any real platform).
    with pytest.raises(NativeAgentError, match="No native binary for platform"):
        NativeAgentLauncher.resolve_binary(binaries, tmp_path)


def test_resolve_binary_missing_file(tmp_path):
    binaries = {"linux-x64": "bin/agent", "win-x64": "bin/agent.exe"}
    with pytest.raises(NativeAgentError, match="Native binary not found"):
        NativeAgentLauncher.resolve_binary(
            binaries, tmp_path, platform_triple="linux-x64"
        )


def test_resolve_binary_success(tmp_path):
    (tmp_path / "bin").mkdir()
    target = tmp_path / "bin" / "agent"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    resolved = NativeAgentLauncher.resolve_binary(
        {"linux-x64": "bin/agent"}, tmp_path, platform_triple="linux-x64"
    )
    assert resolved == target.resolve()


# ---------------------------------------------------------------------------
# start() + handshake
# ---------------------------------------------------------------------------


def test_start_performs_handshake(launcher, fake_agent):
    proc = _start(launcher, fake_agent)
    try:
        assert launcher.is_alive(proc)
        info = launcher.server_info(proc)
        assert info["serverInfo"]["name"] == "fake-native"
        assert info["protocolVersion"] == "2.0"
    finally:
        launcher.stop(proc)


def test_start_missing_binary(launcher, tmp_path):
    with pytest.raises(NativeAgentError, match="binary not found"):
        launcher.start(tmp_path, "does-not-exist", handshake=False)


def test_start_immediate_crash(launcher, fake_agent):
    # A binary that dies on startup is caught either by the spawn grace window
    # ("exited immediately") or, if the interpreter shim is slower than that,
    # by the handshake ("process died"). Either way it raises and surfaces the
    # crash stderr rather than registering a dead agent.
    with pytest.raises(NativeAgentError, match="simulated startup crash"):
        _start(launcher, fake_agent, mode="crash")


def _fake_state(proc, reader, stderr_lines):
    return SimpleNamespace(
        proc=proc,
        _stderr_reader=reader,
        recent_stderr=lambda: "\n".join(stderr_lines),
    )


class _DeferredStderrReader:
    """Publishes the crash line only once join() runs, like a lagging reader."""

    def __init__(self, stderr_lines):
        self.stderr_lines = stderr_lines
        self.join_timeouts = []

    def join(self, timeout):
        self.join_timeouts.append(timeout)
        self.stderr_lines.append("boom: simulated startup crash")


def test_dead_process_error_waits_for_crashed_stderr_reader(launcher):
    stderr_lines = []
    reader = _DeferredStderrReader(stderr_lines)
    proc = SimpleNamespace(pid=1234, returncode=3, wait=lambda timeout: 3)

    error = launcher._dead_process_error(_fake_state(proc, reader, stderr_lines))

    assert "process died (exit code 3" in str(error)
    assert "boom: simulated startup crash" in str(error)
    assert len(reader.join_timeouts) == 1
    assert 0 < reader.join_timeouts[0] <= 1


def test_dead_process_error_waits_when_exit_not_yet_reapable(launcher):
    # stdout EOF can arrive a moment before the exit is reapable (poll() still
    # None); the crash text must not be lost in that window.
    stderr_lines = []
    reader = _DeferredStderrReader(stderr_lines)
    proc = SimpleNamespace(pid=1234, returncode=None, poll=lambda: None)

    def wait(timeout):
        proc.returncode = 3
        return 3

    proc.wait = wait

    error = launcher._dead_process_error(_fake_state(proc, reader, stderr_lines))

    assert "exit code 3" in str(error)
    assert "boom: simulated startup crash" in str(error)


def test_dead_process_error_does_not_wait_for_live_stderr_reader(launcher):
    # A native agent may close stdout while staying alive with stderr open:
    # the wait for its exit is bounded and its live reader is never joined.
    class ActiveStderrReader:
        def join(self, timeout):
            pytest.fail("must not wait for stderr of a live process")

    wait_timeouts = []

    def wait(timeout):
        wait_timeouts.append(timeout)
        raise subprocess.TimeoutExpired("agent", timeout)

    proc = SimpleNamespace(pid=5678, returncode=None, wait=wait)

    error = launcher._dead_process_error(_fake_state(proc, ActiveStderrReader(), []))

    assert "process died (exit code None" in str(error)
    assert len(wait_timeouts) == 1
    assert 0 < wait_timeouts[0] <= 1


def test_crash_reported_promptly_when_descendant_holds_stderr(launcher, fake_agent):
    # A grandchild inherits stderr and outlives the agent by seconds; the
    # handshake failure must still carry the crash text without waiting on it.
    began = time.monotonic()
    with pytest.raises(NativeAgentError, match="crash with orphaned stderr") as exc:
        _start(launcher, fake_agent, mode="crash_orphan_stderr")
    elapsed = time.monotonic() - began
    holder = re.search(r"holder=(\d+)", str(exc.value))
    if holder:
        os.kill(int(holder.group(1)), signal.SIGTERM)
    assert holder, "the crash line must reach the error"
    assert elapsed < 15.0, f"waited {elapsed:.1f}s on a descendant's stderr"


def test_start_bad_handshake_is_fatal(launcher, fake_agent):
    with pytest.raises(NativeAgentError, match="handshake failed"):
        _start(launcher, fake_agent, mode="bad_handshake")


def test_start_handshake_timeout(launcher, fake_agent):
    # Silent agent never replies to initialize -> handshake times out.
    launcher.startup_timeout = 0.5
    with pytest.raises(NativeAgentError, match="handshake timed out"):
        _start(launcher, fake_agent, mode="silent")


# ---------------------------------------------------------------------------
# send_rpc()
# ---------------------------------------------------------------------------


def test_send_rpc_round_trip(launcher, fake_agent):
    proc = _start(launcher, fake_agent)
    try:
        result = launcher.send_rpc(proc, "echo", {"hello": "world"})
        assert result == {"echo": {"hello": "world"}}
    finally:
        launcher.stop(proc)


def test_send_rpc_tools_list(launcher, fake_agent):
    proc = _start(launcher, fake_agent)
    try:
        result = launcher.send_rpc(proc, "tools/list")
        names = [t["name"] for t in result["tools"]]
        assert names == ["echo"]
    finally:
        launcher.stop(proc)


def test_send_rpc_sequential_ids(launcher, fake_agent):
    # Many round-trips on one process must each get their own response.
    proc = _start(launcher, fake_agent)
    try:
        for i in range(5):
            result = launcher.send_rpc(proc, "echo", {"i": i})
            assert result == {"echo": {"i": i}}
    finally:
        launcher.stop(proc)


def test_send_rpc_propagates_jsonrpc_error(launcher, fake_agent):
    proc = _start(launcher, fake_agent)
    try:
        with pytest.raises(NativeAgentError, match="kaboom"):
            launcher.send_rpc(proc, "boom")
    finally:
        launcher.stop(proc)


def test_send_rpc_timeout(launcher, fake_agent):
    proc = _start(launcher, fake_agent)
    try:
        with pytest.raises(NativeAgentTimeout, match="did not answer"):
            launcher.send_rpc(proc, "slow", timeout=0.3)
    finally:
        launcher.stop(proc, graceful=False)


def test_send_rpc_unmanaged_process(launcher, fake_agent):
    proc = _start(launcher, fake_agent)
    launcher.stop(proc)
    # After stop the process is no longer tracked.
    with pytest.raises(NativeAgentError, match="not managed by this launcher"):
        launcher.send_rpc(proc, "ping")


def test_ping(launcher, fake_agent):
    proc = _start(launcher, fake_agent)
    try:
        assert launcher.ping(proc) is True
    finally:
        launcher.stop(proc)


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------


def test_graceful_stop(launcher, fake_agent):
    proc = _start(launcher, fake_agent)
    code = launcher.stop(proc)
    assert proc.poll() is not None
    assert code == 0


def test_stop_is_idempotent(launcher, fake_agent):
    proc = _start(launcher, fake_agent)
    launcher.stop(proc)
    # Second stop on an already-dead process must not raise.
    launcher.stop(proc)
    assert proc.poll() is not None


def test_force_stop_kills_process(launcher, fake_agent):
    proc = _start(launcher, fake_agent)
    launcher.stop(proc, graceful=False)
    assert proc.poll() is not None


# ---------------------------------------------------------------------------
# start_from_manifest()
# ---------------------------------------------------------------------------


def test_start_from_manifest_wrong_platform(launcher, tmp_path):
    from gaia.hub.manifest import AgentManifest, CppConfig

    manifest = AgentManifest(
        id="fast-agent",
        name="Fast Agent",
        version="1.0.0",
        description="native",
        author="AMD",
        license="MIT",
        language="cpp",
        cpp=CppConfig(binaries={"some-other-plat": "bin/agent"}),
        source_path=tmp_path / "gaia-agent.yaml",
    )
    with pytest.raises(NativeAgentError, match="No native binary for platform"):
        launcher.start_from_manifest(manifest, platform_triple="win-x64")


def test_start_from_manifest_not_native(launcher, tmp_path):
    from gaia.hub.manifest import AgentManifest

    manifest = AgentManifest(
        id="py-agent",
        name="Py Agent",
        version="1.0.0",
        description="python",
        author="AMD",
        license="MIT",
        language="python",
        source_path=tmp_path / "gaia-agent.yaml",
    )
    with pytest.raises(NativeAgentError, match="not a native agent"):
        launcher.start_from_manifest(manifest)


def test_start_from_manifest_runs_fake_agent(launcher, tmp_path):
    from gaia.hub.manifest import AgentManifest, CppConfig

    binary = _write_fake_agent(tmp_path)
    triple = current_platform()
    manifest = AgentManifest(
        id="fast-agent",
        name="Fast Agent",
        version="1.0.0",
        description="native",
        author="AMD",
        license="MIT",
        language="cpp",
        cpp=CppConfig(binaries={triple: binary}),
        source_path=tmp_path / "gaia-agent.yaml",
    )
    proc = launcher.start_from_manifest(manifest, env={"GAIA_FAKE_MODE": "normal"})
    try:
        assert launcher.is_alive(proc)
        assert launcher.send_rpc(proc, "ping") == {}
    finally:
        launcher.stop(proc)
