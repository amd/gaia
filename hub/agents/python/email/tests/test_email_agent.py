# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Smoke tests for the standalone gaia-agent-email package."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


def test_build_registration_shape():
    import gaia_agent_email as m

    reg = m.build_registration()
    assert reg.id == "email"
    assert reg.name == "Email Triage"
    assert reg.namespaced_agent_id == "installed:email"
    assert reg.source == "installed"
    assert reg.tags == ["email", "gmail", "calendar", "triage"]
    assert reg.icon == "mail"
    # Provider-superset connector list — Google + Microsoft (#962, #1275).
    connector_ids = {c.connector_id for c in reg.required_connections}
    assert connector_ids == {"google", "microsoft"}


def test_conversation_starters_match_agent():
    import gaia_agent_email as m
    from gaia_agent_email.agent import EmailTriageAgent

    reg = m.build_registration()
    assert reg.conversation_starters == list(EmailTriageAgent.CONVERSATION_STARTERS)
    assert reg.name == EmailTriageAgent.AGENT_NAME
    assert reg.description == EmailTriageAgent.AGENT_DESCRIPTION


def test_required_connectors_match_agent():
    import gaia_agent_email as m
    from gaia_agent_email.agent import EmailTriageAgent

    reg = m.build_registration()
    built = [
        (c.connector_id, tuple(c.scopes), c.reason) for c in reg.required_connections
    ]
    expected = [
        (c.connector_id, tuple(c.scopes), c.reason)
        for c in EmailTriageAgent.REQUIRED_CONNECTORS
    ]
    assert built == expected


def test_can_import_agent():
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    assert EmailTriageAgent is not None
    assert EmailAgentConfig is not None


def test_contract_import_is_light():
    # The contract module must import without dragging in the agent/backends —
    # the REST + MCP surfaces depend on this (#1229, #1104).
    from gaia_agent_email.contract import EmailTriageRequest, EmailTriageResponse

    assert EmailTriageRequest is not None
    assert EmailTriageResponse is not None


# ---------------------------------------------------------------------------
# LLM triage endpoint tests
# ---------------------------------------------------------------------------


def _make_single_request():
    """Build a minimal SingleEmailInput contract request."""
    from gaia_agent_email.contract import (
        EmailAddress,
        EmailMessage,
        EmailTriageRequest,
        SingleEmailInput,
    )

    msg = EmailMessage(
        message_id="msg-001",
        subject="Team lunch tomorrow?",
        from_=EmailAddress(email="alice@example.com"),
        body="Hey, are you joining us for lunch tomorrow at noon?",
    )
    payload = SingleEmailInput(
        message=msg,
        principal=EmailAddress(email="bob@example.com"),
    )
    return EmailTriageRequest(payload=payload)


def _fake_chat(category="NEEDS_RESPONSE", summary="Alice invites Bob to lunch."):
    """Minimal stub that makes classify_email_llm and summarize_email_llm succeed."""
    import json
    import types

    class _FakeChat:
        def send_messages(self, messages, system_prompt="", **kwargs):
            resp = types.SimpleNamespace()
            # Return JSON if it looks like a classification request, else a summary.
            if "Classify" in (messages[0].get("content", "") if messages else ""):
                resp.text = json.dumps(
                    {"category": category, "confidence": 0.9, "reasoning": "test"}
                )
            else:
                resp.text = summary
            return resp

    return _FakeChat()


def test_triage_service_uses_llm():
    """EmailTriageService.triage_request uses the LLM path with a fake chat."""
    from gaia_agent_email.api_routes import EmailTriageService

    service = EmailTriageService()
    request = _make_single_request()
    chat = _fake_chat(category="NEEDS_RESPONSE", summary="Alice invites Bob to lunch.")

    response = service.triage_request(request, chat=chat)

    assert response.result.category == "NEEDS_RESPONSE"
    assert "Alice" in response.result.summary or "lunch" in response.result.summary
    assert response.result.message_id == "msg-001"


def _fake_chat_with_usage(
    category="NEEDS_RESPONSE",
    summary="Alice invites Bob to lunch.",
    usage=None,
):
    """Like ``_fake_chat`` but the classify call's response also carries a
    Lemonade chat-completion-shaped ``.usage`` dict -- exercises the REST
    path's real ``_aggregate_usage`` -> ``aggregate_usage_stats`` delegation
    (#1891). The summarize call's response deliberately carries NO usage/stats
    (plain ``.text`` only, like the original ``_fake_chat``) so the expected
    aggregate is deterministic regardless of whether ``summarize_email_llm``
    itself is ever updated to also prefer ``.usage``.
    """
    import json
    import types

    usage = usage or {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "tokens_per_second": 25.0,
    }

    class _FakeChat:
        def send_messages(self, messages, system_prompt="", **kwargs):
            resp = types.SimpleNamespace()
            content = messages[0].get("content", "") if messages else ""
            if "Classify" in content:
                resp.text = json.dumps(
                    {"category": category, "confidence": 0.9, "reasoning": "test"}
                )
                resp.usage = dict(usage)
            else:
                resp.text = summary
            return resp

    return _FakeChat()


