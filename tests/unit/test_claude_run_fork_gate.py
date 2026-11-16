# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``claude-run.yml`` checks out fork code under ``pull_request_target`` with secrets
in scope (``allow-unsafe-pr-checkout: true``). That is safe only while no step
after the checkout builds, installs or runs the checked-out tree unless gated to
same-repo ``pull_request_target`` PRs. The workflow states this as an INVARIANT
comment; these tests enforce it (#2948).

A step is gated when its ``if:`` is a pure ``&&`` conjunction that requires both
``github.event_name == 'pull_request_target'`` and
``github.event.pull_request.head.repo.fork == false``, or a positive check on the
output/outcome of a step that is already gated (a skipped step sets neither, so
the gate carries over). Any top-level ``||`` or a negation voids the gate.

After a checkout, every ``uses:`` must be on ``_ALLOWED_ACTIONS``; a local action
(``./...``, composite actions included) is read from the checked-out tree and is
never allowed. ``anthropics/claude-code-action`` is allowlisted rather than treated
as executing the tree: its Bash access is the accepted, documented risk in
claude.yml's SECURITY header, bounded by ``--allowedTools``.
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

FORK_GATE = "github.event.pull_request.head.repo.fork == false"
EVENT_GATE = "github.event_name == 'pull_request_target'"

# Remote actions allowed after a checkout. Anything else must be added here
# deliberately, by someone who has checked it does not run the workspace.
_ALLOWED_ACTIONS = re.compile(
    r"^(?:actions/checkout@\S+|actions/setup-python@\S+|actions/upload-artifact@\S+"
    r"|anthropics/claude-code-action@[0-9a-f]{40})$"
)

# Programs that build, install or run code (or read config) from the working tree.
_EXEC_PROGRAMS = (
    r"npm|npx|pnpm|yarn|node|bun|deno|pip(?:3(?:\.\d+)?)?|uv|uvx|poetry|pipx|"
    r"make|cmake|pytest|tox|nox|python(?:3(?:\.\d+)?)?|pre-commit|docker|gaia|"
    r"claude|go|cargo|bash|sh|zsh|pwsh|source|eval|\."
)
# Paths into the checkout: relative, or rooted at the workspace.
_WORKSPACE_PATH = (
    r"[\"']?(?:\.{0,2}/\S|\$\{?GITHUB_WORKSPACE\}?/|\$\{\{\s*github\.workspace\s*\}\}/)"
)
# A command starts at a line start, after a separator (; & && | || { ( `), or
# inside $( ... ).
_CMD_START = r"(?:^|[;&|(`{]|\$\(|\bthen\b|\bdo\b|\belse\b)"
# Keywords and wrappers that may sit in front of the command itself.
_PREFIX = (
    r"(?:\s*(?:!|\{)(?=\s)"
    r"|\s*(?:if|elif|while|until|sudo|env|exec|time|nohup|xargs|timeout\s+\S+)\b"
    r"(?:\s+-\S+)*"
    r"|\s*\w+=\S*)*"
)
_EXEC_RE = re.compile(
    _CMD_START
    + _PREFIX
    + r"\s*(?:(?:"
    + _EXEC_PROGRAMS
    + r")(?=\s|$|;)|"
    + _WORKSPACE_PATH
    + r")",
    re.MULTILINE,
)
_CHECKOUT_RUN_RE = re.compile(r"\b(?:gh\s+pr\s+checkout|git\s+(?:checkout|switch))\b")
_SAFE_SHELLS = {None, "bash", "sh"}

_GATED_CONJUNCT = re.compile(
    r"^steps\.([\w-]+)\.(?:outputs\.[\w-]+\s*==\s*'[^']+'|"
    r"outcome\s*==\s*'(?:success|failure)')$"
)


def _strip_comments(script: str) -> str:
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


def _runs_tree(step: dict) -> bool:
    if "run" not in step:
        return False
    if step.get("shell") not in _SAFE_SHELLS:
        return True  # python/node/pwsh shells resolve imports from the workspace
    return bool(_EXEC_RE.search(_strip_comments(str(step["run"]))))


def _checks_out(step: dict) -> bool:
    if (step.get("uses") or "").startswith("actions/checkout@"):
        return True
    return "run" in step and bool(
        _CHECKOUT_RUN_RE.search(_strip_comments(str(step["run"])))
    )


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


def _conjuncts(expr: str) -> list[str] | None:
    """Flattened top-level ``&&`` terms, or None when a top-level ``||`` exists."""
    text = _unwrap(expr)
    if len(_split_top_level(text, "||")) > 1:
        return None
    terms = []
    for part in map(_unwrap, _split_top_level(text, "&&")):
        inner = _split_top_level(part, "&&")
        if len(inner) > 1 and len(_split_top_level(part, "||")) == 1:
            terms.extend(_conjuncts(part) or [])
        else:
            terms.append(part)
    return terms


def _is_gated(expr, gated_ids: set[str]) -> bool:
    terms = _conjuncts(_normalize_if(expr))
    if not terms or terms == [""]:
        return False
    if FORK_GATE in terms and EVENT_GATE in terms:
        return True
    for term in terms:
        m = _GATED_CONJUNCT.match(term)
        if m and m.group(1) in gated_ids:
            return True
    return False


def fork_gate_violations(workflow: dict) -> list[str]:
    """Names of steps after a checkout that may run the tree without the gate."""
    problems = []
    for job in (workflow.get("jobs") or {}).values():
        gated_ids: set[str] = set()
        checked_out = False
        for step in job.get("steps") or []:
            name = step.get("name") or step.get("id") or "<unnamed>"
            gated = _is_gated(step.get("if"), gated_ids)
            if gated and step.get("id"):
                gated_ids.add(step["id"])
            checked_out = checked_out or _checks_out(step)
            if not checked_out:
                continue
            uses = step.get("uses") or ""
            if uses and not _ALLOWED_ACTIONS.match(uses):
                problems.append(name)
            elif _runs_tree(step) and not gated:
                problems.append(name)
    return problems


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _all_workflows() -> list[tuple[Path, dict]]:
    return [(p, _load(p)) for p in sorted(WORKFLOWS.glob("*.y*ml"))]


def _with_values(node, key: str):
    """Every value stored under ``key`` anywhere in a parsed YAML tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from _with_values(v, key)
    elif isinstance(node, list):
        for item in node:
            yield from _with_values(item, key)


# --- the live workflows -----------------------------------------------------


def test_claude_run_has_no_ungated_step_that_runs_the_checkout():
    assert fork_gate_violations(_load(WORKFLOW)) == [], (
        "claude-run.yml runs checked-out (possibly fork) code without requiring "
        f"`{EVENT_GATE}` and `{FORK_GATE}` in the step's `if:`, or uses an action "
        "not on _ALLOWED_ACTIONS — see the INVARIANT at its PR checkout."
    )


def test_the_guard_actually_sees_the_evidence_stage():
    """If the classifier went blind, the live test above would pass vacuously."""
    steps = _load(WORKFLOW)["jobs"]["run"]["steps"]
    by_id = {s.get("id"): s for s in steps}
    for step_id in ("evidence_install", "evidence", "evidence_secondary"):
        assert _runs_tree(by_id[step_id]), step_id
    assert _is_gated(by_id["surface"]["if"], set())


def test_only_claude_run_opts_into_unsafe_pr_checkout():
    offenders = sorted(
        p.name
        for p, wf in _all_workflows()
        if p != WORKFLOW
        and any(
            v not in (False, None) for v in _with_values(wf, "allow-unsafe-pr-checkout")
        )
    )
    assert offenders == [], (
        f"{offenders} opt into fork checkout under a privileged trigger; only "
        "claude-run.yml is guarded by this test."
    )


def test_evidence_is_requested_only_by_pull_request_target_callers():
    # The evidence stage is only designed for pull_request_target.
    for path, wf in _all_workflows():
        for name, job in (wf.get("jobs") or {}).items():
            if not str(job.get("uses", "")).endswith("claude-run.yml"):
                continue
            if not (job.get("with") or {}).get("generate_evidence"):
                continue
            terms = _conjuncts(_normalize_if(job.get("if"))) or []
            assert (
                EVENT_GATE in terms
            ), f"{path.name} job {name!r} requests evidence outside {EVENT_GATE}"


# --- the guard catches violations (mutated copies, never the live file) -----

INJECTED = "injected step"
GATE = f"{EVENT_GATE} && {FORK_GATE}"


@pytest.fixture
def workflow() -> dict:
    return copy.deepcopy(_load(WORKFLOW))


def _inject(tmp_path: Path, workflow: dict, step: dict, after_id: str = "surface"):
    steps = workflow["jobs"]["run"]["steps"]
    idx = next(i for i, s in enumerate(steps) if s.get("id") == after_id)
    steps.insert(idx + 1, {"name": INJECTED, **step})
    path = tmp_path / "claude-run.yml"
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    return fork_gate_violations(_load(path))


_VIOLATIONS = {
    "pip-ungated": {"run": 'pip install -e ".[dev]"'},
    "npm-event-only-gate": {"if": EVENT_GATE, "run": "npm ci"},
    "fork-gate-only": {"if": FORK_GATE, "run": "pip install ."},
    "pytest-after-&&": {"run": "echo start && python -m pytest tests/"},
    "make-multiline": {"run": "set -e\nmake build"},
    "relative-script": {"run": "./scripts/setup.sh"},
    "bash-script": {"run": "bash scripts/setup.sh"},
    "node-in-subst": {"run": "out=$(node scripts/x.js)"},
    "uv-behind-sudo-env": {"run": "sudo -E env FOO=1 uv sync"},
    "gaia-in-then": {"run": "if true; then gaia init; fi"},
    "if-condition": {"run": "if npm test; then echo ok; fi"},
    "while-condition": {"run": "while pytest; do sleep 1; done"},
    "negated-command": {"run": "! npm ci"},
    "brace-group": {"run": "{ npm ci; }"},
    "background-&": {"run": "sleep 1 & npm ci"},
    "eval": {"run": 'eval "$(cat x)"'},
    "workspace-env-path": {"run": '"$GITHUB_WORKSPACE/x.sh"'},
    "workspace-expr-path": {"run": "${{ github.workspace }}/x.sh"},
    "python3.12": {"run": "python3.12 setup.py build"},
    "pre-commit": {"run": "pre-commit run --all-files"},
    "docker-build": {"run": "docker build ."},
    "local-action": {"uses": "./.github/actions/setup-python"},
    "gated-local-action": {"if": GATE, "uses": "./.github/actions/setup-venv"},
    "unlisted-remote-action": {"uses": "actions/setup-node@v5"},
    "gated-unlisted-action": {"if": GATE, "uses": "pre-commit/action@v3"},
    "unpinned-claude-action": {"uses": "anthropics/claude-code-action@v1"},
    "python-shell": {"shell": "python {0}", "run": "import gaia"},
    "or-bypass": {"if": f"{GATE} || always()", "run": "pip install ."},
    "negated": {"if": f"!({GATE})", "run": "pip install ."},
    "fork-!=-true": {
        "if": f"{EVENT_GATE} && github.event.pull_request.head.repo.fork != true",
        "run": "pip install .",
    },
    "negated-output": {
        "if": "steps.surface.outputs.testable != 'true'",
        "run": "pip install .",
    },
    "skipped-outcome": {"if": "steps.surface.outcome == 'skipped'", "run": "pip ."},
    "ungated-dependency": {"if": "steps.post.outcome == 'success'", "run": "make"},
    "wrapped-or-bypass": {"if": f"({GATE} || always())", "run": "pip install ."},
    "parenthesized-or-bypass": {
        "if": f"(always()) || ({EVENT_GATE} && {FORK_GATE})",
        "run": "pip install .",
    },
}


@pytest.mark.parametrize("step", _VIOLATIONS.values(), ids=_VIOLATIONS.keys())
def test_guard_flags_a_violating_step(tmp_path, workflow, step):
    assert _inject(tmp_path, workflow, step) == [INJECTED]


_SAFE = {
    "event-and-fork-gate": {"if": GATE, "run": "pip install ."},
    "wrapped-expression": {
        "if": "${{ inputs.generate_evidence && " + GATE + " }}",
        "run": "npm ci",
    },
    "parenthesized-gate": {"if": f"!cancelled() && ({GATE})", "run": "make"},
    "gate-and-inner-or": {"if": f"{GATE} && (inputs.a || inputs.b)", "run": "make"},
    "transitive-gate": {"if": "steps.surface.outputs.testable == 'true'", "run": "tox"},
    "comment-only": {"run": "# pip install -e .\ngit diff origin/main...HEAD"},
    "words-in-echo": {"run": 'echo "run npm ci, then make" >> "$GITHUB_STEP_SUMMARY"'},
    "jq-grep": {"run": "jq -r .x < pr-files.txt | grep -c src/"},
    "allowlisted-action": {"uses": "actions/upload-artifact@v7"},
}


@pytest.mark.parametrize("step", _SAFE.values(), ids=_SAFE.keys())
def test_guard_accepts_a_safe_step(tmp_path, workflow, step):
    assert _inject(tmp_path, workflow, step) == []


def test_guard_flags_a_step_gated_on_an_ungated_injected_step(tmp_path, workflow):
    steps = workflow["jobs"]["run"]["steps"]
    steps.insert(1, {"name": "probe", "id": "probe", "run": "echo ok=true"})
    violations = _inject(
        tmp_path,
        workflow,
        {"if": "steps.probe.outputs.ok == 'true'", "run": "pip install ."},
    )
    assert violations == [INJECTED]


def test_guard_starts_watching_at_a_gh_pr_checkout():
    wf = {
        "jobs": {
            "j": {
                "steps": [
                    {"name": "before", "run": "npm ci"},
                    {"name": "checkout", "run": 'gh pr checkout "$PR"'},
                    {"name": INJECTED, "run": "npm ci"},
                ]
            }
        }
    }
    assert fork_gate_violations(wf) == [INJECTED]


def test_unsafe_checkout_check_ignores_comments():
    commented = yaml.safe_load(
        "jobs:\n  j:\n    steps:\n      - uses: actions/checkout@v7\n"
        "        # allow-unsafe-pr-checkout: true\n"
    )
    live = yaml.safe_load(
        "jobs:\n  j:\n    steps:\n      - uses: actions/checkout@v7\n"
        "        with:\n          allow-unsafe-pr-checkout: true\n"
    )
    assert list(_with_values(commented, "allow-unsafe-pr-checkout")) == []
    assert list(_with_values(live, "allow-unsafe-pr-checkout")) == [True]
