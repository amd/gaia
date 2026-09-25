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
    subdirs = sorted(
        d.name for d in INSTALLER_DIR.iterdir() if d.is_dir() and any(d.iterdir())
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
