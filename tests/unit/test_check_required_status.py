# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for .github/scripts/check_required_status.py (issue #2767).

Proves the required-checks gate's core claim BY CONSTRUCTION: given a fixed
set of workflow-derived requirements and a fixed Checks API snapshot, does
`evaluate()` correctly classify what's OK, what's still pending, and — the
part that matters — what's a terminal FAILURE? In particular this is where
the #2755 "stuck at skipped" scenario is proven to turn into a fail, without
needing a live GitHub Actions run to observe it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "scripts"
    / "check_required_status.py"
)
_spec = importlib.util.spec_from_file_location("check_required_status", _SCRIPT_PATH)
crs = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["check_required_status"] = crs
_spec.loader.exec_module(crs)  # type: ignore[union-attr]


def write_workflow(tmp_path: Path, filename: str, content: str) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / filename
    path.write_text(content)
    return wf_dir


# ---------------------------------------------------------------------------
# load_requirements() — derivation from real-shaped workflow YAML
# ---------------------------------------------------------------------------


def test_load_requirements_derives_prefix_and_paths(tmp_path):
    wf_dir = write_workflow(
        tmp_path,
        "lint.yml",
        """
on:
  push:
    branches: [ main ]
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
    paths:
      - 'src/**'
      - 'hub/**'
jobs:
  lint:
    name: Run Code Quality Checks
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    reqs = crs.load_requirements(str(wf_dir), exclude=[])
    assert len(reqs) == 1
    assert reqs[0].name_prefix == "Run Code Quality Checks"
    assert reqs[0].exact is True  # static name -> exact match, not prefix
    assert reqs[0].paths == ["src/**", "hub/**"]
    assert reqs[0].source_file == "lint.yml"


def test_load_requirements_collapses_matrix_expression_to_prefix(tmp_path):
    wf_dir = write_workflow(
        tmp_path,
        "test_unit.yml",
        """
on:
  pull_request:
    types: [opened, synchronize]
jobs:
  unit-tests:
    name: "Unit Tests (py${{ matrix.python-version }})"
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12']
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    reqs = crs.load_requirements(str(wf_dir), exclude=[])
    assert reqs[0].name_prefix == "Unit Tests (py"
    assert reqs[0].exact is False  # matrix-templated -> prefix match


def test_load_requirements_skips_workflows_with_no_pull_request_trigger(tmp_path):
    wf_dir = write_workflow(
        tmp_path,
        "nightly.yml",
        """
on:
  schedule:
    - cron: '0 0 * * *'
jobs:
  nightly:
    name: Nightly
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    assert crs.load_requirements(str(wf_dir), exclude=[]) == []


def test_load_requirements_excludes_continue_on_error_jobs(tmp_path):
    # The macOS smoke job in the real test_unit.yml is explicitly
    # continue-on-error — its own author already declared it non-blocking,
    # so the gate must not turn it into a hard requirement.
    wf_dir = write_workflow(
        tmp_path,
        "test_unit.yml",
        """
on:
  pull_request:
    types: [opened]
jobs:
  unit-tests:
    name: Unit Tests
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
  unit-tests-macos:
    name: Unit Tests (macOS smoke)
    runs-on: macos-latest
    continue-on-error: true
    steps:
      - run: echo hi
""",
    )
    reqs = crs.load_requirements(str(wf_dir), exclude=[])
    names = [r.name_prefix for r in reqs]
    assert names == ["Unit Tests"]


def test_load_requirements_honors_exclude_list(tmp_path):
    wf_dir = write_workflow(
        tmp_path,
        "build_tui.yml",
        """
on:
  pull_request:
    branches: [ main ]
jobs:
  build:
    name: Build TUI
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    assert crs.load_requirements(str(wf_dir), exclude=["build_tui.yml"]) == []


# ---------------------------------------------------------------------------
# is_gated_off() — the third state (#2767 checkpoint-2 redirect)
#
# The gate must NOT skip itself via a job-level `if:` that mirrors the
# audited jobs' own draft-gate, because that reproduces this issue's exact
# bug inside the mechanism meant to catch it: nothing runs, nothing is red,
# the checks page reads green. is_gated_off() is what main() consults
# INSTEAD of a job-level skip, so the gate always executes and always
# freshly decides whether to report neutral or do the real check.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "is_draft,labels,expected",
    [
        (True, set(), True),  # fresh draft, no label -> suites legitimately silent
        (True, {"ready_for_ci"}, False),  # draft but opted in -> must be enforced
        (False, set(), False),  # ready for review -> must be enforced
        (False, {"ready_for_ci"}, False),
    ],
)
def test_is_gated_off(is_draft, labels, expected):
    assert crs.is_gated_off(is_draft, labels) is expected


# ---------------------------------------------------------------------------
# is_path_relevant()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "paths,paths_ignore,changed,expected",
    [
        (None, None, ["docs/x.mdx"], True),  # no filter -> always relevant
        (["src/**"], None, ["src/gaia/cli.py"], True),
        (["src/**"], None, ["docs/x.mdx"], False),
        (["hub/agents/email/**"], None, ["hub/agents/email/python/agent.py"], True),
        (None, ["docs/**"], ["docs/x.mdx"], False),  # everything changed is ignored
        (
            None,
            ["docs/**"],
            ["docs/x.mdx", "src/gaia/cli.py"],
            True,
        ),  # one non-ignored file
    ],
)
def test_is_path_relevant(paths, paths_ignore, changed, expected):
    req = crs.Requirement(
        name_prefix="X",
        exact=True,
        paths=paths,
        paths_ignore=paths_ignore,
        source_file="f.yml",
        job_id="x",
    )
    assert crs.is_path_relevant(req, changed) is expected


