# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fixed test contract for the email-agent capability matrix (#2013).

This is the fixed contract ``packaging/capability_matrix.py`` satisfies. The
module introspects four surfaces (internal ``@tool`` agent-loop functions,
REST verbs, MCP tools, eval-gate coverage), renders a committed
``CAPABILITY_MATRIX.md``, and asserts nothing drifts silently:

- AC1: the committed matrix doc is byte-identical to a freshly regenerated one.
- AC2: ``tools_count`` (52) is identical across ``gaia-agent.yaml``,
  ``gaia_agent_email.__init__.build_registration()``, and an AST-derived count.
- AC3: every one of the 22 exposed ops (18 REST + 4 MCP) is annotated with an
  eval suite name or the "no quality eval" sentinel — closed-set, bidirectional.
- AC4: the MCP-scope decision (4 tools + rationale) is pinned and current.
- AC5: every eval suite has a non-trivial follow-up plan, and ``followups`` is
  pinned as CI-unwired today.

``packaging/`` has no ``__init__.py`` (mirrors ``server.py``/``stamp_version.py``)
so the module is loaded by file path, exactly like ``test_stamp_version.py`` and
``test_gen_binaries_lock.py`` load their sibling packaging scripts.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip cleanly when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

import yaml  # noqa: E402

# ---------------------------------------------------------------------------
# Path anchoring — mirrors gen_scorecard.py's documented hop chain:
#   packaging/ -> email/ -> python/ -> agents/ -> hub/ -> repo root
# From this test file (hub/agents/email/python/tests/test_capability_matrix.py):
#   parents[1] = email root, parents[5] = repo root.
# ---------------------------------------------------------------------------
_EMAIL_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MATRIX_PATH = _EMAIL_ROOT / "packaging" / "capability_matrix.py"
_COMMITTED_MATRIX_DOC = _EMAIL_ROOT / "CAPABILITY_MATRIX.md"
_GAIA_AGENT_YAML = _EMAIL_ROOT / "gaia-agent.yaml"
_GATE_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "email"

_spec = importlib.util.spec_from_file_location("capability_matrix", _MATRIX_PATH)
capability_matrix = importlib.util.module_from_spec(_spec)
sys.modules["capability_matrix"] = capability_matrix
_spec.loader.exec_module(capability_matrix)


# ---------------------------------------------------------------------------
# Ground truth (hard-coded, verified against the code as of 2026-07-13 —
# see the plan for issue #2013 §1 for the derivation of every number below).
# ---------------------------------------------------------------------------

# 12 mixins in gaia_agent_email/tools/, keyed by module stem, 52 tools total.
_EXPECTED_TOOLS_BY_MIXIN = {
    "read_tools": 8,
    "organize_tools": 15,
    "reply_tools": 5,
    "calendar_tools": 6,
    "schedule_tools": 4,
    "preference_tools": 4,
    "delete_tools": 3,
    "phishing_tools": 2,
    "voice_tools": 2,
    "followup_tools": 1,
    "profile_tools": 1,
    "summarize_tools": 1,
}
_EXPECTED_TOOLS_TOTAL = 52
assert sum(_EXPECTED_TOOLS_BY_MIXIN.values()) == _EXPECTED_TOOLS_TOTAL

_EXPECTED_MCP_COUNT = 4
_EXPECTED_EVAL_SUITE_COUNT = 6
_EXPECTED_REST_FUNCTIONAL_COUNT = 18
_EXPECTED_REST_IN_CONTRACT_COUNT = 21

# The 6 eval suites are the *_gate_thresholds.json fixture stems at the repo
# root (NOT under hub/agents/email/python/tests/ — this package ships no such
# fixtures of its own).
_EXPECTED_EVAL_SUITE_NAMES = {
    "briefing",
    "drafting",
    "perf",
    "quality",
    "action_items",
    "followups",
}

# The 4 MCP tools EmailTriageMCPAgent.get_mcp_tool_definitions() returns.
_EXPECTED_MCP_TOOL_NAMES = frozenset(
    {"triage_email", "triage_email_batch", "draft_reply", "send_email"}
)

_NO_EVAL_SENTINEL = "no quality eval (contract-tested only)"

# Op-naming scheme for OP_EVAL_COVERAGE (contract decision fixed by this test,
# issue #2013): REST ops are named by their path suffix after "/v1/email/",
# with a " (GET)"/" (POST)" disambiguation suffix ONLY on the one path exposed
# under both verbs (calendar/events). MCP ops are named by their literal tool
# name. This is the naming capability_matrix.py's OP_EVAL_COVERAGE must use.
_EXPECTED_REST_OP_NAMES = {
    "triage",
    "triage/batch",
    "search",
    "prescan",
    "briefing",
    "draft",
    "send",
    "confirm",
    "archive",
    "unarchive",
    "quarantine",
    "unquarantine",
    "calendar/events (GET)",
    "calendar/events/preview",
    "calendar/events (POST)",
    "calendar/events/respond",
    # #2016 streaming agent-loop surface: POST /v1/email/query and its cancel.
    "query",
    "query/{run_id}/cancel",
}
assert len(_EXPECTED_REST_OP_NAMES) == _EXPECTED_REST_FUNCTIONAL_COUNT
_EXPECTED_OP_NAMES = _EXPECTED_REST_OP_NAMES | set(_EXPECTED_MCP_TOOL_NAMES)
assert len(_EXPECTED_OP_NAMES) == _EXPECTED_REST_FUNCTIONAL_COUNT + _EXPECTED_MCP_COUNT


