# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""RED-first tests: NPU auto-select is actually WIRED, not cosmetic (#1439).

``gaia_agent_email.model_select.resolve_default_email_model`` (see
``test_model_select.py``) decides FLM vs the GGUF default per Lemonade
server. This file locks in that the resolver's answer actually reaches
every surface that loads a model for the email agent:

- ``api_routes._resolve_email_model_id`` (readiness probe + trap test below)
- ``api_routes._compute_init_status`` (``GET /v1/email/init``)
- ``api_routes.EmailTriageService._build_llm_chat`` (the REAL triage call --
  THE most important regression: today ``_build_llm_chat`` builds its
  ``AgentConfig`` with no ``model=`` kwarg, so ``/v1/email/init`` could
  report FLM selected while the actual triage call silently runs the SDK's
  default model instead)
- ``agent.EmailTriageAgent.__init__`` (CLI / agent-loop path), including the
  device-aware embedder threaded into ``init_memory(embedding_model=...)``
  so an FLM chat model doesn't get evicted by a GGUF (Vulkan) embedder.

``gaia_agent_email.model_select`` does not exist yet -- every test in this
file is expected to fail collection with ``ModuleNotFoundError`` until a
teammate implements it. That is the correct RED state.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gaia_agent_email")

import requests  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.api_routes import (  # noqa: E402
    EmailTriageService,
    _compute_init_status,
    _resolve_email_model_id,
)
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.model_select import (  # noqa: E402
    NPU_EMAIL_MODEL_ID,
    _reset_model_select_cache,
)

from gaia.agents.registry import get_embedding_model_for_device  # noqa: E402
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME  # noqa: E402
from gaia.llm.lemonade_manager import LemonadeManager  # noqa: E402


class _MinimalMailBackend:
    """Satisfies the GmailBackend protocol just enough to construct."""


class _MinimalCalendarBackend:
    """Satisfies the CalendarBackend protocol just enough to construct."""


def _make_config(tmp_path, **overrides):
    kwargs = dict(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    kwargs.update(overrides)
    return EmailAgentConfig(**kwargs)


# ---------------------------------------------------------------------------
# (a) api_routes wiring
# ---------------------------------------------------------------------------


def test_resolve_email_model_id_delegates_to_resolver_with_base_url():
    with patch(
        "gaia_agent_email.api_routes.resolve_default_email_model",
        return_value="sentinel-model",
    ) as mock_resolve:
        result = _resolve_email_model_id("http://127.0.0.1:9631")

    mock_resolve.assert_called_once_with("http://127.0.0.1:9631")
    assert result == "sentinel-model"


def test_resolve_email_model_id_default_base_url_is_none():
    with patch(
        "gaia_agent_email.api_routes.resolve_default_email_model",
        return_value="sentinel-default",
    ) as mock_resolve:
        result = _resolve_email_model_id()

    mock_resolve.assert_called_once_with(None)
    assert result == "sentinel-default"


def test_compute_init_status_threads_non_default_base_url(monkeypatch):
    """Regression: today ``_compute_init_status`` calls
    ``_resolve_email_model_id()`` with no args, silently ignoring its own
    ``base_url`` parameter. It must thread that same base_url through.
    """

    def _fake_get(url, *args, **kwargs):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", _fake_get)

    with patch(
        "gaia_agent_email.api_routes._resolve_email_model_id",
        return_value="sentinel-model",
    ) as mock_resolve:
        _compute_init_status(base_url="http://127.0.0.1:9632")

    mock_resolve.assert_called_once_with("http://127.0.0.1:9632")


def test_build_llm_chat_uses_resolved_model_id_the_trap_test(monkeypatch):
    """THE regression guard: a resolver that exists but never reaches the
    real ``AgentConfig(model=...)`` is cosmetic. Fake the reachability +
    model-presence preflight to succeed, patch the resolver to a known
    sentinel, and assert the constructed chat client actually carries it.
    """
    probe_base = "http://127.0.0.1:9633/api/v1"
    sentinel_model = "sentinel-resolved-model"

    def _fake_get(url, *args, **kwargs):
        if url == f"{probe_base}/health":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"version": "10.10.0"}
            return resp
        if url == f"{probe_base}/models":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"data": [{"id": sentinel_model}]}
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    with patch(
        "gaia_agent_email.api_routes.resolve_default_email_model",
        return_value=sentinel_model,
    ):
        chat = EmailTriageService()._build_llm_chat(base_url="http://127.0.0.1:9633")

    assert chat.config.model == sentinel_model


