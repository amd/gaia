# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship wheel gate catches missing or misplaced starter skills.

``check_wheel_starter_skills.py`` is the last build gate before twine checks a
wheel. These tests pin both halves of that gate: which skills it expects from
the checkout, and which wheel paths count as a delivered skill.
"""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PACKAGE = TESTS.parent
PACKAGING = PACKAGE / "packaging"
ACTION = TESTS.parents[4] / ".github" / "actions" / "build-agent-wheel" / "action.yml"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, PACKAGING / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load("gaia_check_wheel_starter_skills", "check_wheel_starter_skills.py")


def _wheel(tmp_path: Path, names: list[str]) -> Path:
    wheel = tmp_path / "gaia_agent_gaia-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in names:
            archive.writestr(name, "")
    return wheel


def test_expected_skills_come_from_the_hub_pack():
    assert check.STARTER_SKILLS == PACKAGE.parents[2] / "skills"
    names = check.starter_skill_names()
    assert {"coding", "research-report"} <= names
    assert all((check.STARTER_SKILLS / name / "SKILL.md").is_file() for name in names)


def test_wheel_skill_names_reads_only_the_current_gaiapy_prefix(tmp_path):
    wheel = _wheel(
        tmp_path,
        [
            "gaiapy/skills/coding/SKILL.md",
            "gaiapy/skills/research-report/SKILL.md",
            "gaia_agent/skills/legacy/SKILL.md",
            "gaiapy/skills/no-manifest/README.md",
        ],
    )

    assert check.wheel_skill_names(wheel) == {"coding", "research-report"}


def test_a_wheel_with_every_checkout_skill_passes(tmp_path, capsys, monkeypatch):
    names = check.starter_skill_names()
    wheel = _wheel(
        tmp_path, [f"gaiapy/skills/{name}/SKILL.md" for name in sorted(names)]
    )
    monkeypatch.setattr(sys, "argv", ["check_wheel_starter_skills.py", str(wheel)])

    assert check.main() == 0
    assert (
        f"verified {len(names)} starter skills in gaiapy/skills/"
        in capsys.readouterr().out
    )


def test_a_wheel_missing_a_starter_skill_names_it(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "starter_skill_names", lambda: {"alpha", "beta"})
    wheel = _wheel(tmp_path, ["gaiapy/skills/alpha/SKILL.md"])
    monkeypatch.setattr(sys, "argv", ["check_wheel_starter_skills.py", str(wheel)])

    with pytest.raises(SystemExit) as caught:
        check.main()

    message = str(caught.value)
    assert "missing starter skills: beta" in message
    assert "alpha" not in message


def test_an_empty_wheel_cannot_pass(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(check, "starter_skill_names", lambda: {"alpha", "beta"})
    wheel = _wheel(tmp_path, [])
    monkeypatch.setattr(sys, "argv", ["check_wheel_starter_skills.py", str(wheel)])

    with pytest.raises(SystemExit) as caught:
        check.main()

    message = str(caught.value)
    assert "missing starter skills: alpha, beta" in message
    assert capsys.readouterr().out == ""


def test_an_old_package_path_cannot_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "starter_skill_names", lambda: {"alpha"})
    wheel = _wheel(
        tmp_path,
        [
            "gaia_agent/skills/alpha/SKILL.md",
            "gaiapy/skills/alpha/README.md",
        ],
    )
    monkeypatch.setattr(sys, "argv", ["check_wheel_starter_skills.py", str(wheel)])

    with pytest.raises(SystemExit) as caught:
        check.main()

    assert "missing starter skills: alpha" in str(caught.value)


def test_the_build_action_runs_the_gate_after_the_build():
    text = ACTION.read_text(encoding="utf-8")
    build_step = text.index("name: Build wheel + sdist")
    check_step = text.index('check_wheel_starter_skills.py"')
    twine_step = text.index("name: Check with twine")

    assert build_step < check_step < twine_step
    assert "if: inputs.dist == 'gaia-agent-gaia'" in text
