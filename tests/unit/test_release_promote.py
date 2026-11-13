# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/release_promote.py: find the dry run that verified a tag, and compare a
release against it.

Promotion is only as safe as this lookup, so each way a manifest can fail to
vouch for a tag -- another commit, another version, a failed check, an expired
or missing manifest -- is tried here and must be refused with a reason.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import release_promote as rp  # noqa: E402  pylint: disable=wrong-import-position

SHA = "a" * 40
OTHER = "b" * 40
RC = "v0.30.0-rc3"
FINAL = "v0.30.0"


def _art(flavor, name, digest, tag):
    return {
        "flavor": flavor,
        "name": name,
        "kind": "x",
        "sha256": digest,
        "size": 1,
        "destinations": [
            {"channel": "github-release", "tag": tag, "prerelease": flavor == "rc"}
        ],
    }


def manifest(commit=SHA, rc_tag=RC, errors=None, schema=1):
    return {
        "schema": schema,
        "dry_run": True,
        "commit": commit,
        "rc_tag": rc_tag,
        "final_tag": FINAL,
        "artifacts": [
            _art("rc", "setup-rc.exe", "1" * 64, rc_tag),
            _art("rc", "wheel-rc.whl", "2" * 64, rc_tag),
            _art("final", "setup.exe", "3" * 64, FINAL),
            {
                "flavor": "rc",
                "name": "pkg.tgz",
                "kind": "npm",
                "sha256": "4" * 64,
                "size": 1,
                "destinations": [{"channel": "npm", "package": "x"}],
            },
        ],
        "errors": errors or [],
    }


def _runs(*ids):
    return lambda repo, sha: [
        {"databaseId": i, "url": f"https://x/runs/{i}", "createdAt": f"2026-11-0{i}"}
        for i in ids
    ]


# ── locate ──────────────────────────────────────────────────────────────────


def test_locate_takes_the_newest_run_that_vouches_for_the_tag():
    manifests = {1: manifest(), 2: manifest()}
    found = rp.locate("o/r", SHA, RC, _runs(1, 2), lambda r, i: manifests[i])
    assert found.run_id == 2 and found.flavor == "rc"


def test_locate_matches_a_final_to_the_twin():
    found = rp.locate("o/r", SHA, FINAL, _runs(1), lambda r, i: manifest())
    assert found.flavor == "final"


def test_locate_skips_a_run_that_built_nothing_and_says_so():
    # A nightly that skipped an already-verified commit passes with no manifest.
    manifests = {1: manifest(), 2: None}
    found = rp.locate("o/r", SHA, RC, _runs(1, 2), lambda r, i: manifests[i])
    assert found.run_id == 1
    assert any("no live release-train-manifest" in s for s in found.skipped)


@pytest.mark.parametrize(
    "bad,reason",
    [
        (manifest(commit=OTHER), "of the tagged commit itself"),
        (manifest(rc_tag="v0.30.0-rc2"), "Artifacts embed their version"),
        (manifest(errors=["twin-diff failed"]), "failed check"),
        (manifest(schema=2), "schema"),
    ],
)
def test_locate_refuses_a_manifest_that_cannot_vouch(bad, reason):
    with pytest.raises(rp.PromoteError) as e:
        rp.locate("o/r", SHA, RC, _runs(1), lambda r, i: bad)
    msg = str(e.value)
    assert reason.lower() in msg.lower()
    # Actionable: names the fix.
    assert "gh workflow run release_train_dryrun.yml" in msg


def test_locate_with_no_runs_at_all_says_what_to_dispatch():
    with pytest.raises(rp.PromoteError, match="no passing.*verified v0.30.0-rc3") as e:
        rp.locate("o/r", SHA, RC, _runs(), lambda r, i: manifest())
    assert "-f rc_tag=v0.30.0-rc3." in str(e.value)
    with pytest.raises(rp.PromoteError) as e:
        rp.locate("o/r", SHA, FINAL, _runs(), lambda r, i: manifest())
    assert "-f rc_tag=v0.30.0-rcN." in str(e.value)


def test_locate_refuses_a_malformed_tag():
    with pytest.raises(ValueError):
        rp.locate("o/r", SHA, "not-a-tag", _runs(1), lambda r, i: manifest())


# ── compare ─────────────────────────────────────────────────────────────────


def test_expected_assets_are_the_flavors_github_release_files_only():
    m = manifest()
    assert rp.expected_release_assets(m, "rc", RC) == {
        "setup-rc.exe": "1" * 64,
        "wheel-rc.whl": "2" * 64,
    }
    assert rp.expected_release_assets(m, "final", FINAL) == {"setup.exe": "3" * 64}


