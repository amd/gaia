# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for email connector grant + scope consent in the Agent UI (#1770).

Covers:
- Scope resolution: installed:email REQUIRED_CONNECTORS returns the full
  Google+Microsoft scope set independent of chat-agent activation.
- Negative (a): connector connected but installed:email not granted →
  structured AGENT_NOT_GRANTED (not 500).
- Negative (b): granted mail scopes but missing calendar.events → scope-guard
  path reports CONNECTION_MISSING_SCOPES; triage/draft/send mail-only still pass.
- Negative (c): no mailbox connected → 503 from get_send_backend
  (the fail-loud guard on the #1768-mounted /v1/email surface).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("gaia_agent_email")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.outlook_scopes import (  # noqa: E402
    OUTLOOK_CALENDAR_SCOPES,
    OUTLOOK_MAIL_SCOPES,
)
from gaia_agent_email.scopes import (  # noqa: E402
    ALL_SCOPES,
    CALENDAR_SCOPES,
    GMAIL_SCOPES,
)

from gaia.agents.registry import AgentRegistration, AgentRegistry  # noqa: E402
from gaia.connectors.context import _agent_context  # noqa: E402
from gaia.connectors.errors import AuthRequiredError  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _email_required_connections() -> list:
    """Return the REQUIRED_CONNECTORS for installed:email (from the live class)."""
    return list(EmailTriageAgent.REQUIRED_CONNECTORS)


# ---------------------------------------------------------------------------
# Scope resolution (positive) — independent of chat-agent activation
# ---------------------------------------------------------------------------


class TestScopeResolution:
    """installed:email REQUIRED_CONNECTORS returns full scope sets for both
    providers, resolved from the registered agent class — NOT from any active
    chat-session context."""

    def test_google_scopes_present(self):
        """Resolved Google scopes include all Gmail + Calendar scopes."""
        reqs = _email_required_connections()
        google_req = next((r for r in reqs if r.connector_id == "google"), None)
        assert (
            google_req is not None
        ), "No Google ConnectorRequirement in REQUIRED_CONNECTORS"

        google_scopes = set(google_req.scopes)
        for scope in GMAIL_SCOPES:
            assert (
                scope in google_scopes
            ), f"Missing Gmail scope {scope!r} in REQUIRED_CONNECTORS"
        for scope in CALENDAR_SCOPES:
            assert (
                scope in google_scopes
            ), f"Missing Calendar scope {scope!r} in REQUIRED_CONNECTORS"

    def test_google_scopes_exact(self):
        """Google scopes are exactly ALL_SCOPES — no extras, no gaps."""
        reqs = _email_required_connections()
        google_req = next(r for r in reqs if r.connector_id == "google")
        assert set(google_req.scopes) == set(ALL_SCOPES), (
            f"Google scopes mismatch: got {sorted(google_req.scopes)}, "
            f"expected {sorted(ALL_SCOPES)}"
        )

    def test_microsoft_scopes_present(self):
        """Resolved Microsoft scopes include mail + calendar scopes."""
        reqs = _email_required_connections()
        ms_req = next((r for r in reqs if r.connector_id == "microsoft"), None)
        assert (
            ms_req is not None
        ), "No Microsoft ConnectorRequirement in REQUIRED_CONNECTORS"

        ms_scopes = set(ms_req.scopes)
        for scope in OUTLOOK_MAIL_SCOPES:
            assert scope in ms_scopes, f"Missing Outlook mail scope {scope!r}"
        for scope in OUTLOOK_CALENDAR_SCOPES:
            assert scope in ms_scopes, f"Missing Outlook calendar scope {scope!r}"

    def test_microsoft_scopes_exact(self):
        """Microsoft scopes are exactly OUTLOOK_MAIL_SCOPES + OUTLOOK_CALENDAR_SCOPES."""
        reqs = _email_required_connections()
        ms_req = next(r for r in reqs if r.connector_id == "microsoft")
        expected = set(OUTLOOK_MAIL_SCOPES + OUTLOOK_CALENDAR_SCOPES)
        assert set(ms_req.scopes) == expected, (
            f"Microsoft scopes mismatch: got {sorted(ms_req.scopes)}, "
            f"expected {sorted(expected)}"
        )

    def test_scope_resolution_independent_of_chat_activation(self):
        """Scope requirements come from the agent class, NOT from an active chat session.

        Simulates the common case where a user is on the chat tab (no email agent
        active) but opens Settings → Connectors and sees the email agent's
        requirements correctly listed.
        """
        # Build an isolated registry with only the email agent installed
        # (no chat session active, no chat agent selected)
        registry = AgentRegistry.__new__(AgentRegistry)
        registry._agents = {}
        registry._logger = MagicMock()

        # Replicate the installed-agent registration path from
        # AgentRegistry._load_entry_point_registration
        email_reg = AgentRegistration(
            id="email",
            name="Email Triage",
            description="Email triage agent",
            source="installed",
            conversation_starters=[],
            factory=lambda **kw: None,
            agent_dir=None,
            models=[],
            required_connections=list(EmailTriageAgent.REQUIRED_CONNECTORS),
            namespaced_agent_id="installed:email",
        )
        registry._agents["email"] = email_reg

        # Verify scope resolution goes through the registry registration —
        # independent of any chat session context variable
        all_regs = registry.list()
        email_entry = next(
            r for r in all_regs if r.namespaced_agent_id == "installed:email"
        )
        assert len(email_entry.required_connections) == 2  # Google + Microsoft

        google_req = next(
            r for r in email_entry.required_connections if r.connector_id == "google"
        )
        ms_req = next(
            r for r in email_entry.required_connections if r.connector_id == "microsoft"
        )

        assert set(google_req.scopes) == set(ALL_SCOPES)
        assert set(ms_req.scopes) == set(OUTLOOK_MAIL_SCOPES + OUTLOOK_CALENDAR_SCOPES)

    def test_required_connectors_has_both_providers(self):
        """REQUIRED_CONNECTORS declares exactly two providers: google and microsoft."""
        reqs = _email_required_connections()
        connector_ids = {r.connector_id for r in reqs}
        assert (
            "google" in connector_ids
        ), "Google provider missing from REQUIRED_CONNECTORS"
        assert (
            "microsoft" in connector_ids
        ), "Microsoft provider missing from REQUIRED_CONNECTORS"
        assert len(reqs) == 2, (
            f"Expected exactly 2 REQUIRED_CONNECTORS entries (google + microsoft), "
            f"got {len(reqs)}: {[r.connector_id for r in reqs]}"
        )


