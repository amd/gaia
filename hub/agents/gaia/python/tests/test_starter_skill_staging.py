"""The flagship wheel build stages every repository starter skill."""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
sys.path.insert(0, str(PACKAGING))

import stage_starter_skills as staging  # noqa: E402


def test_staging_copies_all_starter_skills_and_restores_package_tree(
    tmp_path, monkeypatch
):
    package_skills = tmp_path / "skills"
    package_skills.mkdir()
    existing = next(iter(staging.starter_skill_names()))
    (package_skills / existing).mkdir()
    monkeypatch.setattr(staging, "PACKAGE_SKILLS", package_skills)

    before = {path.name for path in package_skills.iterdir() if path.is_dir()}

    with staging.staged_starter_skills() as copied:
        copied_names = {path.name for path in copied}
        assert copied_names == staging.starter_skill_names() - before
        assert staging.starter_skill_names() <= {
            path.name for path in package_skills.iterdir() if path.is_dir()
        }
        for name in staging.starter_skill_names():
            assert (package_skills / name / "SKILL.md").is_file() or name == existing

    after = {path.name for path in package_skills.iterdir() if path.is_dir()}
    assert after == before
