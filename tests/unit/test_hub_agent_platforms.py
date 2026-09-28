# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A binary hub agent's manifest platforms must match the binaries it ships.

``requirements.platforms`` is the install gate (``gaia.hub.compatibility``);
``binaries.lock.json`` is what the release actually publishes. If the two drift,
users on a published platform are refused at install (#4218), or users on an
unpublished one pass the gate and fail at download.
"""

import json
from pathlib import Path

import pytest
import yaml

_HUB_AGENTS = Path(__file__).resolve().parents[2] / "hub" / "agents"


def _binary_agents():
    return sorted(
        p.parent.parent.name
        for p in _HUB_AGENTS.glob("*/npm/binaries.lock.json")
        if (p.parent.parent / "python" / "gaia-agent.yaml").is_file()
    )


def _lock_platforms(lock: dict) -> set:
    # schema 1.x: top-level "binaries"; schema 3.x: the agent's own "sidecar"
    # component (the bundled TUI component covers more platforms on purpose).
    if "binaries" in lock:
        return set(lock["binaries"])
    return set(lock["components"]["sidecar"]["platforms"])


def _to_lock_key(triple: str) -> str:
    # Manifests use hub triples (win-x64); lock files use Node keys (win32-x64).
    os_name, arch = triple.split("-", 1)
    return f"{'win32' if os_name == 'win' else os_name}-{arch}"


def test_binary_agents_are_discovered():
    assert {"gaia", "email"} <= set(_binary_agents())


@pytest.mark.parametrize("agent_id", _binary_agents())
def test_manifest_platforms_match_published_binaries(agent_id):
    agent_dir = _HUB_AGENTS / agent_id
    manifest = yaml.safe_load(
        (agent_dir / "python" / "gaia-agent.yaml").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (agent_dir / "npm" / "binaries.lock.json").read_text(encoding="utf-8")
    )

    declared = {_to_lock_key(t) for t in manifest["requirements"]["platforms"]}
    assert declared == _lock_platforms(lock)