@pytest.fixture(scope="module")
def matrix():
    return capability_matrix.derive_matrix(_REPO_ROOT)


# ---------------------------------------------------------------------------
# AC1 — committed matrix doc + drift
# ---------------------------------------------------------------------------


def test_committed_capability_matrix_is_up_to_date(matrix):
    assert _COMMITTED_MATRIX_DOC.exists(), (
        f"{_COMMITTED_MATRIX_DOC} is missing — generate it with "
        f"`python hub/agents/email/python/packaging/capability_matrix.py`"
    )
    committed = _COMMITTED_MATRIX_DOC.read_text(encoding="utf-8")
    fresh = capability_matrix.render_markdown(matrix)
    assert committed == fresh, (
        "CAPABILITY_MATRIX.md is stale — regenerate it with "
        "`python hub/agents/email/python/packaging/capability_matrix.py`"
    )


# ---------------------------------------------------------------------------
# AC2 — tools_count defined + asserted (52, 3 independent sources)
# ---------------------------------------------------------------------------


def test_tools_count_matches_derived(matrix):
    manifest = yaml.safe_load(_GAIA_AGENT_YAML.read_text(encoding="utf-8"))
    manifest_count = manifest["tools_count"]

    import gaia_agent_email

    registration_count = gaia_agent_email.build_registration().tools_count

    ast_count = matrix.tools_total

    assert manifest_count == _EXPECTED_TOOLS_TOTAL
    assert registration_count == _EXPECTED_TOOLS_TOTAL
    assert ast_count == _EXPECTED_TOOLS_TOTAL


def test_gaia_agent_yaml_carries_definition_comment():
    """The comment block directly above ``tools_count`` in gaia-agent.yaml must
    carry the documented definition. The marker is SLICED from
    ``TOOLS_COUNT_DEFINITION`` itself (not a duplicated string literal), so the
    yaml comment, the matrix Definitions section, and this test all trace to
    the one constant — rewording the constant without updating the yaml
    comment fails here."""
    definition = capability_matrix.TOOLS_COUNT_DEFINITION
    marker = "internal @tool-decorated agent-loop functions"
    assert marker in definition, (
        "marker phrase no longer appears in TOOLS_COUNT_DEFINITION — update "
        "this test's slice and the gaia-agent.yaml comment together"
    )

    lines = _GAIA_AGENT_YAML.read_text(encoding="utf-8").splitlines()
    idx = next(i for i, line in enumerate(lines) if line.startswith("tools_count:"))
    comment_block: list[str] = []
    j = idx - 1
    while j >= 0 and lines[j].lstrip().startswith("#"):
        comment_block.append(lines[j])
        j -= 1
    assert comment_block, "no comment block directly above tools_count"
    assert marker in "\n".join(comment_block), (
        f"the comment above tools_count in {_GAIA_AGENT_YAML} must carry the "
        f"TOOLS_COUNT_DEFINITION marker phrase: {marker!r}"
    )


def test_internal_tool_counts_per_mixin(matrix):
    tools_by_mixin = matrix.tools_by_mixin
    assert dict(tools_by_mixin) == _EXPECTED_TOOLS_BY_MIXIN


def test_surface_counts_match_code(matrix):
    mcp_tools = matrix.mcp_tools
    eval_suites = matrix.eval_suites
    rest_functional = matrix.rest_functional_count
    rest_in_contract = matrix.rest_in_contract_count

    assert len(mcp_tools) == _EXPECTED_MCP_COUNT
    assert len(eval_suites) == _EXPECTED_EVAL_SUITE_COUNT
    assert rest_functional == _EXPECTED_REST_FUNCTIONAL_COUNT
    assert rest_in_contract == _EXPECTED_REST_IN_CONTRACT_COUNT


def test_tools_count_guard_rejects_ast_drift():
    """Changing only the AST-derived count must be detectable as a mismatch
    against the yaml/__init__.py literals (proves the guard is non-vacuous,
    not that the AST count is hardcoded to agree with the other two sources).
    The #2013 scenario: a new @tool lands but the literals are not bumped."""
    manifest = yaml.safe_load(_GAIA_AGENT_YAML.read_text(encoding="utf-8"))
    manifest_count = manifest["tools_count"]

    import gaia_agent_email

    registration_count = gaia_agent_email.build_registration().tools_count
    assert manifest_count == registration_count == _EXPECTED_TOOLS_TOTAL

    drifted_ast_count = _EXPECTED_TOOLS_TOTAL + 1

    with pytest.raises(ValueError) as excinfo:
        capability_matrix.reconcile_tools_count(
            manifest_count=manifest_count,
            registration_count=registration_count,
            ast_count=drifted_ast_count,
        )
    message = str(excinfo.value)
    assert str(manifest_count) in message
    assert str(drifted_ast_count) in message


