# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the model_id kwarg selection logic in _chat_helpers.py.

Covers the three-branch precedence chain introduced by the #841 fix:
  1. custom_model setting wins over everything
  2. Session-explicit model (anything != DB default) is honored
  3. model_id kwarg OMITTED when session is at the DB default, so that the
     custom agent's kwargs.setdefault("model_id", ...) fires (the #841 fix)

Also pins: streaming vs non-streaming silent_mode values, static source-grep
guard against reintroduction of the antipattern, post-construction pre-flight
contract, and built-in ChatAgent (agent_type="chat") behavior unchanged.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from gaia.ui.database import SESSION_DEFAULT_MODEL as _DB_DEFAULT

# ── Helpers ──────────────────────────────────────────────────────────────────


def _run_sync(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_session(model=_DB_DEFAULT, agent_type="bot"):
    return {
        "document_ids": [],
        "model": model,
        "agent_type": agent_type,
        "session_id": "sess-1",
    }


def _make_db(custom_model=None):
    db = MagicMock()
    db.get_messages.return_value = []
    db.get_setting.return_value = custom_model
    db.list_documents.return_value = []
    db.update_session.return_value = None
    db.get_session.return_value = {}
    return db


def _make_registry(resolve_model_return=None, setdefault_model="SetdefaultChose-GGUF"):
    """Return (registry_mock, captured_dict).

    captured["kwargs"] holds the kwargs received by create_agent.
    The fake agent's model_id mimics kwargs.setdefault: if model_id was NOT
    passed, it is set to setdefault_model; otherwise it keeps the passed value.
    """
    registry = MagicMock()
    registry.get.return_value = True  # agent_type is registered
    registry.resolve_model.return_value = resolve_model_return

    captured = {}

    def _spy(agent_id, **kwargs):
        captured["kwargs"] = dict(kwargs)
        fake = MagicMock()
        fake.model_id = kwargs.get("model_id", setdefault_model)
        fake.process_query.return_value = "ok"
        fake.conversation_history = []
        fake.indexed_files = set()
        return fake

    registry.create_agent.side_effect = _spy
    return registry, captured


def _call_non_streaming(session, db, agent_type_override=None, session_id="sess-1"):
    import gaia.ui._chat_helpers as _helpers
    from gaia.ui._chat_helpers import _get_chat_response
    from gaia.ui.models import ChatRequest

    # Clear the agent cache so tests don't interfere with each other.
    with _helpers._agent_cache_lock:
        _helpers._agent_cache.clear()

    request = ChatRequest(
        session_id=session_id,
        message="hi",
        stream=False,
        agent_type=agent_type_override,
    )
    session = dict(session)
    session.setdefault("session_id", session_id)
    return _run_sync(_get_chat_response(db, session, request))


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestModelKwargSelection:
    """Verify the three-branch model_id selection at both call sites."""

    def test_custom_model_setting_wins_over_everything(self):
        """db.get_setting('custom_model') result always reaches create_agent as model_id."""
        registry, captured = _make_registry(setdefault_model="AgentPref-GGUF")
        db = _make_db(custom_model="UserPicked-GGUF")
        session = _make_session(model=_DB_DEFAULT)

        with (
            patch("gaia.ui._chat_helpers._agent_registry", registry),
            patch("gaia.ui._chat_helpers._maybe_load_expected_model"),
        ):
            _call_non_streaming(session, db)

        assert captured["kwargs"].get("model_id") == "UserPicked-GGUF"

    def test_session_explicit_model_honored(self):
        """A session model that differs from the DB default is forwarded as model_id."""
        registry, captured = _make_registry()
        db = _make_db(custom_model=None)
        session = _make_session(model="UserChose-GGUF")

        with (
            patch("gaia.ui._chat_helpers._agent_registry", registry),
            patch("gaia.ui._chat_helpers._maybe_load_expected_model"),
        ):
            _call_non_streaming(session, db)

        assert captured["kwargs"].get("model_id") == "UserChose-GGUF"

    def test_model_id_kwarg_omitted_when_session_at_db_default(self):
        """Core #841 fix: model_id kwarg must be ABSENT when session == DB default.

        kwargs.setdefault only fires when the key is absent. The pre-fix code
        always passes model_id=<session default> explicitly, defeating setdefault.
        After the fix, model_id is omitted so the agent's __init__ governs.
        """
        registry, captured = _make_registry(setdefault_model="SetdefaultChose-GGUF")
        db = _make_db(custom_model=None)
        session = _make_session(model=_DB_DEFAULT)

        with (
            patch("gaia.ui._chat_helpers._agent_registry", registry),
            patch("gaia.ui._chat_helpers._maybe_load_expected_model"),
        ):
            _call_non_streaming(session, db)

        assert "model_id" not in captured.get("kwargs", {}), (
            "Issue #841: model_id kwarg must be omitted when session is at DB default; "
            f"got kwargs={captured.get('kwargs')}"
        )
        # The spy's setdefault model should be what the agent ends up with.
        assert (
            captured.get("kwargs", {}).get("model_id", "SetdefaultChose-GGUF")
            == "SetdefaultChose-GGUF"
        )

    def test_model_id_kwarg_omitted_when_session_model_is_none(self):
        """model_id kwarg is omitted when session model is None (unset session)."""
        registry, captured = _make_registry(setdefault_model="SetdefaultChose-GGUF")
        db = _make_db(custom_model=None)
        session = _make_session(model=None)

        with (
            patch("gaia.ui._chat_helpers._agent_registry", registry),
            patch("gaia.ui._chat_helpers._maybe_load_expected_model"),
        ):
            _call_non_streaming(session, db)

        assert "model_id" not in captured.get("kwargs", {}), (
            f"model_id kwarg must be omitted when session model is None; "
            f"got kwargs={captured.get('kwargs')}"
        )

    def test_non_streaming_path_silent_mode_true_preserved(self):
        """Non-streaming create_agent call must pass silent_mode=True."""
        registry, captured = _make_registry()
        db = _make_db(custom_model=None)
        session = _make_session(model=_DB_DEFAULT)

        with (
            patch("gaia.ui._chat_helpers._agent_registry", registry),
            patch("gaia.ui._chat_helpers._maybe_load_expected_model"),
        ):
            _call_non_streaming(session, db)

        assert captured.get("kwargs", {}).get("silent_mode") is True, (
            "Non-streaming path must pass silent_mode=True to create_agent; "
            f"got kwargs={captured.get('kwargs')}"
        )
        assert "streaming" not in captured.get(
            "kwargs", {}
        ), "Non-streaming path must not pass streaming=True to create_agent"


class TestStaticRegressionGuard:
    """Source-level pin against reintroduction of the antipattern."""

    def test_no_direct_model_id_kwarg_in_create_agent_calls(self):
        """registry.create_agent must never be called with model_id=model_id directly.

        The pre-fix antipattern was:
            registry.create_agent(agent_type, model_id=model_id, ...)
        which always passes the kwarg explicitly, defeating kwargs.setdefault.

        ChatAgentConfig(model_id=model_id, ...) is legitimate and intentionally
        excluded from this check — only create_agent calls are guarded.

        This test catches future regressions at the source level in <5ms.
        """
        import re

        src = (Path(__file__).parents[4] / "src/gaia/ui/_chat_helpers.py").read_text()
        # Matches the old antipattern: create_agent(... model_id=model_id ...) as a
        # DIRECT kwarg (not inside a nested call like _build_create_kwargs).
        # [^()]* stops at any parenthesis so nested helper calls aren't matched.
        match = re.search(r"create_agent\([^()]*model_id=model_id", src, re.DOTALL)
        assert not match, (
            "Issue #841 regression: registry.create_agent must not receive "
            "model_id=model_id as a direct kwarg. Build create_kwargs conditionally "
            "and omit model_id when no explicit user choice exists.\n"
            f"Match found at: {match.group()[:80]!r}"
        )


class TestPostConstructionPreflight:
    """Verify pre-flight uses agent.model_id (not pre-call model_id variable)."""

    def test_preflight_receives_agent_effective_model(self):
        """_maybe_load_expected_model must be called with the agent's actual model_id.

        When model_id kwarg is omitted, the agent's __init__ sets model_id via
        setdefault AFTER construction. The pre-fix code called
        _maybe_load_expected_model(model_id) with the pre-call variable (DB
        default), missing the agent's actual effective model. The fix calls it
        with agent.model_id so Lemonade pre-flight fires for the right model.
        """
        registry, captured = _make_registry(setdefault_model="SetdefaultChose-GGUF")
        db = _make_db(custom_model=None)
        session = _make_session(model=_DB_DEFAULT)

        preflight_calls = []

        def _spy_preflight(model_id, *args, **kwargs):
            preflight_calls.append(model_id)

        with (
            patch("gaia.ui._chat_helpers._agent_registry", registry),
            patch(
                "gaia.ui._chat_helpers._maybe_load_expected_model",
                side_effect=_spy_preflight,
            ),
        ):
            _call_non_streaming(session, db)

        assert preflight_calls, "_maybe_load_expected_model was never called"
        # After the fix, pre-flight must use the agent's actual model_id
        # ("SetdefaultChose-GGUF"), not the DB default it was seeded with.
        assert preflight_calls[-1] == "SetdefaultChose-GGUF", (
            f"Pre-flight must use agent.model_id after construction; "
            f"got {preflight_calls[-1]!r} (expected 'SetdefaultChose-GGUF')"
        )


class TestBuiltinChatAgentUnchanged:
    """Pin AC4: built-in ChatAgent (agent_type='chat') behavior is unchanged."""

    def test_chat_agent_type_bypasses_registry(self):
        """agent_type='chat' must not go through registry.create_agent."""
        registry, captured = _make_registry()
        db = _make_db(custom_model=None)
        session = _make_session(model=_DB_DEFAULT, agent_type="chat")

        fake_agent = MagicMock()
        fake_agent.process_query.return_value = "ok"
        fake_agent.conversation_history = []
        fake_agent.indexed_files = set()
        fake_agent.rag = None

        with (
            patch("gaia.ui._chat_helpers._agent_registry", registry),
            patch("gaia.ui._chat_helpers._maybe_load_expected_model"),
            patch("gaia.agents.chat.agent.ChatAgent", return_value=fake_agent),
            patch("gaia.agents.chat.agent.ChatAgentConfig"),
        ):
            _call_non_streaming(session, db, agent_type_override=None)

        # registry.create_agent must NOT have been called for the chat path
        registry.create_agent.assert_not_called()
