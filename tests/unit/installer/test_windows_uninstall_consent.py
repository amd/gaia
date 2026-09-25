# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Both NSIS uninstallers must disclose shared-data deletion and default to No."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "relative",
    [
        "installer/nsis/installer.nsh",
        "installer/tui/nsis/gaia-setup.nsi",
    ],
)
def test_recursive_user_data_deletion_has_complete_default_no_consent(relative):
    source = (ROOT / relative).read_text(encoding="utf-8")
    match = re.search(
        r"MessageBox MB_YESNO\|MB_ICONQUESTION\|MB_DEFBUTTON2\s+\\\s*"
        r'"(?P<prompt>Delete ALL files[^"\n]+)"\s+\\\s*'
        r'/SD IDNO IDNO \+2\s+RMDir /r "\$PROFILE\\\.gaia"',
        source,
        re.IGNORECASE,
    )
    assert (
        match
    ), "Recursive shared-data deletion requires explicit consent, default No, and a skip on No"
    prompt = match["prompt"]
    for detail in (
        "$PROFILE\\.gaia",
        "chats",
        "documents",
        "custom agents",
        "skills",
        "connector sign-ins and permissions",
        "memory",
        "MCP server settings",
        "config",
        "logs",
        "shared Python and terminal runtimes",
        "Other GAIA installations",
        "cannot be undone",
        "Choose No",
    ):
        assert detail in prompt
