# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A file a skill's own tool saves counts as saved by the turn.

The completion check knew the built-in write tools by name. A skill tool that
saved the requested file through ``gaia.skills.context`` left nothing on that
record, so a turn with the file on disk ended "incomplete: No successful write
to `deck.pptx` is recorded for this turn".
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.completion import CompletionEvidence

from .test_skill_context import (  # noqa: F401
    _load,
    agent,
    allowed,
    home,
)

pytestmark = pytest.mark.usefixtures("user_terminal")

_SAVES = (
    "with ctx.open_write(file_path, 'wb') as fh:\n"
    "    fh.write(b'deck text')\n"
    "return {'status': 'success', 'saved': file_path}\n"
)
_ONLY_CHECKS = "ctx.check_write(file_path)\nreturn {'status': 'success'}\n"


def _script(agent, *turns):
    left = list(turns)

    def send(*_a, **_kw):
        assert left, "unexpected model call"
        return MagicMock(text=json.dumps(left.pop(0)), stats={})

    agent.chat.send_messages.side_effect = send
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    return left


def test_the_turn_is_complete_when_a_skill_tool_saved_the_file(
    agent, tmp_path, allowed, monkeypatch
):
    monkeypatch.chdir(allowed)
    _load(agent, tmp_path, "deck-probe", [], _SAVES)
    deck = allowed / "deck.pdf"
    left = _script(
        agent,
        {"tool": "deck-probe/probe", "tool_args": {"file_path": str(deck)}},
        {"answer": f"I saved the deck to `{deck}`."},
    )

    result = agent.process_query(f"Build the deck and save it to `{deck}`")

    assert not left
    assert deck.read_bytes() == b"deck text"
    assert "No successful write" not in result["result"]
    assert result["completion_gaps"] == []
    assert result["status"] == "success", result["result"]


def test_a_path_that_was_only_cleared_is_not_a_write(
    agent, tmp_path, allowed, monkeypatch
):
    monkeypatch.chdir(allowed)
    _load(agent, tmp_path, "check-probe", [], _ONLY_CHECKS)
    deck = allowed / "deck.pdf"
    claim = f"I saved the deck to `{deck}`."
    _script(
        agent,
        {"tool": "check-probe/probe", "tool_args": {"file_path": str(deck)}},
        *[{"answer": claim}] * 3,
    )

    result = agent.process_query(f"Build the deck and save it to `{deck}`")

    assert not deck.exists()
    assert result["status"] == "incomplete"
    assert "No successful write" in result["result"]


def test_the_ledger_counts_only_a_file_that_changed(tmp_path):
    evidence = CompletionEvidence("Save it to out.pptx", str(tmp_path))
    out = tmp_path / "out.pptx"

    evidence.expect_skill_write(str(out))
    evidence.record("some-skill/build", {}, {"status": "success"}, True)
    assert evidence.written_files() == []

    evidence.expect_skill_write(str(out))
    out.write_bytes(b"deck")
    evidence.record("some-skill/build", {}, {"status": "success"}, True)
    assert [p.lower() for p in evidence.written_files()] == [str(out).lower()]

    # Nothing carries over to a later call that wrote nothing.
    evidence.record("read_file", {"file_path": "x"}, {"status": "success"}, True)
    assert len(evidence.written_files()) == 1
