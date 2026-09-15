# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Guard: every eval scenario's ``agent_type`` names an agent that exists.

A scenario pointed at a removed agent never reaches the model — every turn
gets the backend's "couldn't load the agent" reply and the scenario reports
INFRA_ERROR forever (#3883). This check needs no Lemonade, so the drift is
caught in CI instead of in an eval run.
"""

import re
from pathlib import Path

import pytest
import yaml

from gaia.agents.registry import AgentRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_DIR = REPO_ROOT / "eval" / "scenarios"
HUB_AGENTS_DIR = REPO_ROOT / "hub" / "agents"

_ENTRY_POINT_SECTION = re.compile(
    r'^\[project\.entry-points\."gaia\.agent"\]\s*$(?P<body>.*?)(?=^\[|\Z)',
    re.MULTILINE | re.DOTALL,
)
_ENTRY_POINT_ID = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=", re.MULTILINE)


def _hub_agent_ids() -> set[str]:
    """Agent ids the hub wheels register via their ``gaia.agent`` entry points.

    Read from source rather than ``importlib.metadata`` so the guard doesn't
    depend on which hub wheels happen to be installed in the test env.
    """
    ids: set[str] = set()
    for pyproject in HUB_AGENTS_DIR.glob("*/python/pyproject.toml"):
        match = _ENTRY_POINT_SECTION.search(pyproject.read_text(encoding="utf-8"))
        if match:
            ids.update(_ENTRY_POINT_ID.findall(match.group("body")))
    return ids


def _scenario_agent_types() -> list[tuple[str, str]]:
    pairs = []
    for path in sorted(SCENARIO_DIR.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "agent_type" in data:
            pairs.append((str(path.relative_to(REPO_ROOT)), data["agent_type"]))
    return pairs


def test_scenarios_declare_agent_types():
    assert _scenario_agent_types(), f"no scenario declares agent_type in {SCENARIO_DIR}"


def test_hub_entry_points_parsed():
    # The chat wheel's three profile ids and the flagship must be visible, or
    # the guard below would pass vacuously on a parsing regression.
    assert {"chat", "doc", "file", "gaia"} <= _hub_agent_ids()


@pytest.mark.parametrize(
    "scenario,agent_type",
    _scenario_agent_types(),
    ids=lambda v: v if "/" in str(v) else None,
)
def test_scenario_agent_type_resolves(scenario, agent_type):
    registry = AgentRegistry()
    registry._register_builtin_agents()
    known = {reg.id for reg in registry.list()} | _hub_agent_ids()

    assert registry.canonical_id(agent_type) in known, (
        f"{scenario} sets agent_type '{agent_type}', which no registered agent "
        f"or legacy alias resolves to. Valid ids: {', '.join(sorted(known))}. "
        "Point the scenario at one of these (see src/gaia/agents/registry.py)."
    )
