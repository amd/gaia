#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Code-derived capability matrix for the flagship GAIA agent.

Mirrors ``hub/agents/email/python/packaging/capability_matrix.py`` (#2013):
several surfaces exist (the registered agent-loop tool inventory, the REST
sidecar API, and the eval suites) and nothing else guarantees they describe the
same agent. This module introspects each surface directly from source/config —
never from memory, never re-typed by hand — and renders the result to a
committed ``CAPABILITY_MATRIX.md`` that CI diffs against a fresh regeneration.

Placement note: this file lives in ``packaging/``, NOT inside the ``gaia_agent``
package. ``packaging/freeze.py`` does a blanket ``--collect-submodules
gaia_agent`` when building the frozen sidecar binary, so any module under the
package ships in the shipped artifact. This is a dev/CI tool that reads
repo-root ``tests/fixtures/`` and a sibling hub package — it must never ship.
``packaging/`` has no ``__init__.py`` by design, so invoke by **script path**,
never ``-m``::

    python hub/agents/gaia/python/packaging/capability_matrix.py           # write
    python hub/agents/gaia/python/packaging/capability_matrix.py --check   # CI drift check

Surface mechanisms (one per surface, no fallback hedges):

- Registered tools — a pure AST parse of the flagship's tool source of truth:
  ``gaia_agent_chat.tool_bundles``'s ``FULL_CORE_TOOLS`` ∪ every
  ``FULL_BUNDLES`` member. The full-profile CORE ∪ bundles must equal the
  registry exactly (drift-guarded by ``tests/unit/test_chat_tool_bundles.py``
  and this package's ``tests/test_gaia_agent.py``, which builds the live
  agent), so the union IS the registered surface — without instantiating an
  agent or touching the env-dependent ``_TOOL_REGISTRY``.
- REST — a pure AST parse of ``gaia_agent/server.py``'s route decorators
  (``@router.get/post`` plus the ``build_app`` probe routes). No import, no
  FastAPI app construction.
- Evals — ``sorted(glob(<repo_root>/tests/fixtures/gaia/*_gate_thresholds.json))``
  plus the ``eval/scenarios/gaia_*`` category glob. Both are empty today —
  rendered honestly as "no eval suites wired yet", never invented.

There is no MCP surface: ``gaia-agent.yaml`` declares ``mcp_server: false``
(the flagship is the host loop that *consumes* MCP servers, not one exposed as
an MCP tool). That declaration is read from the manifest and pinned.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Tuple

# Repo root via the fixed hop chain:
# packaging/ -> python/ -> gaia/ -> agents/ -> hub/ -> repo root.
_PACKAGING_DIR = Path(__file__).resolve().parent
_PYTHON_ROOT = _PACKAGING_DIR.parent
_AGENT_ROOT = _PYTHON_ROOT.parent
_REPO_ROOT = _AGENT_ROOT.parent.parent.parent

_TOOL_BUNDLES_PATH = (
    _REPO_ROOT
    / "hub"
    / "agents"
    / "chat"
    / "python"
    / "gaia_agent_chat"
    / "tool_bundles.py"
)
_SKILL_LIBRARY_TOOLS_PATH = (
    _REPO_ROOT / "src" / "gaia" / "agents" / "tools" / "skill_library_tools.py"
)
_SERVER_PATH = _PYTHON_ROOT / "gaia_agent" / "server.py"
_INIT_PATH = _PYTHON_ROOT / "gaia_agent" / "__init__.py"
_GAIA_AGENT_YAML = _PYTHON_ROOT / "gaia-agent.yaml"
_GATE_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "gaia"
_SCENARIOS_DIR = _REPO_ROOT / "eval" / "scenarios"

# Committed, generated artifact — at the agent root so the hub page, npm docs,
# and python package all sit beside one honest inventory.
ARTIFACT_PATH = _AGENT_ROOT / "CAPABILITY_MATRIX.md"

# REST ops that are probe/utility endpoints: excluded from the functional-verb
# count, still counted in the frozen contract's total.
_PROBE_OPS = frozenset({"init", "/health", "/version", "/v1/gaia/version"})

_NO_EVAL_SENTINEL = "no quality eval (contract-tested only)"

# Template, not a finished string — the surface counts are derived, so a
# literal here silently drifts from what this file computes.
TOOLS_COUNT_DEFINITION = (
    "tools_count = the number of registered agent-loop tools for the default "
    "construction (prompt_profile='full', memory available): FULL_CORE_TOOLS "
    "unioned with every FULL_BUNDLES member in gaia_agent_chat.tool_bundles "
    "(which the flagship's registry must equal exactly, including the 8 "
    "skill-library tools, the 4 code-index tools, and the load_tools escape "
    "hatch). This is the REGISTERED size — what the agent can do. Dynamic tool "
    "loading means a single turn only shows the model a subset of it, and it "
    "is distinct from the REST surface's {rest_functional} functional verbs, "
    "a purpose-built streaming facade for external callers."
)


def tools_count_definition(rest_functional: int) -> str:
    """Render :data:`TOOLS_COUNT_DEFINITION` against the derived surface counts."""
    return TOOLS_COUNT_DEFINITION.format(rest_functional=rest_functional)


# Every exposed REST functional op -> the eval suite that exercises it for
# quality, or the sentinel meaning "only contract/shape-tested". Op names
# mirror ``_derive_rest``'s scheme: the route path after the ``/v1/gaia``
# prefix, without a leading slash. All three carry the sentinel today — the
# sequence pins in ``eval_baselines/query_sequences/`` shape-test the stream,
# and the judged suites land with the gaia eval corpus (plan phases 2-4).
OP_EVAL_COVERAGE: Dict[str, str] = {
    "query": _NO_EVAL_SENTINEL,
    "query/{run_id}/cancel": _NO_EVAL_SENTINEL,
    "query/{run_id}/respond": _NO_EVAL_SENTINEL,
}

# The no-MCP decision is deliberate, not an oversight — pinned so it cannot
# silently regress. Kept in agreement with gaia-agent.yaml's interfaces block.
MCP_SCOPE_DECISION = {
    "mcp_server": False,
    "rationale": (
        "The flagship is the host loop that CONSUMES MCP servers (the "
        "manifest declares consumes_mcp_servers, and connector-activated MCP "
        "tools register into its own registry at runtime) — it is not itself "
        "exposed as an MCP tool. An orchestrating model that wants the "
        "flagship as a tool drives the REST /query surface or the stdio "
        "transport instead."
    ),
}

# What happens next for the (currently empty) eval-suite surface. State that is
# derivable from fixtures is derived and rendered next to this prose, never
# duplicated inside it.
EVAL_FOLLOWUP_PLAN = (
    "No judged eval suite is wired yet — the deterministic tier (this matrix, "
    "the binary-gate pins, the package's contract tests) and the committed SSE "
    "sequence pins under eval_baselines/query_sequences/ are the current "
    "coverage. Follow-up: the gaia eval corpus (eval/scenarios/gaia_* "
    "categories driven by `gaia eval agent --agent-type gaia`) lands with its "
    "gate-threshold manifests under tests/fixtures/gaia/, at which point this "
    "matrix derives per-suite enforce flags exactly as the email agent's does."
)


@dataclass
class CapabilityMatrix:
    """Small, lean introspection result — a plain container, no ceremony."""

    tools_total: int
    core_tools: FrozenSet[str]
    bundles: Dict[str, FrozenSet[str]]
    skill_library_tools: Tuple[str, ...]
    rest_functional_count: int
    rest_in_contract_count: int
    rest_op_names: List[str]
    mcp_server_declared: bool
    eval_suites: Dict[str, dict] = field(default_factory=dict)
    scenario_categories: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Surface 1: registered tools (AST over tool_bundles.py, no agent construction)
# ---------------------------------------------------------------------------


def _string_constants(node: ast.AST) -> FrozenSet[str]:
    return frozenset(
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


def derive_full_profile_tools(
    tool_bundles_path: Path = _TOOL_BUNDLES_PATH,
) -> Tuple[FrozenSet[str], Dict[str, FrozenSet[str]]]:
    """``(FULL_CORE_TOOLS, {bundle_name: members})`` — pure AST, no import."""
    tree = ast.parse(
        tool_bundles_path.read_text(encoding="utf-8"), filename=str(tool_bundles_path)
    )
    core: FrozenSet[str] | None = None
    bundles: Dict[str, FrozenSet[str]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            continue
        name = node.targets[0].id
        if name == "FULL_CORE_TOOLS":
            core = _string_constants(node.value)
        elif name == "FULL_BUNDLES":
            if not isinstance(node.value, ast.List):
                raise ValueError(
                    f"FULL_BUNDLES in {tool_bundles_path} is not a list literal"
                )
            for call in node.value.elts:
                if not isinstance(call, ast.Call):
                    raise ValueError(
                        f"FULL_BUNDLES entry in {tool_bundles_path} is not a "
                        "ToolBundle(...) call"
                    )
                kwargs = {k.arg: k.value for k in call.keywords}
                if "name" not in kwargs or "members" not in kwargs:
                    raise ValueError(
                        f"a FULL_BUNDLES ToolBundle in {tool_bundles_path} lacks "
                        "name= or members="
                    )
                bundle_name = kwargs["name"].value
                bundles[bundle_name] = _string_constants(kwargs["members"])
    if core is None or not bundles:
        raise ValueError(
            f"could not find FULL_CORE_TOOLS and FULL_BUNDLES in "
            f"{tool_bundles_path} — the flagship's tool source of truth moved; "
            "update this generator's parse."
        )
    return core, bundles


def _derive_skill_library_tool_names(
    path: Path = _SKILL_LIBRARY_TOOLS_PATH,
) -> Tuple[str, ...]:
    """AST-extract ``SKILL_LIBRARY_TOOL_NAMES`` — the framework's own pinned
    list of the 8 skill-library tools, cross-checked into the bundle union."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "SKILL_LIBRARY_TOOL_NAMES"
        ):
            return tuple(
                n.value
                for n in ast.walk(node.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            )
    raise ValueError(f"no SKILL_LIBRARY_TOOL_NAMES assignment found in {path}")


# ---------------------------------------------------------------------------
# Surface 2: REST — pure AST parse of server.py's route decorators
# ---------------------------------------------------------------------------


def _decorator_route(dec: ast.expr, module_constants: Dict[str, str]):
    """``(owner, method, path)`` for a ``@router/app.<method>("...")`` decorator,
    or ``None``. Resolves f-string paths against module-level string constants
    (the ``f"/v1/{AGENT_ID}/version"`` probe)."""
    if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
        return None
    func = dec.func
    if not (
        isinstance(func.value, ast.Name)
        and func.value.id in {"router", "app"}
        and func.attr in {"get", "post", "put", "delete", "patch"}
    ):
        return None
    if not dec.args:
        return None
    path_node = dec.args[0]
    if isinstance(path_node, ast.Constant) and isinstance(path_node.value, str):
        path = path_node.value
    elif isinstance(path_node, ast.JoinedStr):
        parts: List[str] = []
        for value in path_node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue) and isinstance(
                value.value, ast.Name
            ):
                name = value.value.id
                if name not in module_constants:
                    raise ValueError(
                        f"route decorator interpolates {name!r}, which is not a "
                        "module-level string constant in server.py — update the "
                        "matrix parser."
                    )
                parts.append(module_constants[name])
            else:
                raise ValueError(
                    "route decorator uses an f-string this parser cannot "
                    "resolve — update the matrix parser."
                )
        path = "".join(parts)
    else:
        raise ValueError(
            "route decorator path is neither a string literal nor a resolvable "
            "f-string — update the matrix parser."
        )
    return func.value.id, func.attr.upper(), path


def _derive_rest(server_path: Path = _SERVER_PATH):
    """``(functional_count, in_contract_count, op_names)`` from server.py.

    Router routes are served under the ``/v1/gaia`` prefix and named by their
    decorator path without the leading slash; ``build_app``'s inline probe
    routes keep their full path. Probes are excluded from the functional count.
    """
    tree = ast.parse(server_path.read_text(encoding="utf-8"), filename=str(server_path))
    module_constants = {
        node.targets[0].id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }

    ops: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            route = _decorator_route(dec, module_constants)
            if route is None:
                continue
            owner, _method, path = route
            ops.append(path.lstrip("/") if owner == "router" else path)

    if not ops:
        raise ValueError(f"no route decorators found in {server_path}")

    in_contract_count = len(ops)
    functional = sorted(op for op in ops if op not in _PROBE_OPS)
    return len(functional), in_contract_count, functional


# ---------------------------------------------------------------------------
# Surface 3: no MCP — pinned from the manifest, raw-text (no yaml dependency)
# ---------------------------------------------------------------------------


def _read_manifest_flag(key: str, manifest_path: Path = _GAIA_AGENT_YAML) -> bool:
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            value = stripped.split(":", 1)[1].strip().lower()
            if value in {"true", "false"}:
                return value == "true"
            raise ValueError(
                f"{key!r} in {manifest_path} is {value!r}, expected true/false"
            )
    raise ValueError(f"no {key!r} line found in {manifest_path}")


# ---------------------------------------------------------------------------
# Surface 4: eval suites + scenario categories — globs, honestly empty today
# ---------------------------------------------------------------------------


def _derive_eval_suites(gate_fixtures_dir: Path) -> Dict[str, dict]:
    """Gate-threshold fixtures under tests/fixtures/gaia/. An absent directory
    or empty glob means no eval suite is wired yet — rendered as exactly that,
    never invented and never an error."""
    suites: Dict[str, dict] = {}
    if not gate_fixtures_dir.is_dir():
        return suites
    suffix = "_gate_thresholds.json"
    for path in sorted(gate_fixtures_dir.glob(f"*{suffix}")):
        suite_name = path.name[: -len(suffix)]
        data = json.loads(path.read_text(encoding="utf-8"))
        suites[suite_name] = {
            "enforce": bool(data.get("enforce", False)),
            "acceptance_enforce": data.get("acceptance_enforce"),
        }
    return suites


def _derive_scenario_categories(scenarios_dir: Path = _SCENARIOS_DIR) -> List[str]:
    """``eval/scenarios/gaia_*`` category directories (none yet — plan phase 2)."""
    if not scenarios_dir.is_dir():
        return []
    return sorted(p.name for p in scenarios_dir.glob("gaia_*") if p.is_dir())


# ---------------------------------------------------------------------------
# Reconciliation guard — three independent sources must agree, and a mismatch
# names the offending values rather than failing silently.
# ---------------------------------------------------------------------------


def reconcile_tools_count(
    *, manifest_count: int, registration_count: int, ast_count: int
) -> int:
    """Return the agreed ``tools_count`` if all three sources match.

    Raises ``ValueError`` naming every value when ``gaia-agent.yaml``,
    ``__init__.py``'s ``AgentRegistration(tools_count=...)``, and the
    AST-derived bundle union disagree.
    """
    values = {
        "gaia-agent.yaml": manifest_count,
        "__init__.py AgentRegistration()": registration_count,
        "AST-derived (FULL_CORE_TOOLS | FULL_BUNDLES)": ast_count,
    }
    distinct = set(values.values())
    if len(distinct) != 1:
        detail = ", ".join(f"{name}={val}" for name, val in values.items())
        raise ValueError(f"tools_count sources disagree: {detail}")
    return distinct.pop()


def _read_manifest_tools_count(manifest_path: Path = _GAIA_AGENT_YAML) -> int:
    """Read the ``tools_count`` literal from gaia-agent.yaml (raw-text parse —
    no yaml dependency in this packaging script). Fails loud when absent."""
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("tools_count:"):
            return int(line.split(":", 1)[1].strip())
    raise ValueError(f"no 'tools_count:' line found in {manifest_path}")


def _read_registration_tools_count(init_path: Path = _INIT_PATH) -> int:
    """AST-extract the ``tools_count=<int>`` keyword from ``__init__.py``'s
    ``AgentRegistration(...)`` call — static, no package import needed."""
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
        f"no tools_count=<int> keyword found in {init_path} — "
        "build_gaia() must declare it"
    )


# ---------------------------------------------------------------------------
# Top-level derivation
# ---------------------------------------------------------------------------


def derive_matrix(repo_root: Path | None = None) -> CapabilityMatrix:
    """Introspect every surface live from source and return the result.

    ``repo_root`` is accepted for testability (pointing the fixture/scenario
    globs at a synthetic tree); the tool/REST/manifest paths always resolve
    from this module's own location — the fixed hop chain, never a walk-up.
    """
    core, bundles = derive_full_profile_tools()
    union: set = set(core)
    for members in bundles.values():
        union |= members
    tools_total = len(union)

    skill_library_tools = _derive_skill_library_tool_names()
    missing = set(skill_library_tools) - union
    if missing:
        raise ValueError(
            f"SKILL_LIBRARY_TOOL_NAMES entries {sorted(missing)} are not in the "
            "full-profile bundle union — the skills/skill_hub bundles in "
            "gaia_agent_chat.tool_bundles drifted from the framework mixin."
        )

    # Fail loud at generation/--check time too, not only under pytest: a
    # tools_count drift must never render a matrix that papers over it.
    reconcile_tools_count(
        manifest_count=_read_manifest_tools_count(),
        registration_count=_read_registration_tools_count(),
        ast_count=tools_total,
    )

    rest_functional_count, rest_in_contract_count, rest_op_names = _derive_rest()

    # Closed-set, bidirectional: a new REST op must land in OP_EVAL_COVERAGE
    # (with a suite name or the sentinel) before the matrix will render at all.
    if set(OP_EVAL_COVERAGE) != set(rest_op_names):
        raise ValueError(
            "OP_EVAL_COVERAGE and the derived REST surface disagree: "
            f"only-in-coverage={sorted(set(OP_EVAL_COVERAGE) - set(rest_op_names))}, "
            f"only-in-server={sorted(set(rest_op_names) - set(OP_EVAL_COVERAGE))}. "
            "Annotate every functional op with a suite name or the sentinel."
        )

    mcp_server_declared = _read_manifest_flag("mcp_server")
    if mcp_server_declared != MCP_SCOPE_DECISION["mcp_server"]:
        raise ValueError(
            "gaia-agent.yaml's interfaces.mcp_server no longer matches the "
            "pinned MCP_SCOPE_DECISION — a real MCP surface appeared (or "
            "vanished); update the decision and this matrix together."
        )

    gate_fixtures_dir = (
        (repo_root / "tests" / "fixtures" / "gaia")
        if repo_root is not None
        else _GATE_FIXTURES_DIR
    )
    scenarios_dir = (
        (repo_root / "eval" / "scenarios") if repo_root is not None else _SCENARIOS_DIR
    )

    return CapabilityMatrix(
        tools_total=tools_total,
        core_tools=frozenset(core),
        bundles=bundles,
        skill_library_tools=skill_library_tools,
        rest_functional_count=rest_functional_count,
        rest_in_contract_count=rest_in_contract_count,
        rest_op_names=rest_op_names,
        mcp_server_declared=mcp_server_declared,
        eval_suites=_derive_eval_suites(gate_fixtures_dir),
        scenario_categories=_derive_scenario_categories(scenarios_dir),
    )


# ---------------------------------------------------------------------------
# Rendering — pinned newline + utf-8, every enumerated list sorted (avoids
# cross-platform freshness flakes, mirrors the email generator).
# ---------------------------------------------------------------------------


def render_markdown(matrix: CapabilityMatrix) -> str:
    lines: List[str] = []
    lines.append(
        "<!-- Generated by python/packaging/capability_matrix.py -- do not edit by hand. -->"
    )
    lines.append("# GAIA Flagship Agent Capability Matrix")
    lines.append("")
    lines.append(
        "Code-derived surface inventory for the flagship `gaia` agent. "
        "Regenerate with:"
    )
    lines.append("")
    lines.append("```")
    lines.append("python hub/agents/gaia/python/packaging/capability_matrix.py")
    lines.append("```")
    lines.append("")

    lines.append("## Definitions")
    lines.append("")
    definition_body = tools_count_definition(matrix.rest_functional_count).removeprefix(
        "tools_count = "
    )
    lines.append(f"- **tools_count**: {definition_body}")
    lines.append(
        f"- **no quality eval sentinel**: `{_NO_EVAL_SENTINEL}` -- the op is "
        "contract/shape-tested only; no judged quality bar exists for it."
    )
    lines.append("")

    lines.append("## Capability matrix")
    lines.append("")
    lines.append(
        f"{matrix.rest_functional_count} exposed ops "
        f"({matrix.rest_functional_count} REST functional, no MCP surface) "
        "and their eval coverage:"
    )
    lines.append("")
    lines.append("| Op | Surface | Eval coverage |")
    lines.append("|---|---|---|")
    for op in sorted(OP_EVAL_COVERAGE):
        lines.append(f"| `{op}` | REST | {OP_EVAL_COVERAGE[op]} |")
    lines.append("")
    lines.append(
        "The committed SSE sequence pins under "
        "`python/eval_baselines/query_sequences/` shape-test the `/query` "
        "stream (canonical event vocabulary, ordering, single terminal) via "
        "`gaia.eval.sidecar_harness` — contract coverage, not a judged "
        "quality bar."
    )
    lines.append("")

    lines.append("## Surface totals")
    lines.append("")
    lines.append(
        f"- Registered agent-loop tools: **{matrix.tools_total}** "
        f"(CORE {len(matrix.core_tools)} + {len(matrix.bundles)} bundles; "
        "bundles overlap CORE and each other by design, so per-bundle counts "
        "sum past the unique total)"
    )
    for bundle in sorted(matrix.bundles):
        lines.append(f"  - `{bundle}`: {len(matrix.bundles[bundle])}")
    lines.append(
        f"- Skill-library tools (framework `SKILL_LIBRARY_TOOL_NAMES`, all in "
        f"the union): **{len(matrix.skill_library_tools)}**"
    )
    for name in sorted(matrix.skill_library_tools):
        lines.append(f"  - `{name}`")
    lines.append(
        f"- REST functional verbs: **{matrix.rest_functional_count}** "
        f"({matrix.rest_in_contract_count} total operations in the sidecar "
        "contract, including the health/version/init probes)"
    )
    for op in matrix.rest_op_names:
        lines.append(f"  - `{op}`")
    lines.append("- MCP tools: **0** (see MCP Scope Decision)")
    lines.append(f"- Eval suites: **{len(matrix.eval_suites)}**")
    if matrix.eval_suites:
        for suite in sorted(matrix.eval_suites):
            info = matrix.eval_suites[suite]
            lines.append(
                f"  - `{suite}`: enforce={info['enforce']}, "
                f"acceptance_enforce={info['acceptance_enforce']}"
            )
    else:
        lines.append(
            "  - no eval suites wired yet (no "
            "`tests/fixtures/gaia/*_gate_thresholds.json` fixtures exist)"
        )
    lines.append(
        f"- Judged scenario categories (`eval/scenarios/gaia_*`): "
        f"**{len(matrix.scenario_categories)}**"
    )
    for category in matrix.scenario_categories:
        lines.append(f"  - `{category}`")
    lines.append("")

    lines.append("## MCP Scope Decision")
    lines.append("")
    lines.append(f"`interfaces.mcp_server: {str(matrix.mcp_server_declared).lower()}`")
    lines.append("")
    lines.append(MCP_SCOPE_DECISION["rationale"])
    lines.append("")

    lines.append("## Eval Enforcement Status & Follow-up Plan")
    lines.append("")
    lines.append(EVAL_FOLLOWUP_PLAN)
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI — mirrors the email generator's build / --check idiom
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
        description="Generate or verify the flagship gaia agent capability matrix."
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
            "python hub/agents/gaia/python/packaging/capability_matrix.py",
            file=sys.stderr,
        )
        return 1

    written = write_artifact(args.output)
    print(f"Wrote capability matrix: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
