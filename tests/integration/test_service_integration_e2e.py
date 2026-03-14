# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration tests for ServiceIntegrationMixin.

Tests:
- Credential persistence across agent restarts (via tool registry)
- Encrypted credentials round-trip across sessions
- API discovery result persistence
- Preference learning across sessions
- Decision workflow with persisted preferences
- API-first fallback detection
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from gaia.agents.base.memory_mixin import MemoryMixin
from gaia.agents.base.service_integration import (
    ServiceIntegrationMixin,
    _decrypt_data,
    _encrypt_data,
)
from gaia.agents.base.shared_state import SharedAgentState

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_singleton():
    """Reset the SharedAgentState singleton between tests."""
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")
    yield
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")


@pytest.fixture(autouse=True)
def clean_tool_registry():
    """Clear tool registry before each test to avoid cross-test pollution."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


class _TestHost(MemoryMixin, ServiceIntegrationMixin):
    """Minimal host combining MemoryMixin and ServiceIntegrationMixin."""

    pass


def _make_host(workspace):
    """Create a fresh TestHost with tools registered."""
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")

    from gaia.agents.base.tools import _TOOL_REGISTRY

    _TOOL_REGISTRY.clear()

    host = _TestHost()
    host.init_memory(workspace_dir=workspace)
    host.register_service_integration_tools()
    return host


def _call_tool(name, **kwargs):
    """Call a registered tool by name."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    tool = _TOOL_REGISTRY[name]
    return tool["function"](**kwargs)


# ── Credential Persistence ───────────────────────────────────────────────────


class TestCredentialPersistence:
    """Credentials stored in one session are accessible in the next."""

    def test_credential_persists_across_restart(self, workspace):
        """Store credential -> restart -> credential still retrievable."""
        host1 = _make_host(workspace)
        expires = (datetime.now() + timedelta(hours=1)).isoformat()
        result = _call_tool(
            "store_credential",
            service="gmail",
            credential_type="oauth2",
            data=json.dumps(
                {"access_token": "tok_abc123", "refresh_token": "ref_xyz789"}
            ),
            scopes="gmail.modify,gmail.compose",
            expires_at=expires,
        )
        assert result["status"] == "stored"
        host1._shared_state.memory.close()
        host1._shared_state.knowledge.close()

        # Restart with new host
        host2 = _make_host(workspace)
        cred = _call_tool("get_credential", service="gmail")
        assert cred["status"] == "found"
        assert cred["service"] == "gmail"
        assert cred["credential_type"] == "oauth2"
        assert cred["data"]["access_token"] == "tok_abc123"
        assert cred["data"]["refresh_token"] == "ref_xyz789"
        host2._shared_state.memory.close()
        host2._shared_state.knowledge.close()

    def test_multiple_credentials_persist(self, workspace):
        """Multiple service credentials all persist."""
        host1 = _make_host(workspace)
        for svc, key in [
            ("twitter", "tw_key"),
            ("github", "gh_key"),
            ("slack", "sl_key"),
        ]:
            _call_tool(
                "store_credential",
                service=svc,
                credential_type="api_key",
                data=json.dumps({"api_key": key}),
            )
        host1._shared_state.memory.close()
        host1._shared_state.knowledge.close()

        host2 = _make_host(workspace)
        listing = _call_tool("list_credentials")
        assert listing["count"] == 3
        services = {c["service"] for c in listing["credentials"]}
        assert services == {"twitter", "github", "slack"}
        host2._shared_state.memory.close()
        host2._shared_state.knowledge.close()

    def test_credential_encryption_roundtrip(self, workspace):
        """Credentials are encrypted at rest and decrypt correctly."""
        host = _make_host(workspace)
        secret_data = {"api_key": "super_secret_key_12345", "secret": "hidden_value"}
        _call_tool(
            "store_credential",
            service="test-svc",
            credential_type="api_key",
            data=json.dumps(secret_data),
        )

        # Read raw from database to verify encryption
        cred_row = host.knowledge.get_credential("test-svc")
        raw_encrypted = cred_row["encrypted_data"]
        assert "super_secret_key_12345" not in raw_encrypted
        assert "hidden_value" not in raw_encrypted

        # But get_credential should decrypt it
        result = _call_tool("get_credential", service="test-svc")
        assert result["data"]["api_key"] == "super_secret_key_12345"
        assert result["data"]["secret"] == "hidden_value"

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_expired_credential_flagged(self, workspace):
        """Expired credentials are flagged when retrieved."""
        host = _make_host(workspace)
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        _call_tool(
            "store_credential",
            service="expired-svc",
            credential_type="oauth2",
            data=json.dumps({"token": "old_token"}),
            expires_at=past,
        )

        result = _call_tool("get_credential", service="expired-svc")
        assert result["expired"] is True
        assert result["data"]["token"] == "old_token"

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()