# ---------------------------------------------------------------------------
# evaluate() — the core gate logic
# ---------------------------------------------------------------------------


def _req(prefix, paths=None, exact=True):
    return crs.Requirement(
        name_prefix=prefix,
        exact=exact,
        paths=paths,
        paths_ignore=None,
        source_file="f.yml",
        job_id=prefix,
    )


def test_evaluate_all_present_and_passing_is_fully_ok():
    reqs = [_req("Run Code Quality Checks"), _req("Unit Tests (py", exact=False)]
    runs = [
        crs.CheckRun(
            name="Run Code Quality Checks", status="completed", conclusion="success"
        ),
        crs.CheckRun(
            name="Unit Tests (py3.10)", status="completed", conclusion="success"
        ),
        crs.CheckRun(
            name="Unit Tests (py3.11)", status="completed", conclusion="success"
        ),
    ]
    result = crs.evaluate(reqs, changed_files=["src/gaia/cli.py"], check_runs=runs)
    assert result.is_fully_ok
    assert not result.is_terminal_failure
    assert {r.name_prefix for r in result.ok} == {
        "Run Code Quality Checks",
        "Unit Tests (py",
    }


def test_evaluate_absent_check_is_pending_not_terminal():
    # This is the PR #2599 scenario before it's had time to register: no
    # matching check-run row exists yet. Must be retry-worthy, not an
    # instant failure, or the gate would false-fail on every PR's first poll.
    reqs = [_req("Test Email Agent")]
    result = crs.evaluate(
        reqs, changed_files=["hub/agents/email/python/x.py"], check_runs=[]
    )
    assert not result.is_fully_ok
    assert not result.is_terminal_failure
    assert result.missing == reqs


def test_evaluate_stuck_skipped_check_is_terminal_failure():
    # The #2755 signature: the job DID get scheduled at some point (its
    # own draft-gate `if:` evaluated false while the PR was a draft) and
    # never got a fresh triggering event to re-evaluate — so it sits at a
    # completed, terminal "skipped" conclusion forever. This must fail the
    # gate, not pass it, or the whole point of building this is moot.
    reqs = [_req("Test Email Agent")]
    runs = [
        crs.CheckRun(name="Test Email Agent", status="completed", conclusion="skipped")
    ]
    result = crs.evaluate(
        reqs, changed_files=["hub/agents/email/python/x.py"], check_runs=runs
    )
    assert result.is_terminal_failure
    assert result.failed[0][0].name_prefix == "Test Email Agent"
    assert "skipped" in result.failed[0][1]


