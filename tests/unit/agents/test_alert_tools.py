# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""alert_owner: registered only under the daemon with agent alerts on, and it
sends only the reason, through the agent's own custody secret."""

import pytest

from gaia.agents.tools import alert_tools
from gaia.agents.tools.alert_tools import AlertToolsMixin, agent_alerts_allowed


class Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def under_daemon(monkeypatch):
    monkeypatch.setenv("GAIA_HOST_CUSTODY_URL", "http://127.0.0.1:9/")
    monkeypatch.setenv("GAIA_HOST_CUSTODY_SECRET", "s3cret")


def test_not_offered_outside_the_daemon(monkeypatch):
    monkeypatch.delenv("GAIA_HOST_CUSTODY_URL", raising=False)
    monkeypatch.delenv("GAIA_HOST_CUSTODY_SECRET", raising=False)
    assert agent_alerts_allowed() is False


def test_offered_only_when_the_user_allows_it(under_daemon, monkeypatch):
    import httpx

    seen = []

    def get(url, headers, timeout):
        seen.append((url, headers))
        return Response(200, {"allowed": False})

    monkeypatch.setattr(httpx, "get", get)
    assert agent_alerts_allowed() is False
    assert seen == [
        ("http://127.0.0.1:9/host/v1/alerts", {"Authorization": "Bearer s3cret"})
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Response(200, {"allowed": True}))
    assert agent_alerts_allowed() is True


def test_alert_sends_only_the_reason(under_daemon, monkeypatch):
    import httpx

    posted = []

    def post(url, json, headers, timeout):
        posted.append((url, json))
        return Response(200, {"raised": True, "alert_id": 3})

    monkeypatch.setattr(httpx, "post", post)
    out = AlertToolsMixin()._alert_owner("x" * 900)
    assert out["status"] == "success"
    [(url, body)] = posted
    assert url.endswith("/host/v1/alerts") and set(body) == {"reason"}
    assert len(body["reason"]) == 500


def test_alert_reports_a_setting_turned_off(under_daemon, monkeypatch):
    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: Response(200, {"raised": False, "message": "off"}),
    )
    assert AlertToolsMixin()._alert_owner("hi") == {
        "status": "not_sent",
        "message": "off",
    }


def test_alert_reports_an_unreachable_daemon(under_daemon, monkeypatch):
    import httpx

    def down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", down)
    assert AlertToolsMixin()._alert_owner("hi")["status"] == "error"
    assert alert_tools._custody() == ("http://127.0.0.1:9", "s3cret")
