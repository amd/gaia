# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Contract tests for the preflight in ``packaging/freeze.py``.

PyInstaller only warns when a ``--collect-all`` target is not installed, so the
email binary shipped without faiss and booted with semantic memory recall off.
The preflight makes that a build failure; these tests keep it and the release
workflow's freeze env in step.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
_spec = importlib.util.spec_from_file_location("email_freeze", PACKAGING / "freeze.py")
assert _spec and _spec.loader
freeze = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freeze)

RELEASE_WORKFLOW = (
    Path(__file__).resolve().parents[5]
    / ".github"
    / "workflows"
    / "release_agent_email.yml"
)

#: import name -> the distribution the freeze env has to install for it.
DISTRIBUTIONS = {"faiss": "faiss-cpu"}


def _freeze_install_step() -> str:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    start = text.index("- name: Install deps + PyInstaller (frozen-binary env)")
    end = text.index("- name:", start + 1)
    return text[start:end]


def test_memory_index_is_collected():
    assert "faiss" in freeze.COLLECT_ALL


@pytest.mark.parametrize("module", freeze.COLLECT_ALL)
def test_preflight_refuses_when_collect_target_missing(module, monkeypatch):
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **kw: (
            None if name == module else real_find_spec(name, *a, **kw)
        ),
    )
    with pytest.raises(SystemExit) as excinfo:
        freeze._verify_collect_targets()
    assert module in str(excinfo.value)


@pytest.mark.parametrize("module", freeze.COLLECT_ALL)
def test_release_freeze_env_installs_collect_target(module):
    distribution = DISTRIBUTIONS.get(module, module)
    install_lines = [
        line
        for line in _freeze_install_step().splitlines()
        if line.strip().startswith("uv pip install")
    ]
    assert any(distribution in line for line in install_lines), (
        f"{module} is collected by freeze.py but the release workflow's freeze "
        f"step does not install {distribution}, so the release build would refuse "
        "to run."
    )
