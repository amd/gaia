# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for GAIA Agent UI chat helper functions.

Tests the pure helper functions in gaia.ui._chat_helpers:
- _build_history_pairs: conversation history pairing
- _resolve_rag_paths: document ID -> file path resolution
- _compute_allowed_paths: filesystem scope derivation
- _find_last_tool_step: backward step search
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.ui._chat_helpers import (
    _build_history_pairs,
    _canonical_agent_type,
    _compute_allowed_paths,
    _find_last_tool_step,
    _resolve_rag_paths,
    set_agent_registry,
)

# ── _build_history_pairs ──────────────────────────────────────────────────


class TestBuildHistoryPairs:
    """Tests for _build_history_pairs()."""

    def test_empty_messages(self):
        assert _build_history_pairs([]) == []

    def test_single_user_assistant_pair(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = _build_history_pairs(messages)
        assert result == [("Hello", "Hi there")]

    def test_multiple_pairs(self):
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        result = _build_history_pairs(messages)
        assert result == [("Q1", "A1"), ("Q2", "A2")]

    def test_orphan_user_message_skipped(self):
        """A user message without a following assistant reply is skipped."""
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2 (no reply)"},
        ]
        result = _build_history_pairs(messages)
        assert result == [("Q1", "A1")]

    def test_orphan_user_then_valid_pair(self):
        """An orphan user message doesn't misalign subsequent pairs."""
        messages = [
            {"role": "user", "content": "orphan"},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
        ]
        result = _build_history_pairs(messages)
        assert result == [("Q1", "A1")]

    def test_system_messages_skipped(self):
        """System messages are silently skipped."""
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        result = _build_history_pairs(messages)
        assert result == [("Hi", "Hello")]

    def test_consecutive_assistant_messages(self):
        """Two assistant messages in a row: first breaks a pair, second skipped."""
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "assistant", "content": "A2 (duplicate)"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        result = _build_history_pairs(messages)
        assert result == [("Q1", "A1"), ("Q2", "A2")]

    def test_only_assistant_messages(self):
        messages = [
            {"role": "assistant", "content": "Unsolicited"},
        ]
        assert _build_history_pairs(messages) == []

    def test_only_user_messages(self):
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "user", "content": "Q2"},
        ]
        assert _build_history_pairs(messages) == []

    def test_preserves_content_exactly(self):
        """Verify content is not modified or truncated."""
        long_content = "x" * 10000
        messages = [
            {"role": "user", "content": long_content},
            {"role": "assistant", "content": long_content},
        ]
        result = _build_history_pairs(messages)
        assert result == [(long_content, long_content)]

    def test_empty_content_strings(self):
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": ""},
        ]
        result = _build_history_pairs(messages)
        assert result == [("", "")]

    def test_interleaved_system_messages_between_pair(self):
        """A system message between user and assistant breaks the pair."""
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "system", "content": "injected"},
            {"role": "assistant", "content": "A1"},
        ]
        # user at [0] sees system at [1], not assistant -> skip
        # system at [1] is skipped
        # assistant at [2] is skipped (no preceding user)
        result = _build_history_pairs(messages)
        assert result == []


# ── _resolve_rag_paths ────────────────────────────────────────────────────


