# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The desktop installer must leave Lemonade and the sign-in choice alone on update.

The bundled MSI force-stops every lemond on the PC, and the old version's
uninstaller deletes the sign-in entry before ``customInstall`` runs, so both are
decided from what ``customInit`` reads first.
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


def test_prior_install_is_read_from_both_hives_before_anything_is_removed():
    init = _macro("customInit")
    assert 'ReadRegStr $gaiaPriorInstall HKCU "${UNINSTALL_REGISTRY_KEY}"' in init
    assert 'ReadRegStr $gaiaPriorInstall HKLM "${UNINSTALL_REGISTRY_KEY}"' in init
    assert "ReadRegStr $gaiaAutostart HKCU" in init


def test_the_msi_runs_only_on_a_fresh_install():
    install = _macro("customInstall")
    guard = install.rindex('${If} $gaiaPriorInstall == ""')
    assert guard < install.index("msiexec")
    assert "${OrIf}" not in install[guard:]


def test_an_update_writes_the_sign_in_entry_only_if_it_was_there():
    install = _macro("customInstall")
    guard = install.index('${OrIf} $gaiaAutostart != ""')
    assert guard < install.index('WriteRegStr HKCU "Software\\Microsoft')