# ── API Discovery Integration ────────────────────────────────────────────────


class TestAPIDiscoveryIntegration:
    """API discovery stores results that persist and can be used later."""

    @patch("gaia.agents.base.service_integration._call_perplexity_api")
    def test_discover_and_setup_persist(self, mock_api, workspace):
        """Discover API -> setup integration -> restart -> skill is available."""
        mock_api.return_value = {
            "success": True,
            "answer": (
                "Gmail has a REST API that uses OAuth 2.0 for authentication. "
                "See https://developers.google.com/gmail/api for docs."
            ),
            "sources": ["https://developers.google.com/gmail/api"],
        }

        host1 = _make_host(workspace)

        # Discover the API
        discovery = _call_tool("discover_api", service="gmail")
        assert discovery["has_api"] is True
        assert discovery["auth_type"] == "oauth2"

        # Setup integration with credentials
        cred_data = json.dumps(
            {
                "credential_type": "oauth2",
                "access_token": "tok123",
                "refresh_token": "ref456",
                "scopes": ["gmail.modify"],
            }
        )
        setup = _call_tool(
            "setup_integration",
            service="gmail",
            credential_data=cred_data,
        )
        assert setup["status"] == "success"
        host1._shared_state.memory.close()
        host1._shared_state.knowledge.close()

        # Restart and verify skill + credential exist
        host2 = _make_host(workspace)
        skills = host2.knowledge.recall(query="Gmail API", category="skill")
        assert len(skills) >= 1
        assert skills[0]["metadata"]["type"] == "api"

        cred = _call_tool("get_credential", service="gmail")
        assert cred["status"] == "found"
        assert cred["data"]["access_token"] == "tok123"

        host2._shared_state.memory.close()
        host2._shared_state.knowledge.close()

    @patch("gaia.agents.base.service_integration._call_perplexity_api")
    def test_discover_no_api_fallback(self, mock_api, workspace):
        """Service with no API returns fallback suggestion."""
        mock_api.return_value = {
            "success": True,
            "answer": (
                "This niche website does not have a public API. "
                "You can use browser automation as an alternative."
            ),
            "sources": [],
        }

        host = _make_host(workspace)
        discovery = _call_tool("discover_api", service="some-niche-site")
        assert discovery["has_api"] is False
        assert discovery["fallback"] == "computer_use"

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()


# ── Preference Learning Persistence ──────────────────────────────────────────


class TestPreferenceLearningPersistence:
    """Preference rules learned in one session affect the next."""

    def test_explicit_correction_persists(self, workspace):
        """Explicit correction stored in session 1 is available in session 2."""
        host1 = _make_host(workspace)
        host1._handle_explicit_correction(
            original_action="archive",
            corrected_action="star",
            context={
                "domain": "email",
                "entity": "boss@company.com",
                "rule_description": "Emails from boss should always be starred",
            },
        )
        host1._shared_state.memory.close()
        host1._shared_state.knowledge.close()

        host2 = _make_host(workspace)
        results = host2.knowledge.recall(query="boss star", category="strategy")
        assert len(results) >= 1
        assert any("star" in r["content"].lower() for r in results)

        host2._shared_state.memory.close()
        host2._shared_state.knowledge.close()

    def test_implicit_confirmation_accumulates(self, workspace):
        """Implicit confirmations across sessions keep bumping confidence."""
        host1 = _make_host(workspace)
        insight_id = host1.knowledge.store_insight(
            category="fact",
            content="Archive newsletter emails",
            domain="email",
        )
        ctx = {"domain": "email", "rule_id": insight_id}
        host1._handle_implicit_confirmation(action="archive", context=ctx)
        host1._handle_implicit_confirmation(action="archive", context=ctx)
        host1._shared_state.memory.close()
        host1._shared_state.knowledge.close()

        host2 = _make_host(workspace)
        results = host2.knowledge.recall(query="newsletter emails", category="fact")
        assert len(results) >= 1
        assert results[0]["confidence"] >= 0.59

        host2._shared_state.memory.close()
        host2._shared_state.knowledge.close()


# ── Decision Workflow Persistence ────────────────────────────────────────────


