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


class TestRealRepository:
    def test_no_workflow_filters_on_base_branch(self):
        """Regression guard for #2767 against the repo's actual workflows."""
        assert check_workflow_triggers.run_check() == 0
