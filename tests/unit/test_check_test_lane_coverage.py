# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/check_test_lane_coverage.py.

Each test builds an ephemeral repo in tmp_path — a `.github/workflows/` tree, a
`tests/` tree and an allowlist — and points the checker's module-level paths at
it, so nothing here depends on the real repository's current coverage.

The cases that matter are the ones where a wrong answer is silent: a path form
the parser fails to recognise credits nothing and reports a false offender, and
a `tests/` token the parser credits too eagerly hides a real one.
"""

import sys
from pathlib import Path

import pytest
import yaml

# Ensure util/ is importable regardless of where pytest is invoked from.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_test_lane_coverage as guard  # noqa: E402


def _write_workflow(repo: Path, name: str, steps: list, strategy: dict = None) -> Path:
    """Write a one-job workflow whose steps are the given run strings."""
    job = {"runs-on": "ubuntu-latest", "steps": [{"run": s} for s in steps]}
    if strategy:
        job["strategy"] = strategy
    doc = {"name": name, "on": {"push": {}}, "jobs": {"build": job}}
    wf_dir = repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / f"{name}.yml"
    path.write_text(yaml.dump(doc), encoding="utf-8")
    return path


def _write_tests(repo: Path, relative_paths: list) -> None:
    for rel in relative_paths:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_x():\n    assert True\n", encoding="utf-8")


def _write_allowlist(repo: Path, files: dict) -> Path:
    path = repo / "util" / "test_lane_allowlist.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"files": files}), encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An ephemeral repo with the checker's paths pointed at it."""
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard, "WORKFLOW_DIR", tmp_path / ".github" / "workflows")
    monkeypatch.setattr(guard, "ACTION_DIR", tmp_path / ".github" / "actions")
    monkeypatch.setattr(
        guard, "ALLOWLIST_PATH", tmp_path / "util" / "test_lane_allowlist.yml"
    )
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    _write_allowlist(tmp_path, {})
    return tmp_path


# ---------------------------------------------------------------------------
# Path forms a lane can use to name a test
# ---------------------------------------------------------------------------


class TestLanePathExtraction:
    def _paths(self, repo, script, matrix=None):
        found, unresolved = guard.lane_paths_in_script(
            script, matrix or {}, ["tests", "hub/agents/demo/python/tests"]
        )
        assert not unresolved
        return found

    def test_directory_target(self, repo):
        assert self._paths(repo, "pytest tests/unit/ -v") == {"tests/unit"}

    def test_single_file(self, repo):
        assert self._paths(repo, "python -m pytest tests/unit/test_a.py -v") == {
            "tests/unit/test_a.py"
        }

    def test_several_files_on_one_line(self, repo):
        assert self._paths(repo, "python -m pytest tests/a.py tests/b.py -v") == {
            "tests/a.py",
            "tests/b.py",
        }

    def test_node_id_suffix_resolves_to_the_file(self, repo):
        # test_chat_agent.yml names classes, not whole files.
        assert self._paths(repo, "python -m pytest tests/test_c.py::TestX -v") == {
            "tests/test_c.py"
        }

    def test_bash_line_continuation(self, repo):
        script = "python -m pytest tests/unit/test_a.py \\\n  tests/unit/test_b.py -v"
        assert self._paths(repo, script) == {
            "tests/unit/test_a.py",
            "tests/unit/test_b.py",
        }

    def test_powershell_line_continuation(self, repo):
        script = "python -m pytest tests/integration/test_e2e.py `\n  -v --tb=short"
        assert self._paths(repo, script) == {"tests/integration/test_e2e.py"}

    def test_script_style_invocation(self, repo):
        # test_mcp.yml runs its targets as plain scripts, not through pytest.
        assert self._paths(repo, "python tests/mcp/test_simple.py") == {
            "tests/mcp/test_simple.py"
        }

    def test_mixed_file_and_directory(self, repo):
        assert self._paths(repo, "pytest tests/test_rag.py tests/unit/rag/ -v") == {
            "tests/test_rag.py",
            "tests/unit/rag",
        }

    def test_windows_separators_normalise(self, repo):
        assert self._paths(repo, r"pytest tests\unit\test_a.py") == {
            "tests/unit/test_a.py"
        }