# ---------------------------------------------------------------------------
# Negative (a): connector connected but installed:email not granted → AGENT_NOT_GRANTED
# ---------------------------------------------------------------------------


class TestAgentNotGranted:
    """connector connected (google/microsoft) but installed:email has no grant
    → AuthRequiredError.Reason.AGENT_NOT_GRANTED raised, not 500."""

    def test_google_connected_no_grant_raises_agent_not_granted(self):
        """get_access_token_sync for google raises AGENT_NOT_GRANTED when no grant exists.

        Uses the sync variant to avoid asyncio complications in pytest.
        Patches check_agent_grant at its import site in api.py so the eager
        grant-check fires before any network call.
        """
        from gaia.connectors.api import get_access_token_sync

        # installed:email agent context — as in production tool calls
        with _agent_context("installed:email"):
            # Patch at the import site in api.py (where it's called)
            with patch("gaia.connectors.api.check_agent_grant", return_value=False):
                with pytest.raises(AuthRequiredError) as exc_info:
                    get_access_token_sync(
                        provider="google",
                        scopes=list(GMAIL_SCOPES),
                    )
        err = exc_info.value
        assert (
            err.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED
        ), f"Expected AGENT_NOT_GRANTED, got {err.reason}"
        assert err.agent_id == "installed:email"
        assert err.provider == "google"

    def test_microsoft_connected_no_grant_raises_agent_not_granted(self):
        """get_access_token_sync for microsoft raises AGENT_NOT_GRANTED when no grant exists."""
        from gaia.connectors.api import get_access_token_sync

        with _agent_context("installed:email"):
            with patch("gaia.connectors.api.check_agent_grant", return_value=False):
                with pytest.raises(AuthRequiredError) as exc_info:
                    get_access_token_sync(
                        provider="microsoft",
                        scopes=list(OUTLOOK_MAIL_SCOPES),
                    )
        err = exc_info.value
        assert err.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED
        assert err.agent_id == "installed:email"
        assert err.provider == "microsoft"

    def test_agent_not_granted_error_is_not_generic_500(self):
        """AGENT_NOT_GRANTED maps to 403 (not 500) via _raise_http_for."""
        from gaia.ui.routers.connectors import _raise_http_for

        err = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="installed:email",
            missing_scopes=list(GMAIL_SCOPES),
        )
        http_exc = _raise_http_for(err)
        assert (
            http_exc.status_code == 403
        ), f"AGENT_NOT_GRANTED must map to 403, got {http_exc.status_code}"
        detail = http_exc.detail
        assert detail.get("error") == "agent_not_granted"
        assert "installed:email" in str(detail.get("agent_id", ""))
        assert "google" in str(detail.get("connector_id", ""))

    def test_agent_not_granted_without_context_bypasses_grant_check(self):
        """When no agent context is set, the grant check is bypassed (None agent_id).

        This documents the escape hatch: CLI/debug callers without a context
        are not subject to the grant gate.
        """
        from gaia.connectors.api import get_access_token_sync
        from gaia.connectors.context import current_agent_id

        # No _agent_context active
        assert current_agent_id() is None

        # Without a context, check_agent_grant is not called even if no grant exists.
        # The call will fail at load_connection (no stored connection) — but NOT
        # at the grant check stage.
        with patch(
            "gaia.connectors.api.check_agent_grant", return_value=False
        ) as mock_grant:
            with patch("gaia.connectors.api.get_provider"):
                with patch("gaia.connectors.api.load_connection", return_value=None):
                    with pytest.raises(AuthRequiredError) as exc_info:
                        get_access_token_sync(
                            provider="google", scopes=list(GMAIL_SCOPES)
                        )

        # The error should be NOT_CONNECTED (from load_connection → None),
        # not AGENT_NOT_GRANTED.
        assert exc_info.value.reason is AuthRequiredError.Reason.NOT_CONNECTED
        # check_agent_grant was NOT called (no agent context)
        mock_grant.assert_not_called()


