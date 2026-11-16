# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/check_release_ci.py: a final only publishes a commit CI passed; a
release candidate is never held by tests.

Most commits on main have no passing run of their own (the next merge cancels
it, path filters skip others), so on a final the gate dispatches what is
missing against the tag and waits. These tests pin that a final never treats
"no evidence" as green, never dispatches the same workflow twice, and ignores
the stale runs that made it dispatch; and that a candidate reads each check
once, reports every state, never dispatches or waits, and exits 0.
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
UNIT_RC = next(c for c in crc.RC_REPORTED if c.workflow == "test_unit.yml")


def run(conclusion, status="completed", minutes=0, run_id=1, event="workflow_dispatch"):
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "event": event,
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


def C(workflow, jobs=(), dispatch=True):
    return crc.Check(workflow, jobs, dispatch)


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _job_names(name: str) -> dict[str, str]:
    """Display name -> job id."""
    return {j.get("name", jid): jid for jid, j in _workflow(name)["jobs"].items()}


ALL_SETS = crc.RC_REPORTED + crc.FINAL_REQUIRED + crc.FINAL_ADVISORY


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


NPU_ROWS = ("NPU agent chat (Windows, cold)", "NPU agent chat (Linux, cold)")


def test_the_rc_report_lists_the_linux_suite_first_and_on_its_own():
    # Reported, never gating; Linux and the import floor stay their own row so
    # the summary says whether the suite the release is built on passed.
    assert [(c.workflow, c.jobs) for c in crc.RC_REPORTED] == [
        ("lint.yml", ()),
        ("test_unit.yml", (LINUX, FLOOR)),
        (
            "test_unit.yml",
            (MACOS, "Unit Tests (Windows, py3.12)", "Offline Integration Tests"),
        ),
        ("test_gaia_cli_linux.yml", ()),
        ("release_train_dryrun.yml", ()),
        ("test_npu_agent_chat.yml", (NPU_ROWS[0],)),
        ("test_npu_agent_chat.yml", (NPU_ROWS[1],)),
    ]


def test_the_final_requires_windows_npu_and_only_reports_linux():
    # #6764: the Linux runners cannot lock enough memory for the NPU model.
    required = [c.jobs for c in crc.FINAL_REQUIRED if c.workflow.startswith("test_npu")]
    advisory = [(c.workflow, c.jobs) for c in crc.FINAL_ADVISORY]
    assert required == [(NPU_ROWS[0],)]
    assert advisory == [("test_npu_agent_chat.yml", (NPU_ROWS[1],))]


def test_a_push_run_that_skipped_the_linux_npu_row_does_not_pass_as_it():
    # A main push starts only the Windows row; the Linux row is skipped.
    (linux,) = crc.FINAL_ADVISORY
    jobs = {1: [job(NPU_ROWS[0]), job(NPU_ROWS[1], "skipped")]}
    v = crc.judge(linux, [run("success")], "amd/gaia", "v1.0.0", _jobs_for(jobs))
    assert v.state == "failed"
    (windows,) = [c for c in crc.FINAL_REQUIRED if c.workflow.startswith("test_npu")]
    v = crc.judge(windows, [run("success")], "amd/gaia", "v1.0.0", _jobs_for(jobs))
    assert v.state == "success"


def test_a_final_requires_or_reports_everything_an_rc_sees():
    final = {}
    for c in crc.FINAL_REQUIRED + crc.FINAL_ADVISORY:
        final.setdefault(c.workflow, []).append(c)
    for c in crc.RC_REPORTED:
        assert c.workflow in final, c.label
        # The whole run, or at least the same jobs.
        assert any(
            not f.jobs or set(c.jobs) <= set(f.jobs) for f in final[c.workflow]
        ), c.label


def test_an_advisory_check_is_never_also_required():
    required = {(c.workflow, c.jobs) for c in crc.FINAL_REQUIRED}
    for c in crc.FINAL_ADVISORY:
        assert (c.workflow, c.jobs) not in required, c.label
        assert (c.workflow, ()) not in required, c.label