def test_compare_sorts_every_asset_into_one_bucket():
    expected = {"a": "1", "b": "2", "c": "3"}
    published = {"a": "1", "b": "X", "z": "9"}
    c = rp.compare(expected, published)
    assert (c.identical, c.differ, c.missing, c.unverified) == (
        ["a"],
        ["b"],
        ["c"],
        ["z"],
    )


def _run_compare(tmp_path, monkeypatch, published, strict):
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest()), encoding="utf-8")
    monkeypatch.setattr(rp, "published_digests", lambda repo, tag: published)
    argv = ["compare", "--repo", "o/r", "--tag", RC, "--manifest", str(path)]
    return rp.main(argv + (["--strict"] if strict else []))


def test_compare_reports_a_rebuild_without_failing(tmp_path, monkeypatch, capsys):
    rc = _run_compare(tmp_path, monkeypatch, {"setup-rc.exe": "f" * 64}, False)
    captured = capsys.readouterr()
    out = captured.out
    assert rc == 0
    assert "**1** differ" in out and "**1** verified but not on the release" in out
    # The summary is stdout; the annotation goes to stderr, not into the summary.
    assert "::warning::" not in out
    assert "::warning::0 of 2 verified assets" in captured.err


def test_strict_compare_fails_on_any_byte_that_was_not_verified(
    tmp_path, monkeypatch, capsys
):
    rc = _run_compare(tmp_path, monkeypatch, {"setup-rc.exe": "f" * 64}, True)
    assert rc == 1
    assert "::error::2 asset(s)" in capsys.readouterr().err


def test_strict_compare_passes_a_fully_promoted_release(tmp_path, monkeypatch):
    published = {"setup-rc.exe": "1" * 64, "wheel-rc.whl": "2" * 64, "x.sig": "0"}
    assert _run_compare(tmp_path, monkeypatch, published, True) == 0


def test_published_digests_ignores_failed_uploads_and_undigested_assets(monkeypatch):
    release = {
        "assets": [
            {"name": "ok", "state": "uploaded", "digest": "sha256:" + "1" * 64},
            {"name": "broken", "state": "starter", "digest": "sha256:" + "2" * 64},
            {"name": "old", "state": "uploaded", "digest": None},
        ]
    }
    monkeypatch.setattr(rp, "_gh_json", lambda args: release)
    assert rp.published_digests("o/r", RC) == {"ok": "1" * 64}


# ── publish.yml: report only, after the release exists ─────────────────────


def _publish_job():
    wf = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    )
    return wf["jobs"]["verified-build-report"]


def test_the_report_runs_after_the_release_and_cannot_block_it():
    job = _publish_job()
    assert "github-release" in job["needs"]
    assert "!cancelled()" in job["if"]
    assert "needs.github-release.result == 'success'" in job["if"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert "environment" not in job
    wf = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    )
    for name, other in wf["jobs"].items():
        needs = other.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        assert "verified-build-report" not in needs, f"{name} waits on the report"


def test_the_report_does_not_use_strict_yet():
    run = "\n".join(s.get("run", "") for s in _publish_job()["steps"])
    assert "release_promote.py locate" in run
    assert "release_promote.py compare" in run
    assert "--strict" not in run


def test_a_hung_gh_call_fails_with_a_reason_not_forever(monkeypatch):
    def hang(*args, **kwargs):
        assert kwargs["timeout"] == rp.GH_TIMEOUT_S
        raise rp.subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(rp.subprocess, "run", hang)
    with pytest.raises(rp.PromoteError, match="did not finish"):
        rp.published_digests("o/r", RC)


def _release_after_a_spent_budget(monkeypatch, reset_in):
    """The release read is refused once on a spent budget; returns the waits."""
    waits, reset = [], int(time.time()) + reset_in

    def run(args, **kw):
        if args[:3] == ["gh", "api", "rate_limit"]:
            core = {"remaining": 0, "reset": reset}
            return rp.subprocess.CompletedProcess(args, 0, json.dumps(core), "")
        if not waits:
            msg = "gh: API rate limit exceeded for installation ID 1 (HTTP 403)"
            return rp.subprocess.CompletedProcess(args, 1, "", msg)
        return rp.subprocess.CompletedProcess(args, 0, '{"assets": []}', "")

    monkeypatch.setattr(rp.subprocess, "run", run)
    monkeypatch.setattr(time, "sleep", waits.append)
    return waits