# ---------------------------------------------------------------------------
# Negative (b): granted mail scopes but missing calendar.events
# ---------------------------------------------------------------------------


class TestMissingCalendarScope:
    """Granted gmail.modify + gmail.send but NOT calendar.events.

    The existing scope-guard plumbing reports CONNECTION_MISSING_SCOPES for
    calendar tool calls; triage/draft/send (which only need gmail.modify and
    gmail.send) still work.

    Calendar tools are agent-loop-only, NOT on the REST API — this is a
    REGRESSION test on the existing fail-loud plumbing.  No new agent logic.
    """

    MAIL_ONLY_SCOPES = list(GMAIL_SCOPES)  # gmail.modify + gmail.send
    CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"

    def test_missing_calendar_scope_raises_connection_missing_scopes(self):
        """get_access_token_sync for calendar.events with mail-only OAuth scopes
        raises CONNECTION_MISSING_SCOPES (not a silent empty return or 500).

        Simulates: installed:email is granted in the ledger (check_agent_grant
        passes), but the stored OAuth token only has gmail.modify + gmail.send —
        no calendar.events — so the OAuth scope coverage check fires.
        """
        from gaia.connectors.api import get_access_token_sync

        calendar_scope = self.CALENDAR_SCOPE

        # installed:email has a grant for the calendar scope in the grant ledger,
        # but the stored OAuth connection lacks the calendar.events scope.
        with _agent_context("installed:email"):
            # Patch at the import site in api.py — check_agent_grant returns True
            # (agent is granted), so we proceed to the OAuth scope coverage check.
            with patch("gaia.connectors.api.check_agent_grant", return_value=True):
                with patch("gaia.connectors.api.get_provider"):
                    with patch(
                        "gaia.connectors.api.load_connection",
                        return_value={
                            "scopes": self.MAIL_ONLY_SCOPES,
                            "account_email": "user@example.com",
                        },
                    ):
                        with pytest.raises(AuthRequiredError) as exc_info:
                            get_access_token_sync(
                                provider="google",
                                scopes=[calendar_scope],
                            )
        err = exc_info.value
        assert (
            err.reason is AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES
        ), f"Expected CONNECTION_MISSING_SCOPES, got {err.reason}"
        assert calendar_scope in err.missing_scopes

    def test_missing_calendar_scope_maps_to_403_with_missing_scopes(self):
        """CONNECTION_MISSING_SCOPES maps to 403 + missing_scopes payload via router."""
        from gaia.ui.routers.connectors import _raise_http_for

        calendar_scope = self.CALENDAR_SCOPE
        err = AuthRequiredError(
            AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES,
            provider="google",
            agent_id="installed:email",
            missing_scopes=[calendar_scope],
        )
        http_exc = _raise_http_for(err)
        # CONNECTION_MISSING_SCOPES is not NOT_CONNECTED/REAUTH_REQUIRED, so
        # it falls through to the 403 branch in _raise_http_for
        assert http_exc.status_code == 403
        detail = http_exc.detail
        assert calendar_scope in detail.get("missing_scopes", [])

    def test_mail_scopes_sufficient_for_grant_check(self):
        """Triage/draft/send (gmail.modify + gmail.send) pass grant check without calendar.

        check_agent_grant passes when the required scopes are a subset of the
        granted mail scopes.
        """
        from gaia.connectors.grants import check_agent_grant

        # Simulate ledger: installed:email has gmail.modify + gmail.send
        with patch(
            "gaia.connectors.grants.list_agent_grants",
            return_value={"installed:email": self.MAIL_ONLY_SCOPES},
        ):
            # gmail.modify alone — passes (triage read/organize)
            assert check_agent_grant(
                "google",
                "installed:email",
                ["https://www.googleapis.com/auth/gmail.modify"],
            )
            # gmail.send alone — passes (send/reply)
            assert check_agent_grant(
                "google",
                "installed:email",
                ["https://www.googleapis.com/auth/gmail.send"],
            )
            # calendar.events alone — fails (calendar tool guard fires)
            assert not check_agent_grant(
                "google",
                "installed:email",
                [self.CALENDAR_SCOPE],
            )

    def test_calendar_scope_required_for_full_email_required_connectors(self):
        """calendar.events is present in installed:email's REQUIRED_CONNECTORS Google entry.

        This ensures the Connectors panel consent dialog asks for the full
        scope set (including calendar) upfront on first connect.
        """
        reqs = _email_required_connections()
        google_req = next(r for r in reqs if r.connector_id == "google")
        assert self.CALENDAR_SCOPE in google_req.scopes, (
            f"calendar.events must be in REQUIRED_CONNECTORS Google scopes "
            f"so the consent dialog asks for it. Got: {google_req.scopes}"
        )


