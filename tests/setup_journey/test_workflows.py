# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What the setup journey's workflows must keep true, plus its small checker."""

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
OS_MATRIX = {"windows-latest", "macos-latest", "ubuntu-24.04"}
DRAFT_GATE = "contains(github.event.pull_request.labels.*.name, 'ready_for_ci')"


def _load(name: str):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _checker():
    path = REPO / "installer" / "headless" / "check_real_setup.py"
    spec = importlib.util.spec_from_file_location("check_real_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPrJourney:
    wf = _load("test_setup_journey.yml")
    job = wf["jobs"]["journey"]

    def test_runs_on_prs_and_dispatch_and_covers_its_own_paths(self):
        triggers = self.wf[True]
        assert set(triggers) == {"pull_request", "workflow_dispatch"}
        paths = triggers["pull_request"]["paths"]
        for path in (
            ".github/workflows/test_setup_journey.yml",
            "installer/headless/**",
            "src/gaia/installer/**",
            "src/gaia/llm/lemonade_embedded.py",
            "src/gaia/llm/gpu_memory.py",
            "src/gaia/ui/setup*",
            "src/gaia/apps/webui/**",
            "tests/setup_journey/**",
        ):
            assert path in paths

    def test_three_oses_none_cancelling_the_others_behind_the_draft_gate(self):
        assert set(self.job["strategy"]["matrix"]["os"]) == OS_MATRIX
        assert self.job["strategy"]["fail-fast"] is False
        assert DRAFT_GATE in self.job["if"]
        assert self.wf["permissions"] == {"contents": "read"}

    def test_nothing_hides_a_failure(self):
        assert "continue-on-error" not in (
            WORKFLOWS / "test_setup_journey.yml"
        ).read_text(encoding="utf-8")

    def test_uploads_screenshots_even_when_a_journey_fails(self):
        uploads = [
            s for s in self.job["steps"] if "upload-artifact" in str(s.get("uses"))
        ]
        names = [s["with"]["name"] for s in uploads]
        assert any("screens" in n and "matrix.os" in n for n in names)
        assert all(s["if"] == "always()" for s in uploads)


class TestNightlyDownload:
    wf = _load("test_setup_real_download.yml")
    job = wf["jobs"]["download"]

    def test_scheduled_on_three_oses_with_no_cache(self):
        assert "schedule" in self.wf[True] and "workflow_dispatch" in self.wf[True]
        assert set(self.job["strategy"]["matrix"]["os"]) == OS_MATRIX
        text = (WORKFLOWS / "test_setup_real_download.yml").read_text(encoding="utf-8")
        assert "actions/cache" not in text and "cache: " not in text

    def test_never_passes_chat_model_to_a_real_init(self):
        # `gaia init` exits 2 on --chat-model without --check.
        runs = [s.get("run", "") for s in self.job["steps"]]
        init = next(r for r in runs if "gaia.cli init" in r)
        assert "--chat-model" not in init and "--yes" in init
        assert any("config set default_model" in r for r in runs)


class TestInstallerSetupPhase:
    wf = _load("test_installers_headless.yml")

    def _rows(self, job):
        return self.wf["jobs"][job]["strategy"]["matrix"]["include"]

    def test_only_desktop_rows_opt_in_and_never_the_upgrade(self):
        rows = self._rows("windows") + self._rows("posix")
        chosen = {r["row"] for r in rows if r.get("setup")}
        assert chosen == {
            "win-setup-ui",
            "win-setup-both",
            "win-agent-ui-exe",
            "mac-arm64-agent-ui-dmg",
            "ubuntu22-agent-ui-deb",
            "ubuntu24-agent-ui-deb",
            "ubuntu22-appimage",
            "ubuntu24-appimage",
        }

    def test_only_a_pushed_tag_runs_it(self):
        for job in ("windows", "posix"):
            guard = self.wf["jobs"][job]["env"]["SETUP_PHASE"]
            assert "github.event_name == 'push'" in guard
            assert "github.ref_type == 'tag'" in guard
            assert "matrix.setup == true" in guard

    def test_no_row_lists_it_so_prs_never_run_it(self):
        for row in self._rows("windows") + self._rows("posix"):
            assert "setup" not in row["phases"].split()

    def test_the_older_release_handling_stays(self):
        for script in ("posix-journey.sh", "windows-journey.ps1"):
            text = (REPO / "installer" / "headless" / script).read_text(
                encoding="utf-8"
            )
            assert "GAIA_SKIP_GAIA_INIT" in text and "setup" in text


class TestChecker:
    GOOD_PLAN = {
        "chat_model": {"id": "Small", "downloaded": True},
        "lemonade": {"reachable": True},
    }

    def test_a_finished_setup_has_no_problems(self):
        assert not _checker().problems_with_setup(
            {"ready": True}, self.GOOD_PLAN, "Small"
        )

    @pytest.mark.parametrize(
        ("check", "plan", "says"),
        [
            ({"ready": False, "stage": "load", "reasons": ["no"]}, GOOD_PLAN, "load"),
            ({"ready": True}, {**GOOD_PLAN, "chat_model": {"id": "Big"}}, "Big"),
            ({"ready": True}, {**GOOD_PLAN, "lemonade": {"reachable": False}}, "reach"),
        ],
    )
    def test_says_what_is_wrong(self, check, plan, says):
        problems = _checker().problems_with_setup(check, plan, "Small")
        assert problems and says in " ".join(problems)

    def test_a_used_home_is_not_cold(self, tmp_path):
        cache = tmp_path / "hub"
        assert _checker().problems_with_cold_start(tmp_path, cache) == []
        (cache / "models--a--b").mkdir(parents=True)
        (tmp_path / ".gaia").mkdir()
        assert len(_checker().problems_with_cold_start(tmp_path, cache)) == 2
