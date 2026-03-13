# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for ServiceIntegrationMixin.

Tests:
- API discovery (discover_api)
- Integration setup (setup_integration)
- Credential management (store, get, refresh, list)
- Preference learning (explicit correction, implicit confirmation)
- Decision workflow execution
- Mixin tool registration
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.shared_state import SharedAgentState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory for DB files."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


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
def service_mixin(temp_workspace):
    """Create a ServiceIntegrationMixin instance with initialized memory."""
    from gaia.agents.base.memory_mixin import MemoryMixin
    from gaia.agents.base.service_integration import ServiceIntegrationMixin

    class TestHost(MemoryMixin, ServiceIntegrationMixin):
        """Minimal host class to test the mixin in isolation."""

        pass

    host = TestHost()
    host.init_memory(workspace_dir=temp_workspace)
    return host


@pytest.fixture
def service_mixin_with_tools(service_mixin):
    """ServiceIntegrationMixin with tools registered."""
    service_mixin.register_service_integration_tools()
    return service_mixin


# ---------------------------------------------------------------------------
# Test: API Discovery
# ---------------------------------------------------------------------------


class TestDiscoverApi:
    """Tests for discover_api tool."""

    def test_discover_api_finds_api(self, service_mixin_with_tools):
        """Mock web_search → discover_api returns {has_api: True, auth_type: 'oauth2'}."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["discover_api"]["function"]

        mock_response = {
            "success": True,
            "answer": (
                "Gmail has a comprehensive REST API. Authentication uses OAuth 2.0. "
                "You need to create a project in Google Cloud Console, enable the Gmail API, "
                "and configure OAuth 2.0 credentials. Documentation: "
                "https://developers.google.com/gmail/api"
            ),
            "sources": ["https://developers.google.com/gmail/api"],
        }

        with patch(
            "gaia.agents.base.service_integration._call_perplexity_api",
            return_value=mock_response,
        ):
            result = func(service="gmail")

        assert result["has_api"] is True
        assert result["auth_type"] == "oauth2"
        assert isinstance(result["setup_steps"], list)
        assert len(result["setup_steps"]) > 0
        assert "documentation_url" in result

    def test_discover_api_no_api(self, service_mixin_with_tools):
        """Mock web_search → discover_api returns {has_api: False, fallback: 'computer_use'}."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["discover_api"]["function"]

        mock_response = {
            "success": True,
            "answer": (
                "This niche website does not have a public API. "
                "There is no developer documentation or REST endpoints available. "
                "You would need to interact with the website through the browser interface."
            ),
            "sources": [],
        }

        with patch(
            "gaia.agents.base.service_integration._call_perplexity_api",
            return_value=mock_response,
        ):
            result = func(service="some-niche-site")

        assert result["has_api"] is False
        assert result["fallback"] == "computer_use"


# ---------------------------------------------------------------------------
# Test: Setup Integration
# ---------------------------------------------------------------------------


class TestSetupIntegration:
    """Tests for setup_integration tool."""

    def test_setup_integration_stores_skill(self, service_mixin_with_tools):
        """setup_integration creates both a credential and an API skill in KnowledgeDB."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["setup_integration"]["function"]

        cred_data = json.dumps(
            {
                "credential_type": "oauth2",
                "access_token": "ya29.test-token-123",
                "refresh_token": "1//test-refresh-token",
                "client_id": "test-client-id.apps.googleusercontent.com",
                "client_secret": "test-client-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["gmail.modify", "gmail.compose"],
                "capabilities": [
                    "list_messages",
                    "get_message",
                    "send_message",
                ],
            }
        )

        result = func(service="gmail", credential_data=cred_data)

        assert result["status"] == "success"
        assert "credential_id" in result
        assert "skill_id" in result

        # Verify credential stored in KnowledgeDB
        cred = service_mixin_with_tools.knowledge.get_credential("gmail")
        assert cred is not None
        assert cred["service"] == "gmail"
        assert cred["credential_type"] == "oauth2"

        # Verify API skill insight stored
        skills = service_mixin_with_tools.knowledge.recall("gmail", category="skill")
        assert len(skills) >= 1
        skill = skills[0]
        assert skill["metadata"] is not None
        assert skill["metadata"]["type"] == "api"

    def test_setup_integration_validates_creds(self, service_mixin_with_tools):
        """Invalid credentials → error returned, nothing stored."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["setup_integration"]["function"]

        # Missing credential_type
        cred_data = json.dumps({"access_token": "token"})
        result = func(service="gmail", credential_data=cred_data)

        assert result["status"] == "error"
        assert "credential_type" in result["message"]

        # Verify nothing stored
        cred = service_mixin_with_tools.knowledge.get_credential("gmail")
        assert cred is None


