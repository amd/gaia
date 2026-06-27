# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EmailSidecarProxy: forward live routes unchanged; gate not-yet-built routes."""

import importlib.util

import pytest

from gaia.ui.email_sidecar.errors import RouteNotAvailableError
from gaia.ui.email_sidecar.proxy import EmailSidecarProxy


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, payload):
        self._payload = payload
        self.posts = []
        self.gets = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return _Resp(self._payload)

    def get(self, url, timeout=None):
        self.gets.append(url)
        return _Resp(self._payload)


def test_triage_forwards_and_returns_envelope_unchanged():
    envelope = {"request_kind": "single", "result": {"category": "primary"}}
    sess = _Session(envelope)
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    out = proxy.triage({"payload": {"kind": "single"}})
    assert out == envelope
    assert sess.posts[0][0] == "http://127.0.0.1:9100/v1/email/triage"
    assert sess.posts[0][1] == {"payload": {"kind": "single"}}


def test_base_url_trailing_slash_normalized():
    sess = _Session({"ok": True})
    proxy = EmailSidecarProxy("http://127.0.0.1:9100/", session=sess)
    proxy.triage({})
    assert sess.posts[0][0] == "http://127.0.0.1:9100/v1/email/triage"


def test_draft_and_send_target_correct_routes():
    sess = _Session({"ok": True})
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    proxy.draft({"to": [], "subject": "s", "body": "b"})
    proxy.send({"to": [], "subject": "s", "body": "b", "confirmation_token": "t"})
    assert [u for u, _ in sess.posts] == [
        "http://127.0.0.1:9100/v1/email/draft",
        "http://127.0.0.1:9100/v1/email/send",
    ]


def test_health_and_version_get_routes():
    sess = _Session({"status": "ok"})
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    assert proxy.health()["status"] == "ok"
    proxy.version()
    assert sess.gets == [
        "http://127.0.0.1:9100/health",
        "http://127.0.0.1:9100/version",
    ]


@pytest.mark.parametrize(
    "method,issue",
    [
        ("pre_scan_inbox", "pre-scan"),
        ("search_inbox", "1781"),
        ("archive", "1779"),
        ("quarantine", "1779"),
        ("calendar", "1780"),
    ],
)
def test_future_routes_gated(method, issue):
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=_Session({}))
    with pytest.raises(RouteNotAvailableError, match=issue):
        getattr(proxy, method)()


@pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent_email") is None
    or importlib.util.find_spec("uvicorn") is None,
    reason="email agent + uvicorn required for live proxy round-trip",
)
def test_live_proxy_health_and_version_roundtrip(monkeypatch):
    # Boundary check (mocks prove 'we called it', not 'the call is valid'): spawn
    # a real dev-mode sidecar and round-trip /health + /version through the proxy
    # over real HTTP to prove the wire contract holds.
    from gaia.ui.email_sidecar.manager import EmailSidecarManager

    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    m = EmailSidecarManager(health_timeout=60.0)
    base = m.start()
    try:
        proxy = EmailSidecarProxy(base, timeout=10.0)
        assert proxy.health()["status"] == "ok"
        version = proxy.version()
        assert "apiVersion" in version and "agentVersion" in version
    finally:
        m.shutdown()
