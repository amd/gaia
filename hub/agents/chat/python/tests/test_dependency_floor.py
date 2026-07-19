# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guards the amd-gaia dependency floor against the chat agent's real core needs.

The chat agent imports ``get_embedding_model_for_device`` from
``gaia.agents.registry`` at module load (``agent.py:32``); that symbol first
shipped in core v0.22.0. With a lower floor a fresh resolver may legally select
an older core and the agent dies at startup with ImportError (#2112). The email
agent got the same floor bump and a guard test; this mirrors it so the chat
agent's floor can't silently regress and re-introduce #2112 with green CI.
"""

from __future__ import annotations

import re
from pathlib import Path

CHAT_ROOT = Path(__file__).resolve().parents[1]

# First core release shipping gaia.agents.registry.get_embedding_model_for_device.
REQUIRED_FLOOR = (0, 22, 0)


def _floor_tuple(version: str) -> tuple[int, ...]:
    # Pad shortened versions ("0.22" -> (0, 22, 0)) so a two-part floor doesn't
    # compare short and fail spuriously against a three-part requirement.
    parts = tuple(int(p) for p in version.strip().split(".")[:3])
    return (parts + (0, 0, 0))[:3]


def test_agent_module_imports_and_registry_symbol_exists():
    """The exact import chain that crashed fresh installs in #2112."""
    import gaia_agent_chat.agent  # noqa: F401
    from gaia.agents.registry import get_embedding_model_for_device

    assert callable(get_embedding_model_for_device)


def test_pyproject_floor_covers_registry_symbol():
    pyproject = (CHAT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'"amd-gaia>=([0-9.]+)"', pyproject)
    assert match, "pyproject.toml must declare an amd-gaia>=X.Y.Z floor"
    assert _floor_tuple(match.group(1)) >= REQUIRED_FLOOR, (
        f"amd-gaia floor {match.group(1)} predates "
        "gaia.agents.registry.get_embedding_model_for_device (first shipped in "
        "0.22.0); a fresh resolver may select a core that ImportErrors at "
        "agent start (#2112)"
    )


def test_manifest_floors_match_pyproject():
    """gaia-agent.yaml repeats the floor twice; both must stay in lock-step."""
    manifest = (CHAT_ROOT / "gaia-agent.yaml").read_text(encoding="utf-8")
    min_gaia = re.search(r'min_gaia_version:\s*"([0-9.]+)"', manifest)
    dep = re.search(r'"amd-gaia>=([0-9.]+)"', manifest)
    assert min_gaia, "gaia-agent.yaml must declare min_gaia_version"
    assert dep, "gaia-agent.yaml must declare an amd-gaia>=X.Y.Z dependency"

    pyproject = (CHAT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    pyproject_floor = re.search(r'"amd-gaia>=([0-9.]+)"', pyproject)
    assert pyproject_floor

    assert min_gaia.group(1) == dep.group(1) == pyproject_floor.group(1), (
        "amd-gaia floor drift: pyproject.toml "
        f"({pyproject_floor.group(1)}) vs gaia-agent.yaml min_gaia_version "
        f"({min_gaia.group(1)}) / python dependency ({dep.group(1)})"
    )
