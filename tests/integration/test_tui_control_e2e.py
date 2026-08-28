# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""End-to-end: the real Go TUI control server driven by the real Python client.

Every other test of this feature mocks one side or the other, which proves only
that a call was made — not that the two halves agree on the wire. This one boots
`gaia --control` for real, discovers it through `~/.gaia/tui/control.json`, and
drives it with the same helper functions the MCP tools call, so a drift in the
endpoint paths, the error envelope, the 408 timeout contract, or the `state`
field names fails here instead of in a user's terminal.

Skips (never fails) when Go or the TUI source is unavailable, or on Windows,
where the pty this needs does not exist.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gaia.mcp.servers import tui_mcp

REPO_ROOT = Path(__file__).resolve().parents[2]
TUI_DIR = REPO_ROOT / "tui"

# Building the TUI is the slow part; the drive itself takes under a second.
BUILD_TIMEOUT = 300
STARTUP_TIMEOUT = 30


def _skip_reason():
    if os.name != "posix":
        return "needs a pty; POSIX only"
    if not (TUI_DIR / "go.mod").exists():
        return f"no TUI source at {TUI_DIR}"
    if shutil.which("go") is None:
        return "go toolchain not installed"
    return None


class _Drain:
    """Continuously read the TUI's pty and stderr so it never blocks on write."""

    def __init__(self, pty_fd, stderr):
        self._pty_fd = pty_fd
        self._stderr = stderr
        self.stderr_text = []
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._drain_pty, daemon=True),
            threading.Thread(target=self._drain_stderr, daemon=True),
        ]
        for t in self._threads:
            t.start()

    def _drain_pty(self):
        while not self._stop.is_set():
            try:
                if not os.read(self._pty_fd, 65536):
                    return
            except OSError:
                return

    def _drain_stderr(self):
        for line in iter(self._stderr.readline, b""):
            self.stderr_text.append(line.decode(errors="replace"))

    def stop(self):
        self._stop.set()


@pytest.fixture(scope="module")
def tui_binary(tmp_path_factory):
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)

    out = tmp_path_factory.mktemp("tui-bin") / "gaia"
    build = subprocess.run(
        ["go", "build", "-o", str(out), "./cmd/gaia"],
        cwd=TUI_DIR,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
        check=False,
    )
    if build.returncode != 0:
        pytest.fail(f"go build failed:\n{build.stderr}")
    return out


@pytest.fixture
def live_tui(tui_binary, tmp_path, monkeypatch):
    """Start the real TUI under a pty and wait until it is discoverable."""
    import pty

    home = tmp_path / "tui-home"
    home.mkdir()
    monkeypatch.setenv(tui_mcp.ENV_TUI_HOME, str(home))

    # A pty: Bubble Tea needs a terminal on stdin/stdout, and this keeps the
    # test's own output clean of alt-screen escapes.
    controller, follower = pty.openpty()
    env = dict(os.environ)
    env[tui_mcp.ENV_TUI_HOME] = str(home)

    proc = subprocess.Popen(
        [str(tui_binary), "--control", "--debug"],
        stdin=follower,
        stdout=follower,
        stderr=subprocess.PIPE,
        env=env,
        close_fds=True,
    )
    os.close(follower)

    # A real terminal consumes what the TUI writes. Nothing here does, so both
    # the pty and the stderr pipe fill (--debug logs every key) and the TUI
    # blocks mid-write — which looks exactly like a hung control server.
    drained = _Drain(controller, proc.stderr)

    control_file = home / "control.json"
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            drained.stop()
            os.close(controller)
            pytest.fail(
                f"TUI exited early (rc={proc.returncode}):\n"
                + "".join(drained.stderr_text)
            )
        if control_file.exists():
            break
        time.sleep(0.05)
    else:
        proc.kill()
        drained.stop()
        os.close(controller)
        pytest.fail(f"TUI never published {control_file} within {STARTUP_TIMEOUT}s")

    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        drained.stop()
        os.close(controller)


def _ready(cols=120, rows=40):
    """Give the TUI a terminal size and wait for it to be past the splash.

    The anchor has to hold whatever this machine has installed. `gaia-tui` boots
    splash -> readiness gate -> chat, and where it STOPS depends on whether
    Lemonade is up and the models are down — so the only host-independent
    landmark is the product name, which the gate header ("Getting GAIA ready")
    and the chat welcome ("Welcome to GAIA") both carry.
    """
    resized = tui_mcp._resize(cols, rows)
    assert resized.get("status") != "error", resized
    waited = tui_mcp._wait_for(contains="GAIA", timeout_ms=20000)
    assert waited.get("matched") is True, waited


