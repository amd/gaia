# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the eval-only Claude provider opt-in (GAIA_EVAL_AGENT_PROVIDER).

The opt-in exists so `gaia eval agent --agent-type gaia` can drive a
Claude-backed agent on machines that must never start Lemonade (plan §5c).
The contract under test, per CLAUDE.md's assert-the-shape rule:

- env unset  -> kwargs identical to today (no use_claude / claude_model keys)
- claude + model -> use_claude=True and claude_model reach create_agent
- claude without model -> ValueError naming BOTH env vars
- any other value -> ValueError listing the valid values
- never a silent fallback to Lemonade

The registry is mocked throughout — no real agent, no Lemonade.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from gaia.ui._chat_helpers import (
    _EVAL_CLAUDE_MODEL_ENV,
    _EVAL_PROVIDER_ENV,
    _build_create_kwargs,
)
from gaia.ui.database import SESSION_DEFAULT_MODEL as _DB_DEFAULT

# ── _build_create_kwargs (direct) ────────────────────────────────────────────


def test_env_unset_leaves_kwargs_unchanged(monkeypatch):
    monkeypatch.delenv(_EVAL_PROVIDER_ENV, raising=False)
    monkeypatch.delenv(_EVAL_CLAUDE_MODEL_ENV, raising=False)

    kwargs = _build_create_kwargs(custom_model=None, model_id=None)

    assert "use_claude" not in kwargs
    assert "claude_model" not in kwargs


def test_empty_env_value_means_unset(monkeypatch):
    monkeypatch.setenv(_EVAL_PROVIDER_ENV, "   ")

    kwargs = _build_create_kwargs(custom_model=None, model_id=None)

    assert "use_claude" not in kwargs


def test_claude_provider_passes_use_claude_and_model(monkeypatch):
    monkeypatch.setenv(_EVAL_PROVIDER_ENV, "claude")
    monkeypatch.setenv(_EVAL_CLAUDE_MODEL_ENV, "claude-haiku-4-5")

    kwargs = _build_create_kwargs(custom_model=None, model_id=None)

    assert kwargs["use_claude"] is True
    assert kwargs["claude_model"] == "claude-haiku-4-5"


def test_claude_provider_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(_EVAL_PROVIDER_ENV, "Claude")
    monkeypatch.setenv(_EVAL_CLAUDE_MODEL_ENV, "claude-haiku-4-5")

    kwargs = _build_create_kwargs(custom_model=None, model_id=None)

    assert kwargs["use_claude"] is True


def test_claude_without_model_raises_naming_both_vars(monkeypatch):
    monkeypatch.setenv(_EVAL_PROVIDER_ENV, "claude")
    monkeypatch.delenv(_EVAL_CLAUDE_MODEL_ENV, raising=False)

    with pytest.raises(ValueError) as excinfo:
        _build_create_kwargs(custom_model=None, model_id=None)

    message = str(excinfo.value)
    assert _EVAL_PROVIDER_ENV in message
    assert _EVAL_CLAUDE_MODEL_ENV in message


def test_unknown_provider_raises_listing_valid_values(monkeypatch):
    """No silent fallback: 'lemonade', typos, anything unknown is refused."""
    monkeypatch.setenv(_EVAL_PROVIDER_ENV, "lemonade")

    with pytest.raises(ValueError) as excinfo:
        _build_create_kwargs(custom_model=None, model_id=None)

    message = str(excinfo.value)
    assert "claude" in message
    assert _EVAL_PROVIDER_ENV in message


def test_server_startup_validates_the_optin():
    """Source pin (same style as the #841 grep guards): the UI server's
    lifespan must call _eval_provider_kwargs() so a bad env var fails at
    startup, in seconds — not minutes into an eval run."""
    import inspect

    import gaia.ui.server as server_module

    source = inspect.getsource(server_module)
    assert "_eval_provider_kwargs()" in source