def test_tools_count_guard_rejects_manifest_drift(tmp_path):
    """A manifest with a drifted tools_count must fail reconciliation against
    the true __init__.py/AST values — never silently accepted. NEVER mutate
    the committed gaia-agent.yaml in place; always operate on a tmp_path copy.
    """
    original = _GAIA_AGENT_YAML.read_text(encoding="utf-8")
    drifted_manifest = tmp_path / "gaia-agent.yaml"
    drifted_manifest.write_text(
        original.replace(f"tools_count: {_EXPECTED_TOOLS_TOTAL}", "tools_count: 99"),
        encoding="utf-8",
    )
    drifted = yaml.safe_load(drifted_manifest.read_text(encoding="utf-8"))
    assert drifted["tools_count"] == 99
    assert drifted["tools_count"] != _EXPECTED_TOOLS_TOTAL

    import gaia_agent_email

    registration_count = gaia_agent_email.build_registration().tools_count

    with pytest.raises(ValueError) as excinfo:
        capability_matrix.reconcile_tools_count(
            manifest_count=drifted["tools_count"],
            registration_count=registration_count,
            ast_count=_EXPECTED_TOOLS_TOTAL,
        )
    message = str(excinfo.value)
    assert "99" in message


def test_ast_counter_reacts_to_source_change(tmp_path):
    """Feed a synthetic source file with a known number of @tool-decorated
    functions into the module's AST-counting logic and assert it returns
    exactly that count — proving the parser reacts to source content rather
    than returning a hardcoded constant."""
    synthetic = tmp_path / "synthetic_tools.py"
    synthetic.write_text(
        '''
from gaia.agents.base.tools import tool


@tool
def tool_one(x: int) -> int:
    """A plain @tool-decorated function."""
    return x


@tool
async def tool_two(x: int) -> int:
    """An async @tool-decorated function."""
    return x


@tool
def tool_three(x: int) -> int:
    """A third @tool-decorated function."""
    return x


@tool(atomic=True)
def tool_four(x: int) -> int:
    """Call-form @tool(...) — the framework supports both forms; the counter
    must too, or the first mixin adopting it is silently uncounted."""
    return x


def not_a_tool(x: int) -> int:
    """Deliberately undecorated — must NOT be counted."""
    return x
''',
        encoding="utf-8",
    )

    result = capability_matrix.count_tools_in_source(synthetic)
    assert result == 4


def test_mcp_name_parser_handles_async_def(tmp_path):
    """The MCP name extractor must match get_mcp_tool_definitions whether it
    is a plain or async def — symmetry with count_tools_in_source, so a
    future async migration of the MCP server can't silently zero the count."""
    synthetic = tmp_path / "synthetic_mcp.py"
    synthetic.write_text(
        """
class FakeMCPAgent:
    async def get_mcp_tool_definitions(self):
        return [
            {"name": "alpha", "inputSchema": {}},
            {"name": "beta", "inputSchema": {}},
        ]
""",
        encoding="utf-8",
    )
    assert capability_matrix._derive_mcp_tool_names(synthetic) == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# AC3 — eval coverage per exposed op (22 ops, closed-set + bidirectional)
# ---------------------------------------------------------------------------


def test_op_eval_coverage_is_complete_and_closed():
    coverage = capability_matrix.OP_EVAL_COVERAGE
    assert set(coverage) == _EXPECTED_OP_NAMES

    allowed_values = _EXPECTED_EVAL_SUITE_NAMES | {_NO_EVAL_SENTINEL}
    for op, value in coverage.items():
        assert value in allowed_values, (
            f"OP_EVAL_COVERAGE[{op!r}] = {value!r} is not one of the 6 derived "
            f"eval suite names nor the sentinel string"
        )


# ---------------------------------------------------------------------------
# AC4 — MCP scope is a documented decision
# ---------------------------------------------------------------------------


def test_mcp_scope_decision_current(matrix):
    decision = capability_matrix.MCP_SCOPE_DECISION
    assert decision["tools"] == _EXPECTED_MCP_TOOL_NAMES
    assert isinstance(decision["rationale"], str)
    assert decision["rationale"].strip() != ""

    rendered = capability_matrix.render_markdown(matrix)
    assert "## MCP Scope Decision" in rendered


# ---------------------------------------------------------------------------
# AC5 — follow-up plan for gates
# ---------------------------------------------------------------------------


def test_eval_followup_plan_current(matrix):
    plan = capability_matrix.EVAL_FOLLOWUP_PLAN
    assert set(plan) == _EXPECTED_EVAL_SUITE_NAMES

    for suite, text in plan.items():
        assert isinstance(text, str)
        assert (
            len(text) > 10
        ), f"EVAL_FOLLOWUP_PLAN[{suite!r}] looks like a lazy placeholder: {text!r}"

    assert matrix.eval_suites["followups"]["wired"] is False
