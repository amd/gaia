# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the Claude Code session harvester.

Most of these are regression tests for metric bugs that produced a published
report with four wrong numbers, including its headline. Each one asserts that
a metric measures what its name claims.
"""

import json

import pytest

from gaia.factory.harvest.analyze import (
    analyze,
    classify_error,
    error_profile,
    repair_loops,
    thrash_runs,
)
from gaia.factory.harvest.reader import Step, Trace, read_session, tool_family


def write_jsonl(path, records):
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return path


def assistant(tool, args, use_id, msg_id=None):
    return {
        "type": "assistant",
        "timestamp": "2026-08-01T00:00:00Z",
        "message": {
            "role": "assistant",
            "id": msg_id or f"msg_{use_id}",
            "model": "claude-opus-5",
            "content": [
                {"type": "tool_use", "id": use_id, "name": tool, "input": args}
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 20,
            },
        },
    }


def result(use_id, text="ok", is_error=False):
    block = {"tool_use_id": use_id, "type": "tool_result", "content": text}
    if is_error:
        block["is_error"] = True
    return {
        "type": "user",
        "timestamp": "2026-08-01T00:00:01Z",
        "message": {"role": "user", "content": [block]},
    }


def user_turn(text, is_meta=False):
    rec = {
        "type": "user",
        "timestamp": "2026-08-01T00:00:00Z",
        "message": {"role": "user", "content": text},
    }
    if is_meta:
        rec["isMeta"] = True
    return rec


class TestToolFamily:
    def test_known_and_mcp_and_unknown(self):
        assert tool_family("Bash") == "shell"
        assert tool_family("Edit") == "edit"
        assert tool_family("mcp__claudia__list") == "mcp"
        assert tool_family("SomethingNew") == "other"


class TestReader:
    def test_parses_steps_outcomes_and_usage(self, tmp_path):
        p = write_jsonl(
            tmp_path / "s.jsonl",
            [
                user_turn("do the thing"),
                assistant("Bash", {"command": "ls"}, "t1"),
                result("t1"),
                assistant("Edit", {"file_path": "a.py"}, "t2"),
                result("t2", "no such file", is_error=True),
            ],
        )
        trace = read_session(p)
        assert trace.goal == "do the thing"
        assert trace.total_calls == 2
        assert trace.success_count == 1
        assert trace.attempt_count == 2
        assert trace.usage.output_tokens == 10  # two assistant turns
        assert trace.usage.cache_read_tokens == 200

    def test_usage_is_deduped_per_message_id(self, tmp_path):
        """Claude Code emits one record per content block, repeating usage.

        Summing per record double-counted every token roughly 2x.
        """
        blocks = [
            assistant("Bash", {"command": "ls"}, "t1", msg_id="msg_same"),
            assistant("Read", {"file_path": "a.py"}, "t2", msg_id="msg_same"),
            assistant("Edit", {"file_path": "a.py"}, "t3", msg_id="msg_other"),
        ]
        p = write_jsonl(tmp_path / "s.jsonl", [user_turn("go")] + blocks)
        trace = read_session(p)
        assert trace.total_calls == 3  # every tool_use still counted
        assert trace.assistant_turns == 2  # but only two API responses
        assert trace.usage.output_tokens == 10  # 5 per message, not per record
        assert trace.usage.cache_read_tokens == 200

    def test_unresolved_call_is_not_counted_as_success(self, tmp_path):
        """A tool_use with no tool_result is unknown, not successful."""
        p = write_jsonl(
            tmp_path / "s.jsonl",
            [user_turn("go"), assistant("Bash", {"command": "ls"}, "t1")],
        )
        trace = read_session(p)
        assert trace.total_calls == 1
        assert trace.unresolved_count == 1
        assert trace.attempt_count == 0
        assert trace.success_count == 0

    def test_meta_turns_are_not_human_prompts(self, tmp_path):
        """Harness-injected turns inflated the correction metric 8x."""
        p = write_jsonl(
            tmp_path / "s.jsonl",
            [
                user_turn("real goal"),
                assistant("Bash", {"command": "ls"}, "t1"),
                result("t1"),
                user_turn("Stop hook feedback: that is wrong", is_meta=True),
            ],
        )
        trace = read_session(p)
        assert trace.prompts == ["real goal"]

    def test_no_assistant_activity_returns_none(self, tmp_path):
        p = write_jsonl(tmp_path / "s.jsonl", [user_turn("hello?")])
        assert read_session(p) is None

    def test_malformed_lines_are_counted_not_hidden(self, tmp_path):
        p = tmp_path / "s.jsonl"
        with p.open("w", encoding="utf-8") as fh:
            fh.write("{not json\n")
            fh.write(json.dumps(user_turn("go")) + "\n")
            fh.write(json.dumps(assistant("Bash", {"command": "ls"}, "t1")) + "\n")
        trace = read_session(p)
        assert trace.skipped_lines == 1

    def test_arg_hash_distinguishes_different_arguments(self, tmp_path):
        """Identity must cover the FULL argument object, not one key."""
        p = write_jsonl(
            tmp_path / "s.jsonl",
            [
                user_turn("go"),
                assistant("Edit", {"file_path": "a.py", "old_string": "x"}, "t1"),
                result("t1"),
                assistant("Edit", {"file_path": "a.py", "old_string": "y"}, "t2"),
                result("t2"),
            ],
        )
        trace = read_session(p)
        assert trace.steps[0].arg_digest == trace.steps[1].arg_digest  # same file
        assert trace.steps[0].arg_hash != trace.steps[1].arg_hash  # different edit


class TestErrorClassification:
    def test_timeout_wins_over_generic_command_failure(self):
        """A Bash timeout also carries a non-zero exit code."""
        assert (
            classify_error("Exit code 143 Command timed out after 2m 0s") == "timeout"
        )

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("no such file or directory", "file_not_found"),
            ("File has been modified since read, read it first", "stale_read"),
            ("The user doesn't want to proceed", "user_rejected"),
            ("exit code 1", "command_failed"),
            ("something entirely novel", "unclassified"),
        ],
    )
    def test_classes(self, text, expected):
        assert classify_error(text) == expected


def _trace_of(steps):
    t = Trace(session_id="s", project="p")
    t.steps = steps
    return t


def _step(tool, family, arg_hash, digest="", ok=True):
    return Step(tool=tool, family=family, ok=ok, arg_hash=arg_hash, arg_digest=digest)


class TestThrash:
    def test_distinct_edits_to_one_file_are_not_thrash(self):
        """The bug that overstated thrash ~40x."""
        t = _trace_of(
            [
                _step("Edit", "edit", "h1", "a.py"),
                _step("Edit", "edit", "h2", "a.py"),
                _step("Edit", "edit", "h3", "a.py"),
            ]
        )
        assert thrash_runs(t) == []

    def test_identical_repeated_call_is_thrash(self):
        t = _trace_of([_step("Read", "read", "same", "a.py") for _ in range(3)])
        runs = thrash_runs(t)
        assert len(runs) == 1
        assert runs[0][1] == 3


class TestRepairLoops:
    def test_same_file_edit_verify_edit_counts_once(self):
        t = _trace_of(
            [
                _step("Edit", "edit", "h1", "a.py"),
                _step("Bash", "shell", "h2", "pytest"),
                _step("Edit", "edit", "h3", "a.py"),
            ]
        )
        assert repair_loops(t) == 1

    def test_edits_to_different_files_are_not_a_repair_loop(self):
        t = _trace_of(
            [
                _step("Edit", "edit", "h1", "a.py"),
                _step("Bash", "shell", "h2", "pytest"),
                _step("Edit", "edit", "h3", "b.py"),
            ]
        )
        assert repair_loops(t) == 0

    def test_burst_of_edits_does_not_multiply_the_count(self):
        """Overlapping windows previously counted one cycle per leading edit."""
        t = _trace_of(
            [
                _step("Edit", "edit", "h1", "a.py"),
                _step("Edit", "edit", "h2", "a.py"),
                _step("Edit", "edit", "h3", "a.py"),
                _step("Bash", "shell", "h4", "pytest"),
                _step("Edit", "edit", "h5", "a.py"),
            ]
        )
        assert repair_loops(t) == 1


class TestScopeAndProfile:
    def _parent_with_subagent(self):
        parent = _trace_of([_step("Bash", "shell", "p1", "ls")])
        sub = Trace(session_id="sub", project="p", kind="subagent")
        sub.steps = [
            _step("Read", "read", "s1", "a.py"),
            _step("Read", "read", "s2", "b.py", ok=False),
        ]
        parent.subagents.append(sub)
        return parent

    def test_walk_includes_subagents(self):
        parent = self._parent_with_subagent()
        assert len(list(parent.walk())) == 2

    def test_analyze_covers_subagent_calls(self):
        """Excluding subagents silently reported on a fraction of the corpus."""
        parent = self._parent_with_subagent()
        wide = analyze([parent], include_subagents=True)
        narrow = analyze([parent], include_subagents=False)
        assert wide["scope"]["tool_calls_in_scope"] == 3
        assert narrow["scope"]["tool_calls_in_scope"] == 1

    def test_error_profile_separates_main_from_subagent(self):
        prof = error_profile([self._parent_with_subagent()])
        assert prof["main_vs_subagent"]["subagent_failures"] == 1
        assert prof["main_vs_subagent"]["main_failures"] == 0
        assert prof["by_family"]["read"]["failures"] == 1