def test_a_spent_budget_is_waited_out(monkeypatch, capsys):
    waits = _release_after_a_spent_budget(monkeypatch, reset_in=100)
    assert rp.published_digests("o/r", RC) == {}
    assert len(waits) == 1 and 95 <= waits[0] <= 110
    # locate's stdout is GITHUB_OUTPUT: the refused attempt must not land there.
    assert capsys.readouterr().out == ""


def test_a_budget_that_outlasts_the_bound_names_the_rate_limit(monkeypatch):
    waits = _release_after_a_spent_budget(monkeypatch, reset_in=7200)
    with pytest.raises(rp.PromoteError, match="rate limited"):
        rp.published_digests("o/r", RC)
    assert waits == []


def test_missing_ok_writes_only_github_outputs_to_stdout(monkeypatch, capsys, tmp_path):
    """publish.yml tees stdout into $GITHUB_OUTPUT; any other line fails the step."""

    def nothing(*a, **k):
        raise rp.PromoteError("no passing run.\nRuns tried:\n  - x: y\nDispatch one")

    monkeypatch.setattr(rp, "locate", nothing)
    argv = ["locate", "--repo", "o/r", "--sha", SHA, "--tag", RC]
    assert rp.main(argv + ["--out", str(tmp_path), "--missing-ok"]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines == ["found=false"]
    # One annotation, the whole message in it.
    (warning,) = captured.err.splitlines()
    assert warning.startswith("::warning::") and "%0ARuns tried:%0A" in warning


def test_without_missing_ok_a_missing_run_fails(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        rp, "locate", lambda *a, **k: (_ for _ in ()).throw(rp.PromoteError("none"))
    )
    argv = [
        "locate",
        "--repo",
        "o/r",
        "--sha",
        SHA,
        "--tag",
        RC,
        "--out",
        str(tmp_path),
    ]
    assert rp.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "::error::none" in captured.err


def test_a_found_run_writes_only_github_outputs_to_stdout(
    monkeypatch, capsys, tmp_path
):
    found = rp.Located(
        7, "https://x/runs/7", manifest(), "rc", ["https://x/runs/8: no"]
    )
    monkeypatch.setattr(rp, "locate", lambda *a, **k: found)
    argv = [
        "locate",
        "--repo",
        "o/r",
        "--sha",
        SHA,
        "--tag",
        RC,
        "--out",
        str(tmp_path),
    ]
    assert rp.main(argv) == 0
    lines = capsys.readouterr().out.splitlines()
    assert all("=" in line and " " not in line.split("=")[0] for line in lines), lines
    assert "found=true" in lines and "run_id=7" in lines
    assert json.loads((tmp_path / "release-manifest.json").read_text())["commit"] == SHA


def test_runs_reads_every_page_not_a_fixed_limit(monkeypatch):
    seen = {}

    def fake(args):
        seen["args"] = args
        lines = [
            json.dumps({"databaseId": i, "url": f"u{i}", "createdAt": f"t{i}"})
            for i in range(250)
        ]
        return rp.subprocess.CompletedProcess(args, 0, "\n".join(lines) + "\n", "")

    monkeypatch.setattr(rp, "_gh", fake)
    runs = rp._runs("o/r", SHA)
    assert len(runs) == 250
    assert "--paginate" in seen["args"] and "--limit" not in seen["args"]
    assert any(f"head_sha={SHA}" in a and "status=success" in a for a in seen["args"])


def test_runs_turns_garbage_from_github_into_a_reason(monkeypatch):
    monkeypatch.setattr(
        rp, "_gh", lambda a: rp.subprocess.CompletedProcess(a, 0, "<html>\n", "")
    )
    with pytest.raises(rp.PromoteError, match="not JSON"):
        rp._runs("o/r", SHA)


def test_missing_ok_also_reports_malformed_github_json(monkeypatch, capsys, tmp_path):
    def bad(*a, **k):
        raise json.JSONDecodeError("Expecting value", "<html>", 0)

    monkeypatch.setattr(rp, "locate", bad)
    argv = [
        "locate",
        "--repo",
        "o/r",
        "--sha",
        SHA,
        "--tag",
        RC,
        "--out",
        str(tmp_path),
    ]
    assert rp.main(argv + ["--missing-ok"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["found=false"]
    assert captured.err.startswith("::warning::")


def test_the_report_job_cannot_fail_the_publish_run():
    """A red job fails the Publish run, which holds a final's release notes."""
    assert _publish_job()["continue-on-error"] is True
