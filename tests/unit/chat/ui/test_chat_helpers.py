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
    _compute_allowed_paths,
    _find_last_tool_step,
    _resolve_rag_paths,
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

    def test_without_document_ids_returns_library(self, mock_db):
        mock_db.list_documents.return_value = [
            {"filepath": "/lib/x.pdf"},
            {"filepath": "/lib/y.md"},
        ]

        rag_paths, library_paths = _resolve_rag_paths(mock_db, [])
        assert rag_paths == []
        assert library_paths == ["/lib/x.pdf", "/lib/y.md"]

    def test_without_document_ids_skips_no_filepath(self, mock_db):
        mock_db.list_documents.return_value = [
            {"filepath": "/lib/x.pdf"},
            {"id": "orphan"},  # no filepath key
            {"filepath": ""},  # empty filepath (falsy)
        ]

        _, library_paths = _resolve_rag_paths(mock_db, [])
        assert library_paths == ["/lib/x.pdf"]

    def test_empty_document_ids_empty_library(self, mock_db):
        mock_db.list_documents.return_value = []
        rag_paths, library_paths = _resolve_rag_paths(mock_db, [])
        assert rag_paths == []
        assert library_paths == []

    def test_none_document_ids_treated_as_empty(self, mock_db):
        """None is falsy like [], so it falls through to library path."""
        mock_db.list_documents.return_value = [{"filepath": "/lib/a.pdf"}]
        rag_paths, library_paths = _resolve_rag_paths(mock_db, None)
        assert rag_paths == []
        assert library_paths == ["/lib/a.pdf"]

    def test_document_with_filepath_none_skipped(self, mock_db):
        """filepath=None is falsy and should be skipped."""
        mock_db.get_document.return_value = {"filepath": None}
        rag_paths, _ = _resolve_rag_paths(mock_db, ["doc1"])
        assert rag_paths == []


# ── _compute_allowed_paths ────────────────────────────────────────────────


class TestComputeAllowedPaths:
    """Tests for _compute_allowed_paths()."""

    def test_empty_paths_returns_home(self):
        result = _compute_allowed_paths([])
        assert len(result) == 1
        assert result[0] == str(Path.home())

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
