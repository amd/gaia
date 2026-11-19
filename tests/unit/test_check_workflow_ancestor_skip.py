# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""util/check_workflow_ancestor_skip.py: a job gated on needs.* without a status
function is silently skipped when any ancestor skips (#3929, #3922).

Most tests write workflows to a tmp dir; the last ones run the check against the
REAL .github/workflows/ and through util/lint.py, where it now blocks.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_workflow_ancestor_skip as cwas  # noqa: E402
import lint  # noqa: E402


@pytest.fixture
def workflow_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cwas, "WORKFLOW_DIR", tmp_path)
    return tmp_path


def _job_if(workflow_dir: Path, condition: str, name: str = "w.yml") -> int:
    body = (
        "on: push\n"
        "jobs:\n"
        "  a:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: [{run: 'true'}]\n"
        "  b:\n"
        "    needs: a\n"
        f"    if: {condition}\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: [{run: 'true'}]\n"
    )
    (workflow_dir / name).write_text(body, encoding="utf-8")
    return cwas.run_check()


@pytest.mark.parametrize(
    "condition",
    [
        "needs.a.outputs.go == 'true'",
        "needs.a.result == 'success'",
        "${{ needs.a.outputs.go == 'true' }}",
        # A status-function name inside a string literal is not a call.
        "needs.a.outputs.mode == 'always()'",
        "contains(needs.a.outputs.list, 'cancelled()')",
        "needs.a.outputs.x == 'it''s always()'",
        # Lookalike identifiers are not status functions.
        "needs.a.outputs.always_run == 'true'",
        "needs.a.outputs.success == 'true'",
    ],
)
def test_flags_needs_without_a_status_call(workflow_dir, condition, capsys):
    assert _job_if(workflow_dir, f'"{condition}"') == 1
    assert "job `b`" in capsys.readouterr().err


@pytest.mark.parametrize(
    "condition",
    [
        "!cancelled() && needs.a.outputs.go == 'true'",
        "always() && needs.a.result != 'failure'",
        "success() && needs.a.outputs.go == 'true'",
        "failure() || needs.a.result == 'failure'",
        "${{ !cancelled() && needs.a.result == 'success' }}",
        # No cross-job reference: an ancestor's skip cannot affect it.
        "github.repository == 'amd/gaia'",
        # `needs.` only inside a string literal is not a reference.
        "github.ref_name == 'needs.a'",
    ],
)
def test_accepts(workflow_dir, condition):
    assert _job_if(workflow_dir, f'"{condition}"') == 0


def test_non_string_and_missing_if_are_ignored(workflow_dir):
    (workflow_dir / "w.yml").write_text(
        "on: push\njobs:\n  a: {runs-on: x, if: true}\n  b: {runs-on: x}\n",
        encoding="utf-8",
    )
    assert cwas.run_check() == 0


def test_unparseable_workflow_fails(workflow_dir):
    (workflow_dir / "bad.yml").write_text("jobs: [\n", encoding="utf-8")
    assert cwas.run_check() == 1


def test_every_offending_file_is_reported(workflow_dir, capsys):
    _job_if(workflow_dir, "needs.a.result == 'success'", "one.yml")
    assert _job_if(workflow_dir, "needs.a.result == 'success'", "two.yml") == 1
    err = capsys.readouterr().err
    assert "one.yml" in err and "two.yml" in err


def test_real_workflows_are_clean():
    assert cwas.run_check() == 0


def test_lint_blocks_on_a_finding(workflow_dir):
    _job_if(workflow_dir, "needs.a.result == 'success'")
    result = lint.check_workflow_ancestor_skip()
    assert result.passed is False
    assert result.is_warning is False


def test_lint_passes_clean_workflows(workflow_dir):
    _job_if(workflow_dir, "\"!cancelled() && needs.a.result == 'success'\"")
    result = lint.check_workflow_ancestor_skip()
    assert (result.passed, result.is_warning) == (True, False)
