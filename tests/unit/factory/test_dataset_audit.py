# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the integrity audit.

An audit that cannot fail is worse than no audit: it converts an unchecked
dataset into one carrying a green tick. So every test here **breaks** an
invariant on a synthetic record and asserts the audit notices.

Each invariant was violated for real during development, silently, producing a
dataset that looked fine.
"""

import hashlib
import json

import pytest

from gaia.factory.dataset.audit import INVARIANTS, assert_clean, audit
from gaia.factory.harvest.reader import _hash_args


def seal(record):
    """Recompute record_sha256 so a fixture is self-consistent by default."""
    record.pop("record_sha256", None)
    record["record_sha256"] = hashlib.sha256(
        json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return record


def make_record(record_id="r1", **over):
    args = {"command": "ls -la"}
    record = {
        "record_id": record_id,
        "schema_version": 2,
        "session_id": "sess-1",
        "transcript_id": "sess-1",
        "message_id": "msg-1",
        "goal": "do the thing",
        "episode_instruction": "do the thing",
        "episode_id": "sess-1#0",
        "partition": "pool",
        "use_case": "code_review",
        "capability_axes": ["tool_selection"],
        "difficulty": "easy",
        "reasoning": {"visible_text": "", "thinking": None, "status": "none"},
        "scope": "main",
        "parent_session_id": None,
        "step_index": 5,
        "depth_index": 5,
        "tools_available": ["Bash", "Read", "Grep", "Write"],
        "grading_polarity": "match_reference",
        "outcome": {
            "reference_quality": "succeeded",
            "any_error": False,
            "error_classes": [],
        },
        "reference_checks": {},
        "episode_turn_class": {"index": 0, "turn_class": "initial_instruction"},
        "episode_outcome": {"label": "unknown", "confidence": "low", "evidence": []},
        "action": {
            "width": 1,
            "calls": [
                {
                    "tool": "Bash",
                    "family": "shell",
                    "arguments": args,
                    "arg_hash": _hash_args(args),
                    "shell_segments": [],
                }
            ],
        },
        "observation": [{"ok": True, "chars": 3, "text": "ok"}],
        "state": {
            "recent_steps": [{"step_index": 4, "calls": [], "outcomes": []}],
            "files_in_context": {},
            "known_paths": [],
            "observed_binaries": ["ls"],
            "environment_binaries": ["ls"],
        },
    }
    record.update(over)
    return seal(record)


def write(tmp_path, records, side="pool"):
    for name in ("oracle", "pool"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
        (tmp_path / name / "records.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "blobs").mkdir(exist_ok=True)
    (tmp_path / side / "records.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return tmp_path


def test_a_healthy_dataset_is_clean(tmp_path):
    report = audit(write(tmp_path, [make_record()]))
    assert report["clean"], report["violations"]
    assert len(report["invariants_checked"]) == len(INVARIANTS)


def test_missing_records_file_is_an_actionable_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="Build the dataset first"):
        audit(tmp_path)


class TestCatchesRealBreaches:
    """Each of these shipped at least once before being caught."""

    def test_duplicate_record_id(self, tmp_path):
        report = audit(write(tmp_path, [make_record(), make_record()]))
        assert "unique_record_id" in report["violations"]

    def test_duplicate_step_index_in_one_transcript(self, tmp_path):
        """Two decision points shared step 21 because the index was captured at
        message creation rather than at first tool_use."""
        a = make_record("r1", step_index=7)
        b = make_record("r2", message_id="msg-2", step_index=7)
        report = audit(write(tmp_path, [a, b]))
        assert "unique_step_index" in report["violations"]

    def test_lookahead_in_recent_steps(self, tmp_path):
        r = make_record(
            state={
                "recent_steps": [{"step_index": 5}],
                "files_in_context": {},
                "known_paths": [],
                "observed_binaries": [],
                "environment_binaries": [],
            }
        )
        report = audit(write(tmp_path, [seal(r)]))
        assert "no_lookahead_in_state" in report["violations"]

    def test_tools_available_equal_to_the_answer(self, tmp_path):
        """4.6% of records advertised exactly the tools used."""
        report = audit(write(tmp_path, [make_record(tools_available=["Bash"])]))
        assert "tools_available_does_not_leak" in report["violations"]

    def test_tools_available_missing_the_used_tool(self, tmp_path):
        report = audit(write(tmp_path, [make_record(tools_available=["Read", "Grep"])]))
        assert "tools_available_is_a_superset" in report["violations"]

    def test_arg_hash_not_describing_shipped_arguments(self, tmp_path):
        r = make_record()
        r["action"]["calls"][0]["arg_hash"] = "0" * 16
        report = audit(write(tmp_path, [seal(r)]))
        assert "arg_hash_matches_shipped_args" in report["violations"]

    def test_tampered_record_fails_its_checksum(self, tmp_path):
        r = make_record()
        r["goal"] = "tampered after sealing"
        report = audit(write(tmp_path, [r]))
        assert "record_sha256_verifies" in report["violations"]

    def test_subagent_parent_must_be_the_partition_key(self, tmp_path):
        """parent_session_id held the subagent's own file stem for a third of
        the corpus, breaking the replay pointer."""
        r = make_record(
            scope="subagent", transcript_id="agent-abc", parent_session_id="agent-abc"
        )
        report = audit(write(tmp_path, [seal(r)]))
        assert "subagent_parent_is_partition_key" in report["violations"]

    def test_main_record_must_not_claim_a_parent(self, tmp_path):
        report = audit(write(tmp_path, [make_record(parent_session_id="sess-9")]))
        assert "main_record_has_no_parent" in report["violations"]

    def test_failed_reference_must_invert_polarity(self, tmp_path):
        """Otherwise a harness is rewarded for reproducing a timeout."""
        r = make_record(
            outcome={
                "reference_quality": "errored",
                "any_error": True,
                "error_classes": ["timeout"],
            },
            grading_polarity="match_reference",
        )
        report = audit(write(tmp_path, [seal(r)]))
        assert "failed_reference_inverts_polarity" in report["violations"]

    def test_dangling_blob_reference(self, tmp_path):
        r = make_record()
        r["observation"][0]["blob_ref"] = "f" * 64
        report = audit(write(tmp_path, [seal(r)]))
        assert "blob_refs_resolve" in report["violations"]

    def test_missing_reference_checks(self, tmp_path):
        r = make_record()
        del r["reference_checks"]
        report = audit(write(tmp_path, [seal(r)]))
        assert "reference_checks_present" in report["violations"]


def test_assert_clean_raises_on_a_broken_dataset(tmp_path):
    """The build must abort rather than ship a dataset that scores against
    corrupted state and reports a number anyway."""
    write(tmp_path, [make_record(), make_record()])
    with pytest.raises(SystemExit, match="integrity audit FAILED"):
        assert_clean(tmp_path, log=lambda _: None)


def test_assert_clean_passes_on_a_healthy_dataset(tmp_path):
    assert_clean(write(tmp_path, [make_record()]), log=lambda _: None)


def test_episode_id_spanning_transcripts_is_caught(tmp_path):
    """A parent and its subagents share a session_id. Keying episode_id on that
    gave 20 independent agent runs one id, merging them into a fake episode."""
    a = make_record("r1", episode_id="sess-1#0", transcript_id="sess-1")
    b = make_record(
        "r2",
        message_id="m2",
        step_index=9,
        episode_id="sess-1#0",
        transcript_id="agent-abc",
        scope="subagent",
        parent_session_id="sess-1",
    )
    report = audit(write(tmp_path, [seal(a), seal(b)]))
    assert "episode_id_is_per_transcript" in report["violations"]


def test_missing_required_field_is_reported_not_raised(tmp_path):
    """An audit that crashes on a malformed record tells you less than one that
    names what is missing."""
    r = make_record()
    del r["use_case"]
    report = audit(write(tmp_path, [seal(r)]))
    assert "required_fields_present" in report["violations"]
    assert "use_case" in report["examples"]["required_fields_present"][0]


def test_outcome_label_inside_state_is_caught(tmp_path):
    """An outcome label in `state` hands a harness the answer to 'did this work?'
    before it acts. It is grading metadata and belongs beside `action`."""
    r = make_record()
    r["state"]["episode_outcome"] = {"label": "likely_succeeded"}
    report = audit(write(tmp_path, [seal(r)]))
    assert "outcome_not_visible_in_state" in report["violations"]


class TestSelfContainment:
    """A consumer without the private corpus must still be able to use every record.

    `state.transcript_ref` is provenance, not a dependency.
    """

    def test_empty_goal_is_caught(self, tmp_path):
        report = audit(write(tmp_path, [make_record(goal="   ")]))
        assert "harness_prompt_is_complete" in report["violations"]

    def test_missing_tools_available_is_caught(self, tmp_path):
        report = audit(write(tmp_path, [make_record(tools_available=[])]))
        assert "harness_prompt_is_complete" in report["violations"]

    def test_complete_record_passes(self, tmp_path):
        report = audit(write(tmp_path, [make_record()]))
        assert "harness_prompt_is_complete" not in report["violations"]
