# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A fixed bug must stop being replayed into every prompt.

Tool errors are auto-stored as knowledge and retrieved into every later system
prompt under "Known errors to avoid". Nothing expired them and nothing lowered
their confidence, so one bad moment became permanent doctrine.

Observed on the flagship: after the `run_shell_command` hang was fixed, the
prompt still carried

    - run_shell_command: Tool 'run_shell_command' did not return within 180s
      and was abandoned...

and the agent kept telling users that shell access and the network were
unavailable — while running the same commands successfully. The same mechanism
is the likeliest cause of an intermittent "there isn't a skill called
github-triage available" from a session that had recorded that error before the
skill became discoverable.

Two rules restore it:

* transient failures (timeouts, dropped connections) are never persisted — they
  describe a moment, not a rule this agent must obey;
* a tool that succeeds retires the errors stored against it, because a call that
  just worked is direct evidence the tool is not broken.
"""

from __future__ import annotations

import pytest

from gaia.agents.base.memory import MemoryMixin


class TestTransientErrorsAreNotPersisted:
    """A moment must not become doctrine."""

    @pytest.mark.parametrize(
        "message",
        [
            "Tool 'run_shell_command' did not return within 180s and was abandoned.",
            "Command timed out after 30 seconds",
            "HTTPConnectionPool(host='localhost', port=13305): Max retries exceeded",
            "[WinError 10061] Connection refused",
            "The service is temporarily unavailable",
            "Request cancelled",
        ],
    )
    def test_transient_messages_are_recognised(self, message):
        assert MemoryMixin._is_transient_error(message)

    @pytest.mark.parametrize(
        "message",
        [
            "Command 'cmd' is not in the allowed list for security reasons",
            "No skill named 'note-taker'.",
            "Missing required arguments for write_file: path",
            "Access denied: C:\\Windows is not in allowed paths",
        ],
    )
    def test_durable_constraints_are_still_remembered(self, message):
        """These are rules about this agent, and worth carrying forward."""
        assert not MemoryMixin._is_transient_error(message)

    def test_matching_is_case_insensitive(self):
        assert MemoryMixin._is_transient_error("CONNECTION REFUSED")
        assert MemoryMixin._is_transient_error("Timed Out")


class _Store:
    """Minimal stand-in for the knowledge store's two methods used here."""

    def __init__(self, entries):
        self.entries = list(entries)
        self.deleted = []

    def get_by_category(self, category, context=None, limit=10):
        assert category == "error"
        return list(self.entries)

    def delete(self, knowledge_id):
        self.deleted.append(knowledge_id)
        self.entries = [e for e in self.entries if e["id"] != knowledge_id]
        return True


class _Host(MemoryMixin):
    def __init__(self, store):
        self._memory_store = store
        self._memory_context = "global"


class TestSuccessRetiresStaleErrors:
    def test_a_working_tool_drops_its_recorded_errors(self):
        store = _Store(
            [
                {
                    "id": "1",
                    "content": "run_shell_command: Command 'cmd' is not allowed",
                },
                {"id": "2", "content": "run_shell_command: something else went wrong"},
            ]
        )

        _Host(store)._forget_errors_for_tool("run_shell_command")

        assert store.deleted == ["1", "2"]
        assert store.entries == []

    def test_other_tools_are_untouched(self):
        """Scoped by the stored 'tool: ' prefix, not a substring match."""
        store = _Store(
            [
                {"id": "1", "content": "run_shell_command: not allowed"},
                {"id": "2", "content": "load_skill: No skill named 'x'"},
            ]
        )

        _Host(store)._forget_errors_for_tool("load_skill")

        assert store.deleted == ["2"]
        assert [e["id"] for e in store.entries] == ["1"]

    def test_a_broken_store_never_breaks_a_good_call(self):
        """Bookkeeping runs on the success path — it must not raise there."""

        class Exploding(_Store):
            def get_by_category(self, *a, **k):
                raise RuntimeError("database is locked")

        _Host(Exploding([]))._forget_errors_for_tool("run_shell_command")
