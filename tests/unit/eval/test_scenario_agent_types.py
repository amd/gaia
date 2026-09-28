# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Guard: one eval, one agent — no scenario pins its own ``agent_type``.

Every scenario scores the flagship, so a scorecard names a single agent and two
scorecards are comparable. A per-scenario pin breaks both halves of that: it
silently overrode ``--agent-type`` (so the flag did nothing), and it let one run
mix agents, which ``compare_scorecards`` cannot see because it keys on
``scenario_id`` alone and would report "improved/regressed" across two different
agents.

Enforcement is ``validate_scenario``, which rejects the key wherever a scenario
comes from — the repo, ``~/.gaia/eval/scenarios``, or a ``--scenario-dir``. This
file only walks the repo, so it names in-tree offenders early; it is not what
makes the rule hold.

The original hazard this file guarded still applies to the one remaining pin —
the default: a scenario pointed at an agent that does not exist never reaches
the model, every turn gets the backend's "couldn't load the agent" reply, and
the scenario reports INFRA_ERROR forever (#3883). So the default is checked
against the registry here too. Neither check needs Lemonade, so the drift is
caught in CI rather than in an eval run.
"""

import re
from pathlib import Path

import pytest
import yaml

from gaia.agents.registry import AgentRegistry
from gaia.eval.config import DEFAULT_AGENT_TYPE
from gaia.eval.runner import validate_scenario
from gaia.ui._chat_helpers import _SIDECAR_AGENT_TYPES

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


def _builtin_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry._register_builtin_agents()
    return registry


def _known_agent_ids(registry: AgentRegistry) -> set[str]:
    """The ids ``POST /api/sessions`` accepts, sidecars included.

    A sidecar (``email``) ships no ``gaia.agent`` entry point, yet the endpoint
    accepts it, so the guard must too or the two disagree on the same rule.
    """
    return {reg.id for reg in registry.list()} | _hub_agent_ids() | _SIDECAR_AGENT_TYPES


def _scenario_paths() -> list[Path]:
    return sorted(SCENARIO_DIR.rglob("*.yaml"))


def test_scenarios_exist():
    # Without this the pin check below would pass vacuously on a bad glob or a
    # moved scenario directory.
    assert _scenario_paths(), f"no scenarios found under {SCENARIO_DIR}"


def test_hub_entry_points_parsed():
    # The flagship must be visible, or the default check below would pass
    # vacuously on a pyproject-parsing regression.
    assert {"chat", "gaia"} <= _hub_agent_ids()


def test_default_agent_type_resolves():
    registry = _builtin_registry()
    known = _known_agent_ids(registry)

    assert registry.canonical_id(DEFAULT_AGENT_TYPE) in known, (
        f"the eval default agent_type '{DEFAULT_AGENT_TYPE}' resolves to no "
        f"registered agent, so every scenario would report INFRA_ERROR. "
        f"Valid ids: {', '.join(sorted(known))}. Fix DEFAULT_AGENT_TYPE in "
        "src/gaia/eval/config.py or register the agent "
        "(src/gaia/agents/registry.py)."
    )


def test_no_scenario_pins_an_agent_type():
    pinned = []
    for path in _scenario_paths():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "agent_type" in data:
            pinned.append(f"{path.relative_to(REPO_ROOT)} -> {data['agent_type']}")

    assert not pinned, (
        f"{len(pinned)} scenario(s) set 'agent_type', which the eval no longer "
        f"honours — the runner passes --agent-type (default "
        f"'{DEFAULT_AGENT_TYPE}') for every scenario. validate_scenario rejects "
        "the key, so these fail at load. Delete the line; to measure a "
        "different agent pass --agent-type on the command line for the whole "
        "run. Offenders: " + "; ".join(pinned)
    )


def test_validate_scenario_rejects_a_pin_from_any_directory():
    """The guard above only walks the repo; the runner also loads
    ``~/.gaia/eval/scenarios`` and every ``--scenario-dir``. Rejecting at load
    is what covers those, so a local scenario cannot quietly score one agent
    while the scorecard records another.
    """
    scenario = {
        "id": "pinned",
        "category": "rag_quality",
        "persona": "casual_user",
        "agent_type": "chat",
        "setup": {"index_documents": []},
        "turns": [
            {
                "turn": 1,
                "objective": "greet",
                "user_message": "hi",
                "success_criteria": "the agent says hello",
            }
        ],
    }
    with pytest.raises(ValueError, match="agent_type"):
        validate_scenario(Path("anywhere/pinned.yaml"), scenario)

    del scenario["agent_type"]
    validate_scenario(Path("anywhere/pinned.yaml"), scenario)  # should not raise
