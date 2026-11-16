# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.daemon.agent_control.relay_json`` (#2516).

Mirrors ``tests/unit/test_agent_query.py``'s ``ensure_agent`` coverage: the
daemon + requests layer is stubbed so these run without a live daemon or
sidecar.
"""

from __future__ import annotations

import pytest

from gaia.daemon.agent_control import relay_json
from gaia.daemon.errors import DaemonError
from gaia.daemon.instance import DaemonInstance


def _inst():
    return DaemonInstance(
        pid=1, port=2, token="DAEMON-TOKEN", host="127.0.0.1", api_version="1.1"
    )


def test_relay_json_uses_only_the_daemon_token(monkeypatch):
    """The relay call presents the daemon client token, never a sidecar bearer
    the CLI would otherwise have to invent or hold."""
    from gaia.daemon import client

    monkeypatch.setattr(client, "ensure_agent", lambda agent_id: _inst())

    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"level": "earn_trust", "enabled": True}

    def _fake_request(method, url, headers=None, json=None, timeout=None):
        captured.update(method=method, url=url, headers=headers, json=json)
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "request", _fake_request)

    body = relay_json(
        "email",
        "POST",
        "agent/autonomy",
        json_body={"session_id": "cli", "level": "earn_trust"},
    )

    assert body == {"level": "earn_trust", "enabled": True}
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/email/agent/autonomy")
    assert captured["headers"]["Authorization"] == "Bearer DAEMON-TOKEN"
    assert captured["json"] == {"session_id": "cli", "level": "earn_trust"}


def test_relay_json_raises_loud_on_error_status(monkeypatch):
    from gaia.daemon import client

    monkeypatch.setattr(client, "ensure_agent", lambda agent_id: _inst())

    class _Resp:
        status_code = 409

        def json(self):
            return {"detail": "autonomy is off for session 'cli'"}

    import requests

    monkeypatch.setattr(requests, "request", lambda *a, **k: _Resp())

    with pytest.raises(DaemonError) as exc:
        relay_json(
            "email", "POST", "agent/autonomy/run", json_body={"session_id": "cli"}
        )
    assert "autonomy is off for session 'cli'" in str(exc.value)


def test_relay_json_raises_loud_on_transport_failure(monkeypatch):
    from gaia.daemon import client

    monkeypatch.setattr(client, "ensure_agent", lambda agent_id: _inst())

    import requests

    def _boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "request", _boom)

    with pytest.raises(DaemonError) as exc:
        relay_json("email", "GET", "agent/autonomy/cli")
    assert "could not reach the 'email' agent" in str(exc.value)


# --- read budget (#2618) -----------------------------------------------------


def _capture_timeout(monkeypatch):
    from gaia.daemon import client

    monkeypatch.setattr(client, "ensure_agent", lambda agent_id: _inst())
    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {}

    def _fake_request(method, url, headers=None, json=None, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "request", _fake_request)
    return seen


def test_read_budget_outlasts_the_daemon_relay_by_default(monkeypatch):
    from gaia.daemon.timeouts import DEFAULT_AGENT_READ_TIMEOUT

    monkeypatch.delenv("GAIA_AGENT_TOOL_TIMEOUT", raising=False)
    seen = _capture_timeout(monkeypatch)
    relay_json("email", "POST", "agent/autonomy/run", json_body={})
    connect, read = seen["timeout"]
    assert connect == 10.0
    assert read > DEFAULT_AGENT_READ_TIMEOUT > 120


def test_read_budget_follows_the_shared_timeout_setting(monkeypatch):
    from gaia.daemon.timeouts import agent_read_timeout

    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "1200")
    seen = _capture_timeout(monkeypatch)
    relay_json("email", "POST", "agent/autonomy/run", json_body={})
    assert seen["timeout"][1] > agent_read_timeout() >= 1200


@pytest.mark.parametrize("bad", ["soon", "0", "-30"])
def test_a_bad_timeout_setting_fails_before_any_request(monkeypatch, bad):
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", bad)
    seen = _capture_timeout(monkeypatch)
    with pytest.raises(DaemonError, match="GAIA_AGENT_TOOL_TIMEOUT"):
        relay_json("email", "GET", "agent/autonomy/cli")
    assert seen == {}


def test_read_timeout_is_its_own_error_saying_the_work_may_continue(monkeypatch):
    from gaia.daemon import client
    from gaia.daemon.agent_control import AgentResponseTimeoutError

    monkeypatch.setattr(client, "ensure_agent", lambda agent_id: _inst())
    import requests

    def _slow(*a, **k):
        raise requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(requests, "request", _slow)
    with pytest.raises(AgentResponseTimeoutError) as exc:
        relay_json("email", "POST", "agent/autonomy/run", json_body={})
    assert "may still be working" in str(exc.value)
    assert "GAIA_AGENT_TOOL_TIMEOUT" in str(exc.value)


def test_a_connect_timeout_is_not_reported_as_slow_work(monkeypatch):
    from gaia.daemon import client
    from gaia.daemon.agent_control import AgentResponseTimeoutError

    monkeypatch.setattr(client, "ensure_agent", lambda agent_id: _inst())
    import requests

    def _down(*a, **k):
        raise requests.exceptions.ConnectTimeout("no route")

    monkeypatch.setattr(requests, "request", _down)
    with pytest.raises(DaemonError) as exc:
        relay_json("email", "GET", "agent/autonomy/cli")
    assert not isinstance(exc.value, AgentResponseTimeoutError)
    assert "could not reach" in str(exc.value)


def test_http_errors_carry_their_status_code(monkeypatch):
    from gaia.daemon import client
    from gaia.daemon.agent_control import AgentHTTPError

    monkeypatch.setattr(client, "ensure_agent", lambda agent_id: _inst())

    class _Resp:
        status_code = 502

        def json(self):
            return {"detail": "sidecar did not answer"}

    import requests

    monkeypatch.setattr(requests, "request", lambda *a, **k: _Resp())
    with pytest.raises(AgentHTTPError) as exc:
        relay_json("email", "POST", "agent/autonomy/run", json_body={})
    assert exc.value.status_code == 502