def test_every_test_unit_job_is_reported_on_an_rc():
    # A new job must show up in the RC summary, not be silently dropped.
    # "Select unit tests" only feeds the Linux job: if it fails, that job is
    # skipped, and the report says so. The result gate and the nightly alert
    # run no tests of their own.
    rows = [c.jobs for c in crc.RC_REPORTED if c.workflow == "test_unit.yml"]
    named = [n for jobs in rows for n in jobs]
    assert set(_job_names("test_unit.yml")) - set(named) == {
        "Select unit tests",
        "Unit Tests Result",
        "Report the nightly result",
    }
    assert len(named) == len(set(named)), "a job is reported twice"


def test_in_run_critical_jobs_gate_approve_publish():
    approve = _workflow("publish.yml")["jobs"]["approve-publish"]
    for jid in crc.RC_IN_RUN_CRITICAL:
        assert jid in approve["needs"], jid
        assert f"needs.{jid}.result == 'success'" in approve["if"], jid


def test_the_ci_gate_never_holds_an_rc_in_the_workflow_either():
    # Even if the script itself errors (an API outage), an RC still publishes.
    assert "ci-green" not in crc.RC_IN_RUN_CRITICAL
    approve = _workflow("publish.yml")["jobs"]["approve-publish"]
    assert (
        "(needs.validate.outputs.is_rc == 'true' || "
        "needs.ci-green.result == 'success')"
    ) in approve["if"]


def test_the_dry_run_holds_a_final_and_is_never_started_for_it():
    final = next(
        c for c in crc.FINAL_REQUIRED if c.workflow == "release_train_dryrun.yml"
    )
    assert final.dispatch is False
    assert "-f rc_tag=" in final.remedy_args


def test_an_rc_snapshot_reads_each_check_once():
    calls: list[str] = []

    def get_runs(w):
        calls.append(w)
        return []

    verdicts = crc.snapshot("amd/gaia", "v1.0.0-rc1", crc.RC_REPORTED, get_runs)
    assert calls == [c.workflow for c in crc.RC_REPORTED]
    assert {v.state for v in verdicts} == {"missing"}


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


def test_the_rc5_commit_is_reported_on_the_rc_and_fails_the_final_gate():
    unit = {
        1: unit_jobs(**{MACOS: ("failure", "completed")}),
        2: [job(n) for n in NPU_ROWS],
    }

    def get_runs(w):
        if w == "test_unit.yml":
            return [run("failure")]
        if w == "test_npu_agent_chat.yml":
            return [run("success", run_id=2)]
        return [run("success")]

    rc = crc.snapshot(
        "amd/gaia", "v1.0.0-rc1", crc.RC_REPORTED, get_runs, _jobs_for(unit)
    )
    assert [v.state for v in rc if v.workflow == "test_unit.yml"] == [
        "success",
        "failed",
    ]

    started: list = []
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


# ── the release candidate's report ──────────────────────────────────────


def _one_of_each_state():
    states = ["success", "failed", "pending", "missing"]
    return [
        crc.Verdict(
            C(f"{s}.yml"), s, f"https://x/{s}", f"fix {s}" if s != "success" else ""
        )
        for s in states
    ]


def test_the_rc_report_names_every_state_in_plain_words():
    text, warnings = crc.report_rc(_one_of_each_state(), "abc123def4567", "v1.0.0-rc1")
    for row in (
        "| `success.yml` | passed |",
        "| `failed.yml` | failed |",
        "| `pending.yml` | still running |",
        "| `missing.yml` | no run on this commit |",
    ):
        assert row in text
    assert "does not block a release candidate" in text
    assert "v1.0.0-rc1 publishes anyway" in text
    # One warning per check that is not green, each with its remedy in the summary.
    assert len(warnings) == 3
    assert all("the final requires it" in w for w in warnings)
    assert not any("success.yml" in w for w in warnings)
    for s in ("failed", "pending", "missing"):
        assert f"`fix {s}`" in text


def test_an_all_green_rc_report_has_no_warnings():
    green = [crc.Verdict(c, "success", "https://x") for c in crc.RC_REPORTED]
    text, warnings = crc.report_rc(green, "abc123", "v1.0.0-rc1")
    assert warnings == []
    assert "publishes anyway" not in text


