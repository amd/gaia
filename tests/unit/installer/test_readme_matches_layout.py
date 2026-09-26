# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``installer/README.md`` must describe the tree that actually exists (#4254).

The doc previously told readers to pipe ``install.sh`` into ``bash`` (the script
is POSIX ``sh`` and warns that bash-only syntax breaks under dash), called
``debian/``, ``macos/`` and ``linux/`` unpopulated ``.gitkeep`` placeholders
after they had shipped real packaging assets, and never mentioned
``installer/tui/`` at all.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "installer" / "README.md"
INSTALLER_DIR = REPO_ROOT / "installer"


def _text() -> str:
    return README.read_text(encoding="utf-8")


def test_does_not_tell_readers_to_pipe_install_sh_into_bash():
    text = _text()
    assert not re.search(r"install\.sh\s*\\?\s*\|\s*bash", text), (
        "installer/README.md still tells readers to pipe install.sh into bash; "
        "it is POSIX sh and warns that bash-only syntax breaks under dash"
    )
    assert "install.sh | sh" in text or "install.sh \\| sh" in text


def test_documents_every_populated_top_level_directory():
    """Every real subdirectory under installer/ gets a mention in the README."""
    text = _text()
    # Dotfiles don't count as populated: a directory holding only .gitkeep is a
    # placeholder, and requiring the README to document it inverts this check.
    subdirs = sorted(
        d.name
        for d in INSTALLER_DIR.iterdir()
        if d.is_dir() and any(p for p in d.iterdir() if not p.name.startswith("."))
    )
    # Trailing slash: a bare substring match would let "gaia-tui" (the binary
    # name) count as documenting the "tui" directory without ever naming it.
    undocumented = [d for d in subdirs if f"{d}/" not in text]
    assert not undocumented, (
        f"installer/README.md never mentions these populated directories: "
        f"{undocumented}"
    )


def test_does_not_call_populated_directories_gitkeep_placeholders():
    text = _text()
    assert ".gitkeep" not in text, (
        "installer/README.md still calls debian/macos/linux .gitkeep "
        "placeholders, but they now hold real packaging assets"
    )


def test_does_not_call_electron_forge_the_current_builder():
    """Phase C shipped: the Agent UI builds with electron-builder, not forge.

    The README described the migration as still pending ("today uses
    ``electron-forge``", "once that phase lands"), but ``forge.config.cjs`` is
    deleted, ``electron-builder`` is the devDependency, and the
    ``package:{win,mac,linux}`` scripts it called future work already exist.
    """
    webui = REPO_ROOT / "src" / "gaia" / "apps" / "webui"
    assert not (webui / "forge.config.cjs").exists(), (
        "forge.config.cjs is back — re-check this test's premise before "
        "editing installer/README.md"
    )
    assert (webui / "electron-builder.yml").exists()

    text = _text()
    assert "today uses `electron-forge`" not in text
    assert "Once that phase lands" not in text
    assert "electron-builder" in text, (
        "installer/README.md must name the builder the Agent UI actually uses"
    )


def test_documented_agent_ui_build_scripts_exist():
    """Every ``npm run <script>`` the README tells a reader to run must exist."""
    import json

    package_json = REPO_ROOT / "src" / "gaia" / "apps" / "webui" / "package.json"
    scripts = set(json.loads(package_json.read_text(encoding="utf-8"))["scripts"])

    documented = set(re.findall(r"npm run ([a-z][\w:-]*)", _text()))
    assert documented, "no npm scripts found in installer/README.md — regex rotted"

    missing = sorted(documented - scripts)
    assert not missing, (
        f"installer/README.md documents npm scripts that don't exist: {missing}"
    )
