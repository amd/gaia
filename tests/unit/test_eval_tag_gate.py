# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Evals run only on release tags, and only an RC may opt out with [skip eval].

Covers util/eval_tag_gate.py (the decision, and reading a real annotated vs.
lightweight tag through git) and the workflows that run evals on the
self-hosted pool: no PR or nightly triggers, and every self-hosted job of a
tag-triggered eval waits on the gate.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
sys.path.insert(0, str(REPO_ROOT / "util"))

import eval_tag_gate  # noqa: E402

TAG_EVAL_WORKFLOWS = [
    "eval_flagship.yml",
    "eval_retrieval.yml",
    "eval_code_retrieval.yml",
]
# Self-hosted workflows that run `gaia eval` but may keep a PR trigger, and why.
PR_TRIGGER_ALLOWED = {
    # The offline retrieval `pr` suite is one step of the RAG integration tests.
    "test_rag.yml",
}


# --- the decision -------------------------------------------------------------


@pytest.mark.parametrize(
    "event, ref_type, tag, otype, message, run",
    [
        ("push", "tag", "v0.25.0-rc16", "tag", "rc16 [skip eval]", False),
        ("push", "tag", "v0.25.0-rc16", "tag", "rc16 [SKIP EVAL]\n\nnotes", False),
        ("push", "tag", "v0.25.0-rc16", "tag", "rc16", True),
        # A lightweight tag's contents are the COMMIT message; never honour it.
        ("push", "tag", "v0.25.0-rc16", "commit", "fix [skip eval]", True),
        # A final release always runs.
        ("push", "tag", "v0.25.0", "tag", "v0.25.0 [skip eval]", True),
        ("push", "tag", "v0.24.1", "tag", "release", True),
        # Not an RC by release_tag.py's rules, so not skippable.
        ("push", "tag", "v0.25.0-rc0", "tag", "[skip eval]", True),
        ("push", "tag", "v0.25.0-rc16b", "tag", "[skip eval]", True),
        ("workflow_dispatch", "branch", "main", "", "", True),
        ("workflow_dispatch", "tag", "v0.25.0-rc16", "", "", True),
        ("push", "branch", "main", "", "", True),
    ],
)
def test_decide(event, ref_type, tag, otype, message, run):
    assert eval_tag_gate.decide(event, ref_type, tag, otype, message).run is run


def test_skip_and_ignored_marker_are_announced():
    skipped = eval_tag_gate.decide("push", "tag", "v0.25.0-rc16", "tag", "[skip eval]")
    assert "skipped" in skipped.notice and "v0.25.0-rc16" in skipped.notice
    ignored = eval_tag_gate.decide("push", "tag", "v0.25.0", "tag", "[skip eval]")
    assert ignored.run and "Running it" in ignored.notice
    assert eval_tag_gate.decide("push", "tag", "v0.25.0-rc16", "tag", "rc").notice == ""


def test_main_writes_output_notice_and_summary(tmp_path, monkeypatch, capsys):
    out, summary = tmp_path / "out", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.25.0-rc16")
    monkeypatch.setattr(
        eval_tag_gate, "read_tag", lambda tag: ("tag", "rc16 [skip eval]")
    )
    assert eval_tag_gate.main() == 0
    assert out.read_text(encoding="utf-8") == "run=false\n"
    assert "Eval skipped for v0.25.0-rc16" in summary.read_text(encoding="utf-8")
    assert "::notice title=Eval gate::" in capsys.readouterr().out


def test_main_fails_loudly_when_the_tag_cannot_be_read(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "v9.9.9-rc1")
    monkeypatch.chdir(tmp_path)  # not a git repository
    with pytest.raises(RuntimeError, match="cannot read the tag annotation"):
        eval_tag_gate.main()
    assert not (tmp_path / "out").exists()


# --- reading real tags through git -------------------------------------------


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def clone(tmp_path, monkeypatch):
    for key, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }.items():
        monkeypatch.setenv(key, value)
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "commit says [skip eval]")
    _git(origin, "tag", "-a", "v0.25.0-rc16", "-m", "rc16 [skip eval]")
    _git(origin, "tag", "v0.25.0-rc17")  # lightweight
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", "--no-tags", "--depth=1", origin.as_uri(), "work")
    monkeypatch.chdir(work)
    return work


