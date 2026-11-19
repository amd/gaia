# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a package manager's exit code means, in words a person can act on.

winget reports failure as a bare number (``-1978335138``), which neither the
model nor the user can read. The table is winget's own published list:
https://github.com/microsoft/winget-cli/blob/master/doc/windows/package-manager/winget/returnCodes.md
"""

from __future__ import annotations

import os
from typing import Optional

_WINGET = {
    0x8A150006: "The installer could not be started.",
    0x8A150008: "The download failed. Check the internet connection and try again.",
    0x8A150010: "This package has no installer for this PC.",
    0x8A150011: (
        "The downloaded installer did not match its published checksum, so it "
        "was not run. Try again later."
    ),
    0x8A150014: "No package matched that name or id.",
    0x8A150015: "winget has no package sources configured.",
    0x8A150016: (
        "More than one package matched. Name one exactly with --id and --exact."
    ),
    0x8A150019: "This needs administrator rights.",
    0x8A15001B: "The Microsoft Store is blocked by this PC's policy.",
    0x8A15002B: "It is already up to date.",
    0x8A15003A: "This PC's Group Policy blocks the operation.",
    0x8A150045: "winget could not open its package source.",
    0x8A150046: "The package source's terms were not accepted.",
    0x8A15005E: (
        "winget could not verify the Microsoft Store source's certificate, "
        "often because of a proxy or security software. Add '--source winget' "
        "to use the community source instead."
    ),
    0x8A150061: "It is already installed.",
    0x8A150101: "The program is running. Close it, then try again.",
    0x8A150102: "Another installation is in progress. Try again when it finishes.",
    0x8A150103: "A file it needs is in use. Close the program, then try again.",
    0x8A150105: "The disk is full.",
    0x8A150106: "There is not enough free memory to install it.",
    0x8A150107: "The installer needs an internet connection.",
    0x8A150109: "Restart the PC to finish the installation.",
    0x8A15010A: "The installation failed. Restart the PC, then try again.",
    0x8A15010C: "The installation was cancelled in the installer's own window.",
    0x8A15010D: "Another version is already installed.",
    0x8A15010E: "A newer version is already installed.",
    0x8A15010F: "This organisation's policy blocks the installation.",
    0x8A150113: "This package does not support this PC.",
}

_TABLES = {"winget": _WINGET}

#: Non-zero codes that say the work was already done.
_NOTHING_TO_DO = {"winget": frozenset({0x8A15002B, 0x8A150061})}


def _program_name(program: str) -> str:
    name = os.path.basename(program.replace("\\", "/")).lower()
    return name[:-4] if name.endswith(".exe") else name


def nothing_to_do(program: str, return_code: Optional[int]) -> bool:
    """True when *return_code* is non-zero but means there was nothing to do."""
    if not return_code:
        return False
    codes = _NOTHING_TO_DO.get(_program_name(program), frozenset())
    return return_code & 0xFFFFFFFF in codes


def explain(program: str, return_code: Optional[int]) -> Optional[str]:
    """What *return_code* from *program* means, or None when GAIA cannot say.

    *program* may be a path or carry ``.exe``. Windows hands the code back
    signed, so it is read as the unsigned 32-bit value the table uses.
    """
    if not return_code:
        return None
    return _TABLES.get(_program_name(program), {}).get(return_code & 0xFFFFFFFF)