def _settled_view():
    """The view the launch came to rest on: preflight, or chat if all is well."""
    view = tui_mcp._status()["state"]["view"]
    assert view in ("splash", "preflight", "chat"), view
    return view


@pytest.mark.integration
def test_discovery_matches_the_real_control_file(live_tui, tmp_path):
    info, error = tui_mcp.discover()
    assert error is None, error
    assert info["service"] == tui_mcp.SERVICE_ID
    assert info["host"] in tui_mcp.LOOPBACK_HOSTS
    assert info["pid"] == live_tui.pid

    control_file = Path(os.environ[tui_mcp.ENV_TUI_HOME]) / "control.json"
    if os.name == "posix":
        assert oct(control_file.stat().st_mode & 0o777) == "0o600"
    assert len(json.loads(control_file.read_text())["token"]) == 64


@pytest.mark.integration
def test_status_state_field_names_match_the_server(live_tui):
    _ready()
    status = tui_mcp._status()
    assert status.get("status") != "error", status
    assert status["service"] == tui_mcp.SERVICE_ID

    state = status["state"]
    # Every key the Python side reads must exist on the Go side.
    for key in ("view", "agent", "streaming"):
        assert key in state, f"{key!r} missing from the real /status payload: {state}"

    # And the hub-era keys must be GONE, not merely unset: a Python side still
    # reading them would silently get None and report "0 agents visible"
    # forever instead of failing.
    for gone in (
        "hub_tab",
        "hub_tab_index",
        "selected_agent_id",
        "visible_agent_ids",
        "filtering",
        "can_return_to_hub",
    ):
        assert gone not in state, f"{gone!r} is still served: {state}"

    assert _settled_view() in ("preflight", "chat")
    assert status["summary"]


@pytest.mark.integration
def test_keys_and_screen_round_trip(live_tui):
    _ready()
    view = _settled_view()

    # `d` toggles the readiness screen's details pane; in chat it types a
    # character. Either way the frame changes, which is what proves the key
    # reached the model rather than being accepted and dropped.
    before = tui_mcp._screen()["screen"]
    sent = tui_mcp._send_keys(["d"])
    assert sent.get("status") != "error", sent
    # The contract that makes "send then read" race-free: the call returns only
    # once the key has been handled AND redrawn.
    assert sent["settled"] is True, sent
    assert tui_mcp._screen()["screen"] != before, "the key changed nothing on screen"

    # A key that is not a quit must never move the launch to another view.
    assert _settled_view() == view

    screen = tui_mcp._screen()
    assert screen.get("status") != "error", screen
    assert "\x1b[" not in screen["screen"], "plain format leaked ANSI escapes"

    ansi = tui_mcp._screen("ansi")
    assert "\x1b[" in ansi["screen"], "ansi format stripped the escapes"


@pytest.mark.integration
def test_status_reports_running_true_against_a_live_loop(live_tui):
    """`running` is computed, not hardcoded — a quit TUI must be able to say so."""
    _ready()
    assert tui_mcp._status()["running"] is True


@pytest.mark.integration
def test_send_text_reports_what_it_sent(live_tui):
    """There is no search filter to type into any more, but the transport
    contract — every rune accounted for — is what this always tested."""
    _ready()
    typed = tui_mcp._send_text("hello")
    assert typed.get("status") != "error", typed
    assert typed.get("sent_runes") == 5, typed


@pytest.mark.integration
def test_wait_timeout_reports_the_real_screen(live_tui):
    """The 408 contract: a timeout must carry what the screen actually had."""
    _ready()
    out = tui_mcp._wait_for(contains="text that is on no screen", timeout_ms=500)
    assert out["status"] == "error"
    assert out["timed_out"] is True
    assert "GAIA" in out["screen"], out["screen"][:400]
    assert "GAIA" in out["detail"]


@pytest.mark.integration
def test_stale_control_file_is_rejected_after_the_tui_exits(live_tui):
    """A crashed TUI leaves the file behind; the pid check must catch it."""
    _ready()
    control_file = Path(os.environ[tui_mcp.ENV_TUI_HOME]) / "control.json"
    stale = json.loads(control_file.read_text())

    live_tui.terminate()
    live_tui.wait(timeout=10)

    # Simulate the crash case: the file survives the process.
    control_file.write_text(json.dumps(stale), encoding="utf-8")

    info, error = tui_mcp.discover()
    assert info is None
    assert error["status"] == "error"
    assert "gaia tui --control" in error["detail"]
    assert stale["token"] not in error["detail"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
