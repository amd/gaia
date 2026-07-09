# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EmailSidecarProxy: forward the full schema-2.1 contract unchanged."""

import importlib.util

import pytest

from gaia.ui.email_sidecar.errors import SidecarHTTPError
from gaia.ui.email_sidecar.proxy import EmailSidecarProxy


class _Resp:
    def __init__(self, payload, status=200, *, raw_text=None, json_raises=False):
        self._payload, self.status_code = payload, status
        self.text = raw_text if raw_text is not None else str(payload)
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, payload, *, resp=None):
        self._payload = payload
        self._resp = resp  # pre-built _Resp to return (overrides payload)
        self.posts = []
        self.gets = []

    def _make(self):
        return self._resp if self._resp is not None else _Resp(self._payload)

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return self._make()

    def get(self, url, params=None, timeout=None):
        self.gets.append((url, params))
        return self._make()


def test_default_timeout_from_env(monkeypatch):
    # Fix F: no explicit timeout → reads GAIA_EMAIL_SIDECAR_TIMEOUT (default 300).
    monkeypatch.delenv("GAIA_EMAIL_SIDECAR_TIMEOUT", raising=False)
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=_Session({}))
    assert proxy.timeout == 300.0

    monkeypatch.setenv("GAIA_EMAIL_SIDECAR_TIMEOUT", "120")
    proxy2 = EmailSidecarProxy("http://127.0.0.1:9100", session=_Session({}))
    assert proxy2.timeout == 120.0


def test_explicit_timeout_overrides_env(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_SIDECAR_TIMEOUT", "999")
    proxy = EmailSidecarProxy(
        "http://127.0.0.1:9100", session=_Session({}), timeout=60.0
    )
    assert proxy.timeout == 60.0


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
        ("http://127.0.0.1:9100/health", None),
        ("http://127.0.0.1:9100/version", None),
    ]


def test_non_2xx_with_json_detail_raises_actionable_error():
    # The sidecar's actionable detail (e.g. Lemonade down) must survive, not be
    # flattened into a generic HTTPError.
    err = _Resp(
        {"detail": "local LLM triage failed: Lemonade not reachable"}, status=502
    )
    sess = _Session(None, resp=err)
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    with pytest.raises(SidecarHTTPError) as ei:
        proxy.triage({"payload": {}})
    assert ei.value.status_code == 502
    assert "Lemonade not reachable" in ei.value.detail
    assert "/v1/email/triage" in str(ei.value)


def test_non_2xx_non_json_body_uses_text():
    err = _Resp(None, status=500, raw_text="Internal Server Error", json_raises=True)
    sess = _Session(None, resp=err)
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    with pytest.raises(SidecarHTTPError) as ei:
        proxy.send({"to": []})
    assert ei.value.status_code == 500
    assert "Internal Server Error" in ei.value.detail


def test_403_send_gate_detail_preserved():
    err = _Resp(
        {"detail": "Send rejected: missing or invalid confirmation token"}, status=403
    )
    sess = _Session(None, resp=err)
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    with pytest.raises(SidecarHTTPError) as ei:
        proxy.send({"to": []})
    assert ei.value.status_code == 403
    assert "confirmation token" in ei.value.detail


@pytest.mark.parametrize(
    "method,path",
    [
        ("triage_batch", "/v1/email/triage/batch"),
        ("search_inbox", "/v1/email/search"),
        ("pre_scan_inbox", "/v1/email/prescan"),
        ("confirm", "/v1/email/confirm"),
        ("archive", "/v1/email/archive"),
        ("unarchive", "/v1/email/unarchive"),
        ("quarantine", "/v1/email/quarantine"),
        ("unquarantine", "/v1/email/unquarantine"),
        ("calendar_preview", "/v1/email/calendar/events/preview"),
        ("calendar_create", "/v1/email/calendar/events"),
        ("calendar_respond", "/v1/email/calendar/events/respond"),
    ],
)
def test_schema21_post_routes_forward_to_real_endpoints(method, path):
    # Previously-gated routes now FORWARD to the live schema-2.1 endpoints and
    # return the sidecar's envelope unchanged.
    envelope = {"ok": True, "echo": "payload"}
    sess = _Session(envelope)
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    out = getattr(proxy, method)({"x": 1})
    assert out == envelope
    assert sess.posts[0] == (f"http://127.0.0.1:9100{path}", {"x": 1})


def test_pre_scan_inbox_forwards_prescan_envelope_unchanged():
    # The card pipeline depends on this exact shape: /prescan returns
    # {"result": {"kind": "email_pre_scan", ...}} and the proxy passes it through
    # untouched so the chat tool can wrap result in the email_pre_scan envelope.
    envelope = {"result": {"kind": "email_pre_scan", "urgent": [], "actionable": []}}
    sess = _Session(envelope)
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    out = proxy.pre_scan_inbox({"max_messages": 25})
    assert out == envelope
    assert out["result"]["kind"] == "email_pre_scan"


def test_calendar_events_get_forwards_query_params():
    events = {"events": [{"id": "e1", "summary": "Standup"}]}
    sess = _Session(events)
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    out = proxy.calendar_events({"time_min": "2026-06-30T00:00:00Z"})
    assert out == events
    assert sess.gets[0] == (
        "http://127.0.0.1:9100/v1/email/calendar/events",
        {"time_min": "2026-06-30T00:00:00Z"},
    )


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


def _lemonade_up() -> bool:
    try:
        import requests

        from gaia.llm.lemonade_client import _get_lemonade_config

        _, _, base = _get_lemonade_config()
        return requests.get(base.rstrip("/") + "/health", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent_email") is None
    or importlib.util.find_spec("uvicorn") is None,
    reason="email agent + uvicorn required for live triage round-trip",
)
def test_live_triage_roundtrip_through_manager_proxy(monkeypatch):
    # End-to-end 'the call is valid' proof: real dev sidecar + the manager-owned
    # proxy + the FROZEN triage contract. With Lemonade up we get a structured
    # result; with it down we get the sidecar's actionable 502 surfaced verbatim
    # via SidecarHTTPError. Either outcome proves the wire + error path, not a mock.
    from gaia_agent_email.contract import (
        EmailAddress,
        EmailMessage,
        EmailTriageRequest,
        SingleEmailInput,
    )

    from gaia.ui.email_sidecar.errors import SidecarHTTPError
    from gaia.ui.email_sidecar.manager import EmailSidecarManager

    payload = SingleEmailInput(
        message=EmailMessage(
            message_id="msg-roundtrip-1",
            subject="Team lunch tomorrow?",
            from_=EmailAddress(email="alice@example.com"),
            body="Hey, are you joining us for lunch tomorrow at noon? Please reply by EOD.",
        ),
        principal=EmailAddress(email="bob@example.com"),
    )
    body = EmailTriageRequest(payload=payload).model_dump(by_alias=True, mode="json")

    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    with EmailSidecarManager(health_timeout=60.0) as m:
        proxy = m.proxy(timeout=180.0)
        if _lemonade_up():
            result = proxy.triage(body)
            assert result["request_kind"] == "single"
            assert "category" in result["result"]
            assert result["result"]["summary"]
        else:
            with pytest.raises(SidecarHTTPError) as ei:
                proxy.triage(body)
            assert ei.value.status_code == 502
            assert "triage failed" in ei.value.detail.lower()
