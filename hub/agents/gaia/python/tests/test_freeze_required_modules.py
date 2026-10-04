# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Contract tests for the preflight in ``packaging/freeze.py``.

PyInstaller does not fail when a module the binary needs is absent from the
build environment -- it ships a binary that boots and then fails the feature on
the user's machine, where there is no interpreter to install it into. The
preflight is the only thing that turns that into a build failure, so it has to
cover every such module, and the release workflow has to install them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
_spec = importlib.util.spec_from_file_location("gaia_freeze", PACKAGING / "freeze.py")
assert _spec and _spec.loader
freeze = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freeze)

RELEASE_WORKFLOW = (
    Path(__file__).resolve().parents[5]
    / ".github"
    / "workflows"
    / "release_agent_gaia.yml"
)


def _freeze_install_step() -> str:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    start = text.index("- name: Install deps + PyInstaller (frozen-binary env)")
    end = text.index("- name:", start + 1)
    return text[start:end]


def test_claude_client_is_required():
    # The TUI forwards --use-claude to the binary; without anthropic every
    # Claude launch fails with an ImportError the user cannot act on.
    assert "anthropic" in freeze.REQUIRED_IMPORTS


@pytest.mark.parametrize("module", freeze.REQUIRED_IMPORTS)
def test_preflight_refuses_when_required_import_missing(module, monkeypatch):
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


@pytest.mark.parametrize("module", freeze.REQUIRED_IMPORTS)
def test_release_freeze_env_installs_required_import(module):
    step = _freeze_install_step()
    assert any(line.strip().split(" ")[0] == module for line in step.splitlines()), (
        f"{module} is in freeze.REQUIRED_IMPORTS but the release workflow's "
        "freeze step does not install it, so the release build would refuse to run."
    )