def test_triage_service_usage_uses_response_usage_shape():
    """REST path: the classify call's ``response.usage`` (Lemonade
    chat-completion shape) must be aggregated into a correct, non-None
    ``EmailTriageResult.usage`` after ``_aggregate_usage`` is refactored to
    delegate to ``aggregate_usage_stats`` (#1891)."""
    from gaia_agent_email.api_routes import EmailTriageService

    service = EmailTriageService()
    request = _make_single_request()
    chat = _fake_chat_with_usage()

    response = service.triage_request(request, chat=chat)

    assert response.result.usage is not None
    assert response.result.usage.prompt_tokens == 120
    assert response.result.usage.completion_tokens == 30
    assert response.result.usage.total_tokens == 150
    assert response.result.usage.tokens_per_second == pytest.approx(25.0)


def test_body_not_clipped_by_llm_triage():
    """_build_user_prompt must pass the FULL body to the model (no 4 000-char cap)."""
    from gaia_agent_email.tools.llm_triage import _build_user_prompt

    big_body = "x" * 8000
    prompt = _build_user_prompt("subject", "sender@example.com", big_body)
    assert "x" * 8000 in prompt, "body was clipped in llm_triage"


def test_body_not_clipped_by_summarize_tools():
    """_build_user_prompt in summarize_tools must pass the FULL body."""
    from gaia_agent_email.tools.summarize_tools import _build_user_prompt

    big_body = "y" * 8000
    prompt = _build_user_prompt("subject", "sender@example.com", big_body)
    assert "y" * 8000 in prompt, "body was clipped in summarize_tools"


def test_thread_newest_first():
    """Thread body is joined newest-first so recent messages appear at the top."""
    from gaia_agent_email.api_routes import EmailTriageService
    from gaia_agent_email.contract import (
        EmailAddress,
        EmailMessage,
        EmailTriageRequest,
        ThreadInput,
    )

    messages = [
        EmailMessage(
            message_id="old",
            subject="Project update",
            from_=EmailAddress(email="alice@example.com"),
            body="OLD MESSAGE",
        ),
        EmailMessage(
            message_id="new",
            subject="Project update",
            from_=EmailAddress(email="bob@example.com"),
            body="NEW MESSAGE",
        ),
    ]
    payload = ThreadInput(
        thread_id="thread-001",
        messages=messages,
        principal=EmailAddress(email="carol@example.com"),
    )
    request = EmailTriageRequest(payload=payload)

    captured = {}

    class _CapturingService(EmailTriageService):
        def _build_result_llm(self, *, body, **kwargs):
            captured["body"] = body
            return super()._build_result_llm(body=body, **kwargs)

    chat = _fake_chat(category="NEEDS_RESPONSE", summary="Project update thread.")
    service = _CapturingService()
    response = service.triage_request(request, chat=chat)

    assert response.result.message_id == "thread-001"
    assert "body" in captured
    # NEW MESSAGE must appear before OLD MESSAGE in the combined body
    new_pos = captured["body"].index("NEW MESSAGE")
    old_pos = captured["body"].index("OLD MESSAGE")
    assert new_pos < old_pos, "thread body must be newest-first"


def test_triage_fails_fast_when_lemonade_unreachable(monkeypatch):
    """Unreachable Lemonade → prompt LLMTriageError, not a ~30s hang (#1677)."""
    import requests
    from gaia_agent_email.api_routes import EmailTriageService
    from gaia_agent_email.tools.llm_triage import LLMTriageError

    captured = {}

    def _fake_get(url, *args, **kwargs):
        captured["url"] = url
        captured["timeout"] = kwargs.get("timeout")
        raise requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(requests, "get", _fake_get)

    service = EmailTriageService()
    request = _make_single_request()

    # No chat passed → _build_llm_chat runs the reachability probe.
    with pytest.raises(LLMTriageError) as exc:
        service.triage_request(request)

    assert "not reachable" in str(exc.value)
    assert captured["url"].endswith("/health")
    # The probe must use a short *connect* timeout (a tuple), not the 900s
    # scalar the real chat path uses.
    assert isinstance(captured["timeout"], tuple)
    assert captured["timeout"][0] <= 5


