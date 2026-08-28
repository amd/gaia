# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the GAIA TUI control MCP server.

These run without the optional ``mcp`` package and without a TUI: the tools are
plain module-level functions, and the control server is a scripted fake that
replaces the module's ``requests`` handle.
"""

import json
import logging

import psutil
import pytest
import requests

from gaia.mcp.servers import tui_mcp

TOKEN = "f" * 64
PID = 4242
PORT = 8770
BASE_URL = f"http://127.0.0.1:{PORT}"

# ── Fixtures / fakes ─────────────────────────────────────────────────


def write_control(tmp_path, monkeypatch, **overrides):
    """Point GAIA_TUI_HOME at *tmp_path* and write a control.json there."""
    monkeypatch.setenv(tui_mcp.ENV_TUI_HOME, str(tmp_path))
    info = {
        "pid": PID,
        "port": PORT,
        "token": TOKEN,
        "host": "127.0.0.1",
        "service": "gaia-tui-control",
        "api_version": "v1",
        "started_at": 1730000000.0,
        "version": "dev",
    }
    info.update(overrides)
    (tmp_path / "control.json").write_text(json.dumps(info), encoding="utf-8")
    return info


def set_pid_alive(monkeypatch, alive):
    """Make ``pid_alive`` deterministic regardless of the host's real pids."""
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: alive)

    class _NoProcess:
        def __init__(self, pid):
            raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", _NoProcess)


class FakeResponse:
    def __init__(self, status_code, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}", response=self
            )


def _api_error(status, code, message, **extra):
    """The control server's error envelope."""
    return FakeResponse(status, {"error": {"code": code, "message": message, **extra}})


