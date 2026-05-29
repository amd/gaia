# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.eval.audit``.

Tests the deterministic architecture audit helpers that inspect
``_chat_helpers.py`` for constants and patterns, plus the full
``run_audit()`` rollup.
"""

import textwrap

import pytest

from gaia.eval.audit import (
    audit_agent_persistence,
    audit_chat_helpers,
    audit_tool_results_in_history,
    run_audit,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_helpers(tmp_path, source):
    """Write a fake _chat_helpers.py into tmp_path and return its Path."""
    p = tmp_path / "_chat_helpers.py"
    p.write_text(textwrap.dedent(source), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# audit_chat_helpers
# ---------------------------------------------------------------------------


class TestAuditChatHelpers:
    def test_extracts_max_constants(self, tmp_path, monkeypatch):
        src = """\
            _MAX_HISTORY_PAIRS = 10
            _MAX_MSG_CHARS = 2000
            _OTHER = 42
        """
        p = _write_helpers(tmp_path, src)
        # Monkeypatch GAIA_ROOT so audit_chat_helpers reads our fake file
        monkeypatch.setattr(
            "gaia.eval.audit.GAIA_ROOT",
            # Need a root where <root>/src/gaia/ui/_chat_helpers.py resolves to our file
            # Easier: just monkeypatch the whole function's file path
            tmp_path,
        )
        # Since audit_chat_helpers hardcodes the path, we need to
        # create the expected directory structure.
        target = tmp_path / "src" / "gaia" / "ui"
        target.mkdir(parents=True)
        (target / "_chat_helpers.py").write_text(textwrap.dedent(src), encoding="utf-8")
        result = audit_chat_helpers()
        assert result["_MAX_HISTORY_PAIRS"] == 10
        assert result["_MAX_MSG_CHARS"] == 2000
        assert "_OTHER" not in result

    def test_returns_empty_on_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.eval.audit.GAIA_ROOT", tmp_path)
        result = audit_chat_helpers()
        assert result == {}


# ---------------------------------------------------------------------------
# audit_agent_persistence
# ---------------------------------------------------------------------------


class TestAuditAgentPersistence:
    def test_stateless_per_message(self, tmp_path):
        p = _write_helpers(tmp_path, 'agent = ChatAgent(model="test")\n')
        assert audit_agent_persistence(p) == "stateless_per_message"

    def test_unknown_when_no_chatagent(self, tmp_path):
        p = _write_helpers(tmp_path, "x = 1\n")
        assert audit_agent_persistence(p) == "unknown"

    def test_unknown_on_missing_file(self, tmp_path):
        assert audit_agent_persistence(tmp_path / "nonexistent.py") == "unknown"


# ---------------------------------------------------------------------------
# audit_tool_results_in_history
# ---------------------------------------------------------------------------


class TestAuditToolResultsInHistory:
    def test_detects_pattern(self, tmp_path):
        src = """\
            agent_steps = agent.run()
            messages.append({"role": "tool", "content": agent_steps})
        """
        p = _write_helpers(tmp_path, src)
        assert audit_tool_results_in_history(p) is True

    def test_false_when_missing_agent_steps(self, tmp_path):
        src = 'messages.append({"role": "user"})\n'
        p = _write_helpers(tmp_path, src)
        assert audit_tool_results_in_history(p) is False

    def test_false_on_missing_file(self, tmp_path):
        assert audit_tool_results_in_history(tmp_path / "nope.py") is False


# ---------------------------------------------------------------------------
# run_audit
# ---------------------------------------------------------------------------


class TestRunAudit:
    def test_returns_audit_key(self, tmp_path, monkeypatch):
        # Create a minimal _chat_helpers.py that satisfies all checks
        target = tmp_path / "src" / "gaia" / "ui"
        target.mkdir(parents=True)
        src = textwrap.dedent("""\
            _MAX_HISTORY_PAIRS = 3
            _MAX_MSG_CHARS = 500
            agent = ChatAgent(model="test")
            agent_steps = agent.run()
            messages.append({"role": "tool", "content": agent_steps})
        """)
        (target / "_chat_helpers.py").write_text(src, encoding="utf-8")
        monkeypatch.setattr("gaia.eval.audit.GAIA_ROOT", tmp_path)

        result = run_audit()
        assert "architecture_audit" in result
        audit = result["architecture_audit"]
        assert audit["history_pairs"] == 3
        assert audit["max_msg_chars"] == 500
        assert audit["tool_results_in_history"] is True
        assert audit["agent_persistence"] == "stateless_per_message"

    def test_recommendations_on_low_history(self, tmp_path, monkeypatch):
        target = tmp_path / "src" / "gaia" / "ui"
        target.mkdir(parents=True)
        src = "_MAX_HISTORY_PAIRS = 2\n"
        (target / "_chat_helpers.py").write_text(src, encoding="utf-8")
        monkeypatch.setattr("gaia.eval.audit.GAIA_ROOT", tmp_path)

        result = run_audit()
        recs = result["architecture_audit"]["recommendations"]
        rec_ids = [r["id"] for r in recs]
        assert "increase_history_pairs" in rec_ids

    def test_blocked_scenarios_on_low_msg_chars(self, tmp_path, monkeypatch):
        target = tmp_path / "src" / "gaia" / "ui"
        target.mkdir(parents=True)
        src = "_MAX_MSG_CHARS = 500\n"
        (target / "_chat_helpers.py").write_text(src, encoding="utf-8")
        monkeypatch.setattr("gaia.eval.audit.GAIA_ROOT", tmp_path)

        result = run_audit()
        blocked = result["architecture_audit"]["blocked_scenarios"]
        assert any(b["scenario"] == "cross_turn_file_recall" for b in blocked)

    def test_no_recommendations_when_values_sufficient(self, tmp_path, monkeypatch):
        target = tmp_path / "src" / "gaia" / "ui"
        target.mkdir(parents=True)
        src = textwrap.dedent("""\
            _MAX_HISTORY_PAIRS = 20
            _MAX_MSG_CHARS = 5000
            agent = ChatAgent(model="test")
            agent_steps = agent.run()
            messages.append({"role": "tool", "content": agent_steps})
        """)
        (target / "_chat_helpers.py").write_text(src, encoding="utf-8")
        monkeypatch.setattr("gaia.eval.audit.GAIA_ROOT", tmp_path)

        result = run_audit()
        assert result["architecture_audit"]["recommendations"] == []
        assert result["architecture_audit"]["blocked_scenarios"] == []