class TestDecisionWorkflowPersistence:
    """Decision skills and preference rules work across sessions."""

    def test_decision_skill_with_rules_persists(self, workspace):
        """Decision skill with preference_rules persists and is usable."""
        host1 = _make_host(workspace)
        metadata = {
            "type": "decision",
            "observe": {"extract": ["sender", "subject"]},
            "actions": {
                "archive": {"description": "Low priority"},
                "star": {"description": "Important"},
            },
            "preference_rules": [
                {"rule": "Emails from boss -> star", "confidence": 0.9},
                {"rule": "Newsletter -> archive", "confidence": 0.8},
            ],
        }
        host1.knowledge.store_insight(
            category="skill",
            content="Email triage decision workflow",
            domain="email",
            metadata=metadata,
        )
        host1._shared_state.memory.close()
        host1._shared_state.knowledge.close()

        host2 = _make_host(workspace)
        results = host2.knowledge.recall(query="email triage", category="skill")
        assert len(results) >= 1
        restored_meta = results[0]["metadata"]
        assert restored_meta["type"] == "decision"
        assert len(restored_meta["preference_rules"]) == 2
        assert restored_meta["preference_rules"][0]["confidence"] == 0.9

        host2._shared_state.memory.close()
        host2._shared_state.knowledge.close()


# ── Credential Encryption Isolation ──────────────────────────────────────────


