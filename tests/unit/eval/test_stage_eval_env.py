# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The staging both eval workflows use before running gaia_* categories."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "stage_eval_env", REPO_ROOT / "tests" / "fixtures" / "gaia" / "stage_eval_env.py"
)
stage_eval_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stage_eval_env)


def test_home_is_required():
    with pytest.raises(SystemExit):
        stage_eval_env.main([])


def test_missing_home_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="not a directory"):
        stage_eval_env.stage(tmp_path / "nope")


def test_stages_fixtures_skills_and_a_trusted_fixture_hub(tmp_path):
    stale = tmp_path / "gaia-eval" / "stale.txt"
    stale.parent.mkdir()
    stale.write_text("left from an earlier run")

    skills_root = stage_eval_env.stage(tmp_path)

    staged = tmp_path / "gaia-eval"
    assert (staged / "csv" / "sales.csv").is_file()
    assert (staged / "mini_repo").is_dir()
    assert not stale.exists(), "an earlier staging must be replaced, not merged"

    assert skills_root == tmp_path / ".gaia" / "skills"
    hub_skills = {
        p.name for p in (REPO_ROOT / "hub" / "skills").iterdir() if p.is_dir()
    }
    installed = {p.name for p in skills_root.iterdir() if p.is_dir()}
    assert hub_skills - stage_eval_env.NOT_PRE_INSTALLED <= installed
    assert not stage_eval_env.NOT_PRE_INSTALLED & installed
    assert (skills_root / "trusted-keys.json").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="drives the POSIX gh shim")
def test_staged_fake_gh_answers_ahead_of_any_real_gh(tmp_path):
    stage_eval_env.stage(tmp_path)
    bin_dir = tmp_path / ".gaia-eval-bin"
    path = os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")])

    assert shutil.which("gh", path=path) == str(bin_dir / "gh")
    proc = subprocess.run(
        ["gh", "--version"],
        env={**os.environ, "PATH": path},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "gaia eval fixture" in proc.stdout


def test_on_windows_the_fake_gh_gets_an_exe_launcher(tmp_path, monkeypatch):
    """CreateProcess resolves only gh.exe, so a .cmd shim alone loses to real gh."""
    from gaia.eval.bench import ghstub

    built = []

    def fake_launcher(python, script):
        built.append(Path(script))
        return b"MZ-launcher"

    monkeypatch.setattr(ghstub, "_exe_launcher", fake_launcher)
    monkeypatch.setattr(stage_eval_env.sys, "platform", "win32")
    bin_dir = stage_eval_env.install_fake_gh(tmp_path)

    assert bin_dir == tmp_path / ".gaia-eval-bin"
    assert (bin_dir / "gh.exe").read_bytes() == b"MZ-launcher"
    assert built == [stage_eval_env.HERE / "fake_gh" / "gh.py"]


def test_restaging_clears_read_only_files(tmp_path):
    stage_eval_env.stage(tmp_path)
    locked = tmp_path / "gaia-eval" / "locked.txt"
    locked.write_text("x")
    locked.chmod(0o444)
    stage_eval_env.stage(tmp_path)
    assert not locked.exists()
    assert (tmp_path / "gaia-eval" / "csv" / "sales.csv").is_file()
