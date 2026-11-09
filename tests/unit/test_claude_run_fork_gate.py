# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``claude-run.yml`` checks out fork code under ``pull_request_target`` with secrets
in scope (``allow-unsafe-pr-checkout: true``). That is safe only while no step
after the checkout builds, installs or runs the checked-out tree unless gated to
same-repo PRs (``github.event.pull_request.head.repo.fork == false``). The
workflow states this as an INVARIANT comment; these tests enforce it (#2948).

A step is gated when its ``if:`` is a pure ``&&`` conjunction containing either
the fork check itself, or a positive check on the output/outcome of a step that
is already gated (a skipped step sets neither, so the gate carries over). Any
``||`` or negation voids the gate. ``anthropics/claude-code-action`` is not
treated as executing the tree: its Bash access is the accepted, documented risk
in claude.yml's SECURITY header, bounded by ``--allowedTools``.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "claude-run.yml"
CALLER = WORKFLOWS / "claude.yml"

FORK_GATE = "github.event.pull_request.head.repo.fork == false"

# Programs that build, install or run code (or read config) from the working tree.
_EXEC_PROGRAMS = (
    r"npm|npx|pnpm|yarn|node|bun|deno|pip|pip3|uv|uvx|poetry|pipx|make|cmake|"
    r"pytest|tox|nox|python|python3|gaia|claude|go|cargo|bash|sh|zsh|pwsh|source|\."
)
# A command starts at a line start, after a separator, or inside $( / backticks,
# optionally behind wrappers like sudo/env/timeout and VAR=value assignments.
_CMD_START = r"(?:^|&&|\|\||[;|(`]|\$\(|\bthen\b|\bdo\b|\belse\b)"
_PREFIX = (
    r"(?:\s*(?:sudo|env|exec|time|nohup|xargs|timeout\s+\S+)\b(?:\s+-\S+)*"
    r"|\s*\w+=\S*)*"
)
_EXEC_RE = re.compile(
    _CMD_START + _PREFIX + r"\s*(?:(?:" + _EXEC_PROGRAMS + r")(?=\s|$)|\.{0,2}/\S)",
    re.MULTILINE,
)
_SAFE_SHELLS = {None, "bash", "sh"}

_GATED_CONJUNCT = re.compile(
    r"^steps\.([\w-]+)\.(?:outputs\.[\w-]+\s*==\s*'[^']+'|"
    r"outcome\s*==\s*'(?:success|failure)')$"
)


def _strip_comments(script: str) -> str:
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


def _executes_tree(step: dict) -> bool:
    uses = step.get("uses") or ""
    if uses.startswith("./"):
        return True  # a local action is read from the checked-out tree
    if "run" not in step:
        return False
    if step.get("shell") not in _SAFE_SHELLS:
        return True  # python/node/pwsh shells resolve imports from the workspace
    return bool(_EXEC_RE.search(_strip_comments(str(step["run"]))))


def _normalize_if(expr) -> str:
    text = " ".join(str(expr or "").split())
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    return text


def _split_top_level(text: str, op: str) -> list[str]:
    """Split on ``op`` outside parentheses and single-quoted strings."""
    parts, depth, quoted, start, i = [], 0, False, 0, 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch in "()":
            depth += 1 if ch == "(" else -1
        elif not quoted and depth == 0 and text.startswith(op, i):
            parts.append(text[start:i].strip())
            start = i = i + len(op)
            continue
        i += 1
    parts.append(text[start:].strip())
    return parts


def _unwrap(text: str) -> str:
    """Drop parentheses that enclose the whole expression, e.g. ``(a && b)``."""
    while text.startswith("(") and text.endswith(")"):
        depth, quoted = 0, False
        for i, ch in enumerate(text):
            if ch == "'":
                quoted = not quoted
            elif not quoted and ch in "()":
                depth += 1 if ch == "(" else -1
                if depth == 0 and i < len(text) - 1:
                    return text  # "(a) && (b)": the first paren closes early
        text = text[1:-1].strip()
    return text


def _is_gated(expr, gated_ids: set[str]) -> bool:
    text = _unwrap(_normalize_if(expr))
    if not text or len(_split_top_level(text, "||")) > 1:
        return False
    for conjunct in map(_unwrap, _split_top_level(text, "&&")):
        if conjunct == FORK_GATE:
            return True
        m = _GATED_CONJUNCT.match(conjunct)
        if m and m.group(1) in gated_ids:
            return True
        if conjunct != text and _is_gated(conjunct, gated_ids):
            return True
    return False


def fork_gate_violations(workflow: dict) -> list[str]:
    """Steps after the first checkout that execute the tree without the fork gate."""
    problems = []
    for job_name, job in (workflow.get("jobs") or {}).items():
        gated_ids: set[str] = set()
        checked_out = False
        for i, step in enumerate(job.get("steps") or []):
            label = f"{job_name} step {i} ({step.get('name') or step.get('id')!r})"
            gated = _is_gated(step.get("if"), gated_ids)
            if gated and step.get("id"):
                gated_ids.add(step["id"])
            if (step.get("uses") or "").startswith("actions/checkout"):
                checked_out = True
                continue
            if checked_out and _executes_tree(step) and not gated:
                problems.append(label)
    return problems


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --- the live workflow ------------------------------------------------------


def test_claude_run_has_no_ungated_step_that_runs_the_checkout():
    assert fork_gate_violations(_load(WORKFLOW)) == [], (
        "claude-run.yml runs checked-out (possibly fork) code without "
        f"`{FORK_GATE}` in the step's `if:` — see the INVARIANT at its PR checkout."
    )


def test_the_guard_actually_sees_the_evidence_stage():
    """If the classifier went blind, the live test above would pass vacuously."""
    steps = _load(WORKFLOW)["jobs"]["run"]["steps"]
    by_id = {s.get("id"): s for s in steps}
    for step_id in ("evidence_install", "evidence", "evidence_secondary"):
        assert _executes_tree(by_id[step_id]), step_id
    assert _is_gated(by_id["surface"]["if"], set())


def test_only_claude_run_opts_into_unsafe_pr_checkout():
    offenders = sorted(
        p.name
        for p in WORKFLOWS.glob("*.y*ml")
        if p != WORKFLOW and "allow-unsafe-pr-checkout" in p.read_text(encoding="utf-8")
    )
    assert offenders == [], (
        f"{offenders} opt into fork checkout under a privileged trigger; only "
        "claude-run.yml is guarded by this test."
    )


def test_evidence_is_requested_only_by_pull_request_target_callers():
    # Under any other event github.event.pull_request is null, and in Actions
    # expressions `null == false` is true, so the fork gate alone would pass.
    for name, job in _load(CALLER)["jobs"].items():
        if not str(job.get("uses", "")).endswith("claude-run.yml"):
            continue
        if not (job.get("with") or {}).get("generate_evidence"):
            continue
        cond = _unwrap(_normalize_if(job.get("if")))
        conjuncts = list(map(_unwrap, _split_top_level(cond, "&&")))
        assert len(_split_top_level(cond, "||")) == 1 and (
            "github.event_name == 'pull_request_target'" in conjuncts
        ), f"claude.yml job {name!r} requests evidence outside pull_request_target"


# --- the guard catches violations (mutated copies, never the live file) -----

_RUN_PREFIX_GATE = "github.event_name == 'pull_request_target'"


@pytest.fixture
def workflow() -> dict:
    return copy.deepcopy(_load(WORKFLOW))


def _inject(tmp_path: Path, workflow: dict, step: dict, after_id: str = "surface"):
    steps = workflow["jobs"]["run"]["steps"]
    idx = next(i for i, s in enumerate(steps) if s.get("id") == after_id)
    steps.insert(idx + 1, step)
    path = tmp_path / "claude-run.yml"
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    return fork_gate_violations(_load(path))


@pytest.mark.parametrize(
    "step",
    [
        {"name": "x", "run": 'pip install -e ".[dev]"'},
        {"name": "x", "if": _RUN_PREFIX_GATE, "run": "npm ci"},
        {"name": "x", "run": "echo start && python -m pytest tests/"},
        {"name": "x", "run": "set -e\nmake build"},
        {"name": "x", "run": "./scripts/setup.sh"},
        {"name": "x", "run": "bash scripts/setup.sh"},
        {"name": "x", "run": "out=$(node scripts/x.js)"},
        {"name": "x", "run": "sudo -E env FOO=1 uv sync"},
        {"name": "x", "run": "if true; then gaia init; fi"},
        {"name": "x", "uses": "./.github/actions/free-disk-space"},
        {"name": "x", "shell": "python {0}", "run": "import gaia"},
        {"name": "x", "if": f"{FORK_GATE} || always()", "run": "pip install ."},
        {"name": "x", "if": f"!({FORK_GATE})", "run": "pip install ."},
        {
            "name": "x",
            "if": "github.event.pull_request.head.repo.fork != true",
            "run": "pip install .",
        },
        {
            "name": "x",
            "if": "steps.surface.outputs.testable != 'true'",
            "run": "pip install .",
        },
        {
            "name": "x",
            "if": "steps.surface.outcome == 'skipped'",
            "run": "pip install .",
        },
        {"name": "x", "if": "steps.post.outcome == 'success'", "run": "pip install ."},
        {"name": "x", "if": f"({FORK_GATE} || always())", "run": "pip install ."},
        {"name": "x", "if": f"(always()) || ({FORK_GATE})", "run": "pip install ."},
    ],
    ids=[
        "pip-ungated",
        "npm-event-only-gate",
        "pytest-after-&&",
        "make-multiline",
        "relative-script",
        "bash-script",
        "node-in-subst",
        "uv-behind-sudo-env",
        "gaia-in-then",
        "local-action",
        "python-shell",
        "or-bypass",
        "negated",
        "fork-!=-true",
        "negated-output",
        "skipped-outcome",
        "ungated-dependency",
        "wrapped-or-bypass",
        "parenthesized-or-bypass",
    ],
)
def test_guard_flags_a_violating_step(tmp_path, workflow, step):
    assert _inject(tmp_path, workflow, step) == ["run step 8 ('x')"]


@pytest.mark.parametrize(
    "step",
    [
        {"name": "x", "if": FORK_GATE, "run": "pip install ."},
        {
            "name": "x",
            "if": "${{ inputs.generate_evidence && " + FORK_GATE + " }}",
            "run": "npm ci",
        },
        {"name": "x", "if": f"!cancelled() && ({FORK_GATE})", "run": "make"},
        {"name": "x", "if": f"{FORK_GATE} && (inputs.a || inputs.b)", "run": "make"},
        {
            "name": "x",
            "if": "steps.surface.outputs.testable == 'true'",
            "run": "pytest",
        },
        {"name": "x", "run": "# pip install -e .\ngit diff origin/main...HEAD"},
        {"name": "x", "run": 'echo "run npm ci, then make" >> "$GITHUB_STEP_SUMMARY"'},
        {"name": "x", "run": "jq -r .x < pr-files.txt | grep -c src/"},
        {"name": "x", "uses": "actions/upload-artifact@v7"},
    ],
    ids=[
        "fork-gate",
        "wrapped-expression",
        "parenthesized-gate",
        "gate-and-inner-or",
        "transitive-gate",
        "comment-only",
        "words-in-echo",
        "jq-grep",
        "remote-action",
    ],
)
def test_guard_accepts_a_safe_step(tmp_path, workflow, step):
    assert _inject(tmp_path, workflow, step) == []


def test_guard_flags_a_step_gated_on_an_ungated_injected_step(tmp_path, workflow):
    steps = workflow["jobs"]["run"]["steps"]
    steps.insert(1, {"name": "probe", "id": "probe", "run": "echo ok=true"})
    path = tmp_path / "claude-run.yml"
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    violations = _inject(
        tmp_path,
        _load(path),
        {"name": "x", "if": "steps.probe.outputs.ok == 'true'", "run": "pip install ."},
    )
    assert violations == ["run step 9 ('x')"]