def test_a_missing_dry_run_on_an_rc_prints_the_command_for_that_candidate():
    (dry,) = [c for c in crc.RC_REPORTED if c.workflow == "release_train_dryrun.yml"]
    v = crc.judge(dry, [], "amd/gaia", "v1.0.0-rc3")
    assert v.remedy == (
        "gh workflow run release_train_dryrun.yml --repo amd/gaia "
        "--ref v1.0.0-rc3 -f rc_tag=v1.0.0-rc3"
    )


def _rc_main(monkeypatch, tmp_path, get_runs, jobs):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(crc, "fetch_runs", lambda repo, w, sha, tok: get_runs(w))
    monkeypatch.setattr(crc, "fetch_jobs", lambda repo, rid, tok: jobs[rid])

    def never(*a, **k):
        pytest.fail("an RC must not dispatch, poll or wait")

    monkeypatch.setattr(crc, "check", never)
    monkeypatch.setattr(crc, "dispatch", never)
    monkeypatch.setattr(crc.time, "sleep", never)
    code = crc.main(["--repo", "amd/gaia", "--sha", "abc123", "--tag", "v1.0.0-rc3"])
    return code, summary.read_text(encoding="utf-8")


def test_an_rc_with_every_check_red_or_absent_still_exits_0(
    monkeypatch, tmp_path, capsys
):
    jobs = {
        1: unit_jobs(**{LINUX: (None, "in_progress"), MACOS: ("failure", "completed")})
    }

    def get_runs(w):
        if w == "lint.yml":
            return [run("failure", run_id=7)]
        if w == "test_unit.yml":
            return [run(None, status="in_progress")]
        return []

    code, summary = _rc_main(monkeypatch, tmp_path, get_runs, jobs)
    assert code == 0
    out = capsys.readouterr().out
    assert "| `lint.yml` | failed |" in summary
    assert f"| `test_unit.yml ({LINUX}, {FLOOR})` | still running |" in summary
    assert "| `test_gaia_cli_linux.yml` | no run on this commit |" in summary
    assert out.count("::warning::") == len(crc.RC_REPORTED)
    assert "::error::" not in out


def test_an_all_green_rc_exits_0_without_warnings(monkeypatch, tmp_path, capsys):
    jobs = {1: unit_jobs(), 2: [job(n) for n in NPU_ROWS]}

    def get_runs(w):
        return [run("success", run_id=2 if w == "test_npu_agent_chat.yml" else 1)]

    code, summary = _rc_main(monkeypatch, tmp_path, get_runs, jobs)
    assert code == 0
    assert "::warning::" not in capsys.readouterr().out
    assert "| passed |" in summary


# ── the API layer ───────────────────────────────────────────────────────


