# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Formatting checks must reach the hub packages, not just ``src/`` and ``tests/``.

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


def test_every_hub_python_package_is_formatted(monkeypatch):
    lint = _load_lint(monkeypatch)

    packages = sorted(p.as_posix() for p in Path("hub/agents").glob("*/python"))

    assert packages, "no hub/agents/*/python packages found"
    for package in packages + ["hub/skills"]:
        assert package in lint.FORMAT_DIRS


def test_the_core_is_still_formatted(monkeypatch):
    lint = _load_lint(monkeypatch)

    assert {"src/gaia", "tests"} <= set(lint.FORMAT_DIRS)