class TestNonInvocationsAreNotCoverage:
    def _paths(self, script):
        found, _ = guard.lane_paths_in_script(script, {}, ["tests"])
        return found

    def test_echo_naming_a_test_path_is_not_coverage(self):
        assert self._paths('echo "running tests/unit/test_a.py now"') == set()

    def test_copy_is_not_coverage(self):
        assert self._paths("cp tests/unit/test_a.py /tmp/") == set()

    def test_flag_values_are_not_read_as_paths(self):
        # -k / -m take a value; naive splitting must not treat it as a path.
        found = self._paths('pytest tests/unit/ -k "not tests/slow" -m "not slow"')
        assert found == {"tests/unit"}

    def test_paths_outside_the_roots_are_ignored(self):
        assert self._paths("pytest src/gaia/foo.py") == set()


# ---------------------------------------------------------------------------
# Matrix expansion and unresolved expressions
# ---------------------------------------------------------------------------


class TestMatrixExpansion:
    def test_matrix_axis_expands_to_every_value(self):
        script = "python -m pytest hub/agents/${{ matrix.package }}/python/tests/ -v"
        matrix = {"package": ["alpha", "beta"]}
        found, unresolved = guard.lane_paths_in_script(
            script,
            matrix,
            ["hub/agents/alpha/python/tests", "hub/agents/beta/python/tests"],
        )
        assert found == {
            "hub/agents/alpha/python/tests",
            "hub/agents/beta/python/tests",
        }
        assert not unresolved

    def test_include_entries_contribute_values(self):
        job = {"strategy": {"matrix": {"include": [{"package": "solo"}]}}}
        assert guard._matrix_values(job) == {"package": ["solo"]}

    def test_unresolvable_expression_is_reported_not_dropped(self):
        # Silently ignoring this would overstate the gap with a false offender.
        script = "pytest tests/${{ env.SUITE }}/ -v"
        found, unresolved = guard.lane_paths_in_script(script, {}, ["tests"])
        assert found == set()
        assert unresolved == {"tests/${{env.SUITE}}"}

    def test_expression_that_merely_contains_a_root_name_is_not_reported(self):
        # "${{ github.repository }}" contains "hub"; that is not a hub suite.
        script = 'python tests/x/test_a.py --repo "${{ github.repository }}"'
        found, unresolved = guard.lane_paths_in_script(
            script, {}, ["tests", "hub/agents/demo/python/tests"]
        )
        assert found == {"tests/x/test_a.py"}
        assert not unresolved

    def test_non_test_expression_is_not_reported(self):
        script = 'pytest tests/unit/ --basetemp="${{ runner.temp }}/pt"'
        found, unresolved = guard.lane_paths_in_script(script, {}, ["tests"])
        assert found == {"tests/unit"}
        assert not unresolved


# ---------------------------------------------------------------------------
# Coverage rule
# ---------------------------------------------------------------------------


class TestIsCovered:
    def test_exact_file_match(self):
        assert guard.is_covered("tests/unit/test_a.py", {"tests/unit/test_a.py"})

    def test_parent_directory_covers_nested_file(self):
        assert guard.is_covered("tests/unit/sub/test_a.py", {"tests/unit"})

    def test_sibling_directory_does_not_cover(self):
        assert not guard.is_covered("tests/integration/test_a.py", {"tests/unit"})

    def test_prefix_string_is_not_a_path_prefix(self):
        # "tests/unit" must not cover "tests/unit_extra/..." by string prefix.
        assert not guard.is_covered("tests/unit_extra/test_a.py", {"tests/unit"})


# ---------------------------------------------------------------------------
# Allowlist parsing
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_entry_without_a_reason_is_rejected(self, repo):
        path = _write_allowlist(repo, {"tests/test_a.py": ""})
        with pytest.raises(ValueError, match="no reason"):
            guard.load_allowlist(path)

    def test_entry_with_a_reason_is_accepted(self, repo):
        path = _write_allowlist(repo, {"tests/test_a.py": "Needs an NPU runner."})
        assert guard.load_allowlist(path) == {"tests/test_a.py": "Needs an NPU runner."}

    def test_missing_file_is_an_error(self, repo):
        with pytest.raises(ValueError, match="allowlist not found"):
            guard.load_allowlist(repo / "util" / "nope.yml")

    def test_empty_mapping_is_valid(self, repo):
        path = _write_allowlist(repo, {})
        assert guard.load_allowlist(path) == {}


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