def test_build_llm_chat_uses_default_model_when_npu_unavailable_inverse(monkeypatch):
    """Inverse of the trap test: with the REAL resolver running (not
    patched) and the NPU reported unavailable, the chat client lands on
    ``DEFAULT_MODEL_NAME`` -- proving the wiring isn't hardcoded to always
    equal whatever the resolver happens to return.
    """
    probe_base = "http://127.0.0.1:9634/api/v1"

    def _fake_get(url, *args, **kwargs):
        if url == f"{probe_base}/health":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"version": "10.10.0"}
            return resp
        if url == f"{probe_base}/system-info":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"devices": {"amd_npu": {"available": False}}}
            return resp
        if url == f"{probe_base}/models":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"data": [{"id": DEFAULT_MODEL_NAME}]}
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    chat = EmailTriageService()._build_llm_chat(base_url="http://127.0.0.1:9634")

    assert chat.config.model == DEFAULT_MODEL_NAME


# ---------------------------------------------------------------------------
# (b) agent.py wiring
# ---------------------------------------------------------------------------


def test_agent_explicit_model_id_skips_resolver(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    cfg = _make_config(tmp_path, model_id="SomeExplicitModel-GGUF")

    with patch.object(
        LemonadeManager, "ensure_ready", return_value=True
    ), patch(
        "gaia_agent_email.agent.resolve_default_email_model"
    ) as mock_resolve:
        agent = EmailTriageAgent(config=cfg)
    try:
        mock_resolve.assert_not_called()
        assert agent.model_id == "SomeExplicitModel-GGUF"
    finally:
        agent.close_db()


def test_agent_resolver_called_with_explicit_base_url(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    cfg = _make_config(
        tmp_path, base_url="http://127.0.0.1:9640", model_id=None
    )

    with patch.object(
        LemonadeManager, "ensure_ready", return_value=True
    ), patch(
        "gaia_agent_email.agent.resolve_default_email_model",
        return_value="sentinel-model",
    ) as mock_resolve:
        agent = EmailTriageAgent(config=cfg)
    try:
        mock_resolve.assert_called_once_with("http://127.0.0.1:9640")
        assert agent.model_id == "sentinel-model"
    finally:
        agent.close_db()


def test_agent_resolver_called_with_env_default_base_url(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    cfg = _make_config(tmp_path, base_url=None, model_id=None)

    with patch.object(
        LemonadeManager, "ensure_ready", return_value=True
    ), patch(
        "gaia_agent_email.agent.resolve_default_email_model",
        return_value="sentinel-model",
    ) as mock_resolve:
        agent = EmailTriageAgent(config=cfg)
    try:
        mock_resolve.assert_called_once_with("http://localhost:13305/api/v1")
        assert agent.model_id == "sentinel-model"
    finally:
        agent.close_db()


def test_agent_threads_npu_embedder_when_flm_resolved(tmp_path, monkeypatch):
    """FLM resolved -> ``init_memory`` gets the device-aware NPU embedder
    (never the FLM-chat literal hardcoded here -- call the real helper).
    """
    expected_embedder = get_embedding_model_for_device("npu")
    cfg = _make_config(tmp_path, model_id=None)

    with patch.object(
        LemonadeManager, "ensure_ready", return_value=True
    ), patch(
        "gaia_agent_email.agent.resolve_default_email_model",
        return_value=NPU_EMAIL_MODEL_ID,
    ), patch.object(EmailTriageAgent, "init_memory") as mock_init_memory:
        agent = EmailTriageAgent(config=cfg)
    try:
        mock_init_memory.assert_called_once()
        assert (
            mock_init_memory.call_args.kwargs.get("embedding_model")
            == expected_embedder
        )
    finally:
        agent.close_db()


def test_agent_leaves_default_embedder_when_e4b_resolved(tmp_path, monkeypatch):
    """Regression: resolver landing on DEFAULT_MODEL_NAME must NOT thread
    the NPU embedder -- ``embedding_model`` stays the unchanged default
    (None -> GGUF nomic embedder)."""
    cfg = _make_config(tmp_path, model_id=None)

    with patch.object(
        LemonadeManager, "ensure_ready", return_value=True
    ), patch(
        "gaia_agent_email.agent.resolve_default_email_model",
        return_value=DEFAULT_MODEL_NAME,
    ), patch.object(EmailTriageAgent, "init_memory") as mock_init_memory:
        agent = EmailTriageAgent(config=cfg)
    try:
        mock_init_memory.assert_called_once()
        assert mock_init_memory.call_args.kwargs.get("embedding_model") is None
    finally:
        agent.close_db()


def test_agent_leaves_default_embedder_with_explicit_non_flm_model(
    tmp_path, monkeypatch
):
    """Regression: an explicit non-FLM ``model_id`` (resolver never
    consulted) also must not thread the NPU embedder."""
    cfg = _make_config(tmp_path, model_id="SomeExplicitModel-GGUF")

    with patch.object(
        LemonadeManager, "ensure_ready", return_value=True
    ), patch.object(EmailTriageAgent, "init_memory") as mock_init_memory:
        agent = EmailTriageAgent(config=cfg)
    try:
        mock_init_memory.assert_called_once()
        assert mock_init_memory.call_args.kwargs.get("embedding_model") is None
    finally:
        agent.close_db()


# ---------------------------------------------------------------------------
# (c) Cross-surface agreement at a non-default base_url
# ---------------------------------------------------------------------------


def test_cross_surface_agreement_at_nondefault_base_url(tmp_path, monkeypatch):
    """Agent, readiness probe, init status, and the real triage chat client
    must all agree on the SAME resolved model at the SAME non-default
    Lemonade base_url -- and the resolver must actually have reached that
    port (9555), never silently falling back to the default (13305).
    """
    _reset_model_select_cache()
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)

    probe_base = "http://127.0.0.1:9555/api/v1"
    captured_urls: list = []

    def _fake_get(url, *args, **kwargs):
        captured_urls.append(url)
        if url == f"{probe_base}/health":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"version": "10.10.0"}
            return resp
        if url == f"{probe_base}/system-info":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"devices": {"amd_npu": {"available": True}}}
            return resp
        if url == f"{probe_base}/models":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"data": [{"id": NPU_EMAIL_MODEL_ID}]}
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    cfg = _make_config(tmp_path, base_url="http://127.0.0.1:9555", model_id=None)
    with patch.object(LemonadeManager, "ensure_ready", return_value=True):
        agent = EmailTriageAgent(config=cfg)
    try:
        assert agent.model_id == NPU_EMAIL_MODEL_ID
    finally:
        agent.close_db()

    assert _resolve_email_model_id("http://127.0.0.1:9555") == NPU_EMAIL_MODEL_ID

    status = _compute_init_status("http://127.0.0.1:9555")
    assert status.model.id == NPU_EMAIL_MODEL_ID

    chat = EmailTriageService()._build_llm_chat(base_url="http://127.0.0.1:9555")
    assert chat.config.model == NPU_EMAIL_MODEL_ID

    assert any("9555" in url for url in captured_urls)
    assert not any("13305" in url for url in captured_urls)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