# ---------------------------------------------------------------------------
# Negative (c): no mailbox connected → 503 from get_send_backend
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ui_client():
    """TestClient for the UI backend without lifespan startup (#1297 hang guard)."""
    from gaia.ui.server import create_app

    app = create_app(db_path=":memory:")
    # Skip lifespan (connectors sync / MCP reload) — it hangs in bare test env (#1297)
    yield TestClient(app, raise_server_exceptions=True)


class TestNoMailboxConnected:
    """Absence of any connected mailbox → 503 (fail-loud, not 500/200/empty).

    Tests the get_send_backend guard in the email REST surface mounted at
    /v1/email by the #1768 router.
    """

    def test_get_send_backend_raises_http_503_no_mailbox(self):
        """get_send_backend raises HTTPException(503) when no mailbox connected."""
        from fastapi import HTTPException
        from gaia_agent_email.api_routes import get_send_backend

        with patch(
            "gaia_agent_email.api_routes.connected_mailbox_providers",
            return_value=[],
        ):
            with pytest.raises(HTTPException) as exc_info:
                get_send_backend()

        exc = exc_info.value
        assert exc.status_code == 503
        detail = exc.detail or ""
        assert (
            "mailbox" in detail.lower() or "connect" in detail.lower()
        ), f"503 detail should be actionable, got: {detail!r}"

    def test_email_health_always_200_via_ui_backend(self, ui_client):
        """GET /v1/email/health is always 200 — not gated by mailbox connection."""
        resp = ui_client.get("/v1/email/health")
        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"

    def test_triage_does_not_require_mailbox(self, ui_client):
        """POST /v1/email/triage works without a connected mailbox (analyzes payload only).

        Triage is pass-by-value — it receives the email in the request body
        and never reads from a live mailbox. A 503 here would be a regression.
        """
        from unittest.mock import patch

        from gaia_agent_email.contract import (
            EmailCategory,
            EmailTriageResponse,
            EmailTriageResult,
        )

        stub_resp = EmailTriageResponse(
            request_kind="single",
            result=EmailTriageResult(
                category=EmailCategory.FYI,
                summary="Test summary.",
                action_items=[],
            ),
        )

        with patch(
            "gaia_agent_email.api_routes.EmailTriageService.triage_request",
            return_value=stub_resp,
        ):
            payload = {
                "schema_version": "2.0",
                "payload": {
                    "kind": "single",
                    "principal": {"name": "User", "email": "user@example.com"},
                    "message": {
                        "message_id": "msg-001",
                        "from": {"name": "Sender", "email": "sender@example.com"},
                        "subject": "Hello",
                        "body": "Just a quick note.",
                    },
                },
            }
            resp = ui_client.post("/v1/email/triage", json=payload)

        assert (
            resp.status_code == 200
        ), f"Triage should succeed without a mailbox connection, got {resp.status_code}: {resp.text}"

    def test_email_routes_mounted_at_ui_backend(self, ui_client):
        """The #1768 email router is mounted at /v1/email on the UI backend."""
        resp = ui_client.get("/v1/email/version")
        assert resp.status_code == 200
        assert "apiVersion" in resp.json()