class TestRunCheck:
    def test_passes_when_every_file_is_named(self, repo, capsys):
        _write_tests(repo, ["tests/unit/test_a.py", "tests/unit/test_b.py"])
        _write_workflow(repo, "unit", ["pytest tests/unit/ -v"])
        assert guard.run_check() == 0
        assert "[OK]" in capsys.readouterr().out

    def test_fails_and_names_the_uncovered_file(self, repo, capsys):
        _write_tests(repo, ["tests/unit/test_a.py", "tests/integration/test_b.py"])
        _write_workflow(repo, "unit", ["pytest tests/unit/ -v"])
        assert guard.run_check() == 1
        err = capsys.readouterr().err
        assert "tests/integration/test_b.py" in err
        assert "tests/unit/test_a.py" not in err

    def test_allowlisted_file_does_not_fail(self, repo):
        _write_tests(repo, ["tests/unit/test_a.py", "tests/integration/test_b.py"])
        _write_workflow(repo, "unit", ["pytest tests/unit/ -v"])
        _write_allowlist(repo, {"tests/integration/test_b.py": "Needs a live mailbox."})
        assert guard.run_check() == 0

    def test_allowlisted_file_that_a_lane_now_runs_is_flagged(self, repo, capsys):
        _write_tests(repo, ["tests/unit/test_a.py"])
        _write_workflow(repo, "unit", ["pytest tests/unit/ -v"])
        _write_allowlist(repo, {"tests/unit/test_a.py": "Needs a live mailbox."})
        assert guard.run_check() == 1
        assert "now run by a lane" in capsys.readouterr().err

    def test_allowlisted_file_that_no_longer_exists_is_flagged(self, repo, capsys):
        _write_tests(repo, ["tests/unit/test_a.py"])
        _write_workflow(repo, "unit", ["pytest tests/unit/ -v"])
        _write_allowlist(repo, {"tests/gone/test_deleted.py": "Needs an NPU runner."})
        assert guard.run_check() == 1
        assert "does not exist" in capsys.readouterr().err

    def test_hub_agent_suites_are_scanned(self, repo, capsys):
        _write_tests(repo, ["hub/agents/demo/python/tests/test_demo.py"])
        _write_workflow(repo, "unit", ["pytest tests/unit/ -v"])
        assert guard.run_check() == 1
        assert "hub/agents/demo/python/tests/test_demo.py" in capsys.readouterr().err

    def test_composite_action_run_steps_count_as_coverage(self, repo):
        _write_tests(repo, ["tests/unit/test_a.py"])
        action = repo / ".github" / "actions" / "run-tests" / "action.yml"
        action.parent.mkdir(parents=True)
        action.write_text(
            yaml.dump(
                {
                    "name": "run-tests",
                    "runs": {
                        "using": "composite",
                        "steps": [{"run": "pytest tests/unit/ -v"}],
                    },
                }
            ),
            encoding="utf-8",
        )
        assert guard.run_check() == 0

    def test_unparseable_workflow_is_an_error_not_a_skip(self, repo, capsys):
        _write_tests(repo, ["tests/unit/test_a.py"])
        _write_workflow(repo, "unit", ["pytest tests/unit/ -v"])
        bad = repo / ".github" / "workflows" / "broken.yml"
        bad.write_text("jobs: [unbalanced\n", encoding="utf-8")
        assert guard.run_check() == 1
        assert "failed to parse" in capsys.readouterr().err

    def test_non_test_python_files_are_not_required_to_be_covered(self, repo):
        _write_tests(repo, ["tests/unit/test_a.py", "tests/unit/helpers.py"])
        _write_workflow(repo, "unit", ["pytest tests/unit/test_a.py -v"])
        assert guard.run_check() == 0

    def test_pycache_is_ignored(self, repo):
        _write_tests(repo, ["tests/unit/test_a.py"])
        _write_tests(repo, ["tests/unit/__pycache__/test_stale.py"])
        _write_workflow(repo, "unit", ["pytest tests/unit/test_a.py -v"])
        assert guard.run_check() == 0
