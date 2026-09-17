# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A session is not a task, and the labels have to survive a misleading ask.

In the reference corpus 300 sessions carry 1,208 asks. Attributing steps to the
ask that caused them is what makes per-task analysis possible at all, so these
tests pin the attribution and the two classification axes.
"""

import pytest

from gaia.factory.harvest.inventory import _looks_like_binary, _switches
from gaia.factory.harvest.tasks import Task, classify, segment, summarise


def _trace(prompts, steps):
    return {"session_id": "s1", "prompts": prompts, "steps": steps}


def _step(tool, family, prompt_index, ok=True, digest=""):
    return {
        "tool": tool,
        "family": family,
        "ok": ok,
        "prompt_index": prompt_index,
        "arg_digest": digest,
    }


class TestAttribution:
    def test_steps_land_on_the_ask_that_caused_them(self):
        tasks = segment(
            _trace(
                ["fix the parser", "now write it up"],
                [
                    _step("Edit", "edit", 0),
                    _step("Bash", "shell", 0),
                    _step("Write", "write", 1),
                ],
            )
        )
        assert [t.n_steps for t in tasks] == [2, 1]
        assert tasks[1].ask == "now write it up"

    def test_an_ask_with_no_tool_calls_is_still_a_task(self):
        """A question answered from context is a real use of the agent."""
        tasks = segment(_trace(["what does this module do?"], []))
        assert len(tasks) == 1 and tasks[0].n_steps == 0

    def test_steps_before_any_ask_are_kept_separately(self):
        tasks = segment(_trace(["go"], [_step("Read", "read", -1)]))
        assert tasks[-1].prompt_index == -1
        assert tasks[-1].ask == "(before any ask)"

    def test_flow_collapses_runs_into_a_shape(self):
        t = Task(
            "s",
            0,
            "x",
            steps=[
                _step("Read", "read", 0),
                _step("Read", "read", 0),
                _step("Bash", "shell", 0),
                _step("Edit", "edit", 0),
            ],
        )
        assert t.flow == "read>shell>edit"


class TestBehaviourBeatsWording:
    """Asks are written in a hurry and often describe other than what happened."""

    def test_research_that_never_touched_the_web_and_rewrote_files_is_not_research(
        self,
    ):
        t = Task(
            "s",
            0,
            "research the options here",
            steps=[
                _step("Edit", "edit", 0),
                _step("Edit", "edit", 0),
            ],
        )
        activity, _ = classify(t)
        assert activity != "research"

    def test_an_implement_ask_that_only_looked_is_not_implementation(self):
        t = Task(
            "s",
            0,
            "add support for the new flag",
            steps=[
                _step("Read", "read", 0),
                _step("Grep", "search", 0),
            ],
        )
        activity, _ = classify(t)
        assert activity in {"answer", "research"}

    def test_review_wins_over_debug_when_both_words_appear(self):
        """A read-only audit that mentions something failed is a review.

        Ordering these the other way mislabelled 147 tasks as debugging, most of
        them audits.
        """
        t = Task("s", 0, "READ-ONLY VERIFICATION. this closes the gap where CI failed")
        assert classify(t)[0] == "review"


class TestInheritance:
    def test_a_follow_up_inherits_the_previous_task(self):
        tasks = segment(
            _trace(
                ["fix the failing build", "just push it"],
                [_step("Bash", "shell", 0), _step("Bash", "shell", 1)],
            )
        )
        assert tasks[1].activity != "unclassified"

    def test_an_inherited_label_is_marked_as_such(self):
        """Inherited labels are weaker evidence and must stay distinguishable."""
        tasks = segment(_trace(["refactor the loader", "ok"], []))
        assert tasks[0].inherited is False
        if tasks[1].activity != "unclassified":
            assert tasks[1].inherited is True


class TestSummarise:
    def test_the_grid_is_two_dimensional(self):
        tasks = segment(_trace(["write the docs"], [_step("Write", "write", 0)]))
        s = summarise(tasks)
        assert s["tasks"] == 1
        assert any("|" in k for k in s["grid"])


class TestShellTokenizer:
    """Shell parsing cannot fully avoid reading into quoted or script bodies."""

    @pytest.mark.parametrize("junk", ["import", "print", "const", "passed", "x", "t"])
    def test_string_and_script_fragments_are_not_programs(self, junk):
        assert not _looks_like_binary(junk)

    @pytest.mark.parametrize("real", ["gh", "git", "grep", "pytest", "npm"])
    def test_real_programs_survive(self, real):
        assert _looks_like_binary(real)

    def test_a_switch_is_attributed_to_the_binary_that_owns_it(self):
        pairs = set(_switches("git log --oneline | head -20"))
        assert ("git", "--oneline") in pairs
        # head -20 and tail -50 are one capability, so the count collapses.
        assert ("head", "-N") in pairs
        assert ("git", "-N") not in pairs

    def test_quoted_content_does_not_become_switches(self):
        """`echo "--- header ---"` was contributing `---"` as a flag."""
        assert not list(_switches('echo "--- section ---"'))
