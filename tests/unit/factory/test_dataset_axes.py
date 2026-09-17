# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Capability-axis, difficulty and partition tests.

Synthetic records only. Each axis test asserts the detector fires on the shape
the corpus analysis says it should, and — more importantly — does *not* fire on
the near-miss, because an axis that tags everything tells a harness designer
nothing.
"""

from gaia.factory.dataset import partition as part
from gaia.factory.dataset.axes import ALL_AXES, axes_for, difficulty_for
from gaia.factory.dataset.extract import split_shell_segments


def record(
    tool="Bash",
    arguments=None,
    depth=0,
    width=None,
    quality="succeeded",
    obs_chars=100,
    calls=None,
):
    if calls is None:
        args = arguments if arguments is not None else {"command": "ls"}
        call = {
            "tool": tool,
            "family": "shell" if tool == "Bash" else "read",
            "arguments": args,
        }
        call["shell_segments"] = (
            split_shell_segments(args.get("command", "")) if tool == "Bash" else []
        )
        calls = [call]
    return {
        "depth_index": depth,
        "action": {"width": width if width is not None else len(calls), "calls": calls},
        "observation": [{"chars": obs_chars} for _ in calls],
        "outcome": {"reference_quality": quality},
    }


def test_every_record_probes_tool_selection():
    assert "tool_selection" in axes_for(record(), None, False)


def test_error_recovery_needs_a_failed_predecessor():
    """Recovery is a property of the transition, not of the record."""
    failed = record(quality="errored")
    assert "error_recovery" in axes_for(record(), failed, False)
    assert "error_recovery" not in axes_for(record(), record(), False)
    assert "error_recovery" not in axes_for(record(), None, False)


def test_compound_command_probes_argument_construction():
    compound = record(arguments={"command": "cd /repo && pytest -q && ruff check ."})
    assert "argument_construction" in axes_for(compound, None, False)


def test_scaffolding_alone_is_not_argument_construction():
    """Two segments where one is `cd` is one substantive action, not composition."""
    axes = axes_for(record(arguments={"command": "cd /repo && ls"}), None, False)
    assert "argument_construction" not in axes
    assert "state_reconstruction" in axes


def test_self_truncation_probes_context_management():
    """54% of shell commands truncate their own output; nobody asked them to."""
    trunc = record(arguments={"command": "grep -rn foo src | head -50"})
    assert "context_management" in axes_for(trunc, None, False)


def test_large_observation_probes_context_management():
    assert "context_management" in axes_for(record(obs_chars=40_000), None, False)


def test_verification_commands_are_tagged():
    for command in (
        "pytest -q",
        "git diff --stat",
        "gh pr checks 123",
        "npm run build",
    ):
        axes = axes_for(record(arguments={"command": command}), None, False)
        assert "verification" in axes, command


def test_plain_command_is_not_verification():
    assert "verification" not in axes_for(
        record(arguments={"command": "ls -la"}), None, False
    )


def test_parallelism_only_when_width_exceeds_one():
    single = record()
    assert "parallelism" not in axes_for(single, None, False)
    wide = record(
        calls=[
            {
                "tool": "Read",
                "family": "read",
                "arguments": {"file_path": "a"},
                "shell_segments": [],
            },
            {
                "tool": "Read",
                "family": "read",
                "arguments": {"file_path": "b"},
                "shell_segments": [],
            },
        ]
    )
    assert "parallelism" in axes_for(wide, None, False)


def test_delegation_is_tagged_by_family():
    delegated = record(
        calls=[
            {
                "tool": "Agent",
                "family": "delegate",
                "arguments": {},
                "shell_segments": [],
            }
        ]
    )
    assert "delegation" in axes_for(delegated, None, False)


def test_regex_pattern_probes_argument_construction():
    grep = record(
        calls=[
            {
                "tool": "Grep",
                "family": "search",
                "arguments": {"pattern": r"def \w+\(.*\)"},
                "shell_segments": [],
            }
        ]
    )
    assert "argument_construction" in axes_for(grep, None, False)
    plain = record(
        calls=[
            {
                "tool": "Grep",
                "family": "search",
                "arguments": {"pattern": "hello"},
                "shell_segments": [],
            }
        ]
    )
    assert "argument_construction" not in axes_for(plain, None, False)


def test_planning_and_stopping():
    assert "multi_step_planning" in axes_for(record(depth=12), None, False)
    assert "multi_step_planning" not in axes_for(record(depth=2), None, False)
    assert "stopping" in axes_for(record(), None, True)


def test_axes_are_returned_in_declared_order():
    """Stable order keeps a record's tags diffable across builds."""
    axes = axes_for(
        record(arguments={"command": "cd /r && pytest | head -5"}), None, True
    )
    assert axes == [a for a in ALL_AXES if a in set(axes)]


