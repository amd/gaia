# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Formatting and style checks must reach hub packages, not just ``src/``.

A PR that only changed code under ``hub/`` got a green lint run that never
looked at it (#6679).
"""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_lint(monkeypatch):
    monkeypatch.chdir(REPO)
    spec = importlib.util.spec_from_file_location(
        "gaia_util_lint", REPO / "util/lint.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hub_python_dirs():
    return sorted(p.as_posix() for p in Path("hub/agents").glob("*/python")) + [
        "hub/skills"
    ]


def test_every_hub_python_package_is_formatted(monkeypatch):
    lint = _load_lint(monkeypatch)

    packages = _hub_python_dirs()

    assert packages[:-1], "no hub/agents/*/python packages found"
    assert set(packages) <= set(lint.HUB_DIRS)
    assert set(packages) <= set(lint.FORMAT_DIRS)


def test_flake8_checks_every_hub_python_package(monkeypatch):
    lint = _load_lint(monkeypatch)
    commands = []

    def fake_run_command(command, check=False):
        del check
        commands.append(command)
        return 0, ""

    monkeypatch.setattr(lint, "run_command", fake_run_command)

    result = lint.check_flake8()

    assert result.passed
    assert len(commands) == 2
    assert set(_hub_python_dirs()) <= set(commands[1])
    assert (
        f"--extend-ignore=E203,W503,E501,F541,W291,W293,E402,F841,E722,{lint.HUB_FLAKE8_IGNORES}"
        in commands[1]
    )


def test_the_core_is_still_formatted(monkeypatch):
    lint = _load_lint(monkeypatch)

    assert {"src/gaia", "tests"} <= set(lint.FORMAT_DIRS)
