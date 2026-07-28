# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guards the amd-gaia dependency floor against the agent's real core needs.

The email agent imports ``get_embedding_model_for_device`` from
``gaia.agents.registry`` at module load; that symbol first shipped in core
v0.22.0. With a lower floor a fresh resolver may legally select an older
core and the agent dies at startup with ImportError (#2112). These tests
pin the floor to the symbol so the two can't silently drift apart again:
if the floor is lowered, or the agent grows an import the declared floor
doesn't cover, one of these fails.

The guided Outlook walkthrough (#2590) adds a second such import:
``gaia_agent_email.tools.setup_walkthrough`` imports ``gaia.connectors.
setup_routes``, a brand-new core module that ships in the NEXT core
release, not the current 0.22.0. **The pyproject floor cannot be bumped to
express that yet** — a dependency declaration can only name a released
version, and bumping it to an unreleased one makes the package
uninstallable everywhere:

    $ uv pip install --dry-run -e . -e hub/agents/email/python
    × No solution found when resolving dependencies:
    ╰─▶ Because only amd-gaia<0.23.0 is available and
        gaia-agent-email==0.5.0 depends on amd-gaia[api]>=0.23.0, we can
        conclude that gaia-agent-email==0.5.0 cannot be used. And because
        only gaia-agent-email==0.5.0 is available and you require
        gaia-agent-email, we can conclude that your requirements are
        unsatisfiable.

So the floor here stays at 0.22.0 (bump it once the release containing
``setup_routes`` is cut) and this file only asserts the module is
importable against whatever core IS resolved locally — it does not, and
cannot, guard the published-sidecar-vs-old-core case. That gate belongs at
publish time instead: see the core-version check in
``.github/workflows/release_agent_email.yml``, which refuses to publish a
build whose resolved core lacks ``gaia.connectors.setup_routes``.
"""

from __future__ import annotations

import re
from pathlib import Path

EMAIL_ROOT = Path(__file__).resolve().parents[1]

# First core release shipping gaia.agents.registry.get_embedding_model_for_device
# (introduced by commit 89db99d6, first tagged in v0.22.0).
REQUIRED_FLOOR = (0, 22, 0)


def _floor_tuple(version: str) -> tuple[int, ...]:
    parts = tuple(int(p) for p in version.strip().split(".")[:3])
    return (parts + (0, 0, 0))[:3]


def test_agent_module_imports_and_registry_symbol_exists():
    """The exact import chain that crashed fresh installs in #2112."""
    import gaia_agent_email.agent  # noqa: F401
    from gaia.agents.registry import get_embedding_model_for_device

    assert callable(get_embedding_model_for_device)


def test_pyproject_floor_covers_registry_symbol():
    pyproject = (EMAIL_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'"amd-gaia\[api\]>=([0-9.]+)"', pyproject)
    assert match, "pyproject.toml must declare an amd-gaia[api]>=X.Y.Z floor"
    assert _floor_tuple(match.group(1)) >= REQUIRED_FLOOR, (
        f"amd-gaia floor {match.group(1)} predates "
        "gaia.agents.registry.get_embedding_model_for_device (first shipped in "
        "0.22.0); a fresh resolver may select a core that ImportErrors at "
        "agent start (#2112)"
    )


def test_setup_walkthrough_module_imports_and_setup_routes_symbol_exists():
    """The import chain that would burn like #2112 IF the pyproject floor
    could express it. It can't (see module docstring) — this only proves
    the module resolves against whatever core is installed locally; the
    real cross-version guard is the publish-time gate, not this test."""
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

    assert (
        min_gaia.group(1) == dep.group(1) == pyproject_floor.group(1)
    ), (
        "amd-gaia floor drift: pyproject.toml "
        f"({pyproject_floor.group(1)}) vs gaia-agent.yaml min_gaia_version "
        f"({min_gaia.group(1)}) / python dependency ({dep.group(1)})"
    )
