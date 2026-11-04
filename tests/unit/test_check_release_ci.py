# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/check_release_ci.py: a release only publishes a commit CI passed.

Most commits on main have no passing run of their own (the next merge cancels
it, path filters skip others), so the gate dispatches what is missing against
the tag and waits. These tests pin that it never treats "no evidence" as green,
never dispatches the same workflow twice, ignores the stale runs that made it
dispatch, and that a candidate is held only by its critical checks while a
final is held by everything.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_release_ci as crc  # noqa: E402

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
LINUX = "Unit Tests (Linux, py3.12)"
FLOOR = "Import Every Module (Linux, py3.10)"
MACOS = "Unit Tests (macOS, py3.12)"
UNIT_RC = next(c for c in crc.RC_CRITICAL if c.workflow == "test_unit.yml")


def run(conclusion, status="completed", minutes=0, run_id=1):
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://example/run/{run_id}",
        "created_at": (T0 + timedelta(minutes=minutes))
        .isoformat()
        .replace("+00:00", "Z"),
    }


def job(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


def unit_jobs(**overrides):
    """test_unit.yml's jobs, all green unless overridden by display name."""
    names = [
        "Select unit tests",
        LINUX,
        FLOOR,
        "Offline Integration Tests",
        MACOS,
        "Unit Tests (Windows, py3.12)",
    ]
    return [job(n, *overrides.get(n, ("success", "completed"))) for n in names]


class Clock:
    def __init__(self):
        self.t = T0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


def C(workflow, jobs=(), tag_input=""):
    return crc.Check(workflow, jobs, tag_input)


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _job_names(name: str) -> dict[str, str]:
    """Display name -> job id."""
    return {j.get("name", jid): jid for jid, j in _workflow(name)["jobs"].items()}


ALL_SETS = crc.RC_CRITICAL + crc.RC_ADVISORY + crc.FINAL_REQUIRED


# ── The lists match the workflows they name ─────────────────────────────


@pytest.mark.parametrize("check", ALL_SETS, ids=lambda c: c.label)
def test_every_check_names_real_jobs_and_inputs(check):
    wf = _workflow(check.workflow)
    on = wf.get("on", wf.get(True))
    assert "workflow_dispatch" in on, f"{check.workflow} cannot be dispatched"
    for name in check.jobs:
        assert name in _job_names(check.workflow), (
            f"{check.workflow} has no job named {name!r}; the gate would hold "
            "every release on a job that never runs"
        )
    if check.tag_input:
        inputs = (on["workflow_dispatch"] or {}).get("inputs", {})
        assert check.tag_input in inputs, check.workflow


def test_the_rc_set_is_exactly_the_agreed_critical_list():
    assert [(c.workflow, c.jobs) for c in crc.RC_CRITICAL] == [
        ("lint.yml", ()),
        ("test_unit.yml", (LINUX, FLOOR)),
        ("release_train_dryrun.yml", ()),
    ]
    assert all(c.why for c in crc.RC_CRITICAL)


def test_a_final_requires_everything_an_rc_sees():
    final = {c.workflow: c for c in crc.FINAL_REQUIRED}
    for c in crc.RC_CRITICAL + crc.RC_ADVISORY:
        assert c.workflow in final, c.label
        # The whole run, or at least the same jobs.
        assert not final[c.workflow].jobs or set(c.jobs) <= set(final[c.workflow].jobs)


def test_every_test_unit_job_is_critical_or_advisory_on_an_rc():
    # A new job must be classified, not silently dropped from the RC view.
    # "Select unit tests" only feeds the Linux job: if it fails, that job is
    # skipped, and a skipped critical job fails the gate.
    named = {n for c in crc.RC_CRITICAL + crc.RC_ADVISORY for n in c.jobs}
    assert set(_job_names("test_unit.yml")) - named == {"Select unit tests"}
    critical = {n for c in crc.RC_CRITICAL for n in c.jobs}
    assert not critical & {n for c in crc.RC_ADVISORY for n in c.jobs}


def test_in_run_critical_jobs_gate_approve_publish():
    approve = _workflow("publish.yml")["jobs"]["approve-publish"]
    for jid in crc.RC_IN_RUN_CRITICAL:
        assert jid in approve["needs"], jid
        assert f"needs.{jid}.result == 'success'" in approve["if"], jid


def test_the_rc_dry_run_is_dispatched_with_its_tag_and_a_final_is_never():
    rc = next(c for c in crc.RC_CRITICAL if c.workflow == "release_train_dryrun.yml")
    final = next(
        c for c in crc.FINAL_REQUIRED if c.workflow == "release_train_dryrun.yml"
    )
    assert rc.tag_input == "rc_tag"
    assert final.tag_input is None


# ── judge(): whole-run checks ───────────────────────────────────────────


@pytest.mark.parametrize(
    "runs, state",
    [
        ([run("success")], "success"),
        # One success on the commit is enough, whatever else ran.
        ([run("cancelled", minutes=5, run_id=2), run("success")], "success"),
        ([run(None, status="in_progress")], "pending"),
        ([run("failure")], "failed"),
        ([run("cancelled")], "failed"),
        ([], "missing"),
    ],
)
def test_judge(runs, state):
    assert crc.judge(C("lint.yml"), runs, "amd/gaia", "v1.0.0-rc1").state == state


# ── judge(): job-level checks ───────────────────────────────────────────


def _jobs_for(mapping):
    return lambda run_id: mapping[run_id]


def test_a_red_macos_leg_does_not_fail_the_rc_unit_check():
    # v0.25.4-rc5: test_unit.yml failed only on macOS and held the candidate.
    jobs = {1: unit_jobs(**{MACOS: ("failure", "completed")})}
    v = crc.judge(UNIT_RC, [run("failure")], "amd/gaia", "v1.0.0-rc1", _jobs_for(jobs))
    assert v.state == "success"


def test_a_still_running_macos_leg_does_not_hold_the_rc():
    jobs = {1: unit_jobs(**{MACOS: (None, "in_progress")})}
    v = crc.judge(
        UNIT_RC,
        [run(None, status="in_progress")],
        "amd/gaia",
        "v1.0.0-rc1",
        _jobs_for(jobs),
    )
    assert v.state == "success"


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled", None])
def test_a_critical_job_that_did_not_pass_fails(conclusion):
    jobs = {1: unit_jobs(**{LINUX: (conclusion, "completed")})}
    v = crc.judge(UNIT_RC, [run("failure")], "amd/gaia", "v1.0.0-rc1", _jobs_for(jobs))
    assert v.state == "failed"
    assert LINUX in v.detail
    assert "gh run rerun 1" in v.remedy


def test_a_critical_job_missing_from_a_finished_run_fails_loudly():
    jobs = {1: [j for j in unit_jobs() if j["name"] != FLOOR]}
    v = crc.judge(UNIT_RC, [run("success")], "amd/gaia", "v1.0.0-rc1", _jobs_for(jobs))
    assert v.state == "failed"
    assert "is not a job of this run" in v.detail


def test_a_critical_job_still_running_is_pending():
    jobs = {1: unit_jobs(**{LINUX: (None, "in_progress")})}
    v = crc.judge(
        UNIT_RC,
        [run(None, status="in_progress")],
        "amd/gaia",
        "v1.0.0-rc1",
        _jobs_for(jobs),
    )
    assert v.state == "pending"


def test_a_critical_job_not_yet_created_in_a_running_run_is_pending():
    jobs = {1: [job("Select unit tests", None, "in_progress")]}
    v = crc.judge(
        UNIT_RC,
        [run(None, status="in_progress")],
        "amd/gaia",
        "v1.0.0-rc1",
        _jobs_for(jobs),
    )
    assert v.state == "pending"


def test_any_run_with_every_critical_job_green_is_enough():
    jobs = {
        2: unit_jobs(**{LINUX: ("failure", "completed")}),
        1: unit_jobs(),
    }
    runs = [run("failure", minutes=5, run_id=2), run("success", run_id=1)]
    v = crc.judge(UNIT_RC, runs, "amd/gaia", "v1.0.0-rc1", _jobs_for(jobs))
    assert v.state == "success"


def test_a_job_level_check_without_a_job_source_is_an_error():
    with pytest.raises(ValueError, match="get_jobs"):
        crc.judge(UNIT_RC, [run("success")], "amd/gaia", "v1.0.0-rc1")


# ── check(): dispatch and wait ──────────────────────────────────────────


def test_already_green_needs_no_dispatch():
    started = []
    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        (C("a.yml"), C("b.yml")),
        lambda w: [run("success")],
        started.append,
        wait_minutes=10,
    )
    assert started == []
    assert {v.state for v in verdicts} == {"success"}