class TestDifficulty:
    def test_shallow_clean_single_call_is_easy(self):
        assert difficulty_for(record(depth=1), 0) == "easy"

    def test_a_failed_reference_is_always_hard(self):
        assert difficulty_for(record(depth=1, quality="errored"), 0) == "hard"

    def test_deep_is_hard_even_when_the_action_is_trivial(self):
        assert difficulty_for(record(depth=40), 0) == "hard"

    def test_prior_failures_in_the_episode_raise_difficulty(self):
        assert difficulty_for(record(depth=1), 2) == "hard"

    def test_middle_ground_is_moderate(self):
        assert difficulty_for(record(depth=6), 0) == "moderate"


class TestPartition:
    def test_assignment_is_deterministic(self):
        sid = "11111111-2222-3333-4444-555555555555"
        assert part.assign(sid) == part.assign(sid)

    def test_assignment_uses_the_specified_rule(self):
        import hashlib

        sid = "abc-def"
        expected = (
            part.ORACLE
            if int(hashlib.sha256(sid.encode()).hexdigest()[:8], 16) % 100 < 30
            else part.POOL
        )
        assert part.assign(sid) == expected

    def test_share_lands_near_target_over_many_ids(self):
        ids = [f"session-{i:05d}" for i in range(4000)]
        oracle = sum(1 for s in ids if part.assign(s) == part.ORACLE)
        assert 27 <= 100 * oracle / len(ids) <= 33

    def test_anonymous_branches_do_not_form_a_work_item(self):
        """Two sessions on detached HEAD share nothing; grouping them invents a link."""
        a = part.work_item("proj", "HEAD", "sess-a")
        b = part.work_item("proj", "HEAD", "sess-b")
        assert a != b

    def test_named_branch_groups_sessions(self):
        a = part.work_item("proj", "feature/x", "sess-a")
        b = part.work_item("proj", "feature/x", "sess-b")
        assert a == b

    def test_audit_is_recomputable(self):
        audit = part.build_audit(["s1", "s2", "s3"])
        for entry in audit.entries:
            assert entry["digest"] == part.session_digest(entry["session_id"])
            assert entry["partition"] == part.assign(entry["session_id"])

    def test_contamination_flags_shared_actions(self):
        def rec(rid, side, tool, arg_hash, session, item):
            return {
                "record_id": rid,
                "partition": side,
                "session_id": session,
                "work_item": item,
                "action": {"calls": [{"tool": tool, "arg_hash": arg_hash}]},
            }

        records = [
            rec("o1", part.ORACLE, "Bash", "aaa", "s1", "wi:x"),
            rec("o2", part.ORACLE, "Bash", "bbb", "s1", "wi:x"),
            rec("p1", part.POOL, "Bash", "aaa", "s2", "wi:x"),
        ]
        report, flagged = part.measure_contamination(records)
        assert flagged == {"o1"}
        assert report["action_overlap"]["overlapping_actions"] == 1
        assert report["work_item_overlap"]["work_items_spanning_partitions"] == 1


class TestToolsAvailable:
    """The offered tool set must not narrow to the answer.

    Using "every tool this transcript touched" is a union over the transcript's
    whole life, so a short subagent that called only ``WebFetch`` advertised
    exactly ``WebFetch``. On the first build 4.6% of records listed precisely the
    tools they used.
    """

    CORE = {"Bash", "Read", "Edit", "Write", "Grep", "WebFetch", "Agent"}
    MCP = {"claudia": {"mcp__claudia__a", "mcp__claudia__b"}, "pw": {"mcp__pw__x"}}

    def test_core_set_is_constant_regardless_of_what_was_used(self):
        from gaia.factory.dataset.build import tools_available_for

        narrow = tools_available_for({"WebFetch"}, self.CORE, self.MCP)
        broad = tools_available_for({"Bash", "Read", "Edit"}, self.CORE, self.MCP)
        assert narrow == broad == sorted(self.CORE)

    def test_a_lone_tool_call_does_not_advertise_itself_alone(self):
        from gaia.factory.dataset.build import tools_available_for

        available = tools_available_for({"WebFetch"}, self.CORE, self.MCP)
        assert len(available) > 1 and "WebFetch" in available

    def test_mcp_servers_expand_to_their_whole_catalog(self):
        """Knowing a server is connected is a session fact, not foresight."""
        from gaia.factory.dataset.build import tools_available_for

        available = tools_available_for({"mcp__claudia__a"}, self.CORE, self.MCP)
        assert "mcp__claudia__b" in available

    def test_unconnected_servers_are_not_offered(self):
        from gaia.factory.dataset.build import tools_available_for

        available = tools_available_for({"mcp__claudia__a"}, self.CORE, self.MCP)
        assert "mcp__pw__x" not in available
