# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guards for the agent-wheel PyPI publish path (issue #1179).

These tests do *not* touch the network or PyPI. They assert the static
invariants that make dual distribution (R2 + PyPI) correct and drift-proof:

* the production-agent list (``setup.py``'s ``AGENT_WHEEL_PACKAGES``) maps
  cleanly to packages under ``hub/agents/<id>/python/`` (via
  ``util/list_agent_packages.py``);
* every such wheel declares the ``gaia-agent-<id>`` name and an ``amd-gaia``
  framework dependency (issue #1179 scope item 3), pinned at the same floor the
  agent manifest advertises as ``min_gaia_version``;
* the publish workflow derives its matrix from that same list, so a new agent
  added to ``AGENT_WHEEL_PACKAGES`` is published automatically with no second
  list to sync. That list is a plain module-level constant rather than an
  ``extras_require`` entry -- see #2240 and the module docstring on
  ``util/list_agent_packages.py`` for why.

The live dual-publish logic is covered by ``test_hub_publisher.py``; the CLI
wiring by ``test_cli_agent.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

if sys.version_info >= (3, 11):
    import tomllib
else:  # setup.py pins tomli for 3.10, which has no stdlib tomllib.
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
UTIL_DIR = REPO_ROOT / "util"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish_agents.yml"

# Infrastructure agents publish as wheels but are loaded by class-path from the
# API server, not discovered via the gaia.agent registry entry point (#1102).
# Currently empty: the routing agent that used to need this exemption was
# removed in the agent-collapse (its only AGENT_MODELS entry went with it).
INFRA_ONLY_AGENT_IDS: set[str] = set()

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
    # Spot-check the surviving hub agents; the helper enforces the full set
    # exists on disk, so this just sanity-checks the mapping direction.
    assert {"email", "chat", "gaia"} <= ids


def test_dist_name_and_directory_convention(packages):
    """Each entry follows gaia-agent-<id> and lives at hub/agents/<id>/python."""
    for p in packages:
        assert p.dist_name == f"gaia-agent-{p.agent_id}"
        assert p.path == lap.AGENTS_DIR / p.agent_id / "python"
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


def _release(version):
    """("0.23" -> (0, 23, 0)) so a shortened floor doesn't false-fail on length."""
    parts = tuple(int(p) for p in str(version).strip().split(".")[:3])
    return (parts + (0, 0, 0))[:3]


def _amd_gaia_floor(requirements):
    """The >= floor of the amd-gaia requirement in *requirements*, or None.

    Parsed with ``packaging`` rather than a regex so PEP 508 spelling that is
    legal but unusual — ``amd-gaia >= 0.23.1``, an ``[api]`` extras group — is
    read the same way pip reads it.
    """
    for raw in requirements:
        parsed = Requirement(str(raw))
        if canonicalize_name(parsed.name) != "amd-gaia":
            continue
        for spec in parsed.specifier:
            if spec.operator == ">=":
                return spec.version
    return None


def test_amd_gaia_floor_matches_manifest_min_gaia_version(packages):
    """Every declared amd-gaia floor names the core the manifest advertises.

    ``min_gaia_version`` gates nothing — it only renders on the hub listing. The
    dependency floor is what pip resolves against, so a lower one silently wins
    and installs the agent onto a core that cannot run it, while the hub page
    still advertises the newer one.
    """
    for p in packages:
        manifest = yaml.safe_load(
            (p.path / "gaia-agent.yaml").read_text(encoding="utf-8")
        )
        declared = manifest.get("min_gaia_version")
        assert declared, f"{p.dist_name}: manifest has no min_gaia_version"

        with (p.path / "pyproject.toml").open("rb") as fh:
            pyproject = tomllib.load(fh)

        floors = {
            "pyproject.toml": _amd_gaia_floor(
                pyproject["project"].get("dependencies") or []
            ),
            # The manifest restates the floor; the schema puts it under `python`.
            "the manifest's python.dependencies": _amd_gaia_floor(
                (manifest.get("python") or {}).get("dependencies") or []
            ),
        }
        assert floors["pyproject.toml"], (
            f"{p.dist_name}: pyproject.toml declares no amd-gaia>= floor, so "
            f"nothing stops pip resolving a core older than {declared}"
        )
        for where, floor in floors.items():
            if floor is None:
                continue
            assert _release(floor) == _release(declared), (
                f"{p.dist_name}: {where} pins amd-gaia>={floor} but the manifest "
                f"declares min_gaia_version {declared}. A core below {declared} "
                f"cannot run this agent, so the floors must match."
            )


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
    """A dist in the list with no on-disk package fails loudly (no silent skip)."""
    fake_setup = tmp_path / "setup.py"
    fake_setup.write_text(
        'AGENT_WHEEL_PACKAGES = ["gaia-agent-doesnotexist"]\nsetup()\n',
        encoding="utf-8",
    )
    with pytest.raises(lap.AgentListError, match="no package at"):
        lap.list_agent_packages(setup_py=fake_setup)


def test_helper_rejects_bad_naming(tmp_path):
    """A dist not following gaia-agent-<id> fails loudly."""
    fake_setup = tmp_path / "setup.py"
    fake_setup.write_text(
        'AGENT_WHEEL_PACKAGES = ["totally-wrong-name"]\nsetup()\n',
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
    assert entry["path"].endswith("hub/agents/email/python")


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