def test_an_empty_check_list_is_refused():
    with pytest.raises(ValueError, match="no checks"):
        crc.check("amd/gaia", "v1", (), lambda w: [], lambda c: None, 1)


def test_the_rc5_commit_passes_the_rc_gate_and_fails_the_final_gate():
    unit = {1: unit_jobs(**{MACOS: ("failure", "completed")})}

    def get_runs(w):
        return [run("failure")] if w == "test_unit.yml" else [run("success")]

    started: list = []
    rc = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        crc.RC_CRITICAL,
        get_runs,
        started.append,
        wait_minutes=10,
        get_jobs=_jobs_for(unit),
    )
    assert {v.state for v in rc} == {"success"}
    assert started == []

    clock = Clock()
    final = crc.check(
        "amd/gaia",
        "v1.0.0",
        crc.FINAL_REQUIRED,
        get_runs,
        started.append,
        wait_minutes=0,
        get_jobs=_jobs_for(unit),
        sleep=clock.sleep,
        now=clock.now,
    )
    # The final re-checks the red workflow once instead of shipping past it.
    assert [c.workflow for c in started] == ["test_unit.yml"]
    assert not crc.report(final, "abc123")[0]


def test_missing_and_cancelled_are_dispatched_once_then_waited_for():
    clock = Clock()
    started: list[str] = []
    polls = {"n": 0}

    def get_runs(w):
        stale = [run("cancelled", minutes=-60)] if w == "cancelled.yml" else []
        if w not in started:
            return stale
        polls["n"] += 1
        mine = clock.now() - T0
        created = int(mine.total_seconds() // 60)
        if polls["n"] <= 2:
            return stale  # the dispatched run has not appeared yet
        if polls["n"] <= 4:
            return stale + [run(None, status="in_progress", minutes=created, run_id=9)]
        return stale + [run("success", minutes=created, run_id=9)]

    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        (C("missing.yml"), C("cancelled.yml")),
        get_runs,
        lambda c: started.append(c.workflow),
        wait_minutes=30,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert sorted(started) == ["cancelled.yml", "missing.yml"]
    assert {v.state for v in verdicts} == {"success"}


def test_a_check_the_gate_must_not_start_fails_at_once_with_the_command():
    started: list = []
    final_dry_run = next(
        c for c in crc.FINAL_REQUIRED if c.workflow == "release_train_dryrun.yml"
    )
    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0",
        (final_dry_run, C("slow.yml")),
        lambda w: [] if w == final_dry_run.workflow else [run(None, "in_progress")],
        started.append,
        wait_minutes=60,
        sleep=lambda s: pytest.fail("waited on a release that cannot pass"),
    )
    assert started == []
    assert verdicts[0].state == "missing"
    ok, text = crc.report(verdicts, "abc123")
    assert not ok
    assert "gh workflow run release_train_dryrun.yml" in text
    assert "-f rc_tag=" in text


