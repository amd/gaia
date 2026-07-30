# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Shared target matrix for the Lemonade installer asset tests.

One table, two consumers — the hermetic shape tests
(``tests/unit/test_lemonade_download_urls.py``) and the live resolution test
(``tests/integration/test_lemonade_release_assets.py``) — so the pairs we
assert on can never drift apart.
"""

from unittest.mock import patch

from gaia.installer.lemonade_installer import LemonadeInstaller
from gaia.version import LEMONADE_VERSION

# (platform.system(), platform.machine(), minimal, expected asset filename)
SUPPORTED_TARGETS = [
    ("Windows", "AMD64", False, "lemonade.msi"),
    ("Windows", "AMD64", True, "lemonade-server-minimal.msi"),
    (
        "Linux",
        "x86_64",
        False,
        f"lemonade-server_{LEMONADE_VERSION}-debian13_amd64.deb",
    ),
    (
        "Linux",
        "aarch64",
        False,
        f"lemonade-server_{LEMONADE_VERSION}-debian13_arm64.deb",
    ),
    # Apple Silicon only — the .pkg payload is arm64, with no Intel build upstream.
    ("Darwin", "arm64", False, f"Lemonade-{LEMONADE_VERSION}-Darwin.pkg"),
]


def make_installer(
    system: str, machine: str, minimal: bool = False
) -> LemonadeInstaller:
    """Build an installer as if running on the given platform/architecture."""
    with (
        patch("platform.system", return_value=system),
        patch("platform.machine", return_value=machine),
    ):
        return LemonadeInstaller(target_version=LEMONADE_VERSION, minimal=minimal)