class FakeTui:
    """A scripted control server: keys mutate state, state drives the screen."""

    exceptions = requests.exceptions

    def __init__(
        self,
        view="chat",
        agent="gaia",
        blocker="",
        service=tui_mcp.SERVICE_ID,
        pid=PID,
        unreachable=False,
        streaming=False,
        overlay="",
        settled=True,
        running=True,
    ):
        self.streaming = streaming
        self.overlay = overlay
        self.settled = settled
        self.running = running
        self.view = view
        self.agent = agent
        self.blocker = blocker
        self.service = service
        self.pid = pid
        self.unreachable = unreachable
        self.seq = 0
        self.keys_sent = []
        self.text_sent = []
        self.requests_seen = []
        self.tokens_seen = []

    # -- state --------------------------------------------------------

    def state(self):
        state = {
            "view": self.view,
            "agent": self.agent,
            "streaming": self.streaming,
        }
        if self.overlay:
            state["overlay"] = self.overlay
        if self.blocker:
            state["blocker"] = self.blocker
        return state

    def screen(self):
        if self.view == "splash":
            return "  G A I A    Your local AI agent — by AMD\n\n  starting GAIA…"
        if self.view == "preflight":
            return (
                f"  Getting {self.agent.upper()} ready\n"
                f"  > [!]   Local AI            not running\n"
                "  r re-check · d details · esc back"
            )
        return f"chat with {self.agent}\n> \n  Ctrl+C quit"

    def press(self, key):
        self.keys_sent.append(key)
        self.seq += 1
        # The readiness gate is the only screen with navigation left, and `r`
        # is the only key that changes what it reports.
        if self.view == "preflight" and key == "r":
            self.blocker = ""
            self.view = "chat"
        elif key == "esc" and self.overlay:
            self.overlay = ""

    # -- transport ----------------------------------------------------

    def _status_body(self):
        return {
            "service": self.service,
            "api_version": "v1",
            "version": "dev",
            "pid": self.pid,
            "running": self.running,
            "uptime_ms": 1000,
            "cols": 80,
            "rows": 24,
            "frame_seq": self.seq,
            "state": self.state(),
        }

    def get(self, url, headers=None, timeout=None, **kwargs):
        return self.request("GET", url, headers=headers, timeout=timeout, **kwargs)

    def request(
        self,
        method,
        url,
        headers=None,
        timeout=None,
        json=None,
        params=None,
        proxies=None,
    ):
        if self.unreachable:
            raise requests.exceptions.ConnectionError("refused")
        assert url.startswith(BASE_URL), url
        # Loopback must never be proxied — HTTP_PROXY would otherwise be handed
        # the bearer token and the screen contents.
        assert proxies == {"http": None, "https": None}, proxies
        auth = (headers or {}).get("Authorization", "")
        self.tokens_seen.append(auth)
        assert auth == f"Bearer {TOKEN}", auth
        path = url.split(tui_mcp.API_PREFIX, 1)[1]
        method = method.upper()
        self.requests_seen.append((method, path))

        # Contract checks the real server enforces (tui/internal/control/
        # server.go): method per route, DisallowUnknownFields on every POST body,
        # and the documented value ranges. Without these the fake would only
        # prove the call was made, not that it would be accepted.
        rejection = self._reject_invalid(method, path, json)
        if rejection is not None:
            return rejection

        if path == "/status":
            return FakeResponse(200, self._status_body())

        if path == "/screen":
            fmt = (params or {}).get("format", "plain")
            text = self.screen()
            return FakeResponse(
                200,
                {
                    "format": fmt,
                    "seq": self.seq,
                    "cols": 80,
                    "rows": 24,
                    "lines": len(text.splitlines()),
                    "screen": text,
                },
            )

        if path == "/keys":
            keys = (json or {}).get("keys", [])
            for key in keys:
                self.press(key)
            return FakeResponse(
                200,
                {
                    "sent": len(keys),
                    "keys": keys,
                    "seq": self.seq,
                    "settled": self.settled,
                },
            )

        if path == "/text":
            text = (json or {}).get("text", "")
            self.text_sent.append(text)
            self.seq += 1
            return FakeResponse(
                200,
                {"sent_runes": len(text), "seq": self.seq, "settled": self.settled},
            )

        if path == "/resize":
            return FakeResponse(
                200,
                {
                    "cols": (json or {}).get("cols"),
                    "rows": (json or {}).get("rows"),
                    "seq": self.seq,
                    "settled": self.settled,
                },
            )

        if path == "/wait":
            return self._wait(json or {})

        return FakeResponse(
            404, {"error": {"code": "not_found", "message": f"no route {path}"}}
        )

    #: route -> (method, allowed body fields) exactly as the Go server declares.
    ROUTES = {
        "/status": ("GET", None),
        "/screen": ("GET", None),
        "/frames": ("GET", None),
        "/keys": ("POST", {"keys", "delay_ms"}),
        "/text": ("POST", {"text", "delay_ms"}),
        "/wait": ("POST", {"contains", "absent", "state", "timeout_ms"}),
        "/resize": ("POST", {"cols", "rows"}),
    }
    STATE_MATCHER_KEYS = {
        "view": str,
        "agent": str,
        "can_return_to_hub": bool,
        "hub_tab": str,
        "selected_agent_id": str,
        "overlay": str,
        "streaming": bool,
        "filtering": bool,
        "hub_tab_index": (int, float),
        "visible_contains": str,
    }

    def _reject_invalid(self, method, path, body):
        route = self.ROUTES.get(path)
        if route is None:
            return None  # the 404 branch below handles unknown routes
        want_method, allowed = route
        if method != want_method:
            return _api_error(405, "method_not_allowed", f"{path} wants {want_method}")
        if allowed is None:
            return None
        if not self.running:
            # tea.Program.Send silently discards while the loop is not consuming,
            # so the server refuses instead of answering 200 with a lie.
            return _api_error(
                503,
                "not_running",
                "the TUI is not accepting input: its event loop is not running",
                hint="check GET /control/v1/status; if running is false, start a "
                "new TUI with the control API enabled",
            )
        unknown = set(body or {}) - allowed
        if unknown:
            return _api_error(400, "bad_json", f"unknown field {sorted(unknown)[0]!r}")

        body = body or {}
        if path == "/keys":
            if not body.get("keys"):
                return _api_error(400, "no_keys", "keys is empty")
            if len(body["keys"]) > tui_mcp.MAX_KEYS_PER_CALL:
                return _api_error(400, "too_many_keys", "too many keys in one call")
        if path in ("/keys", "/text"):
            delay = body.get("delay_ms", 0)
            if not 0 <= delay <= tui_mcp.MAX_DELAY_MS:
                return _api_error(400, "bad_delay", f"delay_ms {delay} out of range")
            count = len(body.get("keys", [])) if path == "/keys" else 1
            if delay * max(count - 1, 0) > tui_mcp.MAX_INJECTION_DELAY_MS:
                return _api_error(
                    400, "delay_budget_exceeded", "the batch would pause too long"
                )
        if path == "/resize":
            cols, rows = body.get("cols"), body.get("rows")
            if not (20 <= cols <= 500 and 5 <= rows <= 200):
                return _api_error(400, "bad_size", f"{cols}x{rows} out of range")
        if path == "/wait":
            timeout = body.get("timeout_ms")
            if timeout is not None and not 0 < timeout <= 600000:
                return _api_error(400, "bad_timeout", f"timeout_ms {timeout} invalid")
            for key, want in (body.get("state") or {}).items():
                want_type = self.STATE_MATCHER_KEYS.get(key)
                if want_type is None:
                    return _api_error(
                        400, "bad_state_matcher", f"unknown state key {key!r}"
                    )
                if not isinstance(want, want_type):
                    return _api_error(
                        400, "bad_state_matcher", f"state key {key!r} has a bad type"
                    )
        return None

    def _wait(self, payload):
        screen = self.screen()
        matched = True
        if payload.get("contains") and payload["contains"] not in screen:
            matched = False
        if payload.get("absent") and payload["absent"] in screen:
            matched = False
        for key, value in (payload.get("state") or {}).items():
            if self.state().get(key) != value:
                matched = False
        if matched:
            return FakeResponse(
                200,
                {
                    "matched": True,
                    "elapsed_ms": 12,
                    "seq": self.seq,
                    "screen": screen,
                },
            )
        return FakeResponse(
            408,
            {
                "error": {
                    "code": "wait_timeout",
                    "message": "wait timed out",
                    "screen": screen,
                    "elapsed_ms": payload.get("timeout_ms", 0),
                    "state": self.state(),
                }
            },
        )