def test_evaluate_failed_check_is_terminal_failure():
    reqs = [_req("Unit Tests (py", exact=False)]
    runs = [
        crs.CheckRun(
            name="Unit Tests (py3.10)", status="completed", conclusion="failure"
        )
    ]
    result = crs.evaluate(reqs, changed_files=["src/gaia/cli.py"], check_runs=runs)
    assert result.is_terminal_failure


def test_evaluate_in_progress_check_is_pending():
    reqs = [_req("Unit Tests (py", exact=False)]
    runs = [
        crs.CheckRun(name="Unit Tests (py3.10)", status="in_progress", conclusion=None)
    ]
    result = crs.evaluate(reqs, changed_files=["src/gaia/cli.py"], check_runs=runs)
    assert not result.is_fully_ok
    assert not result.is_terminal_failure
    assert result.pending == reqs


def test_evaluate_neutral_conclusion_counts_as_passing():
    reqs = [_req("Dependency Review")]
    runs = [
        crs.CheckRun(name="Dependency Review", status="completed", conclusion="neutral")
    ]
    result = crs.evaluate(reqs, changed_files=["setup.py"], check_runs=runs)
    assert result.is_fully_ok


def test_evaluate_ignores_path_irrelevant_requirement():
    # The control case from #2767 itself: PR #2757 legitimately shows no
    # "Security Tests" entries because it never touched src/gaia/agents/**.
    # A gate that doesn't know this would false-fail every unrelated PR.
    reqs = [_req("Security Tests (Linux)", paths=["src/gaia/agents/**"])]
    result = crs.evaluate(reqs, changed_files=["docs/guides/chat.mdx"], check_runs=[])
    assert result.is_fully_ok
    assert result.ok == [] and result.missing == [] and result.failed == []


def test_evaluate_one_bad_matrix_leg_fails_even_if_another_passed():
    reqs = [_req("Unit Tests (py", exact=False)]
    runs = [
        crs.CheckRun(
            name="Unit Tests (py3.10)", status="completed", conclusion="success"
        ),
        crs.CheckRun(
            name="Unit Tests (py3.11)", status="completed", conclusion="failure"
        ),
    ]
    result = crs.evaluate(reqs, changed_files=["src/gaia/cli.py"], check_runs=runs)
    assert result.is_terminal_failure


def test_evaluate_static_name_requires_exact_match_not_prefix():
    # Regression guard: a real workflow (test_hub_agents.yml) has a job
    # simply named "Test". Before `exact` existed, prefix matching meant
    # this requirement was satisfied by ANY check-run starting with "Test"
    # — e.g. a completely unrelated "Test Chat Agent" run — which would
    # have silently defeated the gate for that job.
    reqs = [_req("Test")]
    runs = [
        crs.CheckRun(name="Test Chat Agent", status="completed", conclusion="success")
    ]
    result = crs.evaluate(reqs, changed_files=["hub/agents/x.py"], check_runs=runs)
    assert not result.is_fully_ok


# ---------------------------------------------------------------------------
# most-specific-owner safety net for matrix-templated prefixes
#
# This reproduces two real collisions — one found while writing this
# script, one found LIVE on the throwaway evidence PR (#2775) for #2767.
# test_hub_agents.yml's job is named "Test ${{ matrix.package }}", which
# collapses to the prefix "Test". A plain startswith() would match:
#  (a) an unrelated STATIC name from another job ("Test Chat Agent", or the
#      bare "Test" job build_tui.yml happens to declare), and
#  (b) an unrelated OTHER PREFIX-MODE job's rendered name, e.g.
#      "Test Apps Build (jira)" (test_electron.yml's own, more specific,
#      "Test Apps Build (" prefix) — this one only showed up against the
#      real repo's full file set, which is exactly why load_requirements()
#      is exercised against real multi-file fixtures here rather than only
#      single-job ones.
# _most_specific_owner() is the general fix for both: every check-run name
# belongs to whichever matcher matches it most specifically.
# ---------------------------------------------------------------------------


