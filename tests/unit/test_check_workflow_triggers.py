# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/check_workflow_triggers.py (#2767).

Most tests write ephemeral workflow files to a tmp_path and monkeypatch
WORKFLOW_DIR. The last class runs the check against the REAL
.github/workflows/ — that is the regression test for the bug itself.
"""

import sys
from pathlib import Path

import pytest

# Ensure util/ is importable regardless of where pytest is invoked from.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_workflow_triggers  # noqa: E402


def _write(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / name).write_text(body, encoding="utf-8")


@pytest.fixture
def workflow_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(check_workflow_triggers, "WORKFLOW_DIR", tmp_path)
    return tmp_path


class TestAccepted:
    def test_pull_request_without_branches(self, workflow_dir):
        _write(
            workflow_dir,
            "ok.yml",
            "on:\n  pull_request:\n    types: [opened]\n    paths: ['src/**']\njobs: {}\n",
        )
        assert check_workflow_triggers.run_check() == 0

    def test_push_branches_filter_is_untouched(self, workflow_dir):
        """The bug is base-branch filtering on PRs; push filters are legitimate."""
        _write(
            workflow_dir,
            "ok.yml",
            "on:\n  push:\n    branches: [ main ]\n  pull_request:\n    types: [opened]\njobs: {}\n",
        )
        assert check_workflow_triggers.run_check() == 0

    def test_workflow_with_no_pull_request_trigger(self, workflow_dir):
        _write(
            workflow_dir,
            "ok.yml",
            "on:\n  schedule:\n    - cron: '0 0 * * 0'\njobs: {}\n",
        )
        assert check_workflow_triggers.run_check() == 0

    def test_bare_pull_request_trigger(self, workflow_dir):
        """`pull_request:` with no mapping parses to None, not a dict."""
        _write(workflow_dir, "ok.yml", "on:\n  pull_request:\njobs: {}\n")
        assert check_workflow_triggers.run_check() == 0

    def test_quoted_on_key(self, workflow_dir):
        """`\"on\":` stays a string instead of resolving to the boolean True."""
        _write(
            workflow_dir,
            "ok.yml",
            '"on":\n  pull_request:\n    types: [opened]\njobs: {}\n',
        )
        assert check_workflow_triggers.run_check() == 0


class TestRejected:
    @pytest.mark.parametrize("event", ["pull_request", "pull_request_target"])
    @pytest.mark.parametrize("key", ["branches", "branches-ignore"])
    def test_all_pr_base_filters_are_rejected(self, workflow_dir, capsys, event, key):
        _write(
            workflow_dir,
            "bad.yml",
            f"on:\n  {event}:\n    {key}: [ main ]\njobs: {{}}\n",
        )
        assert check_workflow_triggers.run_check() == 1
        assert f"on.{event}.{key}" in capsys.readouterr().err

    def test_inline_branches_filter(self, workflow_dir):
        _write(
            workflow_dir,
            "bad.yml",
            "on:\n  pull_request:\n    branches: [ main ]\n    types: [opened]\njobs: {}\n",
        )
        assert check_workflow_triggers.run_check() == 1

    def test_block_sequence_branches_filter(self, workflow_dir):
        _write(
            workflow_dir,
            "bad.yml",
            "on:\n  pull_request:\n    branches:\n      - main\n      - release\njobs: {}\n",
        )
        assert check_workflow_triggers.run_check() == 1

    def test_quoted_on_key_with_branches_filter(self, workflow_dir):
        _write(
            workflow_dir,
            "bad.yml",
            '"on":\n  pull_request:\n    branches: [ main ]\njobs: {}\n',
        )
        assert check_workflow_triggers.run_check() == 1

    def test_error_names_the_file_and_the_issue(self, workflow_dir, capsys):
        _write(
            workflow_dir,
            "bad.yml",
            "on:\n  pull_request:\n    branches: [ main ]\njobs: {}\n",
        )
        check_workflow_triggers.run_check()
        stderr = capsys.readouterr().err
        assert "bad.yml" in stderr
        assert "#2767" in stderr

    def test_unparseable_workflow_rejected(self, workflow_dir):
        _write(workflow_dir, "bad.yml", "on:\n  pull_request: [\n    invalid")
        assert check_workflow_triggers.run_check() == 1

    def test_missing_workflow_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            check_workflow_triggers, "WORKFLOW_DIR", tmp_path / "nonexistent"
        )
        assert check_workflow_triggers.run_check() == 1


GATED_JOB = (
    "jobs:\n  t:\n    if: contains(github.event.pull_request.labels.*.name, "
    "'ready_for_ci')\n    runs-on: ubuntu-latest\n    steps: []\n"
)


class TestReadyForCiLabel:
    """#2939: a workflow that checks `ready_for_ci` must wake up on `labeled`."""

    def test_types_with_labeled_accepted(self, workflow_dir):
        _write(
            workflow_dir,
            "ok.yml",
            "on:\n  pull_request:\n    types: [opened, synchronize, labeled]\n"
            + GATED_JOB,
        )
        assert check_workflow_triggers.run_check() == 0

    def test_block_sequence_types_with_labeled_accepted(self, workflow_dir):
        _write(
            workflow_dir,
            "ok.yml",
            "on:\n  pull_request:\n    types:\n      - opened\n      - labeled\n"
            + GATED_JOB,
        )
        assert check_workflow_triggers.run_check() == 0

    def test_workflow_without_the_label_is_not_policed(self, workflow_dir):
        _write(
            workflow_dir,
            "ok.yml",
            "on:\n  pull_request:\n    types: [opened]\njobs: {}\n",
        )
        assert check_workflow_triggers.run_check() == 0

    def test_label_check_without_a_pr_trigger_is_not_policed(self, workflow_dir):
        _write(workflow_dir, "ok.yml", "on:\n  workflow_dispatch:\n" + GATED_JOB)
        assert check_workflow_triggers.run_check() == 0

    def test_types_without_labeled_rejected(self, workflow_dir, capsys):
        _write(
            workflow_dir,
            "bad.yml",
            "on:\n  pull_request:\n"
            "    types: [opened, synchronize, reopened, ready_for_review]\n"
            + GATED_JOB,
        )
        assert check_workflow_triggers.run_check() == 1
        err = capsys.readouterr().err
        assert "bad.yml" in err and "labeled" in err and "#2939" in err

    def test_unlabeled_alone_does_not_count(self, workflow_dir):
        """`unlabeled` is a different event; it never fires when a label is added."""
        _write(
            workflow_dir,
            "bad.yml",
            "on:\n  pull_request:\n    types: [opened, unlabeled]\n" + GATED_JOB,
        )
        assert check_workflow_triggers.run_check() == 1

    def test_bare_pull_request_uses_default_types_and_is_rejected(self, workflow_dir):
        """GitHub's default types are opened/synchronize/reopened — no `labeled`."""
        _write(workflow_dir, "bad.yml", "on:\n  pull_request:\n" + GATED_JOB)
        assert check_workflow_triggers.run_check() == 1

    def test_pull_request_without_types_key_rejected(self, workflow_dir):
        _write(
            workflow_dir,
            "bad.yml",
            "on:\n  pull_request:\n    paths: ['src/**']\n" + GATED_JOB,
        )
        assert check_workflow_triggers.run_check() == 1

    def test_scalar_types_rejected(self, workflow_dir):
        _write(
            workflow_dir,
            "bad.yml",
            "on:\n  pull_request:\n    types: opened\n" + GATED_JOB,
        )
        assert check_workflow_triggers.run_check() == 1

    def test_pull_request_target_is_policed_too(self, workflow_dir):
        _write(
            workflow_dir,
            "bad.yml",
            "on:\n  pull_request_target:\n    types: [opened]\n" + GATED_JOB,
        )
        assert check_workflow_triggers.run_check() == 1

    def test_each_offending_event_is_reported(self, workflow_dir, capsys):
        _write(
            workflow_dir,
            "bad.yml",
            "on:\n  pull_request:\n    types: [opened]\n"
            "  pull_request_target:\n    types: [opened]\n" + GATED_JOB,
        )
        assert check_workflow_triggers.run_check() == 1
        err = capsys.readouterr().err
        assert "on.pull_request.types" in err
        assert "on.pull_request_target.types" in err


class TestRealRepository:
    def test_no_workflow_filters_on_base_branch(self):
        """Regression guard for #2767 and #2939 against the real workflows."""
        assert check_workflow_triggers.run_check() == 0
