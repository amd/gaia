# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The manual macOS uninstall steps must name what the pinned .pkg installs.

These identifiers are not derivable from anything in the repo — they come from
the upstream .pkg BOMs, and upstream renamed them com.lemonade.* ->
ai.lemonadeserver.* somewhere after 11.5.0. Nothing in the tree pinned them, so
the steps went stale silently: they told users to bootout a plist that no longer
exists and to forget receipts that match nothing. Pinning them here means the
next version bump has to look at the BOMs again instead of assuming.

Kept out of test_lemonade_macos_install.py deliberately — that file patches
os.geteuid, which does not exist on Windows, so every test in it errors there.
These assertions are pure string checks and run on every platform.
"""

import pytest

from tests.fixtures.lemonade_assets import make_installer


@pytest.fixture
def steps():
    result = make_installer("Darwin", "arm64")._uninstall_macos()
    assert result.success is False, "manual-only uninstall must not claim success"
    return result.error


@pytest.mark.parametrize(
    "path",
    [
        "/Library/LaunchDaemons/ai.lemonadeserver.server.plist",
        "/Library/LaunchAgents/ai.lemonadeserver.tray.plist",
        "/Applications/lemonade-app.app",
        "/usr/local/bin/lemond",
        "/usr/local/bin/lemonade",
        "/usr/local/bin/lemonade-tray",
    ],
)
def test_names_every_path_the_pkg_installs(steps, path):
    assert path in steps


def test_forgets_receipts_under_the_current_prefix(steps):
    # Receipts are ai.lemonadeserver.server.{core,dev,Runtime,Resources,
    # Applications,Unspecified}; the grep anchors on their shared prefix.
    assert "grep '^ai.lemonadeserver.server'" in steps
    assert "pkgutil --forget" in steps


def test_no_stale_pre_11_8_identifiers_survive(steps):
    assert "com.lemonade" not in steps
