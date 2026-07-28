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