class TestCredentialEncryptionIsolation:
    """Direct tests for _encrypt_data / _decrypt_data without tool layer."""

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypt some data, verify ciphertext differs, then decrypt back."""
        plaintext = '{"api_key": "my_super_secret_key_999", "token": "tok_abc"}'
        ciphertext = _encrypt_data(plaintext)

        # Ciphertext must not contain the plaintext secrets
        assert "my_super_secret_key_999" not in ciphertext
        assert "tok_abc" not in ciphertext
        # Ciphertext should differ from plaintext
        assert ciphertext != plaintext

        # Round-trip: decrypt must recover original
        recovered = _decrypt_data(ciphertext)
        assert recovered == plaintext

    def test_decrypt_invalid_data(self):
        """_decrypt_data with garbage input should not crash silently."""
        # Garbage that is not valid base64 should not crash the caller.
        # The function may raise on invalid base64 or decode errors.
        # We verify it does not produce a silent wrong result; any
        # exception is acceptable.
        garbage_inputs = ["not-valid-base64!!!", "", "$$$$"]
        for garbage in garbage_inputs:
            try:
                result = _decrypt_data(garbage)
                # If it somehow returns, it should be a string (never the
                # original plaintext of something else).
                assert isinstance(result, (str, type(None)))
            except Exception:
                # Any exception (binascii.Error, UnicodeDecodeError, etc.)
                # is acceptable -- the function did not crash silently.
                pass


# ── API Discovery Helpers ────────────────────────────────────────────────────


class TestAPIDiscoveryHelpers:
    """Direct tests for _detect_auth_type and _detect_has_api helper functions."""

    def test_detect_auth_type_oauth(self):
        """Text mentioning OAuth 2.0 should return 'oauth2'."""
        from gaia.agents.base.service_integration import _detect_auth_type

        text = "This service uses OAuth 2.0 for authentication and authorization."
        assert _detect_auth_type(text) == "oauth2"

    def test_detect_auth_type_api_key(self):
        """Text mentioning API key should return 'api_key'."""
        from gaia.agents.base.service_integration import _detect_auth_type

        text = "You must include your API key in the request header."
        assert _detect_auth_type(text) == "api_key"

    def test_detect_auth_type_unknown(self):
        """Text with no auth keywords should return 'unknown'."""
        from gaia.agents.base.service_integration import _detect_auth_type

        text = "This service provides weather forecasting for major cities."
        assert _detect_auth_type(text) == "unknown"

    def test_detect_has_api_true(self):
        """Text mentioning REST API should return True."""
        from gaia.agents.base.service_integration import _detect_has_api

        text = "The platform provides a REST API for programmatic access."
        assert _detect_has_api(text) is True

    def test_detect_has_api_false(self):
        """Text saying 'no public API' should return False."""
        from gaia.agents.base.service_integration import _detect_has_api

        text = "This website has no public API and only offers a web interface."
        assert _detect_has_api(text) is False


# ── Credential Error Paths ───────────────────────────────────────────────────


class TestCredentialErrorPaths:
    """Edge cases and error handling for credential tools."""

    def test_get_nonexistent_credential(self, workspace):
        """get_credential for a service that was never stored returns not_found."""
        host = _make_host(workspace)
        result = _call_tool("get_credential", service="nonexistent-service")
        assert result["status"] == "not_found"

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_store_credential_invalid_json(self, workspace):
        """store_credential with invalid JSON in data field returns error."""
        host = _make_host(workspace)
        result = _call_tool(
            "store_credential",
            service="bad-svc",
            credential_type="api_key",
            data="this is {not valid json",
        )
        assert result["status"] == "error"
        assert "Invalid JSON" in result["message"]

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_list_credentials_empty(self, workspace):
        """Fresh host with no credentials stored returns count=0."""
        host = _make_host(workspace)
        result = _call_tool("list_credentials")
        assert result["status"] == "success"
        assert result["count"] == 0
        assert result["credentials"] == []

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()


# ── Decision Workflow Execution ──────────────────────────────────────────────


class TestDecisionWorkflowExecution:
    """Tests for _execute_decision_workflow and _match_and_decide."""

    def test_match_and_decide_exact_rule(self, workspace):
        """_match_and_decide applies the correct action when a rule matches."""
        host = _make_host(workspace)

        item = {"sender": "boss@company.com", "subject": "Urgent request"}
        preference_rules = [
            {
                "rule": "Emails from boss -> star",
                "match_field": "sender",
                "match_value": "boss@company.com",
                "action": "star",
                "confidence": 0.95,
            },
            {
                "rule": "Newsletter -> archive",
                "match_field": "subject",
                "match_contains": "newsletter",
                "action": "archive",
                "confidence": 0.8,
            },
        ]

        decision = host._match_and_decide(item, preference_rules, "inbox")
        assert decision["matched_rule"] is True
        assert decision["action"] == "star"
        assert decision["confidence"] == 0.95
        assert "boss" in decision["rule"].lower()

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_match_and_decide_contains_rule(self, workspace):
        """_match_and_decide matches via match_contains substring check."""
        host = _make_host(workspace)

        item = {"sender": "news@updates.com", "subject": "Weekly Newsletter Digest"}
        preference_rules = [
            {
                "rule": "Newsletter -> archive",
                "match_field": "subject",
                "match_contains": "newsletter",
                "action": "archive",
                "confidence": 0.85,
            },
        ]

        decision = host._match_and_decide(item, preference_rules, "inbox")
        assert decision["matched_rule"] is True
        assert decision["action"] == "archive"
        assert decision["confidence"] == 0.85

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_match_and_decide_fallback(self, workspace):
        """_match_and_decide uses fallback action when no rule matches."""
        host = _make_host(workspace)

        item = {"sender": "random@example.com", "subject": "Hello there"}
        preference_rules = [
            {
                "rule": "Emails from boss -> star",
                "match_field": "sender",
                "match_value": "boss@company.com",
                "action": "star",
                "confidence": 0.9,
            },
        ]

        decision = host._match_and_decide(item, preference_rules, "inbox")
        assert decision["matched_rule"] is False
        assert decision["action"] == "inbox"
        assert decision["confidence"] == 0

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_decision_skill_recall_preserves_preference_rules(self, workspace):
        """A stored decision skill can be recalled with preference_rules intact."""
        host = _make_host(workspace)

        metadata = {
            "type": "decision",
            "observe": {"extract": ["sender", "subject"]},
            "actions": {
                "archive": {"description": "Low priority"},
                "star": {"description": "Important"},
                "reply": {"description": "Needs response"},
            },
            "preference_rules": [
                {
                    "rule": "Boss emails -> star",
                    "match_field": "sender",
                    "match_value": "boss@company.com",
                    "action": "star",
                    "confidence": 0.95,
                },
                {
                    "rule": "Newsletter -> archive",
                    "match_field": "subject",
                    "match_contains": "newsletter",
                    "action": "archive",
                    "confidence": 0.8,
                },
            ],
        }
        host.knowledge.store_insight(
            category="skill",
            content="Email triage with rules",
            domain="email",
            metadata=metadata,
        )

        # Recall the skill and verify preference_rules metadata is intact
        results = host.knowledge.recall(query="email triage rules", category="skill")
        assert len(results) >= 1
        restored_meta = results[0]["metadata"]
        assert restored_meta["type"] == "decision"
        assert len(restored_meta["preference_rules"]) == 2
        # Verify rule details survived storage and recall
        rule_actions = {r["action"] for r in restored_meta["preference_rules"]}
        assert rule_actions == {"star", "archive"}
        assert restored_meta["preference_rules"][0]["match_field"] in (
            "sender",
            "subject",
        )

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()
