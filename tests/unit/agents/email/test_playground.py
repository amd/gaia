# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the localhost-only email-agent playground page (#1796).

The page's local-only guarantee is structural, so the tests assert the structure:
the page makes no external requests, and the route ships a CSP that pins egress to
``'self'``. (Verifying call *validity* at the boundary, per CLAUDE.md — not just
"the route returns 200".)
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("gaia_agent_email")

import gaia_agent_email.api_routes as email_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gaia_agent_email.playground_html import render_playground_html


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(email_routes.router)
    return TestClient(app)


class TestPlaygroundHtml:
    def test_is_self_contained_html(self):
        html = render_playground_html()
        assert html.lstrip().startswith("<!doctype html>")
        assert "GAIA Email Agent" in html

    def test_pulls_in_no_external_resources(self):
        # Offline + no-egress: nothing loads from a CDN/webfont/remote script.
        html = render_playground_html()
        for forbidden in (
            "googleapis",
            "gstatic",
            "cdnjs",
            "unpkg",
            "<script src",
            "<link ",
            "http://",  # all sidecar fetches are relative same-origin paths
        ):
            assert forbidden not in html, f"unexpected external resource: {forbidden!r}"

    def test_only_calls_relative_sidecar_paths(self):
        # Assert the EXACT endpoints the JS calls (not substrings — "/version"
        # is a substring of "/v1/email/version", which would pass by accident).
        html = render_playground_html()
        for path in (
            '"/v1/email/version"',
            '"/v1/email/triage"',
            '"/v1/email/triage/batch"',
            '"/v1/email/draft"',
            '"/v1/email/init"',
            '"/v1/email/send"',
            '"/v1/email/connectors"',
        ):
            assert path in html, f"expected the page to call {path}"

    def test_no_innerhtml_sink(self):
        # The local-only XSS guarantee rests on textContent-only DOM writes: a
        # triaged email body / model string must never be able to inject markup.
        # Pin it — the page must contain ZERO `.innerHTML` assignments.
        assert ".innerHTML" not in render_playground_html()

    def test_connectors_section_degrades_gracefully(self):
        # The sidecar always mounts /v1/email/connectors, but if the email router
        # is ever mounted somewhere that did not (e.g. embedded in the Agent UI),
        # the page must degrade to an explainer rather than break.
        html = render_playground_html()
        assert "Connectors" in html
        assert "conn-providers" in html
        assert "conn-unavailable" in html
        assert "gaia connectors" in html

    def test_send_has_always_present_mailbox_dropdown(self):
        # Send picks the mailbox via an always-present dropdown, so a 2+ mailbox
        # setup never hits the send API's "can't choose" 400.
        html = render_playground_html()
        assert 'id="send-from"' in html
        assert "populateSend" in html


class TestPlaygroundRoute:
    def test_serves_html_200(self, client):
        r = client.get("/v1/email/playground")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "Playground" in r.text

    def test_csp_pins_egress_to_self(self, client):
        # This header is THE local-only guarantee: the browser refuses any
        # non-local fetch, so email content can't leave the machine.
        csp = client.get("/v1/email/playground").headers.get(
            "content-security-policy", ""
        )
        assert "default-src 'none'" in csp
        assert "connect-src 'self'" in csp
        # The directives that actually close bypasses — a regression that
        # loosened connect-src to a wildcard, or added unsafe-eval, must fail.
        assert "connect-src *" not in csp
        assert "unsafe-eval" not in csp
        assert "base-uri 'none'" in csp
        assert "form-action 'none'" in csp

    def test_excluded_from_openapi_schema(self, client):
        paths = client.get("/openapi.json").json().get("paths", {})
        assert "/v1/email/playground" not in paths
