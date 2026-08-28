# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``LemonadeSupervisor`` — the daemon owns the local model server.

The cases that matter are the ones a developer box hides. This machine almost
always has a server *running*, which makes the fast path the only one anyone
exercises by hand — so every test here drives the STOPPED state explicitly, and
asserts on what actually gets spawned rather than merely that something was
called (CLAUDE.md: a mock proves "we called it", never "the call is valid").

The launcher is faked at the ``resolve_lemonade`` seam, never below it: nothing
in this file asserts an argv the supervisor built, because the supervisor is
required not to build one. The three launch forms do not share a shape, and a
test that pinned one here would have to be rewritten when bundled-lemond
resolution lands behind the same function.
"""

from __future__ import annotations

import os
import threading

import pytest

from gaia.llm import lemonade_supervisor as sup
from gaia.llm.lemonade_launcher import LemonadeTooling
from gaia.llm.lemonade_supervisor import LemonadeStartError, LemonadeSupervisor


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    """Shrink the poll interval so a timeout test takes milliseconds."""
    monkeypatch.setattr(sup, "_PROBE_INTERVAL_S", 0.01)


class FakeClient:
    def __init__(self, base_url="http://localhost:13305/api/v1", up=False):
        self.base_url = base_url
        self.host = "localhost"
        self.port = 13305
        self.up = up
        self.probes = 0

    # Mirrors LemonadeClient.health_check's signature: the supervisor passes a
    # (connect, read) timeout, and a fake that rejected it would make every
    # probe raise TypeError and be swallowed as "not answering".
    def health_check(self, timeout=None):
        self.probes += 1
        if not self.up:
            raise ConnectionError("connection refused")
        return {"status": "ok"}


class FakeProc:
    """A spawned server whose liveness and exit code a test drives."""

    def __init__(self, exit_code=None, pid=4242):
        self._exit_code = exit_code
        self.pid = pid
        self.waited = False

    def poll(self):
        return self._exit_code

    def wait(self, timeout=None):
        self.waited = True
        self._exit_code = self._exit_code if self._exit_code is not None else 0
        return self._exit_code


def _supervisor(tmp_path, client, monkeypatch):
    s = LemonadeSupervisor(log_dir=tmp_path)
    monkeypatch.setattr(s, "_client", lambda: client)
    return s


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def installed(monkeypatch):
    """A resolvable install, so the tests exercise the start rather than the
    not-installed branch. The shape is never asserted."""
    tooling = LemonadeTooling(
        found=True,
        kind="modern",
        client_path=r"C:\lemonade\bin\lemonade.exe",
        server_launcher=r"C:\lemonade\bin\LemonadeServer.exe",
    )
    monkeypatch.setattr(sup, "resolve_lemonade", lambda: tooling)
    return tooling


@pytest.fixture
def free_port(monkeypatch):
    monkeypatch.setattr(sup, "_port_is_open", lambda *a, **k: False)


# ---------------------------------------------------------------------------
# The fast path — the only one most users ever hit
# ---------------------------------------------------------------------------


def test_a_running_server_costs_exactly_one_probe(tmp_path, client, monkeypatch):
    """Not a style point: this sits in front of every agent construction and
    every CLI call, so anything added here is latency paid by users who never
    needed it."""
    client.up = True
    monkeypatch.setattr(
        sup,
        "resolve_lemonade",
        lambda: pytest.fail("resolved tooling on the fast path"),
    )
    s = _supervisor(tmp_path, client, monkeypatch)

    state = s.ensure_running(ctx_size=65536)

    assert state.started is False
    assert client.probes == 1


# ---------------------------------------------------------------------------
# The cold path
# ---------------------------------------------------------------------------


def test_a_stopped_server_is_started_and_the_window_is_passed_to_the_launcher(
    tmp_path, client, monkeypatch, installed
):
    """The ctx_size must reach ``build_start_command``, which is what decides
    how it is carried — an env var for a modern install, a flag for the legacy
    CLI. A server started without it answers /health and then fails every long
    request, which reads as an agent bug rather than a launch bug."""
    monkeypatch.setattr(sup, "_port_is_open", lambda *a, **k: False)
    seen = {}
    real_build = sup.build_start_command

    def spy(tooling, ctx_size):
        seen["ctx_size"] = ctx_size
        return real_build(tooling, ctx_size)

    monkeypatch.setattr(sup, "build_start_command", spy)

    spawned = []

    def fake_spawn(spec):
        spawned.append(spec)
        client.up = True
        return FakeProc()

    monkeypatch.setattr(
        LemonadeSupervisor, "_spawn", lambda self, spec: fake_spawn(spec)
    )
    s = _supervisor(tmp_path, client, monkeypatch)

    state = s.ensure_running(ctx_size=65536)

    assert state.started is True
    assert seen["ctx_size"] == 65536
    assert len(spawned) == 1


def test_not_installed_names_the_installer_and_spawns_nothing(
    tmp_path, client, monkeypatch, free_port
):
    monkeypatch.setattr(
        sup, "resolve_lemonade", lambda: LemonadeTooling(found=False, kind="none")
    )
    monkeypatch.setattr(
        LemonadeSupervisor,
        "_spawn",
        lambda self, spec: pytest.fail("spawned with nothing installed"),
    )
    s = _supervisor(tmp_path, client, monkeypatch)

    with pytest.raises(LemonadeStartError) as e:
        s.ensure_running(ctx_size=65536)

    assert "not running" in str(e.value)
    assert "install" in str(e.value).lower()


def test_a_port_held_by_a_stranger_is_an_error_not_an_eviction(
    tmp_path, client, monkeypatch, installed
):
    """GAIA must never kill what it did not start.

    ``LemonadeClient.launch_server`` frees the port first because a user asked
    it to relaunch. This path is unattended, so an occupied port is a loud error
    naming how to find the holder — never a takeover.
    """
    monkeypatch.setattr(sup, "_port_is_open", lambda *a, **k: True)
    monkeypatch.setattr(sup, "_OCCUPIED_PORT_GRACE_S", 0.05)
    monkeypatch.setattr(
        LemonadeSupervisor,
        "_spawn",
        lambda self, spec: pytest.fail("spawned onto an occupied port"),
    )
    s = _supervisor(tmp_path, client, monkeypatch)

    with pytest.raises(LemonadeStartError) as e:
        s.ensure_running(ctx_size=65536)

    assert "13305" in str(e.value)
    assert "LEMONADE_BASE_URL" in str(e.value)


def test_a_port_that_answers_within_the_grace_window_is_attached_to(
    tmp_path, client, monkeypatch, installed
):
    """A server something else spawned a moment ago is not a stranger."""
    monkeypatch.setattr(sup, "_port_is_open", lambda *a, **k: True)
    monkeypatch.setattr(sup, "_OCCUPIED_PORT_GRACE_S", 5.0)
    monkeypatch.setattr(
        LemonadeSupervisor,
        "_spawn",
        lambda self, spec: pytest.fail("spawned over a server that came up"),
    )

    original = client.health_check

    def comes_up(timeout=None):
        if client.probes >= 2:
            client.up = True
        return original()

    client.health_check = comes_up
    s = _supervisor(tmp_path, client, monkeypatch)

    state = s.ensure_running(ctx_size=65536)
    assert state.started is False
    assert state.owned is False


def test_a_server_that_dies_on_launch_names_the_exit_code_and_the_log(
    tmp_path, client, monkeypatch, installed, free_port
):
    monkeypatch.setattr(
        LemonadeSupervisor, "_spawn", lambda self, spec: FakeProc(exit_code=3)
    )
    monkeypatch.setattr(sup, "_tree_kill", lambda proc, timeout: None)
    s = _supervisor(tmp_path, client, monkeypatch)

    with pytest.raises(LemonadeStartError) as e:
        s.ensure_running(ctx_size=65536, timeout=5.0)

    assert "exited with code 3" in str(e.value)
    assert str(s.log_path()) in str(e.value)


def test_a_server_that_never_answers_is_reaped_rather_than_left_behind(
    tmp_path, client, monkeypatch, installed, free_port
):
    """A half-started server still holds the port and the GPU memory the next
    attempt needs, so a failed start must not leave one running."""
    proc = FakeProc(exit_code=None)
    monkeypatch.setattr(LemonadeSupervisor, "_spawn", lambda self, spec: proc)
    killed = []
    monkeypatch.setattr(sup, "_tree_kill", lambda p, timeout: killed.append(p))
    s = _supervisor(tmp_path, client, monkeypatch)

    with pytest.raises(LemonadeStartError) as e:
        s.ensure_running(ctx_size=65536, timeout=0.05)

    assert "did not answer" in str(e.value)
    assert killed == [proc]
    assert s.is_running is False


def test_a_launcher_that_hands_off_and_exits_zero_is_not_a_failure(
    tmp_path, client, monkeypatch, free_port
):
    """``systemctl --user start lemond`` returns immediately and successfully
    while the server it started is still binding. Treating its exit as death
    would report a working start as broken."""
    monkeypatch.setattr(
        sup,
        "resolve_lemonade",
        lambda: LemonadeTooling(
            found=True,
            kind="modern",
            client_path="/usr/bin/lemonade",
            server_launcher="/usr/bin/lemond",
        ),
    )
    monkeypatch.setattr(
        LemonadeSupervisor, "_spawn", lambda self, spec: FakeProc(exit_code=0)
    )

    original = client.health_check

    def comes_up_after_handoff(timeout=None):
        if client.probes >= 2:
            client.up = True
        return original()

    client.health_check = comes_up_after_handoff
    s = _supervisor(tmp_path, client, monkeypatch)

    assert s.ensure_running(ctx_size=65536, timeout=5.0).started is True


# ---------------------------------------------------------------------------
# Ownership — the daemon reaps what it started, and nothing else
# ---------------------------------------------------------------------------


def test_shutdown_tree_kills_the_server_the_daemon_started(
    tmp_path, client, monkeypatch, installed, free_port
):
    """Tree-kill, not kill: Lemonade spawns llama-server children, and an
    orphan still holds the port and the GPU memory the next start needs."""
    proc = FakeProc()

    def spawn(self, spec):
        client.up = True
        self._proc = proc
        return proc

    monkeypatch.setattr(LemonadeSupervisor, "_spawn", spawn)
    killed = []
    monkeypatch.setattr(sup, "_tree_kill", lambda p, timeout: killed.append(p))
    s = _supervisor(tmp_path, client, monkeypatch)

    s.ensure_running(ctx_size=65536)
    assert s.is_running is True
    assert s.pid == proc.pid

    s.shutdown()
    assert killed == [proc]
    assert s.is_running is False


def test_shutdown_never_kills_a_server_the_daemon_only_found(
    tmp_path, client, monkeypatch
):
    """A Lemonade the user launched from the tray must survive `gaia daemon
    stop`. Reaping it would make stopping the daemon silently take the user's
    own server down with it."""
    client.up = True
    monkeypatch.setattr(
        sup, "_tree_kill", lambda p, timeout: pytest.fail("killed a server we found")
    )
    s = _supervisor(tmp_path, client, monkeypatch)

    state = s.ensure_running(ctx_size=65536)
    assert state.owned is False
    s.shutdown()  # must be a no-op


# ---------------------------------------------------------------------------
# Remote servers are not ours to start
# ---------------------------------------------------------------------------


def test_a_remote_server_is_never_started_here(tmp_path, monkeypatch):
    remote = FakeClient(base_url="http://192.168.1.50:13305/api/v1")
    monkeypatch.setattr(
        LemonadeSupervisor,
        "_spawn",
        lambda self, spec: pytest.fail("started a server for a remote URL"),
    )
    s = _supervisor(tmp_path, remote, monkeypatch)

    with pytest.raises(LemonadeStartError) as e:
        s.ensure_running(ctx_size=65536)

    assert "another machine" in str(e.value)


@pytest.mark.parametrize(
    "host,loopback",
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("127.0.0.5", True),
        ("::1", True),
        ("[::1]", True),
        ("", True),
        ("192.168.1.50", False),
        ("lemonade.example.com", False),
    ],
)
def test_loopback_classification(host, loopback):
    assert sup._is_loopback(host) is loopback


# ---------------------------------------------------------------------------
# Concurrency — the daemon's own threads
# ---------------------------------------------------------------------------


def test_concurrent_callers_in_one_daemon_start_exactly_one_server(
    tmp_path, client, monkeypatch, installed, free_port
):
    """Several requests can land at once (the TUI's gate and a sidecar's own
    readiness check). The loser must re-probe INSIDE the lock and attach to the
    winner's server rather than spawning a second one.

    Cross-PROCESS exclusion needs no lock here: the daemon is already
    single-instance, so there is only ever one supervisor on a machine.
    """
    spawned = []

    def slow_spawn(self, spec):
        spawned.append(spec)
        # The window a second caller would race into.
        threading.Event().wait(0.3)
        client.up = True
        proc = FakeProc()
        self._proc = proc
        return proc

    monkeypatch.setattr(LemonadeSupervisor, "_spawn", slow_spawn)
    s = _supervisor(tmp_path, client, monkeypatch)

    results, errors = [], []

    def run():
        try:
            results.append(s.ensure_running(ctx_size=65536, timeout=5.0))
        except Exception as e:  # noqa: BLE001 — recorded and asserted below
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(spawned) == 1, f"started {len(spawned)} servers, want exactly 1"
    assert sum(1 for r in results if r.started) == 1
    assert len(results) == 4


# ---------------------------------------------------------------------------
# The probe's call into the real client — asserted as VALID, not merely as made
# ---------------------------------------------------------------------------


def test_the_probe_call_is_one_the_real_client_actually_accepts():
    """``_probe`` swallows every exception, so a call the real client rejects
    is indistinguishable from "the server is down".

    That is not hypothetical: passing a ``timeout`` the real ``health_check``
    had no parameter for made EVERY probe raise TypeError, get swallowed, and
    report a healthy server as unreachable — the supervisor then waited out its
    full 120s budget and tree-killed a server that had been up for two minutes.
    Every faked-client test passed throughout, because a fake signature is
    whatever the fake says it is.

    So this binds against the REAL signature (CLAUDE.md: a mock proves "we
    called it", never "the call is valid").
    """
    import inspect

    from gaia.llm.lemonade_client import LemonadeClient

    inspect.signature(LemonadeClient.health_check).bind(
        None, timeout=sup._PROBE_TIMEOUT
    )


def test_a_probe_against_a_closed_port_gives_up_promptly(monkeypatch):
    """The whole point of bounding the probe. A real client, a real socket, no
    server — this must answer in well under the client's scalar default (900s,
    correct for generation and catastrophic for a liveness check)."""
    import time as _time

    from gaia.llm.lemonade_client import LemonadeClient

    # Port 1 is reserved and never listening; 4001 is reserved repo-wide.
    client = LemonadeClient(host="127.0.0.1", port=1, keep_alive=True, verbose=False)
    start = _time.monotonic()
    assert sup._probe(client) is False
    assert _time.monotonic() - start < 10.0


# ---------------------------------------------------------------------------
# The spawn itself — asserted as VALID, not merely as called
# ---------------------------------------------------------------------------

_PROBE_SCRIPT = """
import os, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(
    "CTX=" + os.environ.get("LEMONADE_CTX_SIZE", "") + "\\n"
    "MARKER=" + os.environ.get("GAIA_PARENT_MARKER", ""),
    encoding="utf-8",
)
"""


def test_a_real_spawn_inherits_the_parent_environment_and_carries_the_ctx_window(
    tmp_path, client, monkeypatch, free_port
):
    """Every other test stubs ``_spawn``, which proves the call was made and
    nothing about whether it would work. This one runs a REAL child process
    with the env the REAL ``build_start_command`` produced.

    The invariant is the one ``_spawn``'s own comment flags as a known
    breakage: the child environment must be the parent's env MERGED with the
    spec's, never replaced by it. A bare ``env=spec.env`` drops PATH and
    LOCALAPPDATA and LemonadeServer.exe cannot start — a failure no mock can
    produce, because a mock has no environment to lose. It pins the other half
    too: on a modern install the context window travels in the ENV, not the
    argv, so a spawn that dropped it would come up health-green and then fail
    every long request.
    """
    import shutil
    import sys

    # Named so resolve_lemonade classifies the override as MODERN — the form
    # whose ctx_size rides in the environment. The binary is just a Python
    # interpreter; only the env is under test, so the argv is redirected to the
    # probe script below while spec.env is left exactly as the launcher built it.
    fake_server = tmp_path / ("lemonade.exe" if os.name == "nt" else "lemond")
    shutil.copy(sys.executable, fake_server)

    out = tmp_path / "child-env.txt"
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE_SCRIPT, encoding="utf-8")

    # LEMONADE_SERVER_PATH is resolve_lemonade's documented override, so the
    # tooling and the StartSpec below are the real ones.
    monkeypatch.setenv("LEMONADE_SERVER_PATH", str(fake_server))
    monkeypatch.setenv("GAIA_PARENT_MARKER", "inherited")

    real_build = sup.build_start_command

    def redirect_argv_keep_env(tooling, ctx_size):
        spec = real_build(tooling, ctx_size)
        assert spec.env.get("LEMONADE_CTX_SIZE") == str(ctx_size), (
            "the launcher did not put the context window in the environment, so "
            "this test would pass for the wrong reason"
        )
        spec.argv[1:] = [str(probe), str(out)]
        return spec

    monkeypatch.setattr(sup, "build_start_command", redirect_argv_keep_env)

    s = _supervisor(tmp_path, client, monkeypatch)
    with pytest.raises(LemonadeStartError):
        # The child is a script, not a server, so health never comes up. The
        # spawn is what is under test; the failure after it is expected.
        s.ensure_running(ctx_size=65536, timeout=3.0)

    written = out.read_text(encoding="utf-8")
    assert "CTX=65536" in written, "the context window never reached the child"
    assert "MARKER=inherited" in written, (
        "the child did not inherit the parent environment — a bare env=spec.env "
        "would break LemonadeServer.exe in exactly this way"
    )
