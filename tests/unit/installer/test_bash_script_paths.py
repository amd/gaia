# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression coverage for installer bash script repo-root resolution."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER_DIR = REPO_ROOT / "installer"
SCRIPTS_DIR = INSTALLER_DIR / "scripts"
WEBUI_DIR = REPO_ROOT / "src" / "gaia" / "apps" / "webui"
ELECTRON_DIR = REPO_ROOT / "src" / "gaia" / "electron"


def _script_assignment(script: Path, variable: str) -> str:
    pattern = re.compile(
        rf'^{variable}="\$\(cd "\$SCRIPT_DIR/(?P<traversal>[^"]+)" && pwd\)"$',
        re.MULTILINE,
    )
    match = pattern.search(script.read_text(encoding="utf-8"))
    assert match, f"{script} does not assign {variable} from SCRIPT_DIR"
    return match.group("traversal")


def _literal_assignment(script: Path, variable: str) -> str:
    pattern = re.compile(rf'^{variable}="(?P<value>[^"]+)"$', re.MULTILINE)
    match = pattern.search(script.read_text(encoding="utf-8"))
    assert match, f"{script} does not assign {variable}"
    return match.group("value")


def _resolved_from_scripts_dir(traversal: str) -> Path:
    return (SCRIPTS_DIR / traversal).resolve()


def test_old_one_level_bash_traversal_would_resolve_to_installer_dir():
    """The regression was using ../ from installer/scripts, not ../..."""
    assert _resolved_from_scripts_dir("..") == INSTALLER_DIR
    assert _resolved_from_scripts_dir("..") != REPO_ROOT


def test_start_agent_ui_resolves_project_root_and_webui_dir():
    script = SCRIPTS_DIR / "start-agent-ui.sh"

    project_root = _resolved_from_scripts_dir(
        _script_assignment(script, "PROJECT_ROOT")
    )
    webui_assignment = _literal_assignment(script, "WEBUI_DIR")

    assert project_root == REPO_ROOT
    assert webui_assignment == "$PROJECT_ROOT/src/gaia/apps/webui"
    assert project_root / "src" / "gaia" / "apps" / "webui" == WEBUI_DIR
    assert WEBUI_DIR.is_dir()


def test_build_ui_installer_resolves_repo_root_webui_and_electron_dirs():
    script = SCRIPTS_DIR / "build-ui-installer.sh"

    repo_root = _resolved_from_scripts_dir(_script_assignment(script, "REPO_ROOT"))
    webui_assignment = _literal_assignment(script, "WEBUI_DIR")
    electron_assignment = _literal_assignment(script, "ELECTRON_DIR")

    assert repo_root == REPO_ROOT
    assert webui_assignment == "$REPO_ROOT/src/gaia/apps/webui"
    assert electron_assignment == "$REPO_ROOT/src/gaia/electron"
    assert repo_root / "src" / "gaia" / "apps" / "webui" == WEBUI_DIR
    assert repo_root / "src" / "gaia" / "electron" == ELECTRON_DIR
    assert WEBUI_DIR.is_dir()
    assert ELECTRON_DIR.is_dir()