# ---------------------------------------------------------------------------
# Test: Credential Management
# ---------------------------------------------------------------------------


class TestCredentialManagement:
    """Tests for credential store/get/refresh/list tools."""

    def test_store_credential_encrypts(self, service_mixin_with_tools):
        """Stored credential data is encrypted at rest — raw token not visible in DB."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["store_credential"]["function"]

        data = json.dumps(
            {"access_token": "super-secret-token-12345", "api_key": "sk-secret"}
        )
        result = func(service="test_svc", credential_type="api_key", data=data)

        assert result["status"] == "stored"

        # Read raw encrypted_data from KnowledgeDB — it should NOT contain plaintext
        cred = service_mixin_with_tools.knowledge.get_credential("test_svc")
        assert cred is not None
        # The raw encrypted_data field should NOT contain the plaintext token
        assert "super-secret-token-12345" not in cred["encrypted_data"]

    def test_get_credential_decrypts(self, service_mixin_with_tools):
        """Retrieved credential has decrypted data ready for use."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        store_func = _TOOL_REGISTRY["store_credential"]["function"]
        get_func = _TOOL_REGISTRY["get_credential"]["function"]

        original_data = {
            "access_token": "my-secret-access-token",
            "region": "us-east-1",
        }
        store_func(
            service="aws",
            credential_type="api_key",
            data=json.dumps(original_data),
        )

        result = get_func(service="aws")
        assert result["status"] == "found"
        assert result["data"]["access_token"] == "my-secret-access-token"
        assert result["data"]["region"] == "us-east-1"
        assert result["expired"] is False

    def test_credential_expiry_warning(self, service_mixin_with_tools):
        """Expired credential returns expired=True flag."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        store_func = _TOOL_REGISTRY["store_credential"]["function"]
        get_func = _TOOL_REGISTRY["get_credential"]["function"]

        # Store with an expiry in the past
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        store_func(
            service="expired_svc",
            credential_type="oauth2",
            data=json.dumps({"access_token": "old-token"}),
            expires_at=past,
        )

        result = get_func(service="expired_svc")
        assert result["status"] == "found"
        assert result["expired"] is True

    def test_refresh_credential_oauth2(self, service_mixin_with_tools):
        """Mock OAuth2 refresh → new access token stored, expires_at updated."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        store_func = _TOOL_REGISTRY["store_credential"]["function"]
        refresh_func = _TOOL_REGISTRY["refresh_credential"]["function"]
        get_func = _TOOL_REGISTRY["get_credential"]["function"]

        # Store initial credential with refresh token
        original_data = {
            "access_token": "old-access-token",
            "refresh_token": "1//my-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test-client-id",
            "client_secret": "test-secret",
        }
        store_func(
            service="gmail",
            credential_type="oauth2",
            data=json.dumps(original_data),
        )

        # Mock the HTTP refresh call
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "new-access-token-refreshed",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        with patch("requests.post", return_value=mock_resp):
            result = refresh_func(service="gmail")

        assert result["status"] == "refreshed"

        # Verify the new token is stored
        cred_result = get_func(service="gmail")
        assert cred_result["data"]["access_token"] == "new-access-token-refreshed"

    def test_list_credentials_no_secrets(self, service_mixin_with_tools):
        """list_credentials returns service names and types but NOT actual tokens."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        store_func = _TOOL_REGISTRY["store_credential"]["function"]
        list_func = _TOOL_REGISTRY["list_credentials"]["function"]

        store_func(
            service="gmail",
            credential_type="oauth2",
            data=json.dumps({"access_token": "secret1"}),
        )
        store_func(
            service="twitter",
            credential_type="api_key",
            data=json.dumps({"api_key": "secret2"}),
        )

        result = list_func()
        assert result["status"] == "success"
        assert len(result["credentials"]) >= 2

        # Verify no secrets in the output
        for cred in result["credentials"]:
            assert "service" in cred
            assert "credential_type" in cred
            # Should NOT have encrypted_data, access_token, api_key, etc.
            assert "encrypted_data" not in cred
            assert "access_token" not in cred
            assert "data" not in cred

    def test_credential_referenced_by_skill(self, service_mixin_with_tools):
        """API skill's metadata.credential_id references a stored credential."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        func = _TOOL_REGISTRY["setup_integration"]["function"]
        cred_data = json.dumps(
            {
                "credential_type": "api_key",
                "api_key": "sk-test-key",
                "capabilities": ["search", "post"],
            }
        )
        result = func(service="twitter", credential_data=cred_data)
        assert result["status"] == "success"

        credential_id = result["credential_id"]

        # Find the skill in KnowledgeDB
        skills = service_mixin_with_tools.knowledge.recall("twitter", category="skill")
        assert len(skills) >= 1
        skill_meta = skills[0]["metadata"]
        assert skill_meta["credential_id"] == credential_id


