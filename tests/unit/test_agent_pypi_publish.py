# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guards for the agent-wheel PyPI publish path (issue #1179).

These tests do *not* touch the network or PyPI. They assert the static
invariants that make dual distribution (R2 + PyPI) correct and drift-proof:

* the production-agent list (``setup.py[agents]``) maps cleanly to packages
  under ``hub/agents/python/<id>/`` (via ``util/list_agent_packages.py``);
* every such wheel declares the ``gaia-agent-<id>`` name and an ``amd-gaia``
  framework dependency (issue #1179 scope item 3);
* the publish workflow derives its matrix from that same list, so a new agent
  added to the extra is published automatically with no second list to sync.

The live dual-publish logic is covered by ``test_hub_publisher.py``; the CLI
wiring by ``test_cli_agent.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UTIL_DIR = REPO_ROOT / "util"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish_agents.yml"

# Infrastructure agents publish as wheels but are loaded by class-path from the
# API server, not discovered via the gaia.agent registry entry point (#1102).
INFRA_ONLY_AGENT_IDS = {"routing"}

if str(UTIL_DIR) not in sys.path:
    sys.path.insert(0, str(UTIL_DIR))

import list_agent_packages as lap  # noqa: E402  (path set above)


@pytest.fixture(scope="module")
def packages():
    return lap.list_agent_packages()


def test_production_agent_list_nonempty(packages):
    """setup.py[agents] resolves to at least the migrated agents."""
    assert packages, "no production agent packages derived from setup.py[agents]"
    ids = {p.agent_id for p in packages}
    # Spot-check a couple that have already migrated; the helper enforces the
    # full set exists on disk, so this just sanity-checks the mapping direction.
    assert {"summarize", "analyst", "browser"} <= ids


def test_dist_name_and_directory_convention(packages):
    """Each entry follows gaia-agent-<id> and lives at hub/agents/python/<id>."""
    for p in packages:
        assert p.dist_name == f"gaia-agent-{p.agent_id}"
        assert p.path == lap.PYTHON_AGENTS_DIR / p.agent_id
        assert (p.path / "pyproject.toml").exists()


def test_every_wheel_declares_amd_gaia_dependency(packages):
    """Issue #1179 scope 3: each wheel depends on amd-gaia>={min_gaia_version}.

    An optional ``[extras]`` segment is allowed (e.g. ``amd-gaia[api]>=`` — the
    email wheel pulls the [api] extra so consumers auto-get the REST-server deps
    + keyring; see #1617).
    """
    # amd-gaia, an optional [extras] group, then a >= floor.
    pat = re.compile(r"amd-gaia(\[[^\]]*\])?>=")
    for p in packages:
        pyproject = (p.path / "pyproject.toml").read_text(encoding="utf-8")
        assert pat.search(
            pyproject
        ), f"{p.dist_name}: pyproject.toml is missing an 'amd-gaia>=' dependency"


def test_pyproject_name_matches_dist(packages):
    """The wheel's [project].name equals the published distribution name."""
    for p in packages:
        pyproject = (p.path / "pyproject.toml").read_text(encoding="utf-8")
        assert (
            f'name = "{p.dist_name}"' in pyproject
        ), f"{p.path}/pyproject.toml [project].name != {p.dist_name}"


def test_pyproject_declares_gaia_agent_entry_point(packages):
    """Both install paths (R2 and pip) discover the agent via gaia.agent.

    Infrastructure agents (e.g. routing) are exempt — they are resolved by
    class-path from the API server, not via the registry entry point (#1102).
    """
    for p in packages:
        if p.agent_id in INFRA_ONLY_AGENT_IDS:
            continue
        pyproject = (p.path / "pyproject.toml").read_text(encoding="utf-8")
        assert (
            'entry-points."gaia.agent"' in pyproject
        ), f'{p.dist_name}: missing [project.entry-points."gaia.agent"]'


def test_publish_workflow_exists_and_uses_pypi_action():
    """The CI workflow publishes via gh-action-pypi-publish using OIDC."""
    assert WORKFLOW.exists(), "publish_agents.yml workflow is missing"
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pypa/gh-action-pypi-publish" in text
    # OIDC trusted publishing — no stored token (#1570). The action mints a
    # short-lived id-token PyPI exchanges for an upload token.
    assert "id-token: write" in text
    # PyPI-native immutability rather than custom overwrite logic (#1179).
    assert "skip-existing: true" in text
    # Matrix is generated from the helper, not a hand-maintained second list.
    assert "list_agent_packages.py --format matrix" in text


def test_publish_workflow_only_publishes_on_tags():
    """Publishing is gated on a v* tag; the build job runs on every push/PR."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "startsWith(github.ref, 'refs/tags/v')" in text


def test_helper_matrix_format_matches_packages(packages):
    """--format matrix emits exactly the resolved package set as GHA include[]."""
    import json
    import subprocess  # nosec B404 — fixed argv, no shell

    out = subprocess.run(
        [
            sys.executable,
            str(UTIL_DIR / "list_agent_packages.py"),
            "--format",
            "matrix",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    matrix = json.loads(out.stdout)
    assert "include" in matrix
    assert [e["dist"] for e in matrix["include"]] == [p.dist_name for p in packages]
    assert [e["id"] for e in matrix["include"]] == [p.agent_id for p in packages]


def test_helper_rejects_missing_package(tmp_path):
    """A dist in the extra with no on-disk package fails loudly (no silent skip)."""
    fake_setup = tmp_path / "setup.py"
    fake_setup.write_text(
        'setup(\n    extras_require={"agents": ["gaia-agent-doesnotexist"]},\n)\n',
        encoding="utf-8",
    )
    with pytest.raises(lap.AgentListError, match="no package at"):
        lap.list_agent_packages(setup_py=fake_setup)


def test_helper_rejects_bad_naming(tmp_path):
    """A dist not following gaia-agent-<id> fails loudly."""
    fake_setup = tmp_path / "setup.py"
    fake_setup.write_text(
        'setup(\n    extras_require={"agents": ["totally-wrong-name"]},\n)\n',
        encoding="utf-8",
    )
    with pytest.raises(lap.AgentListError, match="naming convention"):
        lap.list_agent_packages(setup_py=fake_setup)


# ── --only filter tests (#1598) ──────────────────────────────────────────────


def test_only_filter_ids():
    """--only email returns exactly the email agent when using --format ids."""
    import subprocess  # nosec B404 — fixed argv, no shell

    out = subprocess.run(
        [
            sys.executable,
            str(UTIL_DIR / "list_agent_packages.py"),
            "--only",
            "email",
            "--format",
            "ids",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert lines == ["email"], f"expected ['email'] but got {lines!r}"


def test_only_filter_matrix_single_entry():
    """--format matrix --only email yields an include list of length 1 with correct fields."""
    import json
    import subprocess  # nosec B404 — fixed argv, no shell

    out = subprocess.run(
        [
            sys.executable,
            str(UTIL_DIR / "list_agent_packages.py"),
            "--format",
            "matrix",
            "--only",
            "email",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    matrix = json.loads(out.stdout)
    assert "include" in matrix
    assert len(matrix["include"]) == 1
    entry = matrix["include"][0]
    assert entry["id"] == "email"
    assert entry["dist"] == "gaia-agent-email"
    assert entry["path"].endswith("hub/agents/python/email")


def test_only_filter_unknown_id_fails():
    """An unknown agent id with --only exits non-zero and surfaces valid ids."""
    import subprocess  # nosec B404 — fixed argv, no shell

    result = subprocess.run(
        [sys.executable, str(UTIL_DIR / "list_agent_packages.py"), "--only", "nope"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "expected non-zero exit for unknown agent id"
    # Error message should name some valid ids so the user knows what to use.
    assert (
        "nope" in result.stderr
        or "valid" in result.stderr.lower()
        or "email" in result.stderr
    )
