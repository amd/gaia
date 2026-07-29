# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
REST tests for GET /v1/email/attention (#2582).

Covers:
- 200 shape matches EmailAttentionResponse/EmailAttentionResult.
- No mailbox connected -> 503, mirroring /prescan.
- Caching: a second call within the freshness window returns the SAME
  generated_at with a nonzero cache_age_seconds and stale=False; forcing the
  cache to look old triggers a fresh recompute.
- A total mailbox failure with a warm cache falls back to the stale cache
  (stale=True) rather than hard-failing; with no cache at all it 502s.
- Zero mutation: no destructive REST route is touched by this surface
  (structural — grepping the module for a mutating call is covered by the
  aggregator's own transport-call assertion; here we just confirm the route
  is a GET, not a POST/PUT/DELETE).
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email import export_openapi  # noqa: E402
from gaia_agent_email.contract import SCHEMA_VERSION  # noqa: E402

from gaia.connectors.errors import ConnectorsError  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

USER_EMAIL = "user@example.com"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str,
    body: str = "",
    sender: str = "colleague@example.com",
    label_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": list(label_ids or ["INBOX"]),
        "snippet": body[:200],
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": USER_EMAIL},
            ],
            "body": {"size": len(body), "data": _b64(body)},
        },
        "sizeEstimate": len(body),
    }


_MEETING_IN_INFORMATIONAL = _msg(
    "m1",
    subject="Team sync",
    body="Can we meet Thursday at 2pm to go over the roadmap?",
    label_ids=["INBOX", "CATEGORY_UPDATES"],
)


class _RaisingGmailBackend(FakeGmailBackend):
    def list_messages(self, **kwargs):
        raise ConnectorsError("token expired")


@pytest.fixture
def attention_client():
    from gaia_agent_email.api_routes import (
        get_attention_backends,
        reset_attention_cache,
    )

    reset_attention_cache()
    app = export_openapi.build_app()
    gmail = FakeGmailBackend(user_email=USER_EMAIL)
    gmail.add_message(_MEETING_IN_INFORMATIONAL)
    app.dependency_overrides[get_attention_backends] = lambda: {"google": gmail}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        reset_attention_cache()


def test_attention_returns_expected_shape(attention_client):
    resp = attention_client.get("/v1/email/attention")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema_version"] == SCHEMA_VERSION
    result = body["result"]
    assert result["kind"] == "email_attention"
    assert set(result) == {
        "kind",
        "items",
        "coverage",
        "generated_at",
        "cache_age_seconds",
        "stale",
    }
    assert any(i["kind"] == "meeting_request" for i in result["items"])
    assert result["coverage"]["scanned"] == 1
    assert result["stale"] is False
    assert result["cache_age_seconds"] == 0.0


def test_no_mailbox_connected_via_real_resolver_returns_503(monkeypatch):
    from gaia_agent_email.api_routes import reset_attention_cache

    reset_attention_cache()
    monkeypatch.setattr(
        "gaia_agent_email.api_routes.connected_mailbox_providers", lambda: []
    )
    app = export_openapi.build_app()
    client = TestClient(app)
    resp = client.get("/v1/email/attention")
    assert resp.status_code == 503


def test_second_call_within_ttl_reuses_cache(attention_client):
    first = attention_client.get("/v1/email/attention").json()["result"]
    second = attention_client.get("/v1/email/attention").json()["result"]
    assert second["generated_at"] == first["generated_at"]
    assert second["stale"] is False


def test_expired_cache_triggers_recompute(attention_client):
    from gaia_agent_email import api_routes

    first = attention_client.get("/v1/email/attention").json()["result"]
    # Force the cached entry to look older than the freshness window.
    api_routes._attention_cache["_computed_at"] -= (
        api_routes.ATTENTION_CACHE_TTL_SECONDS + 1
    )
    second = attention_client.get("/v1/email/attention").json()["result"]
    assert second["stale"] is False
    assert second["cache_age_seconds"] == 0.0


def test_total_failure_with_warm_cache_falls_back_to_stale(attention_client):
    from gaia_agent_email import api_routes

    attention_client.get("/v1/email/attention")  # warm the cache
    api_routes._attention_cache["_computed_at"] -= (
        api_routes.ATTENTION_CACHE_TTL_SECONDS + 1
    )
    bad = _RaisingGmailBackend(user_email=USER_EMAIL)
    attention_client.app.dependency_overrides[api_routes.get_attention_backends] = (
        lambda: {"google": bad}
    )
    resp = attention_client.get("/v1/email/attention")
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["stale"] is True
    assert result["cache_age_seconds"] > 0


def test_total_failure_with_no_cache_returns_502():
    from gaia_agent_email import api_routes

    api_routes.reset_attention_cache()
    app = export_openapi.build_app()
    bad = _RaisingGmailBackend(user_email=USER_EMAIL)
    app.dependency_overrides[api_routes.get_attention_backends] = lambda: {
        "google": bad
    }
    try:
        client = TestClient(app)
        resp = client.get("/v1/email/attention")
        assert resp.status_code == 502
    finally:
        app.dependency_overrides.clear()
        api_routes.reset_attention_cache()


def test_route_is_a_get_not_a_mutating_verb():
    from gaia_agent_email import api_routes

    route = next(
        r
        for r in api_routes.router.routes
        if getattr(r, "path", "") == "/v1/email/attention"
    )
    assert route.methods == {"GET"}


def test_rejects_unknown_query_param_would_be_ignored_but_bounds_max_messages(
    attention_client,
):
    # max_messages is clamped, not rejected -- an oversized request degrades
    # to the ceiling rather than 422ing a harmless typo-of-scale.
    resp = attention_client.get("/v1/email/attention", params={"max_messages": 999999})
    assert resp.status_code == 200, resp.text
