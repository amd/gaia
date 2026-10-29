# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guards the amd-gaia dependency floor against the email agent's imports.

The email agent imports ``get_embedding_model_for_device`` from
``gaia.agents.registry``, first released in core v0.22.0. Its guided Outlook
walkthrough also imports ``gaia.connectors.setup_routes``, first released in
core v0.23.0. Its runtime imports ``gaia.agents.base.history.fit_pushed_history``,
first released in core v0.25.1. A published package must declare that latter
floor so a fresh resolver cannot install a core that fails at import time.
"""

from __future__ import annotations

import re
from pathlib import Path

EMAIL_ROOT = Path(__file__).resolve().parents[1]

# First core release covering the email agent module-level imports.
REQUIRED_FLOOR = (0, 25, 1)


def _floor_tuple(version: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in version.strip().split(".")[:3])
    return (parts + (0, 0, 0))[:3]


def test_agent_module_imports_and_registry_symbol_exists():
    """The exact import chain that crashed fresh installs in #2112."""
    import gaia_agent_email.agent  # noqa: F401

    from gaia.agents.registry import get_embedding_model_for_device

    assert callable(get_embedding_model_for_device)


def test_pyproject_floor_covers_email_runtime_imports():
    pyproject = (EMAIL_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'"amd-gaia\[api\]>=([0-9.]+)"', pyproject)
    assert match, "pyproject.toml must declare an amd-gaia[api]>=X.Y.Z floor"
    assert _floor_tuple(match.group(1)) >= REQUIRED_FLOOR, (
        f"amd-gaia floor {match.group(1)} predates "
        "the email agent runtime imports (covered by 0.25.1); a fresh resolver "
        "may select a core that ImportErrors when the email agent starts (#4430)"
    )


def test_email_query_routes_imports_newest_core_symbols():
    """The module-level query route imports are covered by the package floor."""
    import gaia_agent_email.query_routes  # noqa: F401
    from gaia.agents.base.history import fit_pushed_history

    assert callable(fit_pushed_history)


def test_setup_walkthrough_module_imports_and_setup_routes_symbol_exists():
    """The import chain covered by the published dependency floor."""
    import gaia_agent_email.tools.setup_walkthrough  # noqa: F401

    from gaia.connectors.setup_routes import ROUTES

    assert "microsoft" in ROUTES


def test_manifest_floors_match_pyproject():
    """gaia-agent.yaml repeats the floor twice; both must stay in lock-step."""
    manifest = (EMAIL_ROOT / "gaia-agent.yaml").read_text(encoding="utf-8")
    min_gaia = re.search(r'min_gaia_version:\s*"([0-9.]+)"', manifest)
    dep = re.search(r'"amd-gaia>=([0-9.]+)"', manifest)
    assert min_gaia, "gaia-agent.yaml must declare min_gaia_version"
    assert dep, "gaia-agent.yaml must declare an amd-gaia>=X.Y.Z dependency"

    pyproject = (EMAIL_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    pyproject_floor = re.search(r'"amd-gaia\[api\]>=([0-9.]+)"', pyproject)
    assert pyproject_floor

    assert min_gaia.group(1) == dep.group(1) == pyproject_floor.group(1), (
        "amd-gaia floor drift: pyproject.toml "
        f"({pyproject_floor.group(1)}) vs gaia-agent.yaml min_gaia_version "
        f"({min_gaia.group(1)}) / python dependency ({dep.group(1)})"
    )
