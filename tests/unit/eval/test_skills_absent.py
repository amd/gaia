# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``setup.skills_absent`` puts a scenario's skills into a known state.

Scenarios in one category share a GAIA home, so a skill one scenario (or an
earlier attempt of the same scenario) installed would leak into the next. The
runner removes the listed skills from the eval home before every attempt.
"""

import shutil
from pathlib import Path

import pytest

from gaia.eval import runner
from gaia.skills.lock import LockEntry, SkillLock

FIXTURE_SKILL = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "gaia"
    / "fixture_hub"
    / "sources"
    / "unit-convert"
)


def _install(home: Path, name: str = "unit-convert") -> Path:
    """Install *name* the way ``install_skill`` leaves it: a directory plus a lock entry."""
    root = home / "skills"
    target = root / name
    target.mkdir(parents=True)
    text = (FIXTURE_SKILL / "SKILL.md").read_text(encoding="utf-8")
    (target / "SKILL.md").write_text(text.replace("unit-convert", name), "utf-8")
    with SkillLock.locked(root) as lock:
        lock.record(LockEntry(name=name, version="1.0.0", path=str(target)))
        lock.save()
    return target


def _scenario(names, sid="skills_hub_search"):
    return {
        "id": sid,
        "category": "gaia_skills_lifecycle",
        "persona": "casual_user",
        "setup": {"index_documents": [], "skills_absent": names},
        "turns": [{"turn": 1, "objective": "X", "success_criteria": "Y"}],
    }


@pytest.fixture
def eval_home(tmp_path, monkeypatch):
    """A per-category eval home, with the user's real home moved out of reach."""
    fake_user = tmp_path / "user"
    fake_user.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_user))
    monkeypatch.setenv("HOME", str(fake_user))
    monkeypatch.setenv("USERPROFILE", str(fake_user))
    home = tmp_path / "eval-home"
    home.mkdir()
    monkeypatch.setenv("GAIA_HOME", str(home))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    return home


def test_a_leftover_install_is_removed_with_its_lock_entry(eval_home):
    target = _install(eval_home)

    assert runner._apply_skills_absent(_scenario(["unit-convert"])) is None

    assert not target.exists()
    assert "unit-convert" not in SkillLock.load(eval_home / "skills")


def test_only_the_listed_skills_are_removed(eval_home):
    _install(eval_home)
    other = _install(eval_home, "rss-digest")

    assert runner._apply_skills_absent(_scenario(["unit-convert"])) is None

    assert other.is_dir()
    assert "rss-digest" in SkillLock.load(eval_home / "skills")


def test_a_skill_that_is_not_installed_is_already_in_the_wanted_state(eval_home):
    assert runner._apply_skills_absent(_scenario(["unit-convert"])) is None
    (eval_home / "skills").mkdir()
    assert runner._apply_skills_absent(_scenario(["unit-convert"])) is None


def test_no_key_touches_nothing(eval_home):
    target = _install(eval_home)
    data = _scenario(None)
    del data["setup"]["skills_absent"]
    assert runner._apply_skills_absent(data) is None
    assert target.is_dir()


@pytest.mark.parametrize(
    "bad", ["../victim", "..", "/etc/passwd", "C:/Windows", "a/b", "", "UPPER"]
)
def test_a_name_that_is_not_a_bare_skill_name_is_refused(eval_home, bad):
    victim = eval_home / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("x", "utf-8")

    error = runner._apply_skills_absent(_scenario([bad]))

    assert error and "skills_absent" in error
    assert (victim / "keep.txt").is_file()
    with pytest.raises(ValueError, match="skills_absent"):
        runner.validate_scenario(Path("x.yaml"), _scenario([bad]))


@pytest.mark.parametrize("bad", ["unit-convert", {"unit-convert": True}, [1], [None]])
def test_a_value_that_is_not_a_list_of_names_is_refused_at_load(bad):
    with pytest.raises(ValueError, match="setup.skills_absent must be a list"):
        runner.validate_scenario(Path("x.yaml"), _scenario(bad))


def test_a_valid_list_passes_validation():
    runner.validate_scenario(Path("x.yaml"), _scenario(["unit-convert"]))


@pytest.mark.parametrize("pin", ["unset", "explicit"])
def test_the_real_home_is_never_touched(eval_home, monkeypatch, pin):
    real = Path.home() / ".gaia"
    target = _install(real)
    if pin == "unset":
        monkeypatch.delenv("GAIA_HOME")
    else:
        monkeypatch.setenv("GAIA_HOME", str(real))

    error = runner._apply_skills_absent(_scenario(["unit-convert"]))

    assert error and "real home" in error and "run_eval_lane.py" in error
    assert target.is_dir()


def test_a_skill_in_a_read_only_root_is_a_setup_error(eval_home, monkeypatch):
    claude_root = Path.home() / ".claude" / "skills"
    shutil.copytree(FIXTURE_SKILL, claude_root / "unit-convert")

    error = runner._apply_skills_absent(_scenario(["unit-convert"]))

    assert error and "could not remove 'unit-convert'" in error
    assert (claude_root / "unit-convert").is_dir()


def test_a_setup_error_skips_the_driver(eval_home, tmp_path, monkeypatch):
    monkeypatch.delenv("GAIA_HOME")
    launched = []
    monkeypatch.setattr(runner, "_run_driver", lambda *a, **k: launched.append(a))

    result = runner.run_scenario_subprocess(
        None,
        _scenario(["unit-convert"]),
        tmp_path,
        "http://127.0.0.1:1",
        "model",
        1.0,
        60,
    )

    assert result["status"] == "SETUP_ERROR"
    assert "real home" in result["error"]
    assert launched == []
