# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unexpected exceptions on /v1/email/* must return structured JSON (#3000).

Starlette's default is a plain-text 500. The sidecar installs an app-level
handler that logs a correlation id and returns JSON without leaking internals.
Connector AuthRequiredError on search must stay 403 with that error's detail.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("gaia_agent_email")

import gaia_agent_email.api_routes as email_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gaia_agent_email.server import install_email_unhandled_exception_handler
from gaia.connectors.errors import AuthRequiredError


def _build_client(*, backend=None):
    app = FastAPI()
    app.include_router(email_routes.router)
    install_email_unhandled_exception_handler(app)
    app.dependency_overrides[email_routes.get_search_backend] = lambda: (
        object() if backend is None else backend
    )
    return TestClient(app, raise_server_exceptions=False)


def _search(client):
    return client.post("/v1/email/search", json={"query": "invoice"})


class TestUnhandledEmailExceptionJson:
    def test_unexpected_keyerror_becomes_json_500(self, monkeypatch):
        def _raise_keyerror(*_args, **_kwargs):
            raise KeyError("graph_payload_missing")

        monkeypatch.setattr(email_routes, "_search_inbox", _raise_keyerror)
        client = _build_client()
        resp = _search(client)
        assert resp.status_code == 500, resp.text
        body = resp.json()
        assert body["detail"] == "Internal server error"
        error_id = body.get("error_id")
        assert isinstance(error_id, str) and error_id
        assert "Traceback" not in resp.text
        assert "KeyError" not in resp.text
        assert "graph_payload_missing" not in resp.text

    def test_auth_required_error_stays_403(self):
        exc = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="installed:email",
        )

        class _FakeBackend:
            def list_messages(self, **_kw):
                raise exc

        client = _build_client(backend=_FakeBackend())
        resp = _search(client)
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == str(exc)
        assert "installed:email" in resp.json()["detail"]