def test_collect_all_name_matchers_spans_every_file_ignoring_exclude(tmp_path):
    wf_dir = write_workflow(
        tmp_path,
        "test_chat_agent.yml",
        """
on:
  pull_request:
jobs:
  test:
    name: Test Chat Agent
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    write_workflow(
        tmp_path,
        "build_tui.yml",
        """
on:
  pull_request:
    branches: [ main ]
jobs:
  build:
    name: Test
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    matchers = crs._collect_all_name_matchers(str(wf_dir))
    # Present even though build_tui.yml would normally be excluded from
    # requirements — a real check-run can still carry that exact name.
    as_tuples = {(m.pattern, m.exact, m.source_file) for m in matchers}
    assert as_tuples == {
        ("Test Chat Agent", True, "test_chat_agent.yml"),
        ("Test", True, "build_tui.yml"),
    }


def test_evaluate_prefix_match_defers_to_more_specific_static_name(tmp_path):
    wf_dir = write_workflow(
        tmp_path,
        "test_hub_agents.yml",
        """
on:
  pull_request:
jobs:
  test-hub-package:
    name: "Test ${{ matrix.package }}"
    strategy:
      matrix:
        package: [blender, docker]
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    write_workflow(
        tmp_path,
        "test_chat_agent.yml",
        """
on:
  pull_request:
jobs:
  test:
    name: Test Chat Agent
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    write_workflow(
        tmp_path,
        "build_tui.yml",
        """
on:
  pull_request:
    branches: [ main ]
jobs:
  build:
    name: Test
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    reqs = crs.load_requirements(str(wf_dir), exclude=["build_tui.yml"])
    hub_req = next(r for r in reqs if r.source_file == "test_hub_agents.yml")
    matchers = crs._collect_all_name_matchers(str(wf_dir))

    # Only "Test Chat Agent" and bare "Test" exist — neither is a real
    # rendering of "Test ${{ matrix.package }}", so without the guard the
    # gate would wrongly call this satisfied.
    runs = [
        crs.CheckRun(name="Test Chat Agent", status="completed", conclusion="success"),
        crs.CheckRun(name="Test", status="completed", conclusion="success"),
    ]
    result = crs.evaluate(
        [hub_req], changed_files=["hub/x.py"], check_runs=runs, all_matchers=matchers
    )
    assert not result.is_fully_ok
    assert result.missing == [hub_req]

    # The real rendered name (matrix value actually substituted) is a
    # legitimate match and must NOT be excluded by the same guard.
    runs_real = [
        crs.CheckRun(name="Test blender", status="completed", conclusion="success")
    ]
    result_real = crs.evaluate(
        [hub_req],
        changed_files=["hub/x.py"],
        check_runs=runs_real,
        all_matchers=matchers,
    )
    assert result_real.is_fully_ok


def test_evaluate_prefix_match_defers_to_more_specific_other_prefix(tmp_path):
    # The variant found LIVE on PR #2775: two DIFFERENT matrix-templated
    # jobs, neither one static, where one job's rendered name happens to
    # extend the other's prefix.
    wf_dir = write_workflow(
        tmp_path,
        "test_hub_agents.yml",
        """
on:
  pull_request:
jobs:
  test-hub-package:
    name: "Test ${{ matrix.package }}"
    strategy:
      matrix:
        package: [blender]
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    write_workflow(
        tmp_path,
        "test_electron.yml",
        """
on:
  pull_request:
jobs:
  test-apps-build:
    name: "Test Apps Build (${{ matrix.app.name }})"
    strategy:
      matrix:
        app: [{name: jira}, {name: example}]
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
    )
    reqs = crs.load_requirements(str(wf_dir), exclude=[])
    hub_req = next(r for r in reqs if r.source_file == "test_hub_agents.yml")
    matchers = crs._collect_all_name_matchers(str(wf_dir))

    # "Test Apps Build (jira)" starts with both "Test" and "Test Apps
    # Build (" — the longer, more specific prefix must win, so this must
    # NOT satisfy test_hub_agents.yml's "Test" requirement.
    runs = [
        crs.CheckRun(
            name="Test Apps Build (jira)", status="completed", conclusion="success"
        )
    ]
    result = crs.evaluate(
        [hub_req], changed_files=["hub/x.py"], check_runs=runs, all_matchers=matchers
    )
    assert not result.is_fully_ok
    assert result.missing == [hub_req]
