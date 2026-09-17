# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The split has to be auditable: same records, correctly labelled, nothing lost.

Two failure modes would be invisible without these. A view that disagrees with
the canonical file produces a score for a set nobody can reproduce; and a
mistyped filter that silently returns nothing reports 0% on a set that was never
loaded.
"""

import json

import pytest

from gaia.factory.dataset.select import apply_filters, describe
from gaia.factory.dataset.usecase_split import (
    MIN_RUNNABLE_RECORDS,
    _episode_task,
    relabel,
    write_split,
)


def _rec(episode, instruction, family="shell", tool="Bash", **kw):
    base = {
        "record_id": kw.pop("record_id", f"{episode}-{family}-{len(instruction)}"),
        "episode_id": episode,
        "episode_instruction": instruction,
        "session_id": "s1",
        "use_case": kw.pop("legacy", "pr_lifecycle"),
        "partition": kw.pop("partition", "pool"),
        "difficulty": kw.pop("difficulty", "moderate"),
        "grading_polarity": "match_reference",
        "capability_axes": ["tool_selection"],
        "outcome": "ok",
        "action": {"width": 1, "calls": [{"tool": tool, "family": family}]},
    }
    base.update(kw)
    return base


class TestEpisodeLabelling:
    def test_all_records_of_an_episode_get_the_same_label(self):
        """Two moments of one task must not disagree about what the task was."""
        recs = [
            _rec(
                "e1", "fix the crash when the parser hits an empty file", record_id="a"
            ),
            _rec(
                "e1", "fix the crash when the parser hits an empty file", record_id="b"
            ),
        ]
        out = relabel(recs)["records"]
        assert len({r["use_case"] for r in out}) == 1

    def test_behaviour_overrides_a_misleading_instruction(self):
        """An instruction saying "research" that only edited files is not research."""
        recs = [
            _rec("e1", "research the options here", family="edit", tool="Edit"),
            _rec(
                "e1",
                "research the options here",
                family="edit",
                tool="Edit",
                record_id="b",
            ),
        ]
        task = _episode_task("e1", recs)
        assert task.use_case != "web_research"

    def test_the_previous_label_is_retained_for_audit(self):
        recs = [_rec("e1", "review this diff for correctness", legacy="code_review")]
        out = relabel(recs)["records"][0]
        assert out["use_case_legacy"] == "code_review"
        assert "track" in out and "activity" in out


class TestContamination:
    def test_boilerplate_episodes_are_dropped_not_relabelled(self):
        """Orchestrator nudges were being scored as though a human had asked."""
        recs = [
            _rec("e1", "[CONTEXT UPDATE: call claudia_rename_task with a displayName]"),
            _rec("e2", "fix the failing build"),
        ]
        result = relabel(recs)
        assert result["dropped_records"] == 1
        assert result["dropped_episodes"] == 1
        assert [r["episode_id"] for r in result["records"]] == ["e2"]

    def test_surviving_records_are_marked_human(self):
        out = relabel([_rec("e1", "add a --trace flag to the CLI")])["records"]
        assert out[0]["instruction_origin"] == "human"


class TestViewsMatchCanonical:
    def test_every_view_contains_only_its_own_use_case_and_loses_nothing(
        self, tmp_path
    ):
        recs = [
            _rec("e1", "fix the crash on empty input"),
            _rec("e2", "review this pull request", record_id="c"),
            _rec("e3", "write the release notes", record_id="d"),
        ]
        kept = relabel(recs)["records"]
        stats = write_split(kept, tmp_path)

        total = 0
        for key in stats:
            lines = (tmp_path / key / "records.jsonl").read_text(encoding="utf-8")
            parsed = [json.loads(x) for x in lines.splitlines() if x.strip()]
            assert all(r["use_case"] == key for r in parsed)
            total += len(parsed)
        assert total == len(kept)

    def test_each_view_ships_its_success_criterion(self, tmp_path):
        kept = relabel([_rec("e1", "fix the crash on empty input")])["records"]
        stats = write_split(kept, tmp_path)
        key = next(iter(stats))
        readme = (tmp_path / key / "README.md").read_text(encoding="utf-8")
        assert "What a passing answer looks like" in readme

    def test_small_sets_are_flagged_rather_than_dropped(self, tmp_path):
        kept = relabel([_rec("e1", "fix the crash on empty input")])["records"]
        stats = write_split(kept, tmp_path)
        key = next(iter(stats))
        assert stats[key]["runnable_alone"] is False
        assert "Too small to score" in (tmp_path / key / "README.md").read_text(
            encoding="utf-8"
        )
        assert MIN_RUNNABLE_RECORDS > 1


class TestSelect:
    RECS = [
        {
            "use_case": "bug_fix",
            "track": "build",
            "difficulty": "hard",
            "partition": "pool",
            "capability_axes": ["tool_selection"],
        },
        {
            "use_case": "bug_fix",
            "track": "build",
            "difficulty": "easy",
            "partition": "oracle",
            "capability_axes": ["verification"],
        },
        {
            "use_case": "code_review",
            "track": "verify",
            "difficulty": "hard",
            "partition": "pool",
            "capability_axes": ["tool_selection", "verification"],
        },
    ]

    def test_filters_combine_with_and_across_fields(self):
        got = apply_filters(self.RECS, {"track": ["build"], "difficulty": ["hard"]})
        assert len(got) == 1

    def test_several_values_of_one_field_combine_with_or(self):
        got = apply_filters(self.RECS, {"use_case": ["bug_fix", "code_review"]})
        assert len(got) == 3

    def test_a_list_field_matches_on_membership(self):
        got = apply_filters(self.RECS, {"capability_axes": ["verification"]})
        assert len(got) == 2

    def test_describe_lists_what_can_be_filtered(self):
        text = describe(self.RECS)
        assert "--use-case" in text and "bug_fix=2" in text


@pytest.mark.parametrize(
    "instruction,expected_not",
    [
        ("[CONTEXT UPDATE: rename your task]", "human"),
        ("Reconnecting after idle. Light status check ONLY", "human"),
    ],
)
def test_known_machine_prompts_are_never_treated_as_human(instruction, expected_not):
    recs = [_rec("e1", instruction)]
    assert relabel(recs)["records"] == []
    assert expected_not == "human"
