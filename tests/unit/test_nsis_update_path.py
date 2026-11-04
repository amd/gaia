# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The desktop installer must leave the sign-in choice alone on update.

GAIA bundles its own Lemonade, so the installer never runs a Lemonade MSI. The
old version's uninstaller deletes the sign-in entry before ``customInstall``
runs, so whether to write it back is decided from what ``customInit`` reads.
"""

import re
from pathlib import Path

NSH = Path(__file__).parents[2] / "installer" / "nsis" / "installer.nsh"


def _macro(name: str) -> str:
    text = NSH.read_text(encoding="utf-8")
    found = re.search(rf"!macro {name}\n(.*?)!macroend", text, re.S)
    assert found, f"{name} is missing from {NSH}"
    return "\n".join(
        line
        for line in found.group(1).splitlines()
        if not line.lstrip().startswith(";")
    )


def test_the_sign_in_entry_is_read_before_the_old_uninstaller_removes_it():
    init = _macro("customInit")
    assert "ReadRegStr $GaiaRunValue HKCU" in init
    assert "ReadRegStr $GaiaRunLegacy HKCU" in init


def test_the_installer_never_runs_a_lemonade_msi():
    assert "msiexec" not in _macro("customInstall")


def test_the_sign_in_entry_is_written_only_if_it_was_there():
    install = _macro("customInstall")
    guard = install.rindex('${If} $GaiaRunValue != ""')
    assert guard < install.index('WriteRegStr HKCU "Software\\Microsoft')