def test_dispatch_runs_the_workflow_on_the_tag(monkeypatch):
    calls = []
    monkeypatch.setattr(
        crc, "_api", lambda url, tok, body=None: calls.append((url, body))
    )
    crc.dispatch("amd/gaia", C("lint.yml"), "v1.0.0-rc2", "tok")
    assert calls == [
        (
            "https://api.github.com/repos/amd/gaia/actions/workflows/"
            "lint.yml/dispatches",
            {"ref": "v1.0.0-rc2"},
        )
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


def _final_main(monkeypatch, required_state, advisory_state):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    seen = {}

    def fake_check(repo, t, checks, get_runs, start_run, wait_minutes, **k):
        seen.update(checks=checks, wait=wait_minutes)
        return [crc.Verdict(c, required_state, "u", "r") for c in checks]

    def fake_snapshot(repo, t, checks, get_runs, get_jobs=None):
        seen["advisory"] = checks
        return [crc.Verdict(c, advisory_state, "u", "r") for c in checks]

    monkeypatch.setattr(crc, "check", fake_check)
    monkeypatch.setattr(crc, "snapshot", fake_snapshot)
    monkeypatch.setattr(crc, "dispatch", lambda *a: pytest.fail("advisory dispatch"))
    code = crc.main(["--repo", "amd/gaia", "--sha", "abc", "--tag", "v1.0.0"])
    return code, seen


@pytest.mark.parametrize("state, code", [("success", 0), ("failed", 1), ("pending", 1)])
def test_a_final_still_waits_on_and_is_held_by_everything(
    monkeypatch, capsys, state, code
):
    got, seen = _final_main(monkeypatch, state, "success")
    assert got == code
    assert seen["checks"] is crc.FINAL_REQUIRED
    assert seen["advisory"] is crc.FINAL_ADVISORY
    assert seen["wait"] == 80
    out = capsys.readouterr().out
    assert ("::error::" in out) == (code == 1)
    assert "::warning::" not in out


@pytest.mark.parametrize("state", ["failed", "pending", "missing"])
def test_a_red_advisory_check_warns_but_never_holds_a_final(monkeypatch, capsys, state):
    code, _ = _final_main(monkeypatch, "success", state)
    assert code == 0
    out = capsys.readouterr().out
    assert "::error::" not in out
    assert out.count("::warning::") == len(crc.FINAL_ADVISORY)
    assert "NPU agent chat (Linux, cold)" in out
    assert "does not hold the release" in out
    assert "| advisory |" in out


def test_the_final_report_shows_advisory_rows_without_blocking():
    req = [crc.Verdict(C("lint.yml"), "success", "u")]
    adv = [crc.Verdict(crc.FINAL_ADVISORY[0], "failed", "u2", "r")]
    ok, text = crc.report(req, "abc123", adv)
    assert ok
    assert "| `lint.yml` | required | success |" in text
    assert f"| `{crc.FINAL_ADVISORY[0].label}` | advisory | failed |" in text
    assert "Nothing was published" not in text


def test_an_rc_says_the_linux_npu_row_is_advisory_on_the_final():
    verdicts = [
        crc.Verdict(c, "failed", "u", "r")
        for c in crc.RC_REPORTED
        if c.workflow == "test_npu_agent_chat.yml"
    ]
    _, warnings = crc.report_rc(verdicts, "abc123", "v1.0.0-rc1")
    windows, linux = warnings
    assert NPU_ROWS[0] in windows and "the final requires it" in windows
    assert NPU_ROWS[1] in linux and "the final reports it as advisory" in linux


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


# ── A spent API budget is waited out (rc23 died on one) ─────────────────

RATE_LIMITED = b'{"message":"API rate limit exceeded for installation ID 1."}'


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self.body


def _api_replies(monkeypatch, *replies):
    """urlopen answers with ``replies`` in order; returns (requests, waits)."""
    import io
    import time
    import urllib.error

    queue, requests, waits = list(replies), [], []

    def urlopen(req, timeout):
        requests.append(req)
        code, headers, body = queue.pop(0)
        if code == 200:
            return _Response(body)
        raise urllib.error.HTTPError(req.full_url, code, "x", headers, io.BytesIO(body))

    monkeypatch.setattr(crc.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(time, "sleep", waits.append)
    return requests, waits


def _spent(reset_in: int) -> dict:
    import time

    return {
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int(time.time()) + reset_in),
    }


RUNS = (200, {}, b'{"workflow_runs": [{"id": 7}]}')


def test_the_gate_waits_out_a_spent_budget_then_reads_the_runs(monkeypatch):
    requests, waits = _api_replies(monkeypatch, (403, _spent(600), RATE_LIMITED), RUNS)
    assert crc.fetch_runs("amd/gaia", "lint.yml", "abc", "tok") == [{"id": 7}]
    assert len(requests) == 2
    assert len(waits) == 1 and 600 <= waits[0] <= 610


def test_a_403_that_is_not_a_rate_limit_fails_at_once(monkeypatch):
    denied = (
        403,
        {"X-RateLimit-Remaining": "4999"},
        b'{"message":"Resource not accessible by integration"}',
    )
    requests, waits = _api_replies(monkeypatch, denied)
    with pytest.raises(RuntimeError, match="actions: write") as exc:
        crc.fetch_runs("amd/gaia", "lint.yml", "abc", "tok")
    assert "rate limited" not in str(exc.value)
    assert (len(requests), waits) == (1, [])


@pytest.mark.parametrize("headers", [{}, {"X-RateLimit-Remaining": "0"}])
def test_a_404_fails_at_once_even_on_a_spent_budget(monkeypatch, headers):
    requests, waits = _api_replies(monkeypatch, (404, headers, b"Not Found"))
    with pytest.raises(RuntimeError, match="HTTP 404"):
        crc.fetch_runs("amd/gaia", "lint.yml", "abc", "tok")
    assert (len(requests), waits) == (1, [])


def test_a_budget_that_outlasts_the_bound_fails_naming_the_rate_limit(monkeypatch):
    late = _spent(crc.RATE_LIMIT_WAIT_SECONDS + 600)
    requests, waits = _api_replies(monkeypatch, (403, late, RATE_LIMITED))
    with pytest.raises(crc.RateLimitWaitExceeded, match="rate limited"):
        crc.fetch_runs("amd/gaia", "lint.yml", "abc", "tok")
    assert (len(requests), waits) == (1, [])


def test_retry_after_is_honoured(monkeypatch):
    limited = (429, {"Retry-After": "30"}, b"slow down")
    requests, waits = _api_replies(monkeypatch, limited, RUNS)
    assert crc.fetch_runs("amd/gaia", "lint.yml", "abc", "tok") == [{"id": 7}]
    assert waits == [30]


def test_a_secondary_limit_backs_off(monkeypatch):
    secondary = (403, {}, b"You have exceeded a secondary rate limit")
    _, waits = _api_replies(monkeypatch, secondary, secondary, RUNS)
    assert crc.fetch_runs("amd/gaia", "lint.yml", "abc", "tok") == [{"id": 7}]
    assert waits == [15, 30]


def test_a_rate_limited_dispatch_is_sent_again_once(monkeypatch):
    requests, waits = _api_replies(
        monkeypatch, (403, _spent(60), RATE_LIMITED), (200, {}, b"")
    )
    crc.dispatch("amd/gaia", C("lint.yml"), "v1.0.0", "tok")
    assert [r.get_method() for r in requests] == ["POST", "POST"]
    assert len(waits) == 1


def test_main_fails_loudly_when_the_budget_outlasts_the_bound(monkeypatch, capsys):
    def spent(*_):
        raise crc.RateLimitWaitExceeded("GET lint.yml runs was rate limited: ...")

    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(crc, "fetch_runs", spent)
    code = crc.main(["--repo", "amd/gaia", "--sha", "abc", "--tag", "v1.0.0-rc1"])
    assert code == 1
    assert "::error::GET lint.yml runs was rate limited" in capsys.readouterr().out


# ── Only a full-suite run of test_unit.yml passes a final ───────────────


FINAL_UNIT = next(c for c in crc.FINAL_REQUIRED if c.workflow == "test_unit.yml")


@pytest.mark.parametrize("event", ["push", "merge_group", "pull_request"])
def test_a_run_that_may_have_selected_tests_does_not_pass_a_final(event):
    # Pushes select on Windows and macOS; the queue and PRs select everywhere.
    v = crc.judge(FINAL_UNIT, [run("success", event=event)], "amd/gaia", "v1.0.0")
    assert v.state == "missing"
    assert "gh workflow run test_unit.yml" in v.remedy


@pytest.mark.parametrize("event", ["workflow_dispatch", "schedule"])
def test_a_dispatched_or_nightly_run_passes_a_final(event):
    v = crc.judge(FINAL_UNIT, [run("success", event=event)], "amd/gaia", "v1.0.0")
    assert v.state == "success"


def test_a_final_dispatches_the_full_suite_when_only_a_push_run_passed():
    started: list = []
    clock = Clock()
    crc.check(
        "amd/gaia",
        "v1.0.0",
        (FINAL_UNIT,),
        lambda w: [run("success", event="push")],
        started.append,
        wait_minutes=0,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert started == [FINAL_UNIT]


def test_a_merge_queue_run_is_never_the_rc_linux_row():
    # A queue run selects tests even on Linux; the row claims the full suite.
    green = {1: unit_jobs()}
    v = crc.judge(
        UNIT_RC,
        [run("success", event="merge_group")],
        "amd/gaia",
        "v1.0.0-rc1",
        _jobs_for(green),
    )
    assert v.state == "missing"


def test_a_check_without_events_reads_every_run():
    v = crc.judge(
        C("lint.yml"), [run("success", event="merge_group")], "amd/gaia", "v1.0.0"
    )
    assert v.state == "success"