def test_lemonade_preflight_is_skipped_when_provider_is_claude(monkeypatch):
    """With the opt-in active, _maybe_load_expected_model must return before
    any Lemonade contact — the preflight's broad except would swallow a
    blocked probe, so assert the base-URL lookup is never even reached."""
    monkeypatch.setenv(_EVAL_PROVIDER_ENV, "claude")
    monkeypatch.setenv(_EVAL_CLAUDE_MODEL_ENV, "claude-haiku-4-5")
    from gaia.llm.lemonade_manager import LemonadeManager
    from gaia.ui._chat_helpers import _maybe_load_expected_model

    probe = MagicMock(side_effect=AssertionError("Lemonade was contacted"))
    monkeypatch.setattr(LemonadeManager, "get_base_url", probe)

    assert _maybe_load_expected_model("Gemma-4-E4B-it-GGUF") is None
    probe.assert_not_called()


def test_optin_composes_with_model_id_precedence(monkeypatch):
    """The provider opt-in does not disturb the #841 model_id branches."""
    monkeypatch.setenv(_EVAL_PROVIDER_ENV, "claude")
    monkeypatch.setenv(_EVAL_CLAUDE_MODEL_ENV, "claude-haiku-4-5")

    kwargs = _build_create_kwargs(custom_model="UserPicked-GGUF", model_id=None)

    assert kwargs["model_id"] == "UserPicked-GGUF"
    assert kwargs["use_claude"] is True


# ── Through the (mocked) registry: the outgoing-call shape ───────────────────


def _make_db():
    db = MagicMock()
    db.get_messages.return_value = []
    db.get_setting.return_value = None
    db.list_documents.return_value = []
    db.update_session.return_value = None
    db.get_session.return_value = {}
    return db


def _make_registry():
    registry = MagicMock()
    registry.get.return_value = True
    registry.resolve_model.return_value = None
    captured = {}

    def _spy(agent_id, **kwargs):
        captured["agent_id"] = agent_id
        captured["kwargs"] = dict(kwargs)
        fake = MagicMock()
        fake.model_id = kwargs.get("model_id", "Whatever-GGUF")
        fake.process_query.return_value = "ok"
        fake.conversation_history = []
        fake.indexed_files = set()
        return fake

    registry.create_agent.side_effect = _spy
    return registry, captured


def _call_non_streaming(agent_type="gaia"):
    import gaia.ui._chat_helpers as _helpers
    from gaia.ui._chat_helpers import _get_chat_response
    from gaia.ui.models import ChatRequest

    with _helpers._agent_cache_lock:
        _helpers._agent_cache.clear()

    session = {
        "document_ids": [],
        "model": _DB_DEFAULT,
        "agent_type": agent_type,
        "session_id": "sess-eval",
    }
    request = ChatRequest(session_id="sess-eval", message="hi", stream=False)
    return asyncio.run(_get_chat_response(_make_db(), session, request))


@pytest.mark.allow_network  # asyncio's Windows self-pipe is a loopback socketpair
def test_registry_receives_claude_kwargs_when_opted_in(monkeypatch):
    monkeypatch.setenv(_EVAL_PROVIDER_ENV, "claude")
    monkeypatch.setenv(_EVAL_CLAUDE_MODEL_ENV, "claude-haiku-4-5")
    registry, captured = _make_registry()

    with (
        patch("gaia.ui._chat_helpers._agent_registry", registry),
        patch("gaia.ui._chat_helpers._maybe_load_expected_model"),
    ):
        _call_non_streaming()

    kwargs = captured["kwargs"]
    assert kwargs["use_claude"] is True
    assert kwargs["claude_model"] == "claude-haiku-4-5"


@pytest.mark.allow_network  # asyncio's Windows self-pipe is a loopback socketpair
def test_registry_receives_no_claude_kwargs_by_default(monkeypatch):
    monkeypatch.delenv(_EVAL_PROVIDER_ENV, raising=False)
    registry, captured = _make_registry()

    with (
        patch("gaia.ui._chat_helpers._agent_registry", registry),
        patch("gaia.ui._chat_helpers._maybe_load_expected_model"),
    ):
        _call_non_streaming()

    kwargs = captured["kwargs"]
    assert "use_claude" not in kwargs
    assert "claude_model" not in kwargs
