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
import os
import sys
import types
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


def test_build_bundles_the_tokenizer_plugin_and_data(tmp_path, monkeypatch):
    """tiktoken's plugin is a hidden import and its staged cache is bundled (#5066)."""
    captured = []
    pyinstaller = types.ModuleType("PyInstaller")
    pyinstaller_main = types.ModuleType("PyInstaller.__main__")
    pyinstaller_main.run = captured.extend
    pyinstaller.__main__ = pyinstaller_main
    monkeypatch.setitem(sys.modules, "PyInstaller", pyinstaller)
    monkeypatch.setitem(sys.modules, "PyInstaller.__main__", pyinstaller_main)
    monkeypatch.setattr(freeze, "HERE", tmp_path)
    monkeypatch.setattr(freeze, "_verify_collect_targets", lambda: None)

    def fake_stage(dest):
        dest.mkdir(parents=True)
        return dest

    monkeypatch.setattr(freeze.tiktoken_bundle, "stage_cache", fake_stage)

    freeze.build(onefile=True)

    tiktoken_bundle = freeze.tiktoken_bundle
    pairs = list(zip(captured, captured[1:]))
    assert ("--hidden-import", tiktoken_bundle.PLUGIN_MODULE) in pairs
    staged = tmp_path / "build" / tiktoken_bundle.BUNDLE_DIR
    assert (
        "--add-data",
        f"{staged}{os.pathsep}{tiktoken_bundle.BUNDLE_DIR}",
    ) in pairs
