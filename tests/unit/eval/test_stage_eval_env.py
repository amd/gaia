# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The staging both eval workflows use before running gaia_* categories."""

import importlib.util
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

    # The git workspaces are built into the staged copy, never the checkout.
    assert (staged / "tiers_resilience" / "journal" / ".git").is_dir()
    assert any((staged / "tiers_git_code").glob("*/.git"))


def test_restaging_replaces_built_git_workspaces(tmp_path):
    # git writes objects read-only; a second staging must still clear them.
    stage_eval_env.stage(tmp_path)
    stage_eval_env.stage(tmp_path)
    assert (tmp_path / "gaia-eval" / "tiers_resilience" / "journal" / ".git").is_dir()