@pytest.fixture
def live_tui(tmp_path, monkeypatch):
    """A discoverable, healthy fake TUI. Returns the FakeTui instance."""

    def _make(**kwargs):
        write_control(tmp_path, monkeypatch)
        set_pid_alive(monkeypatch, True)
        fake = FakeTui(**kwargs)
        monkeypatch.setattr(tui_mcp, "requests", fake)
        return fake

    return _make


# ── Discovery ────────────────────────────────────────────────────────


def test_control_dir_honors_env_per_call(tmp_path, monkeypatch):
    monkeypatch.delenv(tui_mcp.ENV_TUI_HOME, raising=False)
    default = tui_mcp.control_dir()
    assert default.name == "tui"

    monkeypatch.setenv(tui_mcp.ENV_TUI_HOME, str(tmp_path))
    assert tui_mcp.control_dir() == tmp_path
    assert tui_mcp.control_path() == tmp_path / "control.json"

    other = tmp_path / "other"
    monkeypatch.setenv(tui_mcp.ENV_TUI_HOME, str(other))
    # Re-read, not cached from the first call.
    assert tui_mcp.control_dir() == other


def test_discover_no_control_file(tmp_path, monkeypatch):
    monkeypatch.setenv(tui_mcp.ENV_TUI_HOME, str(tmp_path))
    info, error = tui_mcp.discover()
    assert info is None
    assert error["status"] == "error"
    assert "No GAIA TUI is running" in error["detail"]
    assert "control.json" in error["detail"]
    assert "gaia tui --control" in error["detail"]


