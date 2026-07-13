#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Code-derived capability matrix for the GAIA Email Triage agent (#2013).

Four surfaces exist (internal ``@tool`` agent-loop functions, the REST API,
the MCP stdio interface, and the eval-gate suites) and nothing previously
guaranteed they describe the same agent. This module introspects all four
directly from source/config -- never from memory, never re-typed by hand --
and renders the result to a committed ``CAPABILITY_MATRIX.md`` that CI diffs
against a fresh regeneration, the same idiom ``export_openapi.py`` uses for the
REST contract.

Placement note: this file lives in ``packaging/``, NOT inside the
``gaia_agent_email`` package. ``packaging/freeze.py`` does a blanket
``--collect-submodules gaia_agent_email`` when building the frozen sidecar
binary, so any module under the package ships in the shipped artifact. This is
a dev/CI tool that reads repo-root ``tests/fixtures/`` -- it must never ship.
``packaging/`` has no ``__init__.py`` by design (mirrors ``server.py``), so
invoke by **script path**, never ``-m``::

    python hub/agents/python/email/packaging/capability_matrix.py           # write
    python hub/agents/python/email/packaging/capability_matrix.py --check   # CI drift check

Surface mechanisms (one per surface, no fallback hedges):

- Internal ``@tool`` -- AST over ``sorted(tools/*.py)``, matching
  ``FunctionDef``/``AsyncFunctionDef`` whose decorator list names ``tool``.
  Never ``len(_TOOL_REGISTRY)`` (env-dependent 52/57 with ``MemoryMixin``);
  never instantiates any agent.
- REST-in-contract -- reuses ``gaia_agent_email.export_openapi.build_spec()``
  (already imported+tested by ``test_rest_contract.py``) rather than
  re-importing ``api_routes.router`` from scratch.
- MCP -- a pure AST parse of ``mcp_server.py``'s
  ``EmailTriageMCPAgent.get_mcp_tool_definitions`` return literal (reads the 4
  tool-name string literals). No import, no instantiation.
- Evals -- ``sorted(glob(<repo_root>/tests/fixtures/email/*_gate_thresholds.json))``,
  reading ``enforce``/``acceptance_enforce``, plus a curated
  suite -> report-script filename map (the filenames do not follow a single
  uniform pattern -- ``action_items`` maps to ``eval_action_item_report.py``,
  and ``quality``/``perf`` share the generic ``eval_gate_report.py``) whose
  actual on-disk existence is checked to derive ``wired``.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List

# Repo root via the fixed hop chain (copied from gen_scorecard.py:42-46):
# packaging/ -> email/ -> python/ -> agents/ -> hub/ -> repo root.
_PACKAGING_DIR = Path(__file__).resolve().parent
_EMAIL_ROOT = _PACKAGING_DIR.parent
_REPO_ROOT = _EMAIL_ROOT.parent.parent.parent.parent

_TOOLS_DIR = _EMAIL_ROOT / "gaia_agent_email" / "tools"
_MCP_SERVER_PATH = _EMAIL_ROOT / "gaia_agent_email" / "mcp_server.py"
_GAIA_AGENT_YAML = _EMAIL_ROOT / "gaia-agent.yaml"
_GATE_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "email"

# Committed, generated artifact.
ARTIFACT_PATH = _EMAIL_ROOT / "CAPABILITY_MATRIX.md"

# REST path suffixes that are probe/utility endpoints, not part of the 16
# functional verbs (still counted in the 19-operation frozen contract).
_PROBE_SUFFIXES = frozenset({"health", "version", "init"})

# Suite -> the report/gate script whose presence means CI actually consumes
# the eval's output today. Filenames do not follow one uniform pattern, so
# this mapping is curated once and its target checked for on-disk existence
# (never asserted from memory).
_SUITE_REPORT_FILENAMES: Dict[str, str] = {
    "briefing": "eval_briefing_report.py",
    "drafting": "eval_drafting_report.py",
    "action_items": "eval_action_item_report.py",
    "quality": "eval_gate_report.py",
    "perf": "eval_gate_report.py",
    "followups": "eval_followup_report.py",  # confirmed absent -- #2013
}

# ---------------------------------------------------------------------------
# Declared constants -- curated once, machine-guarded (AC2/AC3/AC4/AC5). These
# are the honest single source: no code links a REST route or MCP tool to a
# quality eval, so a reviewed mapping is the only truthful mechanism.
# ---------------------------------------------------------------------------

TOOLS_COUNT_DEFINITION = (
    "tools_count = the number of internal @tool-decorated agent-loop "
    "functions across gaia_agent_email/tools/*.py mixins (one per capability "
    "the agent's own LLM tool-calling loop can invoke). This is distinct "
    "from, and larger than, the REST API's 16 functional verbs and the MCP "
    "interface's 4 task-level tools -- both smaller, purpose-built surfaces "
    "for external callers, not agent-loop tools."
)

_NO_EVAL_SENTINEL = "no quality eval (contract-tested only)"

# The 20 exposed ops (16 REST functional + 4 MCP) -> the eval suite that
# actually exercises them for quality, or the sentinel meaning "only
# contract/shape-tested, no judged quality bar". Op names mirror
# ``_derive_rest_ops``'s naming scheme: the REST path suffix after
# "/v1/email/" (disambiguated with " (GET)"/" (POST)" only where a suffix is
# shared across methods), and the literal MCP tool name.
OP_EVAL_COVERAGE: Dict[str, str] = {
    "triage": "quality",
    "triage/batch": "quality",
    "search": _NO_EVAL_SENTINEL,
    "prescan": _NO_EVAL_SENTINEL,
    "briefing": "briefing",
    "draft": "drafting",
    "send": _NO_EVAL_SENTINEL,
    "confirm": _NO_EVAL_SENTINEL,
    "archive": _NO_EVAL_SENTINEL,
    "unarchive": _NO_EVAL_SENTINEL,
    "quarantine": _NO_EVAL_SENTINEL,
    "unquarantine": _NO_EVAL_SENTINEL,
    "calendar/events (GET)": _NO_EVAL_SENTINEL,
    "calendar/events/preview": _NO_EVAL_SENTINEL,
    "calendar/events (POST)": _NO_EVAL_SENTINEL,
    "calendar/events/respond": _NO_EVAL_SENTINEL,
    "triage_email": "quality",
    "triage_email_batch": "quality",
    "draft_reply": "drafting",
    "send_email": _NO_EVAL_SENTINEL,
}

# AC4: the 4-tool MCP scope is a deliberate decision, not an oversight --
# pinned here so it cannot silently regress.
MCP_SCOPE_DECISION = {
    "tools": frozenset(
        {"triage_email", "triage_email_batch", "draft_reply", "send_email"}
    ),
    "rationale": (
        "MCP exists so a host LLM can invoke the email agent as a tool, not "
        "so an external app can drive the full REST surface over stdio. Its "
        "4 tools are task-level verbs sized for tool-calling (triage / "
        "triage_batch / draft / send) -- explicitly NOT a replica of the "
        "REST API. REST is the integration contract for the npm client; MCP "
        "is the tool-shaped facade for an orchestrating model. Adding an MCP "
        "tool is justified by 'a host LLM needs this verb to use the agent "
        "as a tool', never by 'REST has an endpoint for it'."
    ),
}

# AC5: every eval suite gets a documented follow-up plan -- "what happens
# next" for each. The plan text stays state-agnostic where it can: the LIVE
# enforce/wired state is derived from the fixtures and rendered in each
# suite's section header, so the prose never duplicates a flag that the
# fixture can flip out from under it.
EVAL_FOLLOWUP_PLAN: Dict[str, str] = {
    "briefing": (
        "Judge-scored summary-quality gate for the scheduled daily briefing "
        "(approval / recall / hallucination-free / faithfulness bars). "
        "Follow-up: establish and maintain a passing hardware baseline, and "
        "tighten the bars in the fixture as baselines improve."
    ),
    "drafting": (
        "Judge-scored draft-approval gate (#1269 metric, approval_min 0.70) "
        "run by release_agent_email.yml. Follow-up: establish and maintain a "
        "passing hardware baseline, and raise approval_min once a larger "
        "judged corpus is available."
    ),
    "perf": (
        "Strix Halo perf bars (ttft / throughput / pipeline / memory) run by "
        "release_agent_email.yml. Follow-up: keep the bars in the fixture "
        "calibrated to observed hardware runs -- re-tighten as the agent "
        "gets faster, widen only with measured evidence."
    ),
    "quality": (
        "Triage FP/FN bars that only become meaningful once 4-way "
        "categorization accuracy improves (see the #1266 history), per the "
        "fixture's own _comment; a separate acceptance_enforce release gate "
        "runs on the within-one-bucket metric. Follow-up: flip enforce to "
        "true in the fixture once accuracy stabilizes above the gate's bars."
    ),
    "action_items": (
        "Extraction-quality bars with no judged baseline yet. Follow-up: "
        "generate the first nightly Strix Halo / Gemma-4-E4B baseline (the "
        "#1949 eval's documented follow-up) and flip enforce to true once "
        "it stabilizes."
    ),
    "followups": (
        "Detection-quality bars, CI-unwired: no eval_followup_report.py "
        "exists, unlike the other five suites. Follow-up: #2040 tracks "
        "wiring an eval_followup_report.py plus a workflow step and, "
        "separately, establishing a judged baseline (the #1950 eval's "
        "documented follow-up) before flipping enforce to true."
    ),
}


@dataclass
class CapabilityMatrix:
    """Small, lean introspection result -- a plain container, no ceremony."""

    tools_total: int
    tools_by_mixin: Dict[str, int]
    rest_functional_count: int
    rest_in_contract_count: int
    rest_op_names: List[str]
    mcp_tools: FrozenSet[str]
    eval_suites: Dict[str, dict] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Surface 1: internal @tool count (AST, deterministic, no agent instantiation)
# ---------------------------------------------------------------------------


def _is_tool_decorator(dec: ast.expr) -> bool:
    # The framework supports both bare ``@tool`` and call-form ``@tool(...)``
    # (see gaia.agents.base.tools) -- count both or a mixin adopting
    # ``@tool(atomic=True)`` silently vanishes from the drift guard.
    if isinstance(dec, ast.Name):
        return dec.id == "tool"
    if isinstance(dec, ast.Call):
        return isinstance(dec.func, ast.Name) and dec.func.id == "tool"
    return False


def count_tools_in_source(path: Path) -> int:
    """AST-count ``@tool`` / ``@tool(...)``-decorated function defs.

    Never instantiates an agent and never reads ``_TOOL_REGISTRY`` (which is
    env-dependent -- 52 vs 57 with ``MemoryMixin`` composed in the same
    process by sibling tests).
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(_is_tool_decorator(dec) for dec in node.decorator_list):
                count += 1
    return count


def _derive_tools_by_mixin(tools_dir: Path) -> Dict[str, int]:
    by_mixin: Dict[str, int] = {}
    for path in sorted(tools_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        n = count_tools_in_source(path)
        if n > 0:
            by_mixin[path.stem] = n
    return by_mixin


# ---------------------------------------------------------------------------
# Surface 2: REST -- reuse export_openapi.build_spec(), never re-import routes
# ---------------------------------------------------------------------------


def _derive_rest():
    from gaia_agent_email import export_openapi

    spec = export_openapi.build_spec()
    prefix = "/v1/email/"

    all_ops = []  # (method, suffix)
    for path, ops in spec["paths"].items():
        suffix = path[len(prefix) :] if path.startswith(prefix) else path
        for method in ops:
            all_ops.append((method, suffix))

    in_contract_count = len(all_ops)
    functional_ops = [(m, s) for (m, s) in all_ops if s not in _PROBE_SUFFIXES]
    functional_count = len(functional_ops)

    suffix_counts = Counter(s for (_m, s) in functional_ops)
    op_names = sorted(
        f"{suffix} ({method.upper()})" if suffix_counts[suffix] > 1 else suffix
        for method, suffix in functional_ops
    )

    return functional_count, in_contract_count, op_names


# ---------------------------------------------------------------------------
# Surface 3: MCP -- pure AST parse of the static tool-definition list, no
# import, no instantiation.
# ---------------------------------------------------------------------------


def _derive_mcp_tool_names(mcp_server_path: Path) -> List[str]:
    """Statically extract the tool ``name`` literals from
    ``EmailTriageMCPAgent.get_mcp_tool_definitions``'s returned list of dict
    literals -- source-text only, no import of ``mcp_server.py``."""
    tree = ast.parse(
        mcp_server_path.read_text(encoding="utf-8"), filename=str(mcp_server_path)
    )
    names: List[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "get_mcp_tool_definitions"
        ):
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.List):
                    for elt in stmt.value.elts:
                        if not isinstance(elt, ast.Dict):
                            continue
                        for key, value in zip(elt.keys, elt.values):
                            if (
                                isinstance(key, ast.Constant)
                                and key.value == "name"
                                and isinstance(value, ast.Constant)
                            ):
                                names.append(value.value)
            break
    return names


# ---------------------------------------------------------------------------
# Surface 4: eval suites -- glob the gate-threshold fixtures, read flags
# ---------------------------------------------------------------------------


def _derive_eval_suites(gate_fixtures_dir: Path) -> Dict[str, dict]:
    suites: Dict[str, dict] = {}
    suffix = "_gate_thresholds.json"
    for path in sorted(gate_fixtures_dir.glob(f"*{suffix}")):
        suite_name = path.name[: -len(suffix)]
        data = json.loads(path.read_text(encoding="utf-8"))
        report_filename = _SUITE_REPORT_FILENAMES.get(suite_name)
        wired = bool(report_filename) and (_PACKAGING_DIR / report_filename).exists()
        suites[suite_name] = {
            "enforce": bool(data.get("enforce", False)),
            "acceptance_enforce": data.get("acceptance_enforce"),
            "wired": wired,
        }
    return suites


# ---------------------------------------------------------------------------
# Reconciliation guard (AC2) -- mirrors the version.py / stamp_version.py
# reconcile-by-test convention: three independent sources must agree, and a
# mismatch names the offending values rather than failing silently.
# ---------------------------------------------------------------------------


def reconcile_tools_count(
    *, manifest_count: int, registration_count: int, ast_count: int
) -> int:
    """Return the agreed ``tools_count`` if all three sources match.

    Raises ``ValueError`` naming every value when any of the three
    (``gaia-agent.yaml``, ``__init__.py``'s ``build_registration()``, and the
    AST-derived count) disagree.
    """
    values = {
        "gaia-agent.yaml": manifest_count,
        "__init__.py build_registration()": registration_count,
        "AST-derived": ast_count,
    }
    distinct = set(values.values())
    if len(distinct) != 1:
        detail = ", ".join(f"{name}={val}" for name, val in values.items())
        raise ValueError(f"tools_count sources disagree: {detail}")
    return distinct.pop()


def _read_manifest_tools_count(manifest_path: Path = _GAIA_AGENT_YAML) -> int:
    """Read the ``tools_count`` literal from gaia-agent.yaml (raw-text parse --
    no yaml dependency in this packaging script). Fails loud when absent."""
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("tools_count:"):
            return int(line.split(":", 1)[1].strip())
    raise ValueError(
        f"no 'tools_count:' line found in {manifest_path} -- the manifest "
        "must declare it (see CAPABILITY_MATRIX.md Definitions)"
    )


def _read_registration_tools_count(
    init_path: Path = _EMAIL_ROOT / "gaia_agent_email" / "__init__.py",
) -> int:
    """AST-extract the ``tools_count=<int>`` keyword from ``__init__.py``'s
    ``AgentRegistration(...)`` call -- static, no package import needed."""
    tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "tools_count"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, int)
                ):
                    return kw.value.value
    raise ValueError(
        f"no tools_count=<int> keyword found in {init_path} -- "
        "build_registration() must declare it"
    )


# ---------------------------------------------------------------------------
# Top-level derivation
# ---------------------------------------------------------------------------


def derive_matrix(repo_root: Path | None = None) -> CapabilityMatrix:
    """Introspect all four surfaces live from source and return the result.

    ``repo_root`` is accepted for testability (e.g. pointing at a synthetic
    tree) but the tools/mcp/manifest paths are always resolved from this
    module's own location -- the fixed hop chain, never a dynamic walk-up.
    """
    tools_by_mixin = _derive_tools_by_mixin(_TOOLS_DIR)
    tools_total = sum(tools_by_mixin.values())

    # Fail loud at generation/--check time too, not only under pytest: a
    # tools_count drift must never render a matrix that papers over it.
    reconcile_tools_count(
        manifest_count=_read_manifest_tools_count(),
        registration_count=_read_registration_tools_count(),
        ast_count=tools_total,
    )

    rest_functional_count, rest_in_contract_count, rest_op_names = _derive_rest()

    mcp_tools = frozenset(_derive_mcp_tool_names(_MCP_SERVER_PATH))

    gate_fixtures_dir = (
        (repo_root / "tests" / "fixtures" / "email")
        if repo_root is not None
        else _GATE_FIXTURES_DIR
    )
    eval_suites = _derive_eval_suites(gate_fixtures_dir)

    return CapabilityMatrix(
        tools_total=tools_total,
        tools_by_mixin=tools_by_mixin,
        rest_functional_count=rest_functional_count,
        rest_in_contract_count=rest_in_contract_count,
        rest_op_names=rest_op_names,
        mcp_tools=mcp_tools,
        eval_suites=eval_suites,
    )


# ---------------------------------------------------------------------------
# Rendering -- pinned newline + utf-8, every enumerated list sorted (avoids
# cross-platform freshness flakes, mirrors gen_scorecard.py's sorted globs).
# ---------------------------------------------------------------------------


def render_markdown(matrix: CapabilityMatrix) -> str:
    lines: List[str] = []
    lines.append(
        "<!-- Generated by packaging/capability_matrix.py -- do not edit by hand. -->"
    )
    lines.append("# Email Agent Capability Matrix")
    lines.append("")
    lines.append(
        "Code-derived surface inventory for the GAIA Email Triage agent "
        "(#2013). Regenerate with:"
    )
    lines.append("")
    lines.append("```")
    lines.append("python hub/agents/python/email/packaging/capability_matrix.py")
    lines.append("```")
    lines.append("")

    lines.append("## Definitions")
    lines.append("")
    # The constant leads with "tools_count = " (right for the yaml comment);
    # the bullet already labels it, so strip the prefix to avoid doubling.
    definition_body = TOOLS_COUNT_DEFINITION.removeprefix("tools_count = ")
    lines.append(f"- **tools_count**: {definition_body}")
    lines.append(
        f"- **no quality eval sentinel**: `{_NO_EVAL_SENTINEL}` -- the op is "
        "contract/shape-tested only; no judged quality bar exists for it."
    )
    lines.append("")

    lines.append("## Capability matrix")
    lines.append("")
    exposed_ops = matrix.rest_functional_count + len(matrix.mcp_tools)
    lines.append(
        f"{exposed_ops} exposed ops ({matrix.rest_functional_count} REST "
        f"functional + {len(matrix.mcp_tools)} MCP) and their eval coverage:"
    )
    lines.append("")
    lines.append("| Op | Surface | Eval coverage |")
    lines.append("|---|---|---|")
    for op in sorted(OP_EVAL_COVERAGE):
        surface = "MCP" if op in matrix.mcp_tools else "REST"
        lines.append(f"| `{op}` | {surface} | {OP_EVAL_COVERAGE[op]} |")
    lines.append("")

    lines.append("## Surface totals")
    lines.append("")
    lines.append(f"- Internal `@tool` agent-loop functions: **{matrix.tools_total}**")
    for mixin in sorted(matrix.tools_by_mixin):
        lines.append(f"  - `{mixin}`: {matrix.tools_by_mixin[mixin]}")
    lines.append(
        f"- REST functional verbs: **{matrix.rest_functional_count}** "
        f"({matrix.rest_in_contract_count} total operations in the frozen contract, "
        "including health/version/init probes)"
    )
    lines.append(f"- MCP tools: **{len(matrix.mcp_tools)}**")
    for name in sorted(matrix.mcp_tools):
        lines.append(f"  - `{name}`")
    lines.append(f"- Eval suites: **{len(matrix.eval_suites)}**")
    for suite in sorted(matrix.eval_suites):
        info = matrix.eval_suites[suite]
        lines.append(
            f"  - `{suite}`: enforce={info['enforce']}, "
            f"acceptance_enforce={info['acceptance_enforce']}, wired={info['wired']}"
        )
    lines.append(
        "- Additionally served but **out of the frozen contract** (footnote "
        "context, not guarded machinery): `agent_routes.py` 8 session routes, "
        "`connector_routes.py` 4 OAuth routes, `packaging/server.py` 2 inline "
        "probes -- ~36 total routes served by the sidecar."
    )
    lines.append("")

    lines.append("## MCP Scope Decision")
    lines.append("")
    lines.append(
        f"Tools: {', '.join(f'`{t}`' for t in sorted(MCP_SCOPE_DECISION['tools']))}"
    )
    lines.append("")
    lines.append(MCP_SCOPE_DECISION["rationale"])
    lines.append("")

    lines.append("## Eval Enforcement Status & Follow-up Plan")
    lines.append("")
    for suite in sorted(EVAL_FOLLOWUP_PLAN):
        info = matrix.eval_suites.get(suite, {})
        lines.append(
            f"### `{suite}` (enforce={info.get('enforce')}, "
            f"wired={info.get('wired')})"
        )
        lines.append("")
        lines.append(EVAL_FOLLOWUP_PLAN[suite])
        lines.append("")
    lines.append(
        "Wiring `followups` into CI (report script + workflow step) is "
        "tracked in #2040."
    )
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI -- mirrors export_openapi.py's build / --check idiom
# ---------------------------------------------------------------------------


def write_artifact(path: Path = ARTIFACT_PATH) -> Path:
    matrix = derive_matrix()
    path.write_text(render_markdown(matrix), encoding="utf-8")
    return path


def check_artifact(path: Path = ARTIFACT_PATH) -> bool:
    if not path.exists():
        return False
    matrix = derive_matrix()
    return path.read_text(encoding="utf-8") == render_markdown(matrix)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify the email agent capability matrix."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the committed artifact is stale (no write).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ARTIFACT_PATH,
        help=f"Artifact path (default: {ARTIFACT_PATH}).",
    )
    args = parser.parse_args(argv)

    if args.check:
        if check_artifact(args.output):
            print(f"Capability matrix up to date: {args.output}")
            return 0
        print(
            f"Capability matrix is STALE or missing: {args.output}\n"
            "Regenerate it with:  "
            "python hub/agents/python/email/packaging/capability_matrix.py",
            file=sys.stderr,
        )
        return 1

    written = write_artifact(args.output)
    print(f"Wrote capability matrix: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
