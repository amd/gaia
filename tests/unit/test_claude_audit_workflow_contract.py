# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The proactive Claude audits fail SILENTLY — assert the settings that stop that.

Every failure these tests pin has already happened at least once, and none of
them turned a run red at the time:

* The scheduled-actor gate rejected every run for five weeks and the runs stayed
  green, because ``allowed_bots`` omitted the bot that actors a post-release
  schedule event (#3059). The audit itself filed that bug, twice, and nothing
  pinned it either time.
* A completeness constant hand-synced to a job matrix by comment silently
  under-counted, letting a short SARIF clear real unfixed alerts (#3060).
* The doc walkthrough reported green when its judge wrote no findings at all, so
  a crash read as a pass (#3058).
* Dedup only searched the audit's own label, so one defect became many issues and
  a four-month-old issue was rediscovered 14 times in one night.

These are configuration assertions, not behaviour tests. They exist because the
workflow still runs and still goes green — it just stops auditing anything.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

NIGHTLY_AUDIT = WORKFLOW_DIR / "claude-nightly-audit.yml"
DOC_WALKTHROUGH = WORKFLOW_DIR / "claude-weekly-doc-walkthrough.yml"
SECURITY_AUDIT = WORKFLOW_DIR / "claude-security-audit.yml"

#: Every scheduled Claude workflow that files or publishes findings.
PROACTIVE_WORKFLOWS = (NIGHTLY_AUDIT, DOC_WALKTHROUGH, SECURITY_AUDIT)

#: The action whose actor gate rejects a bot-actored scheduled run.
CLAUDE_ACTION = "anthropics/claude-code-action"

#: On a `schedule` event the actor is whoever last touched the default branch,
#: which in this repo is one of these two bots. Both must be allowed or the run
#: is rejected before Claude starts.
REQUIRED_BOTS = ("github-merge-queue", "github-actions")


def _load(path: Path) -> dict:
    assert path.is_file(), f"{path} is missing"
    # `on:` is parsed by PyYAML 1.1 rules as the boolean True, hence the lookup
    # helper below.
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True)


def _steps(workflow: dict):
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            yield step


def _claude_steps(workflow: dict):
    return [s for s in _steps(workflow) if CLAUDE_ACTION in str(s.get("uses", ""))]


@pytest.fixture(scope="module")
def nightly() -> dict:
    return _load(NIGHTLY_AUDIT)


@pytest.fixture(scope="module")
def walkthrough() -> dict:
    return _load(DOC_WALKTHROUGH)


@pytest.fixture(scope="module")
def security() -> dict:
    return _load(SECURITY_AUDIT)


# ----------------------------------------------------------------------
# The scheduled-actor gate (#3059)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("path", PROACTIVE_WORKFLOWS, ids=lambda p: p.name)
def test_every_scheduled_claude_step_allows_the_bots_that_actor_it(path: Path):
    """Without this the run is rejected before Claude starts — and reports green.

    #3059 is this bug, filed by the audit about itself: the allowlist named only
    `github-merge-queue`, so the first run after any release — where `claude.yml`
    pushes release notes to main as `github-actions[bot]` — was rejected.
    """
    workflow = _load(path)
    assert "schedule" in _triggers(workflow), f"{path.name} is not scheduled"
    steps = _claude_steps(workflow)
    assert steps, f"{path.name} declares no {CLAUDE_ACTION} step"
    for step in steps:
        allowed = str(step["with"].get("allowed_bots", ""))
        missing = [bot for bot in REQUIRED_BOTS if bot not in allowed]
        assert not missing, (
            f"{path.name} step {step.get('name', step.get('id'))!r} omits "
            f"{missing} from allowed_bots ({allowed!r}). A scheduled run actored "
            f"by that bot is rejected before Claude starts, and reports green."
        )


# ----------------------------------------------------------------------
# A partial sweep must never read as a clean bill of health (#3058, #3060)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("path", PROACTIVE_WORKFLOWS, ids=lambda p: p.name)
def test_a_lens_that_wrote_no_findings_fails_its_job(path: Path):
    """Every lens writes `{"findings": []}` when clean, so a missing file means
    the lens never ran. Uploading nothing must fail, not warn."""
    workflow = _load(path)
    uploads = [
        s
        for s in _steps(workflow)
        if "upload-artifact" in str(s.get("uses", ""))
        and "findings" in str(s.get("with", {}).get("path", ""))
    ]
    assert uploads, f"{path.name} uploads no findings artifact"
    for step in uploads:
        assert step["with"].get("if-no-files-found") == "error", (
            f"{path.name}: a missing findings file must fail the job — a silent "
            f"skip reads as 'audited, clean' (#3058)."
        )


def test_the_walkthrough_refuses_to_synthesize_a_partial_sweep(walkthrough: dict):
    """#3058's remaining half: the per-doc upload failing is not enough.

    The synthesize job runs on `!cancelled()`, so without a run-level gate a night
    where guides went unwalked still reached synthesis, found nothing to file, and
    called that a clean run.
    """
    steps = walkthrough["jobs"]["synthesize"]["steps"]
    gate = [s for s in steps if "reported in" in str(s.get("name", ""))]
    assert gate, (
        "the walkthrough's synthesize job needs a step requiring every discovered "
        "doc to have produced findings, matching the nightly audit's dimension gate"
    )
    run = gate[0]["run"]
    assert "exit 1" in run, "a short sweep must fail, not warn"


def test_the_nightly_audit_refuses_to_synthesize_a_partial_sweep(nightly: dict):
    steps = nightly["jobs"]["synthesize"]["steps"]
    gate = [s for s in steps if "reported in" in str(s.get("name", ""))]
    assert gate and "exit 1" in gate[0]["run"]


@pytest.mark.parametrize(
    ("path", "job", "matrix_key", "count_output"),
    [
        (NIGHTLY_AUDIT, "audit", "dimension", "dimension_count"),
        (SECURITY_AUDIT, "audit", "lens", "lens_count"),
    ],
    ids=["nightly-dimensions", "security-lenses"],
)
def test_the_completeness_count_is_derived_from_the_matrix_not_hand_typed(
    path: Path, job: str, matrix_key: str, count_output: str
):
    """#3060: the expected-count constant used to be hand-synced by comment.

    Adding a lens without bumping it made the guard under-count and pass, which
    for the security audit means uploading a short SARIF that marks real unfixed
    alerts as FIXED.
    """
    workflow = _load(path)
    matrix = workflow["jobs"][job]["strategy"]["matrix"][matrix_key]
    assert "fromJSON" in str(matrix) and "preflight.outputs" in str(matrix), (
        f"{path.name}: the {matrix_key} matrix must come from the preflight output "
        f"that also feeds the completeness check, so the two cannot drift"
    )
    body = path.read_text(encoding="utf-8")
    assert re.search(rf"needs\.preflight\.outputs\.{count_output}", body), (
        f"{path.name}: the completeness check must read {count_output} from "
        f"preflight rather than a hand-typed constant (#3060)"
    )


# ----------------------------------------------------------------------
# Dedup: one issue per defect, searched against the whole backlog
# ----------------------------------------------------------------------


@pytest.mark.parametrize("path", (NIGHTLY_AUDIT, DOC_WALKTHROUGH), ids=lambda p: p.name)
def test_the_deterministic_dedup_pass_runs_before_anything_is_filed(path: Path):
    """Model-eyeballed dedup across ~900 open issues produced a 2.35x duplication
    ratio. The clustering + whole-backlog search has to be a real script."""
    workflow = _load(path)
    steps = workflow["jobs"]["synthesize"]["steps"]
    prep_index = next(
        (
            i
            for i, s in enumerate(steps)
            if "prepare_synthesis.py" in str(s.get("run", ""))
        ),
        None,
    )
    assert (
        prep_index is not None
    ), f"{path.name} must run scripts/audit/prepare_synthesis.py before filing"
    claude_index = next(
        i for i, s in enumerate(steps) if CLAUDE_ACTION in str(s.get("uses", ""))
    )
    assert prep_index < claude_index, "dedup must run BEFORE the filing step"


def test_the_dedup_script_exists():
    assert (REPO_ROOT / "scripts" / "audit" / "prepare_synthesis.py").is_file()


@pytest.mark.parametrize("path", (NIGHTLY_AUDIT, DOC_WALKTHROUGH), ids=lambda p: p.name)
def test_lenses_emit_a_root_cause_cluster_key(path: Path):
    """A file-scoped key files N issues for one N-file defect.

    `lemonade-server serve` became five issues in one night that way — one per
    doc walked, for a single fix spanning 163 occurrences.
    """
    body = path.read_text(encoding="utf-8")
    assert "cluster_key" in body, (
        f"{path.name}: findings must carry a root-cause `cluster_key` distinct "
        f"from the per-location `dedup_key`"
    )


@pytest.mark.parametrize("path", (NIGHTLY_AUDIT, DOC_WALKTHROUGH), ids=lambda p: p.name)
def test_synthesis_can_comment_on_an_existing_issue_instead_of_filing(path: Path):
    """The #1077 fix. Dedup that only searched its own label could not see a
    four-month-old milestoned issue, so it re-filed it 14 times in one night."""
    body = path.read_text(encoding="utf-8")
    assert "gh issue comment" in body, (
        f"{path.name}: synthesis must be able to comment on an existing backlog "
        f"issue rather than always filing a new one"
    )


# ----------------------------------------------------------------------
# Run receipts belong in the run, not the tracker
# ----------------------------------------------------------------------


@pytest.mark.parametrize("path", (NIGHTLY_AUDIT, DOC_WALKTHROUGH), ids=lambda p: p.name)
def test_the_run_summary_goes_to_the_job_summary_not_a_new_issue(path: Path):
    """19 bookkeeping issues accumulated at one per run, none actionable."""
    workflow = _load(path)
    steps = workflow["jobs"]["synthesize"]["steps"]
    publishes = [s for s in steps if "triage-report.md" in str(s.get("run", ""))]
    assert publishes, f"{path.name} must publish its run report to a step summary"
    assert any(
        "GITHUB_STEP_SUMMARY" in str(s.get("run", "")) for s in publishes
    ), f"{path.name}: the run report goes to $GITHUB_STEP_SUMMARY"

    body = path.read_text(encoding="utf-8")
    # The receipt title is only a problem when it is an ISSUE title. The same
    # words as a job-summary heading are exactly what should replace it, so match
    # the `gh issue create --title` shape rather than the words alone.
    receipt_issue = re.search(
        r"--title\s+\"(Nightly audit|Weekly audit|Doc walkthrough|Security audit)",
        body,
    )
    assert receipt_issue is None, (
        f"{path.name} still files a per-run receipt issue "
        f"({receipt_issue.group(0) if receipt_issue else ''!r}). A CI run's "
        f"bookkeeping belongs in the run, not the tracker — 19 such issues "
        f"accumulated with nothing actionable in any of them."
    )
    assert "NOT to an issue" in body, (
        f"{path.name}: the synthesis prompt must say explicitly that the run "
        f"report is a file for the job summary, not an issue"
    )


@pytest.mark.parametrize("path", (NIGHTLY_AUDIT, DOC_WALKTHROUGH), ids=lambda p: p.name)
def test_a_dry_run_mode_exists_so_dedup_can_be_validated_without_filing(path: Path):
    """Workflow changes are otherwise untestable without polluting the tracker."""
    workflow = _load(path)
    inputs = _triggers(workflow)["workflow_dispatch"]["inputs"]
    assert "dry_run" in inputs, f"{path.name} needs a dry_run dispatch input"
    body = path.read_text(encoding="utf-8")
    assert (
        "DRY RUN" in body
    ), f"{path.name}: the synthesis prompt must be told when the run is a dry run"


# ----------------------------------------------------------------------
# Cadence honesty and preserved invariants
# ----------------------------------------------------------------------


def test_the_nightly_audit_says_nightly_everywhere_it_says_anything(nightly: dict):
    """Filename, display name, and cron told three different stories."""
    assert NIGHTLY_AUDIT.name == "claude-nightly-audit.yml"
    assert "Nightly" in nightly["name"]
    crons = [entry["cron"] for entry in _triggers(nightly)["schedule"]]
    assert crons and all(
        cron.split()[2:] == ["*", "*", "*"] for cron in crons
    ), f"a workflow named nightly must run every day, got {crons}"
    assert nightly["concurrency"]["group"] == "claude-nightly-audit"


def test_the_doc_walkthrough_really_is_weekly(walkthrough: dict):
    crons = [entry["cron"] for entry in _triggers(walkthrough)["schedule"]]
    assert crons and all(
        cron.split()[4] != "*" for cron in crons
    ), f"a workflow named weekly must pin a day-of-week, got {crons}"


@pytest.mark.parametrize("path", (NIGHTLY_AUDIT, DOC_WALKTHROUGH), ids=lambda p: p.name)
def test_the_wontfix_suppression_mechanism_survives(path: Path):
    """Closing a finding with `audit-wontfix` must silence it forever. It is the
    only way a maintainer can permanently dismiss accepted debt."""
    body = path.read_text(encoding="utf-8")
    assert "audit-wontfix" in body
    prep = (REPO_ROOT / "scripts" / "audit" / "prepare_synthesis.py").read_text(
        encoding="utf-8"
    )
    assert (
        "audit-wontfix" in prep
    ), "the dedup pass must read the suppressed-forever key set"


@pytest.mark.parametrize("path", PROACTIVE_WORKFLOWS, ids=lambda p: p.name)
def test_the_claude_jobs_stay_read_only(path: Path):
    """Same rule as the claude.yml review jobs: report, never edit or run repo code."""
    for step in _claude_steps(_load(path)):
        args = str(step["with"].get("claude_args", ""))
        granted = re.search(r"--allowedTools\s+(\S+)", args)
        assert granted, f"{path.name}: a Claude step declares no --allowedTools"
        # Compared as a SET, not a substring: `Read,Grep,Glob,Bash,Edit` contains
        # `Read,Grep,Glob,Bash`, so a substring check waves Edit straight through.
        assert set(granted.group(1).split(",")) == {"Read", "Grep", "Glob", "Bash"}, (
            f"{path.name} step {step.get('name', step.get('id'))!r} grants "
            f"{granted.group(1)} — an audit reports, it never edits or runs repo code"
        )


def test_the_security_audit_never_files_a_public_issue(security: dict):
    """Findings go to the private code-scanning tab. A public issue would be
    disclosure."""
    body = SECURITY_AUDIT.read_text(encoding="utf-8")
    assert "gh issue create" not in body
    for job in security["jobs"].values():
        assert "issues" not in (
            job.get("permissions") or {}
        ), "no security-audit job may hold issues: write"
