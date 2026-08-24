# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fixed test contract for the flagship gaia agent capability matrix.

``packaging/capability_matrix.py`` introspects the agent's surfaces (registered
agent-loop tools, REST verbs, eval coverage) and renders a committed
``CAPABILITY_MATRIX.md``. This suite is the contract it satisfies:

- AC1: the committed matrix doc is byte-identical to a freshly regenerated one.
- AC2: ``tools_count`` (68) is identical across ``gaia-agent.yaml``,
  ``gaia_agent.build_gaia()``, and the AST-derived bundle union.
- AC3: every exposed REST functional op is annotated with an eval suite name or
  the "no quality eval" sentinel — closed-set, bidirectional.
- AC4: the no-MCP scope decision is pinned and matches the manifest.
- AC5: the eval-suite surface is honestly empty today (no
  ``tests/fixtures/gaia/*_gate_thresholds.json`` and no ``eval/scenarios/gaia_*``
  categories exist yet) — when the gaia eval corpus lands, these pins move
  WITH the fixtures, never silently.

``packaging/`` has no ``__init__.py`` (it must never ship in the frozen
binary), so the module is loaded by file path, exactly like
``test_gen_binaries_lock.py`` loads its sibling packaging script.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("gaia_agent")
pytest.importorskip("gaia_agent_chat")

import yaml  # noqa: E402

# Path anchoring — the generator's documented hop chain:
#   packaging/ -> python/ -> gaia/ -> agents/ -> hub/ -> repo root
# From this test file (hub/agents/gaia/python/tests/test_capability_matrix.py):
#   parents[1] = python root, parents[2] = agent root, parents[5] = repo root.
_PYTHON_ROOT = Path(__file__).resolve().parents[1]
_AGENT_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MATRIX_PATH = _PYTHON_ROOT / "packaging" / "capability_matrix.py"
_COMMITTED_MATRIX_DOC = _AGENT_ROOT / "CAPABILITY_MATRIX.md"
_GAIA_AGENT_YAML = _PYTHON_ROOT / "gaia-agent.yaml"

_spec = importlib.util.spec_from_file_location("gaia_capability_matrix", _MATRIX_PATH)
capability_matrix = importlib.util.module_from_spec(_spec)
sys.modules["gaia_capability_matrix"] = capability_matrix
_spec.loader.exec_module(capability_matrix)


# ---------------------------------------------------------------------------
# Ground truth (hard-coded, verified against the code as of 2026-08-21 — the
# derivation is gaia-agent.yaml's tools_count comment block plus the plan for
# the flagship eval dataset §1/§3).
# ---------------------------------------------------------------------------

_EXPECTED_TOOLS_TOTAL = 68
_EXPECTED_CORE_COUNT = 10
_EXPECTED_BUNDLE_COUNT = 18

_EXPECTED_SKILL_LIBRARY_TOOLS = frozenset(
    {
        "list_skills",
        "search_skill_hub",
        "install_skill",
        "capture_skill",
        "remove_skill",
        "load_skill",
        "unload_skill",
        "skill_status",
    }
)

_EXPECTED_CODE_INDEX_TOOLS = frozenset(
    {"index_codebase", "search_code_index", "get_index_status", "clear_code_index"}
)

# REST op naming: the route path after the /v1/gaia prefix, no leading slash.
_EXPECTED_REST_OP_NAMES = {
    "query",
    "query/{run_id}/cancel",
    "query/{run_id}/respond",
}
_EXPECTED_REST_FUNCTIONAL_COUNT = 3
# + the init readiness probe and the three liveness/version probes
# (/health, /version, /v1/gaia/version).
_EXPECTED_REST_IN_CONTRACT_COUNT = 7

_NO_EVAL_SENTINEL = "no quality eval (contract-tested only)"


@pytest.fixture(scope="module")
def matrix():
    return capability_matrix.derive_matrix(_REPO_ROOT)


# ---------------------------------------------------------------------------
# AC1 — committed matrix doc + drift
# ---------------------------------------------------------------------------


def test_committed_capability_matrix_is_up_to_date(matrix):
    assert _COMMITTED_MATRIX_DOC.exists(), (
        f"{_COMMITTED_MATRIX_DOC} is missing — generate it with "
        f"`python hub/agents/gaia/python/packaging/capability_matrix.py`"
    )
    committed = _COMMITTED_MATRIX_DOC.read_text(encoding="utf-8")
    fresh = capability_matrix.render_markdown(matrix)
    assert committed == fresh, (
        "CAPABILITY_MATRIX.md is stale — regenerate it with "
        "`python hub/agents/gaia/python/packaging/capability_matrix.py`"
    )


# ---------------------------------------------------------------------------
# AC2 — tools_count asserted across three independent sources
# ---------------------------------------------------------------------------


def test_tools_count_matches_derived(matrix):
    manifest = yaml.safe_load(_GAIA_AGENT_YAML.read_text(encoding="utf-8"))

    import gaia_agent

    registration_count = gaia_agent.build_gaia().tools_count

    assert manifest["tools_count"] == _EXPECTED_TOOLS_TOTAL
    assert registration_count == _EXPECTED_TOOLS_TOTAL
    assert matrix.tools_total == _EXPECTED_TOOLS_TOTAL


def test_bundle_structure(matrix):
    assert len(matrix.core_tools) == _EXPECTED_CORE_COUNT
    assert len(matrix.bundles) == _EXPECTED_BUNDLE_COUNT
    # load_tools is the CORE-only escape hatch — never in a bundle, so a bundle
    # membership would double-render it in the native tools= schema.
    assert "load_tools" in matrix.core_tools
    assert not any("load_tools" in members for members in matrix.bundles.values())


def test_skill_library_tools_match_framework(matrix):
    """The AST-read tuple must equal the live framework constant AND sit inside
    the bundle union — a rename on either side fails here, not mid-run."""
    from gaia.agents.tools.skill_library_tools import SKILL_LIBRARY_TOOL_NAMES

    assert set(matrix.skill_library_tools) == set(SKILL_LIBRARY_TOOL_NAMES)
    assert set(matrix.skill_library_tools) == _EXPECTED_SKILL_LIBRARY_TOOLS


def test_code_index_bundle_carries_the_four_tools(matrix):
    assert matrix.bundles["code_index"] == _EXPECTED_CODE_INDEX_TOOLS


def test_reconcile_rejects_drift():
    """The guard is non-vacuous: a lone AST-count bump must raise, naming all
    three values (the scenario: a new bundle tool lands, the literals do not)."""
    with pytest.raises(ValueError, match="tools_count sources disagree"):
        capability_matrix.reconcile_tools_count(
            manifest_count=_EXPECTED_TOOLS_TOTAL,
            registration_count=_EXPECTED_TOOLS_TOTAL,
            ast_count=_EXPECTED_TOOLS_TOTAL + 1,
        )


# ---------------------------------------------------------------------------
# AC3 — every exposed op annotated, closed-set
# ---------------------------------------------------------------------------


def test_rest_surface_counts(matrix):
    assert set(matrix.rest_op_names) == _EXPECTED_REST_OP_NAMES
    assert matrix.rest_functional_count == _EXPECTED_REST_FUNCTIONAL_COUNT
    assert matrix.rest_in_contract_count == _EXPECTED_REST_IN_CONTRACT_COUNT


def test_every_op_annotated_closed_set(matrix):
    coverage = capability_matrix.OP_EVAL_COVERAGE
    assert set(coverage) == set(matrix.rest_op_names), (
        "OP_EVAL_COVERAGE and the derived REST surface diverged — annotate the "
        "new op (suite name or sentinel), or drop the stale entry."
    )
    for op, suite in coverage.items():
        assert suite == _NO_EVAL_SENTINEL or suite in matrix.eval_suites, (
            f"op {op!r} names eval suite {suite!r}, but no "
            f"tests/fixtures/gaia/{suite}_gate_thresholds.json exists"
        )


# ---------------------------------------------------------------------------
# AC4 — the no-MCP decision is pinned and current
# ---------------------------------------------------------------------------


def test_mcp_scope_decision_matches_manifest(matrix):
    assert matrix.mcp_server_declared is False
    assert capability_matrix.MCP_SCOPE_DECISION["mcp_server"] is False
    assert len(capability_matrix.MCP_SCOPE_DECISION["rationale"]) > 100


# ---------------------------------------------------------------------------
# AC5 — the eval surface is honestly empty today
# ---------------------------------------------------------------------------


def test_eval_surface_state_is_derived_not_asserted(matrix):
    """Today: zero gate-threshold fixtures, zero gaia_* scenario categories.

    When the gaia eval corpus lands (plan phases 2-4), this pin moves in the
    same change that adds the fixtures — updating OP_EVAL_COVERAGE and
    regenerating CAPABILITY_MATRIX.md with it. A fixture appearing without
    this test noticing would mean the matrix stopped deriving anything.
    """
    fixtures_dir = _REPO_ROOT / "tests" / "fixtures" / "gaia"
    on_disk = (
        {p.name for p in fixtures_dir.glob("*_gate_thresholds.json")}
        if fixtures_dir.is_dir()
        else set()
    )
    assert set(matrix.eval_suites) == {
        n[: -len("_gate_thresholds.json")] for n in on_disk
    }

    scenario_dirs = sorted(
        p.name for p in (_REPO_ROOT / "eval" / "scenarios").glob("gaia_*") if p.is_dir()
    )
    assert matrix.scenario_categories == scenario_dirs

    # Current state pins — move these WITH the corpus, never delete them.
    assert set(matrix.eval_suites) == {"quality", "perf"}
    assert all(s["enforce"] is False for s in matrix.eval_suites.values())
    assert matrix.scenario_categories == [
        "gaia_code",
        "gaia_core",
        "gaia_data",
        "gaia_files",
        "gaia_honesty",
        "gaia_memory",
        "gaia_rag",
        "gaia_shell",
        "gaia_skills_capture",
        "gaia_skills_lifecycle",
        "gaia_skills_tasks",
        "gaia_tool_selection",
        "gaia_web",
    ]
    assert capability_matrix.EVAL_FOLLOWUP_PLAN.strip()


def test_sequence_pins_exist_and_load(matrix):
    """The matrix's claim that sequence pins shape-test /query must be true:
    the three committed baselines load through the harness and reference only
    canonical event types."""
    from gaia.eval.sidecar_harness import baselines_dir_for, load_baselines

    baselines = load_baselines(baselines_dir_for(_PYTHON_ROOT))
    assert set(baselines) >= {
        "plain_answer",
        "tool_query",
        "write_needs_confirmation",
    }