def test_a_dispatched_run_that_fails_stops_the_release():
    clock = Clock()
    started: list[str] = []

    def get_runs(w):
        if w not in started:
            return []
        return [run("failure", minutes=int((clock.now() - T0).total_seconds() // 60))]

    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        (C("a.yml"),),
        get_runs,
        lambda c: started.append(c.workflow),
        wait_minutes=30,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert started == ["a.yml"]
    (v,) = verdicts
    assert v.state == "failed"
    ok, text = crc.report(verdicts, "abc123")
    assert not ok and "gh run rerun" in text


def test_the_stale_failure_does_not_count_after_dispatch():
    # The old failed run is why we dispatched; it must not end the wait.
    clock = Clock()
    started: list[str] = []
    old_failure = run("failure", minutes=-30)

    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        (C("a.yml"),),
        lambda w: [old_failure],
        lambda c: started.append(c.workflow),
        wait_minutes=5,
        poll_seconds=60,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert started == ["a.yml"]
    (v,) = verdicts
    # Timed out waiting for our run, never "failed" on the stale one.
    assert v.state == "pending"
    assert not crc.report(verdicts, "abc123")[0]


def test_in_progress_on_main_is_waited_for_not_dispatched():
    clock = Clock()
    started: list[str] = []
    states = iter(["in_progress", "in_progress", "done"])

    def get_runs(w):
        s = next(states)
        return [run("success")] if s == "done" else [run(None, status=s)]

    verdicts = crc.check(
        "amd/gaia",
        "v1.0.0-rc1",
        (C("a.yml"),),
        get_runs,
        started.append,
        wait_minutes=30,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert started == []
    assert verdicts[0].state == "success"


# ── advisory and the report ─────────────────────────────────────────────


def test_advisory_checks_are_reported_but_never_block():
    unit = {1: unit_jobs(**{MACOS: ("failure", "completed")})}
    advisory = crc.advise(
        "amd/gaia",
        "v1.0.0-rc1",
        crc.RC_ADVISORY,
        lambda w: [run("failure")] if w == "test_unit.yml" else [],
        _jobs_for(unit),
    )
    assert {v.state for v in advisory} == {"failed", "missing"}
    green = [crc.Verdict(c, "success", "https://x") for c in crc.RC_CRITICAL]
    ok, text = crc.report(green, "abc123", is_rc=True, advisory=advisory)
    assert ok
    assert "| advisory | failed |" in text
    assert "release candidate gate" in text
    assert "final requires them" in text


# ── the API layer ───────────────────────────────────────────────────────


def test_dispatch_passes_the_tag_to_the_named_input(monkeypatch):
    calls = []
    monkeypatch.setattr(crc, "_api", lambda url, tok, body=None: calls.append(body))
    crc.dispatch("amd/gaia", C("d.yml", tag_input="rc_tag"), "v1.0.0-rc2", "tok")
    crc.dispatch("amd/gaia", C("lint.yml"), "v1.0.0-rc2", "tok")
    assert calls == [
        {"ref": "v1.0.0-rc2", "inputs": {"rc_tag": "v1.0.0-rc2"}},
        {"ref": "v1.0.0-rc2"},
    ]


def test_fetch_jobs_reads_the_latest_attempt(monkeypatch):
    urls = []

    def fake(url, tok, body=None):
        urls.append(url)
        return {"jobs": [job(LINUX)]}

    monkeypatch.setattr(crc, "_api", fake)
    assert crc.fetch_jobs("amd/gaia", 42, "tok") == [job(LINUX)]
    assert "/runs/42/jobs?filter=latest" in urls[0]


def test_main_requires_a_token(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert crc.main(["--repo", "amd/gaia", "--sha", "abc", "--tag", "v1.0.0"]) == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_main_refuses_a_tag_that_is_not_a_release(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    assert crc.main(["--repo", "amd/gaia", "--sha", "abc", "--tag", "nightly"]) == 1
    assert "not a release tag" in capsys.readouterr().err


@pytest.mark.parametrize(
    "tag, expected", [("v1.0.0-rc3", "RC_CRITICAL"), ("v1.0.0", "FINAL_REQUIRED")]
)
def test_main_picks_the_set_from_the_tag(monkeypatch, capsys, tag, expected):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    seen = {}

    def fake_check(repo, t, checks, *a, **k):
        seen["checks"] = checks
        return [crc.Verdict(c, "success", "u") for c in checks]

    monkeypatch.setattr(crc, "check", fake_check)
    monkeypatch.setattr(crc, "fetch_runs", lambda *a: [])
    assert crc.main(["--repo", "amd/gaia", "--sha", "abc", "--tag", tag]) == 0
    assert seen["checks"] is getattr(crc, expected)
    out = capsys.readouterr().out
    # An RC prints a warning per advisory check that is not green; a final has none.
    assert ("::warning::advisory" in out) == tag.endswith("rc3")


@pytest.mark.parametrize("code, hinted", [(403, True), (404, False), (502, False)])
def test_only_a_403_blames_permissions(monkeypatch, code, hinted):
    import io
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b"body"))

    monkeypatch.setattr(crc.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as exc:
        crc.fetch_runs("amd/gaia", "lint.yml", "abc", "tok")
    assert f"HTTP {code}" in str(exc.value)
    assert ("actions: write" in str(exc.value)) is hinted
