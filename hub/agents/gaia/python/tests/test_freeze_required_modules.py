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
    / "build_flagship_sidecar.yml"
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


# ---------------------------------------------------------------------------
# run_python scripts (#5088): the binary is the only Python a release user has
# ---------------------------------------------------------------------------

#: Installed by the `rag` extra the freeze step already installs.
_FROM_RAG_EXTRA = {"openpyxl", "docx", "pptx", "reportlab", "pypdf", "numpy", "PIL"}


@pytest.mark.parametrize(
    "module", ["openpyxl", "docx", "pptx", "matplotlib", "pandas", "numpy"]
)
def test_office_and_data_libraries_are_bundled_for_scripts(module):
    assert module in freeze.SCRIPT_LIBRARIES
    assert module not in freeze.EXCLUDES


@pytest.mark.parametrize("module", freeze.SCRIPT_LIBRARIES)
def test_preflight_refuses_when_a_script_library_is_missing(module, monkeypatch):
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


@pytest.mark.parametrize(
    "module", sorted(set(freeze.SCRIPT_LIBRARIES) - _FROM_RAG_EXTRA)
)
def test_release_freeze_env_installs_script_library(module):
    step = _freeze_install_step()
    assert any(line.strip().split(" ")[0] == module for line in step.splitlines()), (
        f"{module} is in freeze.SCRIPT_LIBRARIES but the release workflow's "
        "freeze step does not install it, so the release build would refuse to run."
    )


@pytest.mark.parametrize(
    "name,wanted",
    [
        ("matplotlib.pyplot", True),
        ("matplotlib.backends.backend_agg", True),
        ("matplotlib.backends.backend_pdf", True),
        ("matplotlib.backends.backend_tkagg", False),
        ("matplotlib.backends.backend_qtagg", False),
        ("pandas.tests.frame", False),
        ("numpy.linalg.tests", False),
        ("pandas.testing", True),
        ("xml.etree.ElementTree", True),
    ],
)
def test_script_submodule_filter(name, wanted):
    assert freeze._wanted_submodule(name) is wanted


def test_stdlib_is_offered_without_gui_or_test_suite():
    stdlib = freeze._stdlib_top_level()
    for module in ("statistics", "sqlite3", "csv", "zipfile", "xml", "decimal"):
        assert module in stdlib
    for module in ("tkinter", "test", "idlelib", "turtle"):
        assert module not in stdlib
    assert "tkinter" in freeze.EXCLUDES


def test_smoke_test_uses_the_runner_flag():
    pytest.importorskip("gaia_agent_chat")
    from gaia_agent_chat.python_runner import RUN_PYTHON_FILE_FLAG

    spec = importlib.util.spec_from_file_location(
        "gaia_smoke", PACKAGING / "smoke_test.py"
    )
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    assert smoke.RUN_PYTHON_FILE_FLAG == RUN_PYTHON_FILE_FLAG
