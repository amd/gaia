# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The terminal hub can only publish against a core that serves the host API it needs.

The Go terminal hub reaches the daemon's control plane and the /v1/<agent>/* relay,
which require host API 1.1 (tui/internal/daemon/instance.go). A component manifest that
declares a `min_gaia_version` predating 1.1 promises a core its own binary cannot talk
to, and R2 paths are immutable so a bad publish cannot be withdrawn.

Nothing checked that relationship before, and the gap is invisible in development: an
editable install already serves 1.1, so only users on a released wheel hit it.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENTS_DIR = REPO_ROOT / "hub" / "components"

# The host API the terminal hub requires. Sourced from
# tui/internal/daemon/instance.go — "the control plane and the /v1/<agent>/* relay
# (needs v1.1+)". Mirrored here because the requirement is a Go-side property and this
# guard runs in Python; if the Go side raises its floor, raise this too.
REQUIRED_HOST_API = (1, 1)

# The newest core release known to ship a host API BELOW REQUIRED_HOST_API.
# v0.22.0's wheel ships DAEMON_API_VERSION = "1". A component may therefore not declare
# it as a minimum. Move this forward only when a release genuinely still served an
# older API than the components need.
LAST_CORE_RELEASE_BELOW_REQUIREMENT = (0, 22, 0)


def _parse(version: str) -> tuple:
    return tuple(
        int(p) for p in str(version).strip().strip('"').split(".") if p.isdigit()
    )


def _component_manifests():
    if not COMPONENTS_DIR.is_dir():
        pytest.skip(f"no components directory at {COMPONENTS_DIR}")
    found = sorted(COMPONENTS_DIR.glob("*/gaia-agent.yaml"))
    if not found:
        pytest.skip(f"no component manifests under {COMPONENTS_DIR}")
    return found


def test_repo_daemon_api_still_meets_the_requirement():
    """If the core lowers its host API, the components' contract silently breaks."""
    from gaia.daemon.constants import DAEMON_API_VERSION

    assert _parse(DAEMON_API_VERSION) >= REQUIRED_HOST_API, (
        f"the core now serves host API {DAEMON_API_VERSION}, below the "
        f"{'.'.join(map(str, REQUIRED_HOST_API))} the terminal hub requires "
        "(tui/internal/daemon/instance.go). Either restore the core's version or "
        "lower the terminal hub's requirement deliberately and update this guard."
    )


@pytest.mark.parametrize(
    "manifest_path", _component_manifests(), ids=lambda p: p.parent.name
)
def test_component_min_gaia_version_can_serve_the_host_api(manifest_path):
    """A component must not name a core release that cannot talk to its own binary."""
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    declared = manifest.get("min_gaia_version")

    assert declared, f"{manifest_path} declares no min_gaia_version"

    assert _parse(declared) > LAST_CORE_RELEASE_BELOW_REQUIREMENT, (
        f"{manifest_path.relative_to(REPO_ROOT)} declares min_gaia_version "
        f"{declared}, but core "
        f"{'.'.join(map(str, LAST_CORE_RELEASE_BELOW_REQUIREMENT))} ships a host API "
        f"below the {'.'.join(map(str, REQUIRED_HOST_API))} this component needs. "
        "Publishing it would ship a binary that cannot talk to the core it declares "
        "as its minimum, and R2 paths are immutable. Bump min_gaia_version to the "
        "release that first serves the required host API."
    )
