# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The helpers behind .github/workflows/test_installers_headless.yml.

Only the pure parts run here: naming, timing records, the threshold gate, the
fake model server and the threshold maths. The journey scripts install and
remove real software and refuse to run outside CI; this checks that they refuse.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
HEADLESS = REPO / "installer" / "headless"
WORKFLOW = REPO / ".github" / "workflows" / "test_installers_headless.yml"
THRESHOLDS = REPO / "installer" / "install_time_thresholds.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"headless_{name}", HEADLESS / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HEADLESS))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(HEADLESS))
    return mod


resolve = _load("resolve_release")
journey = _load("journey")
fake = _load("fake_model_server")
propose = _load("propose_thresholds")


def _journey(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
    return subprocess.run(
        [sys.executable, str(HEADLESS / "journey.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        check=check,
    )


# ── release naming ───────────────────────────────────────────────────────────


class TestReleaseNaming:
    def test_asset_and_pip_versions(self):
        assert resolve.asset_version("v0.25.1-rc2") == "0.25.1-rc.2"
        assert resolve.asset_version("v0.25.1") == "0.25.1"
        assert resolve.pep440("v0.25.1-rc2") == "0.25.1rc2"
        assert resolve.core_version("v0.25.1-rc2") == "0.25.1"

    @pytest.mark.parametrize(
        "bad", ["0.25.1", "v0.25", "v0.25.1-beta1", "v0.25.1-rc", "latest"]
    )
    def test_rejects_non_release_tags(self, bad):
        with pytest.raises(ValueError):
            resolve.parse_tag(bad)

    def test_final_sorts_after_its_candidates(self):
        tags = ["v0.25.1", "v0.25.1-rc10", "v0.25.0", "v0.25.1-rc2"]
        assert sorted(tags, key=resolve.sort_key) == [
            "v0.25.0",
            "v0.25.1-rc2",
            "v0.25.1-rc10",
            "v0.25.1",
        ]

    @staticmethod
    def _rel(tag, pre, complete=True):
        names = resolve.expected_assets(tag)
        ids = resolve.required(tag)
        if not complete:
            ids = ids[1:]
        return {
            "tag_name": tag,
            "prerelease": pre,
            "assets": [{"name": names[i]} for i in ids],
        }

    def test_default_is_newest_prerelease_unless_a_final_is_newer(self):
        rel = self._rel
        assert (
            resolve.pick_default([rel("v0.25.0", False), rel("v0.25.1-rc2", True)])
            == "v0.25.1-rc2"
        )
        assert (
            resolve.pick_default([rel("v0.25.1", False), rel("v0.25.1-rc2", True)])
            == "v0.25.1"
        )

    def test_default_skips_a_release_still_being_attached(self, capsys):
        rels = [
            self._rel("v0.25.1-rc2", True),
            self._rel("v0.25.1-rc3", True, complete=False),
        ]
        assert resolve.pick_default(rels) == "v0.25.1-rc2"
        assert "skipping v0.25.1-rc3" in capsys.readouterr().out

    def test_no_complete_release_is_an_error(self):
        with pytest.raises(
            SystemExit, match="no amd/gaia release carries a complete set"
        ):
            resolve.pick_default([self._rel("v0.25.1-rc3", True, complete=False)])

    def test_previous_stable_skips_candidates_of_the_same_version(self):
        rels = [
            {"tag_name": "v0.24.1", "prerelease": False},
            {"tag_name": "v0.25.0", "prerelease": False},
            {"tag_name": "v0.25.1-rc1", "prerelease": True},
        ]
        assert resolve.previous_stable(rels, "v0.25.1-rc2")["tag_name"] == "v0.25.0"
        assert resolve.previous_stable(rels, "v0.25.0")["tag_name"] == "v0.24.1"

    PACKAGES = {"tui-deb", "tui-rpm", "tui-pkg-darwin-arm64", "tui-pkg-darwin-x64"}

    def test_candidates_need_sidecars_and_finals_need_packages(self):
        assert "tui-deb" not in resolve.required("v0.25.1-rc2")
        assert "sidecar-linux-x64" in resolve.required("v0.25.1-rc2")
        assert self.PACKAGES <= set(resolve.required("v0.25.1"))

    @pytest.mark.parametrize("tag", ["v0.25.1-rc4", "v0.25.1-rc10", "v0.26.0-rc1"])
    def test_candidates_since_rc4_must_carry_their_own_packages(self, tag):
        # Required, so a tag-triggered run waits for them instead of falling
        # back to rebuilding ("rebuilt" rows) when it catches them mid-upload.
        assert self.PACKAGES <= set(resolve.required(tag))

    @pytest.mark.parametrize("tag", ["v0.25.1-rc1", "v0.25.1-rc3", "v0.25.0-rc9"])
    def test_older_candidates_still_rebuild_theirs(self, tag):
        assert not self.PACKAGES & set(resolve.required(tag))

    def test_a_candidate_names_its_packages_with_its_own_version(self):
        names = resolve.expected_assets("v0.25.1-rc4")
        assert names["tui-deb"] == "gaia_0.25.1-rc.4_amd64.deb"
        assert names["tui-rpm"] == "gaia-0.25.1-rc.4.x86_64.rpm"
        assert names["tui-pkg-darwin-arm64"] == "gaia-0.25.1-rc.4-darwin-arm64.pkg"
        assert names["tui-pkg-darwin-x64"] == "gaia-0.25.1-rc.4-darwin-x64.pkg"

    def test_terminal_package_names_match_the_build_table(self):
        """The names this waits for are the ones installer/tui/artifacts.py builds."""
        sys.path.insert(0, str(REPO / "installer" / "tui"))
        try:
            spec = importlib.util.spec_from_file_location(
                "tui_artifacts", REPO / "installer" / "tui" / "artifacts.py"
            )
            artifacts = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(artifacts)
        finally:
            sys.path.remove(str(REPO / "installer" / "tui"))
        names = resolve.expected_assets("v1.2.3")
        built = {
            key: name
            for p in artifacts.ARTIFACTS
            for name, key in artifacts.artifacts_for(p, "1.2.3")
        }
        assert names["tui-deb"] == built["linux-x64-deb"]
        assert names["tui-rpm"] == built["linux-x64-rpm"]
        assert names["tui-pkg-darwin-arm64"] == built["darwin-arm64-pkg"]
        assert names["tui-pkg-darwin-x64"] == built["darwin-x64-pkg"]
        assert names["win-setup"] == built["win-x64-setup"]


# ── phase records and the gate ───────────────────────────────────────────────


@pytest.fixture
def results(tmp_path):
    r = tmp_path / "row.json"
    _journey(
        "init",
        "--results",
        str(r),
        "--row",
        "demo-row",
        "--title",
        "Demo",
        "--os-label",
        "X",
        "--installer",
        "k",
        "--release",
        "v9.9.9",
        "--source",
        "release",
        check=True,
    )
    return r


def _thresholds(tmp_path, rows):
    t = tmp_path / "thresholds.json"
    t.write_text(
        json.dumps(
            {
                "gated_metrics": [
                    "install",
                    "launch_to_healthy",
                    "upgrade",
                    "uninstall",
                ],
                "rows": rows,
            }
        )
    )
    return t


def _phase_script(tmp_path, behaviours):
    """A stand-in phase command: per phase, (exit code, output, finding or None)."""
    script = tmp_path / "phase.py"
    script.write_text(
        "import json, os, subprocess, sys\n"
        f"B = {behaviours!r}\n"
        "code, out, finding = B.get(sys.argv[1], (0, '', None))\n"
        "print(out)\n"
        "if finding:\n"
        f"    subprocess.check_call([sys.executable, {str(HEADLESS / 'journey.py')!r}, 'finding',\n"
        "        '--results', os.environ['RESULTS'], '--phase', sys.argv[1],\n"
        "        '--known', os.environ['KNOWN'], '--text', finding])\n"
        "sys.exit(code)\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script), "{phase}"]


def _known(tmp_path, entries):
    k = tmp_path / "known.json"
    k.write_text(json.dumps({"entries": entries}))
    return k


def _run(results, tmp_path, phases, behaviours, known=(), thresholds=None, skip=()):
    k = _known(tmp_path, list(known))
    env = {k_: v for k_, v in os.environ.items() if k_ != "GITHUB_STEP_SUMMARY"}
    env.update(RESULTS=str(results), KNOWN=str(k))
    args = [
        sys.executable,
        str(HEADLESS / "journey.py"),
        "run",
        "--results",
        str(results),
        "--phases",
        " ".join(phases),
        "--known",
        str(k),
        "--thresholds",
        str(thresholds or _thresholds(tmp_path, {})),
    ]
    for s in skip:
        args += ["--skip", s]
    args += ["--", *_phase_script(tmp_path, behaviours)]
    out = subprocess.run(args, capture_output=True, text=True, env=env)
    return out, json.loads(results.read_text())


class TestJourney:
    def test_run_times_each_phase_and_stops_at_the_first_failure(
        self, results, tmp_path
    ):
        out, data = _run(
            results,
            tmp_path,
            ["install", "launch", "health"],
            {"launch": (7, "boom", None)},
        )
        assert out.returncode == 1
        p = data["phases"]
        assert p["install"]["status"] == "pass" and p["install"]["seconds"] >= 0
        assert p["launch"]["status"] == "fail" and p["launch"]["exit_code"] == 7
        assert p["health"]["status"] == "skipped"

    def test_a_known_failure_shows_its_issue_and_the_journey_continues(
        self, results, tmp_path
    ):
        known = [
            {
                "issue": 1234,
                "rows": ["demo-row"],
                "phase": "health",
                "kind": "failure",
                "signature": "GLIBC_2.38' not found",
            }
        ]
        out, data = _run(
            results,
            tmp_path,
            ["health", "uninstall"],
            {"health": (1, "version `GLIBC_2.38' not found", None)},
            known,
        )
        assert out.returncode == 0, out.stdout
        assert data["phases"]["health"] == {
            **data["phases"]["health"],
            "status": "known",
            "issue": 1234,
        }
        assert data["phases"]["uninstall"]["status"] == "pass"
        assert "known #1234" in out.stdout

    def test_a_known_entry_does_not_excuse_a_different_failure(self, results, tmp_path):
        known = [
            {
                "issue": 1234,
                "rows": ["demo-row"],
                "phase": "health",
                "kind": "failure",
                "signature": "GLIBC_2.38' not found",
            }
        ]
        out, data = _run(
            results,
            tmp_path,
            ["health"],
            {"health": (1, "something else", None)},
            known,
        )
        assert out.returncode == 1
        assert data["phases"]["health"]["status"] == "fail"

    def test_a_known_bug_that_stops_reproducing_fails_the_row(self, results, tmp_path):
        known = [
            {
                "issue": 1234,
                "rows": ["demo-row"],
                "phase": "health",
                "kind": "failure",
                "signature": "GLIBC_2.38' not found",
            }
        ]
        out, _ = _run(results, tmp_path, ["health"], {}, known)
        assert out.returncode == 1
        assert "known bug #1234 no longer reproduces" in out.stdout

    def test_a_blocking_known_failure_skips_the_rest_without_failing(
        self, results, tmp_path
    ):
        known = [
            {
                "issue": 99,
                "rows": ["demo-row"],
                "phase": "install",
                "kind": "failure",
                "signature": "Failed to build `cryptography",
                "blocks": True,
            }
        ]
        out, data = _run(
            results,
            tmp_path,
            ["install", "launch"],
            {"install": (1, "error: Failed to build `cryptography==50.0.2`", None)},
            known,
        )
        assert out.returncode == 0, out.stdout
        assert data["phases"]["launch"] == {
            **data["phases"]["launch"],
            "status": "skipped",
            "issue": 99,
        }

    def test_a_new_finding_fails_the_row_but_not_the_phases_after_it(
        self, results, tmp_path
    ):
        out, data = _run(
            results,
            tmp_path,
            ["install", "launch"],
            {"install": (0, "", "gaia-desktop is not on PATH")},
        )
        assert out.returncode == 1
        assert data["phases"]["launch"]["status"] == "pass"
        assert data["findings"] == ["gaia-desktop is not on PATH"]

    def test_a_known_finding_is_reported_and_does_not_fail(self, results, tmp_path):
        known = [
            {
                "issue": 5,
                "rows": ["demo-row"],
                "phase": "install",
                "kind": "finding",
                "signature": "not on PATH",
            }
        ]
        out, data = _run(
            results,
            tmp_path,
            ["install"],
            {"install": (0, "", "`gaia-desktop` is not on PATH")},
            known,
        )
        assert out.returncode == 0, out.stdout
        assert data.get("findings") is None
        assert data["known_findings"][0]["issue"] == 5

    def test_skip_records_not_applicable(self, results, tmp_path):
        out, data = _run(
            results,
            tmp_path,
            ["download", "install"],
            {},
            skip=["download=rebuilt in this run"],
        )
        assert out.returncode == 0
        assert data["phases"]["download"] == {
            "status": "n/a",
            "reason": "rebuilt in this run",
        }

    def test_a_phase_over_its_threshold_fails_naming_row_phase_and_numbers(
        self, results, tmp_path
    ):
        data = json.loads(results.read_text())
        data["phases"]["install"] = {"status": "pass", "seconds": 300.0}
        results.write_text(json.dumps(data))
        t = _thresholds(
            tmp_path, {"demo-row": {"metrics": {"install": {"threshold": 120}}}}
        )
        out = _journey("finish", "--results", str(results), "--thresholds", str(t))
        assert out.returncode == 1
        assert "demo-row / install: took 300s, over its 120s threshold" in out.stdout
        assert "install_time_thresholds.json" in out.stdout

    def test_no_threshold_is_a_warning_not_a_pass_or_a_failure(self, results, tmp_path):
        data = json.loads(results.read_text())
        data["phases"]["install"] = {"status": "pass", "seconds": 30.0}
        results.write_text(json.dumps(data))
        out = _journey(
            "finish",
            "--results",
            str(results),
            "--thresholds",
            str(_thresholds(tmp_path, {})),
        )
        assert out.returncode == 0
        assert "::warning::demo-row / install: no threshold yet" in out.stdout

    def test_download_is_never_gated(self):
        r = {"row": "x", "phases": {"download": {"status": "pass", "seconds": 999.0}}}
        t = {
            "gated_metrics": ["install"],
            "rows": {"x": {"metrics": {"download": {"threshold": 1}}}},
        }
        assert journey.check_thresholds(r, t) == ([], [])

    def test_launch_to_healthy_is_launch_plus_health(self):
        r = {
            "phases": {
                "launch": {"status": "pass", "seconds": 10.0},
                "health": {"status": "pass", "seconds": 50.0},
                "install": {"status": "known", "seconds": 3.0},
            }
        }
        assert journey.metrics_of(r) == {"launch_to_healthy": 60.0}

    def test_bounded_kills_a_command_that_overruns(self):
        out = _journey(
            "bounded",
            "--seconds",
            "1",
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        )
        assert out.returncode == 124

    def test_summarize_fails_when_any_row_failed(self, tmp_path):
        d = tmp_path / "res"
        d.mkdir()
        (d / "a.json").write_text(
            json.dumps(
                {
                    "row": "a",
                    "release": "v1",
                    "phases": {"install": {"status": "pass", "seconds": 1}},
                }
            )
        )
        (d / "b.json").write_text(
            json.dumps(
                {
                    "row": "b",
                    "release": "v1",
                    "phases": {"install": {"status": "fail", "seconds": 1}},
                }
            )
        )
        out = _journey(
            "summarize", "--dir", str(d), "--out", str(tmp_path / "all.json")
        )
        assert out.returncode == 1
        assert "| `a` |" in out.stdout and "| `b` |" in out.stdout
        assert len(json.loads((tmp_path / "all.json").read_text())["results"]) == 2


class TestKnownFailuresFile:
    def test_every_entry_names_an_issue_a_row_a_phase_and_a_signature(self):
        data = json.loads(
            (HEADLESS / "known_failures.json").read_text(encoding="utf-8")
        )
        rows = _workflow_rows()
        for e in data["entries"]:
            assert isinstance(e["issue"], int) and e["issue"] > 0
            assert e["phase"] in journey.PHASES and e["kind"] in ("failure", "finding")
            assert e["signature"].strip()
            for row in e["rows"]:
                assert row in rows, f"#{e['issue']} names unknown row {row}"
                assert (
                    e["phase"] in rows[row]["phases"].split()
                ), f"{row} has no {e['phase']} phase"


# ── the fake model server ────────────────────────────────────────────────────


@pytest.fixture
def server():
    fake._lemonade_version = "1.2.3"
    fake._loaded.clear()
    httpd = fake.LoopbackServer(("127.0.0.1", 0), fake.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _call(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read().decode()


@pytest.mark.allow_network  # loopback only: the server under test
class TestFakeModelServer:
    def test_health_reports_version_and_whatever_was_loaded(self, server):
        _call(
            f"{server}/api/v1/load",
            {"model_name": "Some-Model-GGUF", "ctx_size": 32768},
        )
        health = json.loads(_call(f"{server}/api/v1/health"))
        assert health["status"] == "ok" and health["version"] == "1.2.3"
        assert health["all_models_loaded"][0]["recipe_options"]["ctx_size"] == 32768
        models = json.loads(_call(f"{server}/v1/models"))["data"]
        assert [m["id"] for m in models] == ["Some-Model-GGUF"] and models[0][
            "downloaded"
        ] is True

    def test_chat_answers_streamed_and_not(self, server):
        plain = json.loads(
            _call(f"{server}/api/v1/chat/completions", {"model": "m", "messages": []})
        )
        assert plain["choices"][0]["message"]["content"] == fake.ANSWER
        streamed = _call(
            f"{server}/api/v1/chat/completions",
            {"model": "m", "messages": [], "stream": True},
        )
        assert streamed.rstrip().endswith("data: [DONE]")
        text = "".join(
            json.loads(line[6:])["choices"][0]["delta"].get("content", "")
            for line in streamed.splitlines()
            if line.startswith("data: {")
        )
        assert text == fake.ANSWER

    def test_embeddings_are_unit_vectors_one_per_input(self, server):
        out = json.loads(
            _call(f"{server}/api/v1/embeddings", {"model": "e", "input": ["a", "b"]})
        )
        assert len(out["data"]) == 2
        vec = out["data"][0]["embedding"]
        assert len(vec) == fake.EMBED_DIM and abs(sum(v * v for v in vec) - 1.0) < 1e-6

    def test_unknown_endpoints_404_rather_than_pretend(self, server):
        with pytest.raises(urllib.error.HTTPError) as err:
            _call(f"{server}/api/v1/nonsense")
        assert err.value.code == 404


# ── thresholds ───────────────────────────────────────────────────────────────


class TestThresholds:
    def test_formula(self):
        assert propose.threshold(10) == 70  # +60 s floor dominates small phases
        assert propose.threshold(300) == 450  # x1.5 dominates slow ones
        assert propose.p95([10, 20, 30]) == pytest.approx(29.0)

    def test_build_records_counts_and_sources(self):
        samples = {"r": {"install": [10.0, 12.0, 14.0], "download": [5.0, 50.0, 8.0]}}
        meta = {"r": {"runner_images": {"win25/1"}, "runs": [1, 2, 3]}}
        out = propose.build(samples, meta, [1, 2, 3], "v0.25.1-rc2")
        m = out["rows"]["r"]["metrics"]
        assert m["install"]["samples"] == 3 and m["install"][
            "threshold"
        ] == propose.threshold(m["install"]["p95"])
        assert "threshold" not in m["download"]
        assert out["rows"]["r"]["source_runs"] == [1, 2, 3]

    def test_checked_in_thresholds_trace_to_real_runs(self):
        data = json.loads(THRESHOLDS.read_text())
        assert data["gated_metrics"] == propose.GATED
        assert "download" not in data["gated_metrics"]
        rows = _workflow_rows()
        for row, entry in data["rows"].items():
            assert row in rows, f"{row} has a threshold but no row in the workflow"
            assert entry["source_runs"], f"{row} has no source runs"
            for metric, e in entry["metrics"].items():
                if "threshold" in e:
                    assert (
                        e["samples"] >= 3
                    ), f"{row}/{metric}: threshold from {e['samples']} samples"
                    assert e["threshold"] == propose.threshold(
                        e["p95"]
                    ), f"{row}/{metric} does not follow the formula"


# ── the workflow and the scripts agree ───────────────────────────────────────


def _workflow_rows():
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    rows = {}
    for job in ("windows", "posix"):
        for entry in wf["jobs"][job]["strategy"]["matrix"]["include"]:
            rows[entry["row"]] = entry
    return rows


class TestWorkflow:
    def test_every_row_uses_known_phases(self):
        for row, entry in _workflow_rows().items():
            phases = entry["phases"].split()
            assert phases and set(phases) <= set(journey.PHASES), row
            assert phases == [
                p for p in journey.PHASES if p in phases
            ], f"{row}: phases out of journey order"

    def test_every_kind_has_a_script_branch(self):
        posix = (HEADLESS / "posix-journey.sh").read_text(encoding="utf-8")
        windows = (HEADLESS / "windows-journey.ps1").read_text(encoding="utf-8")
        for row, entry in _workflow_rows().items():
            script = windows if row.startswith("win-") else posix
            assert (
                entry["kind"] in script
            ), f"{row}: kind {entry['kind']} unknown to its script"

    def test_pull_request_trigger_has_no_base_branch_filter(self):
        wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        assert "branches" not in wf[True]["pull_request"]


class TestScriptsRefuseOutsideCi:
    @pytest.mark.skipif(
        os.name == "nt", reason="bash script; the Windows twin is checked below"
    )
    def test_posix_journey_refuses_without_github_actions(self, tmp_path):
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "WORK": str(tmp_path / "w"),
        }
        out = subprocess.run(
            ["bash", str(HEADLESS / "posix-journey.sh"), "install"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert out.returncode != 0
        assert "refusing to run outside GitHub Actions" in out.stderr
        assert not (tmp_path / "w").exists()

    def test_both_scripts_check_ci_before_touching_anything(self):
        posix = (HEADLESS / "posix-journey.sh").read_text(encoding="utf-8")
        windows = (HEADLESS / "windows-journey.ps1").read_text(encoding="utf-8")
        assert posix.index("refusing to run outside GitHub Actions") < posix.index(
            "mkdir -p"
        )
        assert windows.index("refusing to run outside GitHub Actions") < windows.index(
            "New-Item"
        )
        assert "RUNNER_TEMP" in posix and "RUNNER_TEMP" in windows
