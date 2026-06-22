# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Verify that POST /v1/email/triage is reachable on the Agent UI FastAPI app.

Path 2 (in-process) mounts the gaia-agent-email REST router inside the UI
server (gaia.ui.server.create_app).  This test confirms the mount and validates
the schema-2.0 response shape without a running Lemonade server.
"""

from __future__ import annotations

import json
import types

import pytest

# ---------------------------------------------------------------------------
# Skip the whole module when gaia_agent_email or the UI server isn't available.
# ---------------------------------------------------------------------------

pytest.importorskip(
    "gaia_agent_email",
    reason="gaia-agent-email wheel not installed; skip UI email mount test",
)

try:
    from fastapi.testclient import TestClient

    from gaia.ui.server import create_app
except ImportError as _e:
    pytest.skip(f"UI server dependencies not available: {_e}", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers — reuse the same payload factories as tests/test_api.py
# ---------------------------------------------------------------------------


def _single_email_payload(
    *,
    subject: str = "Can you review the Q3 budget?",
    body: str = "Hi, please review the attached Q3 budget and reply by Friday.",
    sender_email: str = "alice@example.com",
    principal_email: str = "me@example.com",
) -> dict:
    """Contract-valid SingleEmailInput request envelope."""
    return {
        "payload": {
            "kind": "single",
            "principal": {"email": principal_email},
            "message": {
                "message_id": "m-1",
                "from": {"name": "Alice", "email": sender_email},
                "to": [{"email": principal_email}],
                "subject": subject,
                "body": body,
            },
        }
    }


# ---------------------------------------------------------------------------
# Fixture — UI app with mocked LLM so no Lemonade required
# ---------------------------------------------------------------------------


@pytest.fixture()
def ui_client(monkeypatch):
    """TestClient for the UI app with the email triage LLM stubbed out."""
    from gaia_agent_email.api_routes import EmailTriageService

    class _FakeChat:
        def send_messages(self, messages, system_prompt="", **kwargs):
            resp = types.SimpleNamespace()
            content = messages[0].get("content", "") if messages else ""
            if "Classify" in content:
                resp.text = json.dumps(
                    {
                        "category": "NEEDS_RESPONSE",
                        "confidence": 0.9,
                        "reasoning": "test",
                    }
                )
            else:
                resp.text = "Alice is asking for a budget review by Friday."
            return resp

    monkeypatch.setattr(
        EmailTriageService, "_build_llm_chat", lambda self, **kw: _FakeChat()
    )

    app = create_app(db_path=":memory:")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUIEmailMount:
    """POST /v1/email/triage is reachable on the Agent UI app (Path 2)."""

    def test_route_is_mounted(self, ui_client):
        """The endpoint returns 200, not 404."""
        resp = ui_client.post("/v1/email/triage", json=_single_email_payload())
        assert (
            resp.status_code == 200
        ), f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_schema_v2_result_shape(self, ui_client):
        """Response has the schema-2.0 fields: category, summary, is_phishing,
        suggested_action."""
        from gaia_agent_email.contract import SCHEMA_VERSION, parse_response

        resp = ui_client.post("/v1/email/triage", json=_single_email_payload())
        assert resp.status_code == 200, resp.text

        # parse_response uses extra="forbid" — any shape drift raises here.
        parsed = parse_response(resp.json())
        assert parsed.schema_version == SCHEMA_VERSION
        assert parsed.request_kind == "single"

        result = parsed.result
        # Core schema-2.0 fields per contract.py
        assert result.category is not None
        assert isinstance(result.summary, str) and result.summary
        assert isinstance(result.is_phishing, bool)
        assert result.suggested_action in ("reply", "none", "archive")

    def test_draft_proposed_for_inbound_email(self, ui_client):
        """An inbound email from a third party yields a draft back to that sender."""
        from gaia_agent_email.contract import parse_response

        resp = ui_client.post(
            "/v1/email/triage",
            json=_single_email_payload(sender_email="bob@example.com"),
        )
        assert resp.status_code == 200, resp.text
        parsed = parse_response(resp.json())
        assert parsed.result.draft is not None
        assert parsed.result.draft.to[0].email == "bob@example.com"