# ---------------------------------------------------------------------------
# Configurable LEMONADE_BASE_URL for embedded-Lemonade consumers (#1888)
# ---------------------------------------------------------------------------


def test_llm_chat_respects_lemonade_base_url_override_env(monkeypatch):
    """LEMONADE_BASE_URL redirects both the pre-flight probes and the
    constructed chat client to the overridden Lemonade server (AC1).

    Note: the ``/draft`` verb composes a scaffold with no LLM call, so it has
    no probe/redirect path to test here — out of scope by design.
    """
    import requests
    from gaia_agent_email.api_routes import EmailTriageService, _resolve_email_model_id

    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9555")
    resolved_model_id = _resolve_email_model_id()
    captured_urls = []

    def _fake_get(url, *args, **kwargs):
        captured_urls.append(url)
        if url == "http://127.0.0.1:9555/api/v1/health":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"version": "10.2.0"}
            return resp
        if url == "http://127.0.0.1:9555/api/v1/system-info":
            # No NPU here — this test is about base_url redirection, not
            # NPU auto-select (#1439); keep the resolved model unchanged.
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"devices": {"amd_npu": {"available": False}}}
            return resp
        if url == "http://127.0.0.1:9555/api/v1/models":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"data": [{"id": resolved_model_id}]}
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    chat = EmailTriageService()._build_llm_chat()

    assert "http://127.0.0.1:9555/api/v1/health" in captured_urls
    # Fails today: _build_llm_chat does not yet probe /models (the planned
    # _assert_model_present is not wired in) — AC2 is not implemented yet.
    assert "http://127.0.0.1:9555/api/v1/models" in captured_urls
    # Exact equality — LemonadeClient normalization is deterministic.
    assert chat.llm_client._backend.base_url == "http://127.0.0.1:9555/api/v1"


def test_llm_chat_respects_lemonade_base_url_explicit_param(monkeypatch):
    """_build_llm_chat(base_url=...) probes the explicit URL with the env
    var unset (AC1)."""
    import requests
    from gaia_agent_email.api_routes import EmailTriageService, _resolve_email_model_id

    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    resolved_model_id = _resolve_email_model_id()
    captured_urls = []

    def _fake_get(url, *args, **kwargs):
        captured_urls.append(url)
        if url == "http://127.0.0.1:9555/api/v1/health":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"version": "10.2.0"}
            return resp
        if url == "http://127.0.0.1:9555/api/v1/system-info":
            # No NPU here — this test is about base_url redirection, not
            # NPU auto-select (#1439); keep the resolved model unchanged.
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"devices": {"amd_npu": {"available": False}}}
            return resp
        if url == "http://127.0.0.1:9555/api/v1/models":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"data": [{"id": resolved_model_id}]}
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    chat = EmailTriageService()._build_llm_chat(base_url="http://127.0.0.1:9555")

    assert "http://127.0.0.1:9555/api/v1/health" in captured_urls
    assert chat.llm_client._backend.base_url == "http://127.0.0.1:9555/api/v1"


def test_wrong_model_raises_llm_triage_error(monkeypatch):
    """Model absent on the Lemonade server → loud LLMTriageError naming the
    model, the fix, and where to verify — never a silent heuristic fallback
    (AC2). ``pytest.raises`` catching the error IS the "no fallback"
    assertion: no chat object is ever returned to fall back with.

    Exercises the planned ``_assert_model_present`` (#1888) via
    ``_build_llm_chat`` — not wired in yet, so this currently fails with
    "DID NOT RAISE" rather than the LLMTriageError it will raise once the
    fix lands.
    """
    import requests
    from gaia_agent_email.api_routes import EmailTriageService, _resolve_email_model_id
    from gaia_agent_email.tools.llm_triage import LLMTriageError

    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9555")
    probe_base = "http://127.0.0.1:9555/api/v1"
    model_id = _resolve_email_model_id()

    def _fake_get(url, *args, **kwargs):
        if url == f"{probe_base}/health":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"version": "10.2.0"}
            return resp
        if url == f"{probe_base}/system-info":
            # No NPU — this test is about the model-absent path (AC2), not
            # NPU auto-select (#1439); keep the resolved model unchanged.
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"devices": {"amd_npu": {"available": False}}}
            return resp
        if url == f"{probe_base}/models":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"data": []}  # model absent
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    with pytest.raises(LLMTriageError) as exc:
        EmailTriageService()._build_llm_chat()

    message = str(exc.value)
    assert (
        f"Model '{model_id}' is not available on the Lemonade Server at {probe_base}"
        in message
    )
    assert (
        f"pull it on that server (POST {probe_base}/pull or 'gaia init'), or "
        "point LEMONADE_BASE_URL at a server that has it" in message
    )
    assert "verify with GET /v1/email/init" in message