def test_read_tag_sees_an_annotated_tag_message(clone):
    otype, message = eval_tag_gate.read_tag("v0.25.0-rc16")
    assert otype == "tag"
    assert "[skip eval]" in message
    assert not eval_tag_gate.decide("push", "tag", "v0.25.0-rc16", otype, message).run


def test_read_tag_never_skips_on_a_lightweight_tag(clone):
    otype, message = eval_tag_gate.read_tag("v0.25.0-rc17")
    assert otype == "commit"
    # The commit message carries the marker; it must not count.
    assert eval_tag_gate.decide("push", "tag", "v0.25.0-rc17", otype, message).run


def test_read_tag_upgrades_a_lightweight_local_copy(clone):
    # actions/checkout may leave a lightweight local ref for an annotated tag.
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(clone, "tag", "v0.25.0-rc16", head)
    assert eval_tag_gate.read_tag("v0.25.0-rc16")[0] == "tag"


# --- the workflows ------------------------------------------------------------


def _load(name):
    return yaml.load(
        (WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )


def _github_glob(pattern, ref):
    """GitHub filter glob: `*` matches anything but `/`."""
    regex = "".join("[^/]*" if c == "*" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, ref) is not None


def _self_hosted(job):
    runs_on = job.get("runs-on", [])
    return "self-hosted" in (runs_on if isinstance(runs_on, list) else [runs_on])


def _runs_gaia_eval(workflow):
    text = yaml.safe_dump(workflow)
    return any(_self_hosted(j) for j in workflow["jobs"].values()) and (
        "gaia eval" in text or "gaia.cli eval" in text or "run_eval_lane" in text
    )


def test_no_self_hosted_eval_runs_on_prs_or_a_schedule():
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = _load(path.name)
        if not isinstance(workflow, dict) or not _runs_gaia_eval(workflow):
            continue
        triggers = workflow["on"]
        if "schedule" in triggers:
            offenders.append(f"{path.name}: schedule")
        if "pull_request" in triggers and path.name not in PR_TRIGGER_ALLOWED:
            offenders.append(f"{path.name}: pull_request")
    assert not offenders, f"evals run only on release tags or dispatch: {offenders}"


@pytest.mark.parametrize("name", TAG_EVAL_WORKFLOWS)
def test_tag_patterns_cover_releases_and_rcs_only(name):
    patterns = _load(name)["on"]["push"]["tags"]
    for tag in ["v0.24.1", "v0.25.0", "v0.25.0-rc14", "v1.0.0-rc1"]:
        assert any(_github_glob(p, tag) for p in patterns), tag
    for tag in [
        "agent-pkg-gaia-v0.2.0",
        "bak/verifyedits-prerebase",
        "pre-rebase-3023",
    ]:
        assert not any(_github_glob(p, tag) for p in patterns), tag
    assert "workflow_dispatch" in _load(name)["on"]


@pytest.mark.parametrize("name", TAG_EVAL_WORKFLOWS)
def test_every_self_hosted_job_waits_on_the_gate(name):
    jobs = _load(name)["jobs"]
    gate = jobs["eval-gate"]
    assert not _self_hosted(gate)
    assert any("util/eval_tag_gate.py" in s.get("run", "") for s in gate["steps"])
    for job_id, job in jobs.items():
        if not _self_hosted(job):
            continue
        needs = job.get("needs", [])
        needs = needs if isinstance(needs, list) else [needs]
        assert "eval-gate" in needs, f"{name}:{job_id} does not need eval-gate"
        assert "needs.eval-gate.outputs.run == 'true'" in job.get("if", ""), job_id


def test_failing_tag_run_files_an_issue_but_a_skipped_one_does_not():
    job = _load("eval_flagship.yml")["jobs"]["release-escalation"]
    condition = job["if"]
    assert "github.event_name == 'push'" in condition
    assert "github.ref_type == 'tag'" in condition
    assert "needs.eval-gate.outputs.run == 'true'" in condition