class TestResolveRagPaths:
    """Tests for _resolve_rag_paths()."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.get_document.return_value = None
        db.list_documents.return_value = []
        return db

    def test_with_document_ids_returns_rag_paths(self, mock_db):
        mock_db.get_document.side_effect = lambda doc_id: {
            "doc1": {"filepath": "/docs/a.pdf"},
            "doc2": {"filepath": "/docs/b.txt"},
        }.get(doc_id)

        rag_paths, library_paths = _resolve_rag_paths(mock_db, ["doc1", "doc2"])
        assert rag_paths == ["/docs/a.pdf", "/docs/b.txt"]
        assert library_paths == []

    def test_with_document_ids_skips_missing(self, mock_db):
        mock_db.get_document.side_effect = lambda doc_id: (
            {"filepath": "/docs/a.pdf"} if doc_id == "doc1" else None
        )

        rag_paths, library_paths = _resolve_rag_paths(mock_db, ["doc1", "missing_doc"])
        assert rag_paths == ["/docs/a.pdf"]
        assert library_paths == []

    def test_with_document_ids_skips_no_filepath(self, mock_db):
        """A document with no filepath field is skipped."""
        mock_db.get_document.return_value = {"id": "doc1"}

        rag_paths, _ = _resolve_rag_paths(mock_db, ["doc1"])
        assert rag_paths == []

    def test_without_document_ids_returns_empty(self, mock_db):
        """No session-specific docs → returns ([], []) to prevent cross-session contamination."""
        mock_db.list_documents.return_value = [
            {"filepath": "/lib/x.pdf"},
            {"filepath": "/lib/y.md"},
        ]

        rag_paths, library_paths = _resolve_rag_paths(mock_db, [])
        assert rag_paths == []
        assert library_paths == []

    def test_without_document_ids_skips_no_filepath(self, mock_db):
        """No session-specific docs → returns ([], []) regardless of global library contents."""
        mock_db.list_documents.return_value = [
            {"filepath": "/lib/x.pdf"},
            {"id": "orphan"},  # no filepath key
            {"filepath": ""},  # empty filepath (falsy)
        ]

        _, library_paths = _resolve_rag_paths(mock_db, [])
        assert library_paths == []

    def test_empty_document_ids_empty_library(self, mock_db):
        mock_db.list_documents.return_value = []
        rag_paths, library_paths = _resolve_rag_paths(mock_db, [])
        assert rag_paths == []
        assert library_paths == []

    def test_none_document_ids_treated_as_empty(self, mock_db):
        """None is falsy like [], so both return lists are empty."""
        mock_db.list_documents.return_value = [{"filepath": "/lib/a.pdf"}]
        rag_paths, library_paths = _resolve_rag_paths(mock_db, None)
        assert rag_paths == []
        assert library_paths == []

    def test_document_with_filepath_none_skipped(self, mock_db):
        """filepath=None is falsy and should be skipped."""
        mock_db.get_document.return_value = {"filepath": None}
        rag_paths, _ = _resolve_rag_paths(mock_db, ["doc1"])
        assert rag_paths == []


# ── _compute_allowed_paths ────────────────────────────────────────────────


class TestComputeAllowedPaths:
    """Tests for _compute_allowed_paths()."""

    def test_empty_paths_returns_cwd(self):
        result = _compute_allowed_paths([])
        assert len(result) == 1
        assert result[0] == str(Path.cwd())

    def test_single_file_returns_parent_dir(self):
        result = _compute_allowed_paths(["/docs/project/report.pdf"])
        assert len(result) == 1
        assert Path(result[0]) == Path("/docs/project")

    def test_multiple_files_same_dir_deduped(self):
        result = _compute_allowed_paths(
            [
                "/docs/project/a.pdf",
                "/docs/project/b.pdf",
            ]
        )
        assert len(result) == 1
        assert Path(result[0]) == Path("/docs/project")

    def test_multiple_files_different_dirs(self):
        result = _compute_allowed_paths(
            [
                "/docs/project/a.pdf",
                "/home/user/data/b.csv",
            ]
        )
        result_set = {Path(p) for p in result}
        assert Path("/docs/project") in result_set
        assert Path("/home/user/data") in result_set

    def test_returns_list_type(self):
        result = _compute_allowed_paths(["/some/path/file.txt"])
        assert isinstance(result, list)


# ── _find_last_tool_step ─────────────────────────────────────────────────


class TestFindLastToolStep:
    """Tests for _find_last_tool_step()."""

    def test_empty_list(self):
        assert _find_last_tool_step([]) is None

    def test_no_tool_steps(self):
        steps = [
            {"type": "thinking", "label": "Thinking"},
            {"type": "plan", "label": "Planning"},
        ]
        assert _find_last_tool_step(steps) is None

    def test_single_tool_step(self):
        step = {"type": "tool", "label": "search", "active": True}
        result = _find_last_tool_step([step])
        assert result is step

    def test_returns_last_tool_not_first(self):
        steps = [
            {"type": "tool", "label": "first_tool"},
            {"type": "thinking", "label": "thinking"},
            {"type": "tool", "label": "second_tool"},
        ]
        result = _find_last_tool_step(steps)
        assert result["label"] == "second_tool"

    def test_returns_reference_not_copy(self):
        """The returned dict is the same object (by identity) as in the list."""
        step = {"type": "tool", "label": "test"}
        result = _find_last_tool_step([step])
        assert result is step
        # Mutations should be visible in the original
        result["active"] = False
        assert step["active"] is False

    def test_tool_step_after_many_non_tool_steps(self):
        steps = [
            {"type": "thinking", "label": "t1"},
            {"type": "plan", "label": "p1"},
            {"type": "status", "label": "s1"},
            {"type": "error", "label": "e1"},
            {"type": "tool", "label": "found_me"},
        ]
        result = _find_last_tool_step(steps)
        assert result["label"] == "found_me"

    def test_tool_step_before_many_non_tool_steps(self):
        steps = [
            {"type": "tool", "label": "first"},
            {"type": "thinking", "label": "t1"},
            {"type": "plan", "label": "p1"},
        ]
        result = _find_last_tool_step(steps)
        assert result["label"] == "first"

    def test_steps_missing_type_key(self):
        """Steps without a 'type' key are safely skipped."""
        steps = [
            {"label": "no type"},
            {"type": "tool", "label": "has_type"},
        ]
        result = _find_last_tool_step(steps)
        assert result["label"] == "has_type"


# ── _canonical_agent_type ─────────────────────────────────────────────────


class TestCanonicalAgentType:
    """Tests for ``_canonical_agent_type``.

    The Apr-20 review removed the silent ``except Exception`` wrapper on
    the registry lookup (CLAUDE.md "fail loudly" rule). These tests
    document the resulting contract: pass-through when no registry is
    set, delegate to ``registry.canonical_id`` otherwise, and propagate
    AttributeError if a caller wires up a registry that doesn't honour
    the protocol.
    """

    def test_returns_input_when_registry_unset(self):
        """No registry installed → return the input unchanged.

        Pre-discovery (e.g. early server startup, test fixtures that
        haven't called ``set_agent_registry``) falls through this path.
        """
        set_agent_registry(None)
        try:
            assert _canonical_agent_type("anything") == "anything"
        finally:
            set_agent_registry(None)

    def test_delegates_to_canonical_id(self):
        """Healthy registry → result of ``registry.canonical_id``.

        Confirms the alias-resolution contract with a fake registry:
        old IDs map to canonical, unknowns pass through.
        """

        class FakeRegistry:
            def canonical_id(self, agent_id: str) -> str:
                return {"chat-lite": "chat"}.get(agent_id, agent_id)

        set_agent_registry(FakeRegistry())
        try:
            assert _canonical_agent_type("chat-lite") == "chat"
            assert _canonical_agent_type("unknown") == "unknown"
        finally:
            set_agent_registry(None)

    def test_propagates_attributeerror_when_registry_lacks_canonical_id(self):
        """A registry mock without ``canonical_id`` must surface the
        AttributeError loudly — the prior ``except Exception: return
        agent_type`` swallowed this and produced silent cache thrash.

        Ratchets the Apr-20 review fix: any future regression that
        reintroduces the broad except will fail this test.
        """
        set_agent_registry(MagicMock(spec=[]))  # no methods on the mock
        try:
            with pytest.raises(AttributeError):
                _canonical_agent_type("chat-lite")
        finally:
            set_agent_registry(None)


# ── Regression: registered-agent streaming path must not double-index ─────


class TestSessionAgentKwargsShape:
    """Tests for _session_agent_kwargs() — the single source of truth for
    per-session ChatAgentConfig field forwarding.

    Every registered-agent code path in ``_chat_helpers`` builds its factory
    kwargs via this helper. The streaming registered-agent branch
    (``_stream_chat_response``) passes ``rag_file_paths=[]`` on purpose so
    ``ChatAgent.__init__`` skips its silent auto-index, leaving the
    user-visible ``_index_rag_with_progress`` call as the sole indexer.
    Passing the real paths here double-indexes on every cache miss and
    surfaces a noisy "Used tool index_documents" card for a 0-work hash-cache
    hit on casual chat turns — see the commit that added this test.
    """

    def test_returns_exactly_the_session_fields(self):
        from gaia.ui._chat_helpers import _session_agent_kwargs

        kwargs = _session_agent_kwargs(
            rag_file_paths=["/a.md"],
            library_paths=["/b.md"],
            allowed=["/root"],
            session_id="sess-1",
        )
        assert set(kwargs) == {
            "rag_documents",
            "library_documents",
            "allowed_paths",
            "ui_session_id",
            "dynamic_tools",
        }

    def test_dynamic_tools_defaults_off_and_forwards_when_set(self):
        """The Beta tool-loader toggle (#1798) defaults off and round-trips
        through the helper — the field that makes the UI toggle apply."""
        from gaia.ui._chat_helpers import _session_agent_kwargs

        off = _session_agent_kwargs(
            rag_file_paths=[],
            library_paths=[],
            allowed=["/root"],
            session_id="s",
        )
        assert off["dynamic_tools"] is False

        on = _session_agent_kwargs(
            rag_file_paths=[],
            library_paths=[],
            allowed=["/root"],
            session_id="s",
            dynamic_tools=True,
        )
        assert on["dynamic_tools"] is True

    def test_empty_rag_file_paths_propagates_to_rag_documents(self):
        """Invariant checked by ``test_streaming_registered_agent_does_not_double_index``.

        If this ever returns a non-empty list when given ``[]``, the
        regression test below still catches it — but this is the cheapest
        failing assertion to debug.
        """
        from gaia.ui._chat_helpers import _session_agent_kwargs

        kwargs = _session_agent_kwargs(
            rag_file_paths=[],
            library_paths=["/lib.md"],
            allowed=["/root"],
            session_id="sess-1",
        )
        assert kwargs["rag_documents"] == []
        assert kwargs["library_documents"] == ["/lib.md"]

    def test_rag_file_paths_round_trip_when_nonempty(self):
        """Built-in chat's non-streaming path DOES forward the real list —
        verify the helper itself is a faithful passthrough and the
        caller-side choice of ``[]`` vs the real list is what varies."""
        from gaia.ui._chat_helpers import _session_agent_kwargs

        paths = ["/docs/a.pdf", "/docs/b.txt"]
        kwargs = _session_agent_kwargs(
            rag_file_paths=paths,
            library_paths=[],
            allowed=["/docs"],
            session_id="sess-2",
        )
        assert kwargs["rag_documents"] == paths


class TestStreamingRegisteredAgentDoesNotDoubleIndex:
    """Source-shape regression: ensure the streaming registered-agent branch
    forwards ``rag_file_paths=[]`` into ``_session_agent_kwargs``.

    We check the source rather than driving ``_stream_chat_response`` because
    the streaming generator threads through Lemonade HTTP, SSE queues, and a
    background thread that all require heavy mocking — a shape test gives
    the regression the same ratcheting effect at a fraction of the cost.
    """

    def test_streaming_registered_agent_passes_empty_rag_file_paths(self):
        import re
        from pathlib import Path as _Path

        src = (
            _Path(__file__).parents[4] / "src" / "gaia" / "ui" / "_chat_helpers.py"
        ).read_text(encoding="utf-8")

        # Locate the streaming registered-agent factory call. It's the only
        # ``registry.create_agent(`` invocation in ``_stream_chat_response``
        # that passes ``streaming=True`` to ``_build_create_kwargs``.
        m = re.search(
            r"registry\.create_agent\(\s*agent_type,\s*\*\*_build_create_kwargs\("
            r"[^)]*?streaming=True[^)]*?\),\s*\*\*_session_agent_kwargs\(([^)]+)\)",
            src,
            re.DOTALL,
        )
        assert m, (
            "Could not locate the streaming registered-agent factory call "
            "in _chat_helpers.py. Did the call-site structure change? "
            "Update this regex or re-assert the invariant another way."
        )
        block = m.group(1)
        # The critical invariant: rag_file_paths must be [] (literal empty
        # list), NOT the outer rag_file_paths variable. Otherwise
        # ChatAgent.__init__ auto-indexes AND _index_rag_with_progress
        # indexes again, double-surfacing the index_documents SSE card.
        assert "rag_file_paths=[]" in block.replace(" ", "").replace("\n", ""), (
            "Streaming registered-agent branch must pass rag_file_paths=[]. "
            "Passing the real list causes double-indexing on every cache "
            "miss. See src/gaia/ui/_chat_helpers.py comment at that call "
            "site for the full rationale."
        )


# ── _stamp_chat_identity ─────────────────────────────────────────────
#
# Regression coverage for the activation-filter bypass discovered during #1005
# UI verification: ``_chat_helpers`` constructs ``ChatAgent(config)`` directly
# at four call sites (built-in Chat streaming/non-streaming + two fallback
# branches). Without stamping the namespaced id, those instances appear to the
# activation filter as ``_gaia_namespaced_agent_id is None``, and
# ``Agent._active_mcp_servers`` falls back to "ad-hoc agent — show every MCP
# server unfiltered". The activations.json ledger gets written but ignored,
# so deactivating ``mcp-github`` for ``installed:chat`` has no effect on what the
# UI's Chat sees.


class TestStampBuiltinChatIdentity:
    """Tests for _stamp_chat_identity().

    The stamp must hit the ChatAgentConfig BEFORE ChatAgent.__init__ runs,
    because ``__init__`` calls ``_register_tools`` which reads the namespaced
    id to decide which MCP servers' tools to surface. Stamping the instance
    after construction is too late and reproduces the bypass bug.
    """

    def test_stamps_field_on_fresh_config(self):
        pytest.importorskip("gaia_agent_chat")
        from gaia_agent_chat.agent import ChatAgentConfig

        from gaia.ui._chat_helpers import _stamp_chat_identity

        config = ChatAgentConfig()
        assert config.namespaced_agent_id is None
        _stamp_chat_identity(config)
        assert config.namespaced_agent_id == "installed:chat"

    def test_is_idempotent_no_overwrite_when_already_set(self):
        # Callers that pre-fill the field with a custom id (e.g. a future
        # custom-Chat wrapper) must NOT be clobbered.
        pytest.importorskip("gaia_agent_chat")
        from gaia_agent_chat.agent import ChatAgentConfig

        from gaia.ui._chat_helpers import _stamp_chat_identity

        config = ChatAgentConfig(namespaced_agent_id="custom:abc:chat")
        _stamp_chat_identity(config)
        assert config.namespaced_agent_id == "custom:abc:chat"

    def test_chat_agent_init_propagates_config_to_instance_attr_before_super(
        self,
    ):
        """``ChatAgent.__init__`` must propagate ``config.namespaced_agent_id``
        to ``self._gaia_namespaced_agent_id`` BEFORE invoking
        ``super().__init__`` (which calls ``_register_tools`` → loads MCP
        servers with the activation filter). A post-super() stamp would be
        too late and silently breaks #1005.

        Asserted by source inspection — the full agent stack is too heavy
        to instantiate in a unit test, but the prelude pattern is stable
        enough to grep for. If someone reorders ChatAgent.__init__ and
        moves the stamp past super(), this test fails before the
        activation filter silently regresses at runtime.
        """
        from pathlib import Path as _Path

        # ChatAgent ships as the standalone gaia-agent-chat wheel (#1102); its
        # source now lives under hub/agents/python/chat/.
        src = (
            _Path(__file__).parents[4]
            / "hub"
            / "agents"
            / "python"
            / "chat"
            / "gaia_agent_chat"
            / "agent.py"
        ).read_text(encoding="utf-8")
        init_start = src.index("def __init__(self, config: Optional[ChatAgentConfig]")
        super_init = src.index("super().__init__(", init_start)
        prelude = src[init_start:super_init]
        assert (
            "self._gaia_namespaced_agent_id = config.namespaced_agent_id" in prelude
        ), (
            "ChatAgent.__init__ must set self._gaia_namespaced_agent_id "
            "from config.namespaced_agent_id BEFORE super().__init__() — "
            "otherwise _register_tools loads MCP tools without the "
            "activation filter and #1005 silently breaks for every "
            "UI Chat session."
        )

    def test_every_direct_ChatAgent_construction_is_pre_stamped(self):
        """Every ``agent = ChatAgent(config)`` in _chat_helpers.py must be
        PRECEDED by ``_stamp_chat_identity(config)`` (within the
        last 5 non-blank lines).

        Structural guard: a fifth direct construction site added without
        the prior stamp would silently re-introduce the activation-filter
        bypass. This catches it at PR time instead of user-bug-report
        time.

        Why "before, not after": ``_register_tools`` runs inside
        ``ChatAgent.__init__``; the activation filter consults the
        namespaced id at that point. Stamping the instance after
        construction is too late — the unfiltered tool set has already
        been registered.
        """
        import re
        from pathlib import Path as _Path

        src = (
            _Path(__file__).parents[4] / "src" / "gaia" / "ui" / "_chat_helpers.py"
        ).read_text(encoding="utf-8")

        lines = src.splitlines()
        offenders = []
        for i, line in enumerate(lines):
            if re.search(r"\bagent\s*=\s*ChatAgent\(", line):
                # Look BACKWARD up to 5 non-blank lines for the stamp call.
                window = []
                j = i - 1
                while j >= 0 and len(window) < 5:
                    if lines[j].strip():
                        window.append(lines[j])
                    j -= 1
                if not any("_stamp_chat_identity(config)" in w for w in window):
                    offenders.append(
                        f"line {i + 1}: {line.strip()!r} — missing prior "
                        f"_stamp_chat_identity(config)"
                    )

        assert not offenders, (
            "Every direct ``agent = ChatAgent(config)`` in _chat_helpers.py "
            "must be PRECEDED by ``_stamp_chat_identity(config)`` "
            "(within the last 5 non-blank lines) so the per-agent "
            "activation filter (#1005) fires correctly. Post-construction "
            "stamping is too late — ChatAgent.__init__ runs "
            "_register_tools immediately and the filter would silently "
            "bypass. Offending sites:\n  - " + "\n  - ".join(offenders)
        )
