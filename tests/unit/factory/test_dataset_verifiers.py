# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the graders a consumer runs against a candidate harness.

Synthetic records only. These matter more than most: a verifier that is subtly
wrong does not crash, it produces a confident score that measures nothing. Every
check below was wrong on the first build in a way that only showed up when the
*reference* action was run through it.
"""

import pytest

from gaia.factory.dataset.verifiers import (
    STATIC_CHECKS,
    check_binaries_known,
    check_old_string_present,
    check_paths_known,
    check_pattern_valid,
    grade,
    normalize_path,
    run_static_checks,
)


def make_record(**over):
    record = {
        "record_id": "r1",
        "grading_polarity": "match_reference",
        "action": {
            "width": 1,
            "calls": [
                {
                    "tool": "Bash",
                    "family": "shell",
                    "arguments": {"command": "ls -la"},
                    "arg_hash": "deadbeef",
                    "shell_segments": [
                        {"leader": "ls", "kind": "substantive", "text": "ls -la"}
                    ],
                }
            ],
        },
        "state": {
            "known_paths": ["<WORKSPACE>/repo/src/a.py", "<WORKSPACE>/repo/docs/"],
            "files_in_context": {},
            "observed_binaries": ["ls"],
            "environment_binaries": ["ls", "grep", "git"],
        },
        "reference_checks": {},
    }
    record.update(over)
    return record


def call(tool, **arguments):
    return {
        "tool": tool,
        "family": "shell",
        "arguments": arguments,
        "shell_segments": [],
    }


class TestPathsKnown:
    def test_known_path_passes(self):
        r = make_record()
        c = call("Read", file_path="<WORKSPACE>/repo/src/a.py")
        assert check_paths_known(c, r).passed is True

    def test_separator_style_does_not_matter(self):
        """Scrubbing yields `<WORKSPACE>/repo\\src\\a.py` for a path stored with
        forward slashes; compared raw, a path the agent had seen looked unseen."""
        r = make_record()
        c = call("Read", file_path=r"<WORKSPACE>/repo\src\a.py")
        assert check_paths_known(c, r).passed is True

    def test_file_inside_a_known_directory_is_plausible(self):
        r = make_record()
        c = call("Read", file_path="<WORKSPACE>/repo/docs/guide.md")
        assert check_paths_known(c, r).passed is True

    def test_invented_path_fails(self):
        r = make_record()
        c = call("Read", file_path="<WORKSPACE>/elsewhere/nope.py")
        result = check_paths_known(c, r)
        assert result.passed is False and "not previously seen" in result.detail

    def test_not_applicable_without_a_path(self):
        assert (
            check_paths_known(call("Bash", command="ls"), make_record()).applicable
            is False
        )


class TestBinariesKnown:
    def _bash(self, *leaders):
        return {
            "tool": "Bash",
            "family": "shell",
            "arguments": {"command": " && ".join(leaders)},
            "shell_segments": [
                {"leader": x, "kind": "substantive", "text": x} for x in leaders
            ],
        }

    def test_binary_in_the_environment_passes_on_first_session_use(self):
        """grep runs 12,764 times corpus-wide; its first use here is not an
        invention, and flagging it made the check 58% wrong on the reference."""
        r = make_record()
        assert "grep" not in r["state"]["observed_binaries"]
        assert check_binaries_known(self._bash("grep"), r).passed is True

    def test_program_absent_from_the_machine_fails(self):
        result = check_binaries_known(self._bash("kubectl"), make_record())
        assert result.passed is False and "kubectl" in result.detail

    def test_script_body_segments_are_ignored(self):
        c = {
            "tool": "Bash",
            "family": "shell",
            "arguments": {"command": "python - <<EOF"},
            "shell_segments": [
                {"leader": "python", "kind": "script_body", "text": "x"},
                {"leader": "open(p", "kind": "script_body", "text": "y"},
            ],
        }
        assert check_binaries_known(c, make_record()).applicable is False

    def test_not_applicable_for_a_non_shell_tool(self):
        assert (
            check_binaries_known(call("Read", file_path="a"), make_record()).applicable
            is False
        )


class TestPatternValid:
    def test_glob_is_validated_as_a_glob(self):
        """`**/*.go` is a perfectly good glob and a broken regex. Compiling it
        with `re` is what made this check look 84% wrong."""
        c = {"tool": "Glob", "family": "search", "arguments": {"pattern": "**/*.go"}}
        assert check_pattern_valid(c, make_record()).passed is True

    def test_regex_is_validated_as_a_regex(self):
        c = {"tool": "Grep", "family": "search", "arguments": {"pattern": r"def \w+\("}}
        assert check_pattern_valid(c, make_record()).passed is True

    def test_broken_regex_fails(self):
        c = {"tool": "Grep", "family": "search", "arguments": {"pattern": "a["}}
        assert check_pattern_valid(c, make_record()).passed is False

    def test_not_applicable_without_a_pattern(self):
        assert (
            check_pattern_valid(call("Bash", command="ls"), make_record()).applicable
            is False
        )


class TestOldStringPresent:
    def _record_with_file(self, content):
        r = make_record()
        r["state"]["files_in_context"] = {
            "<WORKSPACE>/repo/a.py": {"blob": "b1", "chars": len(content)}
        }
        return r, (lambda digest: content if digest == "b1" else None)

    def test_present_passes(self):
        r, reader = self._record_with_file("import os\nprint(1)\n")
        c = call(
            "Edit",
            file_path="<WORKSPACE>/repo/a.py",
            old_string="print(1)",
            new_string="print(2)",
        )
        assert check_old_string_present(c, r, reader).passed is True

    def test_absent_fails(self):
        r, reader = self._record_with_file("import os\n")
        c = call(
            "Edit", file_path="<WORKSPACE>/repo/a.py", old_string="nope", new_string="x"
        )
        assert check_old_string_present(c, r, reader).passed is False

    def test_not_applicable_when_content_is_not_held(self):
        r = make_record()
        c = call(
            "Edit", file_path="<WORKSPACE>/repo/z.py", old_string="a", new_string="b"
        )
        assert check_old_string_present(c, r, lambda d: None).applicable is False


class TestRunStaticChecks:
    def test_one_bad_call_fails_the_whole_action(self):
        """A bad path in a 3-wide dispatch is a bad action, not two-thirds good."""
        r = make_record()
        calls = [
            call("Read", file_path="<WORKSPACE>/repo/src/a.py"),
            call("Read", file_path="<WORKSPACE>/invented/x.py"),
        ]
        assert run_static_checks(calls, r)["paths_known"]["passed"] is False

    def test_every_check_is_reported(self):
        assert set(
            run_static_checks([call("Bash", command="ls")], make_record())
        ) == set(STATIC_CHECKS)

    def test_inapplicable_is_not_a_pass(self):
        """Otherwise a harness raises its score by proposing unverifiable actions."""
        out = run_static_checks([call("Bash", command="ls")], make_record())
        assert out["old_string_present"]["applicable"] is False
        assert out["old_string_present"]["passed"] is None


class TestGrade:
    def test_matching_the_reference_is_credited(self):
        r = make_record()
        out = grade(r, [{"tool": "Bash", "arguments": {"command": "ls -la"}}])
        assert out["tool_exact"] is True and out["credited"] is True

    def test_wrong_tool_is_not_credited(self):
        r = make_record()
        out = grade(r, [{"tool": "WebSearch", "arguments": {"query": "x"}}])
        assert out["tool_exact"] is False and out["credited"] is False

    def test_on_a_failed_reference_repeating_it_scores_zero(self):
        """3.4% of reference actions failed. Reproducing a timeout is not skill."""
        from gaia.factory.harvest.reader import _hash_args

        args = {"command": "sleep 999"}
        r = make_record(grading_polarity="avoid_reference")
        r["action"]["calls"][0]["arguments"] = args
        r["action"]["calls"][0]["arg_hash"] = _hash_args(args)
        out = grade(r, [{"tool": "Bash", "arguments": args}])
        assert out["repeated_failing_action"] is True
        assert out["credited"] is False

    def test_on_a_failed_reference_a_different_valid_action_is_credited(self):
        r = make_record(grading_polarity="avoid_reference")
        out = grade(r, [{"tool": "Bash", "arguments": {"command": "grep -rn x ."}}])
        assert out["repeated_failing_action"] is False
        assert out["credited"] is True

    def test_checks_informative_follows_the_reference_calibration(self):
        """Where the reference failed a check, that check cannot grade anyone."""
        r = make_record()
        r["reference_checks"] = {
            "paths_known": {"applicable": True, "passed": False},
            "binaries_known": {"applicable": True, "passed": True},
            "pattern_valid": {"applicable": False, "passed": None},
            "old_string_present": {"applicable": False, "passed": None},
        }
        out = grade(r, [{"tool": "Bash", "arguments": {"command": "ls"}}])
        assert out["checks_informative"]["paths_known"] is False
        assert out["checks_informative"]["binaries_known"] is True

    def test_width_is_reported_so_fan_out_divergence_is_visible(self):
        r = make_record()
        out = grade(
            r, [{"tool": "Bash", "arguments": {}}, {"tool": "Read", "arguments": {}}]
        )
        assert out["width_reference"] == 1 and out["width_proposed"] == 2

    def test_grade_never_emits_a_single_aggregate_score(self):
        """Merging polarities and checks into one number is the failure mode."""
        out = grade(make_record(), [{"tool": "Bash", "arguments": {"command": "ls"}}])
        assert "score" not in out and "accuracy" not in out


@pytest.mark.parametrize(
    "raw,expected",
    [
        (r"<WORKSPACE>/repo\src\a.py", "<workspace>/repo/src/a.py"),
        ("<WORKSPACE>/repo/src/", "<workspace>/repo/src"),
        ("A/B", "a/b"),
    ],
)
def test_normalize_path(raw, expected):
    assert normalize_path(raw) == expected