def test_discover_malformed_file(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv(tui_mcp.ENV_TUI_HOME, str(tmp_path))
    (tmp_path / "control.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="gaia.mcp.servers.tui_mcp"):
        info, error = tui_mcp.discover()
    assert info is None
    assert "malformed" in error["detail"]
    assert "control.json" in error["detail"]
    assert "gaia tui --control" in error["detail"]
    assert any("malformed" in r.getMessage() for r in caplog.records)


def test_read_control_file_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv(tui_mcp.ENV_TUI_HOME, str(tmp_path))
    assert tui_mcp.read_control_file() is None


def test_read_control_file_missing_required_key(tmp_path, monkeypatch):
    monkeypatch.setenv(tui_mcp.ENV_TUI_HOME, str(tmp_path))
    (tmp_path / "control.json").write_text('{"pid": 1}', encoding="utf-8")
    assert tui_mcp.read_control_file() is None


def test_discover_dead_pid(tmp_path, monkeypatch):
    write_control(tmp_path, monkeypatch)
    set_pid_alive(monkeypatch, False)
    info, error = tui_mcp.discover()
    assert info is None
    assert str(PID) in error["detail"]
    assert "not running" in error["detail"]
    assert "stale control file" in error["detail"]
    assert "gaia tui --control" in error["detail"]


def test_discover_probe_unreachable(tmp_path, monkeypatch):
    write_control(tmp_path, monkeypatch)
    set_pid_alive(monkeypatch, True)
    monkeypatch.setattr(tui_mcp, "requests", FakeTui(unreachable=True))
    info, error = tui_mcp.discover()
    assert info is None
    assert "did not answer the control status probe" in error["detail"]
    assert "recycled" in error["detail"]
    assert "gaia tui --control" in error["detail"]


def test_discover_probe_wrong_service(tmp_path, monkeypatch):
    write_control(tmp_path, monkeypatch)
    set_pid_alive(monkeypatch, True)
    monkeypatch.setattr(tui_mcp, "requests", FakeTui(service="some-other-server"))
    info, error = tui_mcp.discover()
    assert info is None
    assert "Another process now owns the port" in error["detail"]
    assert "gaia tui --control" in error["detail"]


def test_discover_probe_pid_mismatch(tmp_path, monkeypatch):
    write_control(tmp_path, monkeypatch)
    set_pid_alive(monkeypatch, True)
    monkeypatch.setattr(tui_mcp, "requests", FakeTui(pid=PID + 1))
    info, error = tui_mcp.discover()
    assert info is None
    assert "Another process now owns the port" in error["detail"]


def test_discover_happy_path(live_tui):
    live_tui()
    info, error = tui_mcp.discover()
    assert error is None
    assert info["pid"] == PID
    assert info["port"] == PORT
    assert tui_mcp.base_url_of(info) == BASE_URL


def test_discovery_errors_are_distinct_and_never_leak_the_token(tmp_path, monkeypatch):
    details = []

    monkeypatch.setenv(tui_mcp.ENV_TUI_HOME, str(tmp_path))
    details.append(tui_mcp.discover()[1]["detail"])

    (tmp_path / "control.json").write_text("{oops", encoding="utf-8")
    details.append(tui_mcp.discover()[1]["detail"])

    write_control(tmp_path, monkeypatch)
    set_pid_alive(monkeypatch, False)
    details.append(tui_mcp.discover()[1]["detail"])

    set_pid_alive(monkeypatch, True)
    monkeypatch.setattr(tui_mcp, "requests", FakeTui(unreachable=True))
    details.append(tui_mcp.discover()[1]["detail"])

    monkeypatch.setattr(tui_mcp, "requests", FakeTui(service="nope"))
    details.append(tui_mcp.discover()[1]["detail"])

    assert len(set(details)) == len(details), details
    for detail in details:
        assert "gaia tui --control" in detail
        assert TOKEN not in detail
        assert str(PORT) not in detail


# ── _normalize_error ─────────────────────────────────────────────────


def test_normalize_error_unwraps_error_envelope():
    resp = FakeResponse(
        400,
        {
            "error": {
                "code": "unknown_key",
                "message": "unknown key name 'flurb'",
                "hint": "supported: enter, esc, tab, up, down",
            }
        },
    )
    err = requests.exceptions.HTTPError("400", response=resp)
    out = tui_mcp._normalize_error(err, BASE_URL)
    assert out["status"] == "error"
    assert "unknown key name 'flurb'" in out["detail"]
    assert "supported: enter, esc, tab, up, down" in out["detail"]


def test_normalize_error_falls_back_to_http_code():
    resp = FakeResponse(503, None, text="")
    err = requests.exceptions.HTTPError("503", response=resp)
    assert tui_mcp._normalize_error(err, BASE_URL)["detail"] == "HTTP 503"


def test_normalize_error_strips_base_url():
    resp = FakeResponse(500, None, text=f"boom at {BASE_URL}/control/v1/keys")
    err = requests.exceptions.HTTPError("500", response=resp)
    detail = tui_mcp._normalize_error(err, BASE_URL)["detail"]
    assert BASE_URL not in detail
    assert "/control/v1/keys" in detail

    generic = tui_mcp._normalize_error(RuntimeError(f"bad {BASE_URL}/x"), BASE_URL)
    assert BASE_URL not in generic["detail"]


def test_normalize_error_redacts_the_token():
    resp = FakeResponse(
        401,
        {"error": {"code": "unauthorized", "message": f"bad token {TOKEN}"}},
    )
    err = requests.exceptions.HTTPError("401", response=resp)
    detail = tui_mcp._normalize_error(err, BASE_URL, token=TOKEN)["detail"]
    assert TOKEN not in detail
    assert "<redacted>" in detail

    generic = tui_mcp._normalize_error(
        RuntimeError(f"auth={TOKEN}"), BASE_URL, token=TOKEN
    )
    assert TOKEN not in generic["detail"]


def test_normalize_error_connection_error_is_actionable():
    out = tui_mcp._normalize_error(
        requests.exceptions.ConnectionError("refused"), BASE_URL
    )
    assert "gaia tui --control" in out["detail"]
    assert BASE_URL not in out["detail"]


# ── Tools: no TUI running ────────────────────────────────────────────


def test_every_tool_errors_cleanly_when_no_tui(monkeypatch):
    error = tui_mcp._err("No GAIA TUI is running. Start one with: gaia tui --control")
    monkeypatch.setattr(tui_mcp, "discover", lambda *a, **k: (None, error))

    results = {
        "status": tui_mcp._status(),
        "screen": tui_mcp._screen(),
        "send_keys": tui_mcp._send_keys(["enter"]),
        "send_text": tui_mcp._send_text("hello"),
        "wait_for": tui_mcp._wait_for(contains="hi"),
        "resize": tui_mcp._resize(120, 40),
    }
    for name, result in results.items():
        assert result["status"] == "error", name
        assert "gaia tui --control" in result["detail"], name


def test_bad_arguments_are_rejected_before_discovery(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("discover should not be called")

    monkeypatch.setattr(tui_mcp, "discover", _boom)
    assert tui_mcp._screen("html")["status"] == "error"
    assert tui_mcp._send_keys([])["status"] == "error"
    assert tui_mcp._send_keys(5)["status"] == "error"
    assert tui_mcp._send_text("")["status"] == "error"
    assert tui_mcp._resize(0, 40)["status"] == "error"


# ── Tools: happy paths ───────────────────────────────────────────────


def test_status_summary_splash(live_tui):
    """Nothing on the splash takes input — the summary must not invite a key."""
    live_tui(view="splash", agent="gaia")
    out = tui_mcp._status()
    assert "starting up" in out["summary"]
    assert "no key needed" in out["summary"]


def test_status_summary_preflight_names_the_blocking_row(live_tui):
    """Which row refuses is the one thing a caller needs off this screen."""
    live_tui(view="preflight", agent="gaia", blocker="lemonade")
    out = tui_mcp._status()
    assert out["summary"] == (
        "readiness gate, for 'gaia', blocked on 'lemonade' — read the screen for the fix"
    )


def test_status_summary_preflight_still_checking(live_tui):
    """No blocker yet is not the same as ready — say which it is."""
    live_tui(view="preflight", agent="gaia")
    out = tui_mcp._status()
    assert "nothing is refusing the launch yet" in out["summary"]


def test_status_summary_chat(live_tui):
    live_tui(view="chat", agent="email")
    out = tui_mcp._status()
    assert out["summary"] == "chat with 'email'"


def test_status_summary_unknown_view(live_tui):
    fake = live_tui()
    fake.state = lambda: {"view": "unknown"}
    out = tui_mcp._status()
    assert "does not report its view state" in out["summary"]


def test_screen_default_is_plain(live_tui):
    fake = live_tui()
    out = tui_mcp._screen()
    assert out["format"] == "plain"
    assert "chat with" in out["screen"]
    assert ("GET", "/screen") in fake.requests_seen


def test_send_keys_accepts_a_string(live_tui):
    fake = live_tui()
    out = tui_mcp._send_keys("tab, down enter")
    assert out["sent"] == 3
    assert fake.keys_sent == ["tab", "down", "enter"]


def test_send_text(live_tui):
    fake = live_tui()
    out = tui_mcp._send_text("triage my inbox")
    assert out["sent_runes"] == len("triage my inbox")
    assert fake.text_sent == ["triage my inbox"]


def test_resize(live_tui):
    live_tui()
    out = tui_mcp._resize(120, 40)
    assert out["cols"] == 120 and out["rows"] == 40


def test_wait_for_requires_a_matcher(live_tui):
    live_tui()
    out = tui_mcp._wait_for()
    assert out["status"] == "error"
    assert "at least one" in out["detail"]


def test_wait_for_match(live_tui):
    live_tui()
    out = tui_mcp._wait_for(contains="chat with")
    assert out["matched"] is True


def test_wait_for_timeout_returns_the_actual_screen(live_tui):
    live_tui()
    out = tui_mcp._wait_for(contains="never on this screen", timeout_ms=1000)
    assert out["status"] == "error"
    assert "Timed out after 1000 ms" in out["detail"]
    assert "chat with" in out["detail"]  # what WAS on screen
    assert "chat with gaia" in out["screen"]
    assert out["matched"] is False


def test_wait_uses_a_timeout_longer_than_the_wait(live_tui):
    fake = live_tui()
    seen = {}
    original = fake.request

    def _record(method, url, **kwargs):
        if url.endswith("/wait"):
            seen["timeout"] = kwargs.get("timeout")
        return original(method, url, **kwargs)

    fake.request = _record
    tui_mcp._wait_for(contains="chat with", timeout_ms=30000)
    assert seen["timeout"] == 30 + tui_mcp.WAIT_TIMEOUT_SLACK


# ── Argument validation ──────────────────────────────────────────────


def test_resize_rejects_sizes_the_server_would_400(live_tui):
    fake = live_tui()
    for cols, rows in ((10, 3), (600, 24), (80, 400)):
        out = tui_mcp._resize(cols, rows)
        assert out["status"] == "error", (cols, rows)
        assert "out of range" in out["detail"]
    assert ("POST", "/resize") not in fake.requests_seen


def test_send_keys_rejects_a_delay_the_server_would_400(live_tui):
    fake = live_tui()
    assert tui_mcp._send_keys(["enter"], delay_ms=9999)["status"] == "error"
    assert fake.keys_sent == []


def test_send_keys_rejects_a_batch_over_the_delay_budget(live_tui):
    """delay_ms × (count-1) > 10s would outlive the client's HTTP timeout."""
    fake = live_tui()
    out = tui_mcp._send_keys(["down"] * 20, delay_ms=2000)
    assert out["status"] == "error"
    assert "over the 10000 ms cap" in out["detail"]
    assert fake.keys_sent == []
    # Just inside the budget still goes through.
    assert tui_mcp._send_keys(["down"] * 6, delay_ms=2000).get("sent") == 6


def test_settled_is_passed_through_to_the_caller(live_tui):
    live_tui(settled=False)
    assert tui_mcp._send_keys(["down"])["settled"] is False
    assert tui_mcp._send_text("hi")["settled"] is False
    assert tui_mcp._resize(100, 30)["settled"] is False


def test_injection_is_refused_while_the_event_loop_is_not_running(live_tui):
    """503 not_running: a 200 there would be a lie — Send discards the message."""
    live_tui(running=False)
    out = tui_mcp._send_keys(["enter"])
    assert out["status"] == "error"
    assert "not accepting input" in out["detail"]
    assert "running is false" in out["detail"]  # the server's hint survives
    # Reads still work, and the summary leads with why keys would do nothing.
    summary = tui_mcp._status()["summary"]
    assert summary.startswith("NOT ACCEPTING INPUT")
    assert tui_mcp._screen()["screen"]


# ── Post-review hardening ────────────────────────────────────────────


def test_discover_rejects_a_non_loopback_host(tmp_path, monkeypatch):
    """The bearer token must never leave loopback, whatever the file claims."""
    write_control(tmp_path, monkeypatch, host="10.0.0.7")
    set_pid_alive(monkeypatch, True)

    def _explode(*a, **k):  # the probe must not run at all
        raise AssertionError("a request was made to a non-loopback host")

    monkeypatch.setattr(tui_mcp.requests, "get", _explode)

    info, error = tui_mcp.discover()
    assert info is None
    assert "non-loopback host" in error["detail"]
    assert "10.0.0.7" in error["detail"]
    assert TOKEN not in error["detail"]


def test_request_layer_failure_is_not_reported_as_a_non_json_response(monkeypatch):
    """requests' MissingSchema subclasses ValueError — it must not be swallowed.

    Sharing one ``except ValueError`` between the transport and the JSON decode
    reports a malformed control file as "the server returned a non-JSON
    response", sending the reader to debug a TUI that is fine.
    """

    class _BadTransport:
        exceptions = requests.exceptions

        @staticmethod
        def request(*a, **k):
            raise requests.exceptions.MissingSchema("Invalid URL 'http://:0'")

    monkeypatch.setattr(tui_mcp, "requests", _BadTransport)
    out = tui_mcp._request(
        {"host": "127.0.0.1", "port": PORT, "token": TOKEN}, "get", "/status"
    )
    assert out["status"] == "error"
    assert "not JSON" not in out["detail"]
    assert "Invalid URL" in out["detail"]


def test_start_hint_names_a_runnable_command():
    """A remedy the reader cannot type is not a remedy."""
    assert "gaia tui --control" in tui_mcp.START_HINT
    assert "./tui/bin/gaia --control" in tui_mcp.START_HINT
    assert "gaia tui --control" in tui_mcp.RESTART_HINT