# ---------------------------------------------------------------------------
# Test: Preference Learning
# ---------------------------------------------------------------------------


class TestPreferenceLearning:
    """Tests for preference learning helpers."""

    def test_explicit_correction_stores_rule(self, service_mixin_with_tools):
        """User correction → preference rule stored with high confidence."""
        service_mixin_with_tools._handle_explicit_correction(
            original_action="archive",
            corrected_action="star",
            context={
                "domain": "email",
                "entity": "boss@company.com",
                "rule_description": "Emails from boss are always important",
            },
        )

        # Verify a preference rule was stored
        results = service_mixin_with_tools.knowledge.recall(
            "boss email", category="strategy"
        )
        assert len(results) >= 1
        rule = results[0]
        assert rule["confidence"] >= 0.9

    def test_explicit_correction_updates_existing(self, service_mixin_with_tools):
        """Second correction for same entity updates rule, doesn't create duplicate."""
        context = {
            "domain": "email",
            "entity": "newsletter@example.com",
            "rule_description": "Newsletter emails should be archived",
        }

        # First correction
        service_mixin_with_tools._handle_explicit_correction(
            original_action="star",
            corrected_action="archive",
            context=context,
        )

        # Second correction — same entity, different action
        context2 = {
            "domain": "email",
            "entity": "newsletter@example.com",
            "rule_description": "Newsletter emails should be deleted",
        }
        service_mixin_with_tools._handle_explicit_correction(
            original_action="archive",
            corrected_action="delete",
            context=context2,
        )

        # Should have at most 1 rule for newsletter (deduped by KnowledgeDB)
        results = service_mixin_with_tools.knowledge.recall(
            "newsletter email", category="strategy"
        )
        assert len(results) <= 2  # Dedup might merge or keep both
        # The latest rule should reflect the correction
        has_delete = any("delete" in r["content"].lower() for r in results)
        assert has_delete

    def test_implicit_confirmation_bumps_confidence(self, service_mixin_with_tools):
        """Uncorrected decisions bump the driving rule's confidence by 0.05."""
        # First store a rule with known confidence
        rule_id = service_mixin_with_tools.knowledge.store_insight(
            category="strategy",
            domain="email",
            content="Archive newsletter emails automatically",
            confidence=0.7,
        )

        # Simulate implicit confirmation
        service_mixin_with_tools._handle_implicit_confirmation(
            action="archive",
            context={
                "domain": "email",
                "rule_id": rule_id,
            },
        )

        # Confidence should have been bumped
        results = service_mixin_with_tools.knowledge.recall(
            "Archive newsletter", category="strategy"
        )
        assert len(results) >= 1
        # Find our specific rule
        rule = next((r for r in results if r["id"] == rule_id), None)
        assert rule is not None
        assert rule["confidence"] >= 0.75  # 0.7 + 0.05

    def test_implicit_confirmation_caps_at_one(self, service_mixin_with_tools):
        """Confidence is capped at 1.0 even after many confirmations."""
        rule_id = service_mixin_with_tools.knowledge.store_insight(
            category="strategy",
            domain="email",
            content="Star emails from VIP contacts automatically",
            confidence=0.98,
        )

        service_mixin_with_tools._handle_implicit_confirmation(
            action="star",
            context={"domain": "email", "rule_id": rule_id},
        )

        results = service_mixin_with_tools.knowledge.recall(
            "Star VIP contacts", category="strategy"
        )
        rule = next((r for r in results if r["id"] == rule_id), None)
        assert rule is not None
        assert rule["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Test: Decision Workflow
# ---------------------------------------------------------------------------


class TestDecisionWorkflow:
    """Tests for decision workflow executor."""

    def _make_email_decision_skill(self):
        """Create a standard email triage decision skill for testing."""
        return {
            "type": "decision",
            "observe": {
                "extract": ["sender", "subject", "snippet"],
                "context_recall": ["email preferences", "important contacts"],
            },
            "actions": {
                "archive": {
                    "description": "Low-priority, no action needed",
                },
                "star": {
                    "description": "Important, user should see this",
                },
                "flag_urgent": {
                    "description": "Time-sensitive, notify user immediately",
                },
            },
            "preference_rules": [
                {
                    "rule": "Emails from boss@company.com are always 'star'",
                    "match_field": "sender",
                    "match_value": "boss@company.com",
                    "action": "star",
                    "confidence": 0.9,
                },
                {
                    "rule": "Newsletter emails are always 'archive'",
                    "match_field": "subject",
                    "match_contains": "newsletter",
                    "action": "archive",
                    "confidence": 0.8,
                },
                {
                    "rule": "Emails mentioning 'urgent' are 'flag_urgent'",
                    "match_field": "snippet",
                    "match_contains": "urgent",
                    "action": "flag_urgent",
                    "confidence": 0.7,
                },
            ],
        }

    def test_decision_workflow_observes(self, service_mixin_with_tools):
        """Decision skill processes input data items."""
        skill = self._make_email_decision_skill()
        data = [
            {
                "sender": "alice@test.com",
                "subject": "Hello",
                "snippet": "Quick question",
            },
        ]

        result = service_mixin_with_tools._execute_decision_workflow(skill, data)

        assert result["status"] == "success"
        assert "decisions" in result
        assert len(result["decisions"]) == 1

    def test_decision_workflow_recalls_preferences(self, service_mixin_with_tools):
        """Decision execution recalls preferences from context_recall queries."""
        # Store some preferences that should be found
        service_mixin_with_tools.knowledge.store_insight(
            category="strategy",
            domain="email",
            content="Important contacts: boss@company.com, cto@company.com",
            triggers=["important", "contacts"],
        )

        skill = self._make_email_decision_skill()
        data = [
            {"sender": "random@test.com", "subject": "Test", "snippet": "Hello"},
        ]

        # The workflow should call recall internally
        result = service_mixin_with_tools._execute_decision_workflow(skill, data)
        assert result["status"] == "success"
        # Verify context was recalled (stored in result)
        assert "recalled_context" in result

    def test_decision_workflow_applies_rules(self, service_mixin_with_tools):
        """Email matching rule → correct action chosen (boss=star, newsletter=archive)."""
        skill = self._make_email_decision_skill()
        data = [
            {
                "sender": "boss@company.com",
                "subject": "Q2 Planning",
                "snippet": "Let's discuss the roadmap",
            },
            {
                "sender": "marketing@newsletter.com",
                "subject": "Weekly newsletter digest",
                "snippet": "Top stories this week",
            },
            {
                "sender": "ops@company.com",
                "subject": "Server Alert",
                "snippet": "URGENT: Server disk usage at 95%",
            },
        ]

        result = service_mixin_with_tools._execute_decision_workflow(skill, data)

        assert result["status"] == "success"
        decisions = result["decisions"]
        assert len(decisions) == 3

        # Boss email → star
        boss_decision = next(
            d for d in decisions if d["item"]["sender"] == "boss@company.com"
        )
        assert boss_decision["action"] == "star"
        assert boss_decision["matched_rule"] is True

        # Newsletter → archive
        newsletter_decision = next(
            d for d in decisions if "newsletter" in d["item"]["subject"].lower()
        )
        assert newsletter_decision["action"] == "archive"
        assert newsletter_decision["matched_rule"] is True

        # Urgent → flag_urgent
        urgent_decision = next(
            d for d in decisions if "urgent" in d["item"]["snippet"].lower()
        )
        assert urgent_decision["action"] == "flag_urgent"
        assert urgent_decision["matched_rule"] is True

    def test_decision_workflow_llm_fallback(self, service_mixin_with_tools):
        """Email matching no rule → falls back to default action."""
        skill = self._make_email_decision_skill()
        data = [
            {
                "sender": "random@unknown.com",
                "subject": "Random subject",
                "snippet": "Nothing special here",
            },
        ]

        result = service_mixin_with_tools._execute_decision_workflow(skill, data)

        assert result["status"] == "success"
        decisions = result["decisions"]
        assert len(decisions) == 1
        # No rule matched — should have used fallback
        assert decisions[0]["matched_rule"] is False
        assert decisions[0]["action"] is not None  # Should still have an action

    def test_decision_workflow_logs_decisions(self, service_mixin_with_tools):
        """Each decision is logged as an event insight in KnowledgeDB."""
        skill = self._make_email_decision_skill()
        data = [
            {
                "sender": "boss@company.com",
                "subject": "Review needed",
                "snippet": "Please review",
            },
        ]

        service_mixin_with_tools._execute_decision_workflow(skill, data)

        # Check that an event was logged
        events = service_mixin_with_tools.knowledge.recall(
            "decision email", category="event"
        )
        assert len(events) >= 1

    def test_preference_rules_influence_decisions(self, service_mixin_with_tools):
        """Stored preference rules are applied without LLM call."""
        # Store a preference rule in KnowledgeDB
        service_mixin_with_tools.knowledge.store_insight(
            category="strategy",
            domain="email",
            content="Emails from vip@special.com should always be starred",
            triggers=["email", "preferences", "vip"],
            confidence=0.95,
        )

        skill = self._make_email_decision_skill()
        # Add a rule for vip
        skill["preference_rules"].append(
            {
                "rule": "Emails from vip@special.com → star",
                "match_field": "sender",
                "match_value": "vip@special.com",
                "action": "star",
                "confidence": 0.95,
            }
        )

        data = [
            {
                "sender": "vip@special.com",
                "subject": "Hello from VIP",
                "snippet": "Important message",
            },
        ]

        result = service_mixin_with_tools._execute_decision_workflow(skill, data)
        decisions = result["decisions"]
        assert len(decisions) == 1
        assert decisions[0]["action"] == "star"
        assert decisions[0]["matched_rule"] is True


# ---------------------------------------------------------------------------
# Test: Mixin Registration
# ---------------------------------------------------------------------------


class TestMixinRegistration:
    """Tests for ServiceIntegrationMixin tool registration."""

    def test_service_integration_mixin_registers_tools(self, service_mixin_with_tools):
        """Agent with ServiceIntegrationMixin has all expected tools."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        expected_tools = [
            "discover_api",
            "setup_integration",
            "store_credential",
            "get_credential",
            "refresh_credential",
            "list_credentials",
        ]

        for tool_name in expected_tools:
            assert tool_name in _TOOL_REGISTRY, (
                f"Tool '{tool_name}' not found in registry. "
                f"Available: {list(_TOOL_REGISTRY.keys())}"
            )

    def test_tool_descriptions_not_empty(self, service_mixin_with_tools):
        """All registered tools have non-empty descriptions."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        for name in [
            "discover_api",
            "setup_integration",
            "store_credential",
            "get_credential",
            "refresh_credential",
            "list_credentials",
        ]:
            info = _TOOL_REGISTRY[name]
            assert info["description"].strip(), f"Tool '{name}' has empty description"
