# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/check_module_skips.py (#4206).

The guard is only worth having if it sees the skip the lane would hide, so the
collection cases run a real `pytest --collect-only` over an ephemeral repo in
tmp_path, not a stub.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_module_skips as skips_guard  # noqa: E402
import check_test_lane_coverage as lanes  # noqa: E402

MISSING = "gaia_no_such_module_4206"
GATED = f'import pytest\npytest.importorskip("{MISSING}")\n\ndef test_x():\n    pass\n'
PLAIN = "def test_y():\n    pass\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An ephemeral repo with both guards' paths pointed at it."""
    monkeypatch.setattr(lanes, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(lanes, "WORKFLOW_DIR", tmp_path / ".github" / "workflows")
    monkeypatch.setattr(
        lanes, "ALLOWLIST_PATH", tmp_path / "util" / "test_lane_allowlist.yml"
    )
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "util").mkdir()
    _write_allowlist(tmp_path, [])
    return tmp_path


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_allowlist(repo: Path, groups: list) -> None:
    _write(
        repo,
        "util/test_lane_allowlist.yml",
        yaml.dump({"files": {}, "module_skips": groups}),
    )


def _write_lane(repo: Path, steps: list) -> None:
    doc = {
        "on": {"push": {}},
        "jobs": {"unit": {"runs-on": "x", "steps": [{"run": s} for s in steps]}},
    }
    _write(repo, ".github/workflows/lane.yml", yaml.dump(doc))


class TestCollection:
    def test_module_level_importorskip_is_reported(self, repo):
        _write(repo, "tests/unit/test_gated.py", GATED)
        _write(repo, "tests/unit/test_plain.py", PLAIN)
        found = skips_guard.collect_skips(["tests/unit"])
        assert [s["path"] for s in found] == ["tests/unit/test_gated.py"]
        assert MISSING in found[0]["reason"]

    def test_importer_of_a_skipping_module_is_reported_under_its_own_name(self, repo):
        # pytest's -rs line blames the helper's line; the importer must still
        # show up, or its tests vanish without a name (test_chat_inline_web_ssrf).
        _write(repo, "tests/unit/test_gated.py", GATED)
        _write(repo, "tests/unit/test_importer.py", "from test_gated import *\n")
        found = skips_guard.collect_skips(["tests/unit"])
        assert sorted(s["path"] for s in found) == [
            "tests/unit/test_gated.py",
            "tests/unit/test_importer.py",
        ]

    def test_per_test_skip_is_not_a_wholesale_skip(self, repo):
        per_test = (
            "import pytest\n\n"
            "@pytest.mark.skip(reason='later')\n"
            "def test_z():\n    pass\n"
        )
        _write(repo, "tests/unit/test_marked.py", per_test)
        assert skips_guard.collect_skips(["tests/unit"]) == []

    def test_collection_error_fails_loudly(self, repo):
        _write(repo, "tests/unit/test_broken.py", "def test_(:\n")
        with pytest.raises(RuntimeError, match="cannot be checked"):
            skips_guard.collect_skips(["tests/unit"])


class TestJudge:
    ENTRIES = [
        lanes.ModuleSkip("tests/unit/email", "Needs the email wheel.", "email.yml"),
        lanes.ModuleSkip("tests/unit/test_broken.py", "Goldens drifted.", None),
    ]

    def _judge(self, path, workflow="unit.yml"):
        return skips_guard.judge(
            [{"path": path, "reason": "no module"}], self.ENTRIES, workflow
        )

    def test_unlisted_skip_is_a_violation(self):
        violations, _ = self._judge("tests/unit/test_chat.py")
        assert "not in module_skips" in violations[0]

    def test_listed_skip_run_elsewhere_is_allowed(self):
        violations, allowed = self._judge("tests/unit/email/test_a.py")
        assert not violations
        assert allowed == ["tests/unit/email/test_a.py -> email.yml"]

    def test_skip_in_the_lane_meant_to_run_it_is_a_violation(self):
        violations, _ = self._judge("tests/unit/email/test_a.py", "email.yml")
        assert "says email.yml runs this file" in violations[0]

    def test_entry_without_a_lane_is_allowed_and_names_its_reason(self):
        violations, allowed = self._judge("tests/unit/test_broken.py")
        assert not violations
        assert "no lane: Goldens drifted." in allowed[0]


class TestMain:
    def test_unlisted_wholesale_skip_fails_the_lane(self, repo, capsys):
        _write(repo, "tests/unit/test_gated.py", GATED)
        _write_lane(repo, ["pytest tests/unit/ -v"])
        assert skips_guard.main(["--workflow", "lane.yml", "--job", "unit"]) == 1
        assert "tests/unit/test_gated.py" in capsys.readouterr().err

    def test_listed_skip_passes(self, repo, capsys):
        _write(repo, "tests/unit/test_gated.py", GATED)
        _write_lane(repo, ["pytest tests/unit/ -v"])
        _write_allowlist(
            repo,
            [
                {
                    "reason": "Needs a wheel.",
                    "runs_in": "other.yml",
                    "paths": ["tests/unit/test_gated.py"],
                }
            ],
        )
        assert skips_guard.main(["--workflow", "lane.yml", "--job", "unit"]) == 0
        assert "1 wholesale skip(s), all allowlisted" in capsys.readouterr().out

    def test_unknown_job_fails(self, repo, capsys):
        _write_lane(repo, ["pytest tests/unit/ -v"])
        assert skips_guard.main(["--workflow", "lane.yml", "--job", "nope"]) == 1
        assert "no job `nope`" in capsys.readouterr().err

    def test_job_without_a_pytest_command_fails(self, repo, capsys):
        _write_lane(repo, ["echo hi"])
        assert skips_guard.main(["--workflow", "lane.yml", "--job", "unit"]) == 1
        assert "runs no pytest command" in capsys.readouterr().err