def test_wrong_model_check_transport_failure_is_loud(monkeypatch):
    """A /models transport failure must raise LLMTriageError naming
    ``<probe_base>/models`` — never silently skip the model-presence check.

    Same not-wired-in-yet caveat as ``test_wrong_model_raises_llm_triage_error``:
    currently fails with "DID NOT RAISE" instead of LLMTriageError.
    """
    import requests
    from gaia_agent_email.api_routes import EmailTriageService
    from gaia_agent_email.tools.llm_triage import LLMTriageError

    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9555")
    probe_base = "http://127.0.0.1:9555/api/v1"

    def _fake_get(url, *args, **kwargs):
        if url == f"{probe_base}/health":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"version": "10.2.0"}
            return resp
        if url == f"{probe_base}/system-info":
            # No NPU — this test is about a /models transport failure, not
            # NPU auto-select (#1439); keep the resolved model unchanged.
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"devices": {"amd_npu": {"available": False}}}
            return resp
        if url == f"{probe_base}/models":
            raise requests.ConnectionError("reset by peer")
        raise AssertionError(f"unexpected probe URL: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    with pytest.raises(LLMTriageError) as exc:
        EmailTriageService()._build_llm_chat()

    assert f"{probe_base}/models" in str(exc.value)


def test_model_present_check_tolerant_of_user_dot_prefix(monkeypatch):
    """A `user.`-prefixed resolved model id must still match a stripped id in
    the /models listing (and vice versa) — locks in the planned tolerant
    ``_model_ids_match`` comparison (#1888).

    Calls the planned ``_assert_model_present(self, base_url)`` directly (the
    exact seam named in the spec) rather than through ``_build_llm_chat``.
    Today this fails with AttributeError (the method does not exist yet).
    Once the next increment adds ``_assert_model_present`` with an *exact*
    id comparison, this test will still fail — now via a raised
    LLMTriageError — until the tolerant ``_model_ids_match`` fix additionally
    lands. Both failure modes are expected and correct for TDD.
    """
    import requests
    from gaia_agent_email.api_routes import EmailTriageService, _resolve_email_model_id

    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9555")
    probe_base = "http://127.0.0.1:9555/api/v1"
    resolved_model_id = _resolve_email_model_id()
    # Flip the "user." prefix: the listed id differs textually from the
    # resolved id but names the same underlying model.
    if resolved_model_id.startswith("user."):
        listed_id = resolved_model_id[len("user.") :]
    else:
        listed_id = f"user.{resolved_model_id}"

    def _fake_get(url, *args, **kwargs):
        if url == f"{probe_base}/health":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"version": "10.2.0"}
            return resp
        if url == f"{probe_base}/system-info":
            # No NPU — this test is about the user.-prefix tolerant match,
            # not NPU auto-select (#1439); keep the resolved model unchanged.
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"devices": {"amd_npu": {"available": False}}}
            return resp
        if url == f"{probe_base}/models":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"data": [{"id": listed_id}]}
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    # Must NOT raise — the tolerant id match should treat this as present.
    EmailTriageService()._assert_model_present("http://127.0.0.1:9555")


# ---------------------------------------------------------------------------
# tools_count anti-drift guard (#1232 AC1)
# ---------------------------------------------------------------------------


class _MinimalMailBackend:
    """Satisfies the GmailBackend protocol just enough to construct."""


class _MinimalCalendarBackend:
    """Satisfies the CalendarBackend protocol just enough to construct."""


def _build_memory_disabled_agent(tmp_path, monkeypatch):
    """Construct an EmailTriageAgent with memory forced off.

    Mirrors ``tests/test_email_memory.py::_build_agent(memory_disabled=True)``
    so the live tool registry never picks up the 5 memory CRUD tools that a
    Lemonade-connected dev box would otherwise add, which would silently
    corrupt any tools_count comparison.
    """
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


def test_tools_count_matches_live_registry_and_manifest(tmp_path, monkeypatch):
    """build_registration().tools_count and gaia-agent.yaml's tools_count must
    both track the LIVE tool registry, not a hand-maintained literal (#1232).

    A future @tool added to the agent without bumping both pinned copies
    must fail this test.
    """
    import gaia_agent_email as m

    agent = _build_memory_disabled_agent(tmp_path, monkeypatch)
    try:
        # Precondition: memory tools are NOT part of the live count.
        assert agent._memory_store is None

        live = len(agent._tools_registry)

        reg = m.build_registration()
        assert reg.tools_count == live

        manifest_path = Path(__file__).resolve().parents[1] / "gaia-agent.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert manifest["tools_count"] == live
    finally:
        agent.close_db()
