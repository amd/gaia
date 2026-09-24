# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Flag test files that no CI lane ever runs.

A test file nobody runs looks exactly like a test file that passes: it
contributes nothing to any `collected N items` line, so a green PR is green
whether the file is healthy or has been broken for months. #4121 found 43 such
files under `tests/`, and at least one had already rotted undetected.

This check answers one question per file: **does any workflow name a path that
would collect it?** A lane names a file either directly
(`pytest tests/unit/test_foo.py`, `python tests/mcp/test_bar.py`) or through an
ancestor directory (`pytest tests/unit/`, which pytest walks recursively). A
file matched by neither is reported, unless it is listed in
`util/test_lane_allowlist.yml` with a reason — deliberate non-coverage has to be
declared, not inferred from silence.

Scope and limits, stated so the output is not over-read:

- Only `run:` steps are scanned, so a `pytest ...` line quoted inside a prompt
  or a comment is correctly ignored.
- A path is credited only when it appears in a pytest or `python <file>.py`
  invocation. A `tests/...` token in an `echo`, `cp` or `rm` is not coverage.
- `${{ matrix.* }}` is expanded from the job's `strategy.matrix`. Any *other*
  unresolved `${{ }}` in a test path is reported as an error rather than
  skipped, because silently dropping it would overstate the gap.
- **Being named by a lane is not the same as being executed there.** A file that
  a lane collects and then skips wholesale — a module-scope
  `pytest.importorskip` for a dependency the lane never installs — counts as
  covered here and is invisible to this check. That is the second half of #4121
  and needs a separate `--collect-only` guard.
- Every workflow counts equally, including release-only ones. A file named only
  by a `workflow_dispatch` lane passes this check while never running on a PR.

Not wired into `util/lint.py`. Run directly with
`python util/check_test_lane_coverage.py`.
"""

from __future__ import annotations

import itertools
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence, Set, Tuple

import yaml

# Anchor to the repo root so the script works regardless of CWD — matches the
# convention in util/check_workflow_ancestor_skip.py.
REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
ACTION_DIR = REPO_ROOT / ".github" / "actions"
ALLOWLIST_PATH = REPO_ROOT / "util" / "test_lane_allowlist.yml"

# Directories walked for test files. `hub/agents/*/python/tests` is a glob
# because each packaged agent ships its own suite.
TEST_ROOT_GLOBS = ("tests", "hub/agents/*/python/tests")

_EXPRESSION_RE = re.compile(r"\$\{\{(.*?)\}\}")
_MATRIX_REF_RE = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}")
# Bash trailing "\" and PowerShell trailing "`" both continue a command.
_CONTINUATION_RE = re.compile(r"[\\`][ \t]*\r?\n[ \t]*")
_COMMAND_SPLIT_RE = re.compile(r"\r?\n|;|&&|\|\||\|")
_PYTHON_RE = re.compile(r"^python[0-9.]*(\.exe)?$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Workflow parsing
# ---------------------------------------------------------------------------


def _matrix_values(job: Dict[str, Any]) -> Dict[str, List[str]]:
    """Scalar values each `matrix.<key>` can take, from the axes and `include`."""
    strategy = job.get("strategy")
    if not isinstance(strategy, dict):
        return {}
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict):
        return {}

    values: Dict[str, List[str]] = {}
    for key, raw in matrix.items():
        if key in ("include", "exclude"):
            continue
        if isinstance(raw, list):
            values[key] = [str(v) for v in raw if isinstance(v, (str, int, float))]

    include = matrix.get("include")
    if isinstance(include, list):
        for entry in include:
            if not isinstance(entry, dict):
                continue
            for key, val in entry.items():
                if isinstance(val, (str, int, float)):
                    values.setdefault(key, []).append(str(val))

    return {k: sorted(set(v)) for k, v in values.items() if v}


def _expand_matrix(text: str, matrix: Dict[str, List[str]]) -> List[str]:
    """Every concrete string `text` can become once matrix refs are substituted."""
    keys = sorted({m.group(1) for m in _MATRIX_REF_RE.finditer(text)})
    resolvable = [k for k in keys if matrix.get(k)]
    if not resolvable:
        return [text]

    expanded = []
    for combo in itertools.product(*(matrix[k] for k in resolvable)):
        variant = text
        for key, value in zip(resolvable, combo):
            variant = re.sub(
                r"\$\{\{\s*matrix\." + re.escape(key) + r"\s*\}\}", value, variant
            )
        expanded.append(variant)
    return expanded


def _iter_run_blocks(path: Path) -> Iterator[Tuple[str, Dict[str, List[str]]]]:
    """Yield (run_script, matrix_values) for every `run:` step in a YAML file.

    Handles both workflow files (`jobs.<id>.steps`) and composite actions
    (`runs.steps`). Raises on unparseable YAML — a workflow this cannot read is
    a hole in the audit, not something to skip past.
    """
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path.name}: failed to parse: {exc}") from exc

    if not isinstance(doc, dict):
        return

    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            matrix = _matrix_values(job)
            for step in job.get("steps") or []:
                if isinstance(step, dict) and isinstance(step.get("run"), str):
                    yield step["run"], matrix

    runs = doc.get("runs")
    if isinstance(runs, dict):
        for step in runs.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                yield step["run"], {}


def _collapse_expressions(text: str) -> str:
    """Squeeze the spaces out of `${{ x }}` so it survives tokenisation whole."""
    return _EXPRESSION_RE.sub(
        lambda m: "${{" + "".join(m.group(1).split()) + "}}",
        text,
    )


def _split_commands(script: str) -> List[str]:
    """Flatten line continuations, then split a shell script into commands."""
    flattened = _collapse_expressions(_CONTINUATION_RE.sub(" ", script))
    return [c.strip() for c in _COMMAND_SPLIT_RE.split(flattened) if c.strip()]


def _tokenise(command: str) -> List[str]:
    """Split on whitespace, keeping a quoted run as one token.

    Needed so `-k "not tests/slow"` stays a single flag value instead of
    contributing a bogus `tests/slow` path. Backslashes are literal — Windows
    separators must survive. An unbalanced quote closes at end of string rather
    than raising, because splitting a compound command on `;` or `&&` routinely
    cuts a quoted run in half.
    """
    tokens: List[str] = []
    current: List[str] = []
    quote = ""
    for char in command:
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
        elif char in "\"'":
            quote = char
        elif char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _is_test_invocation(tokens: Sequence[str]) -> bool:
    """True for a pytest run or a `python <script>.py` run.

    Anything else — echo, cp, rm — must not credit a path as covered.
    """
    for i, token in enumerate(tokens):
        bare = token.strip("\"'").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if bare in ("pytest", "pytest.exe"):
            return True
        if _PYTHON_RE.match(bare):
            rest = tokens[i + 1 :]
            # `python -m pytest` is caught by the pytest token itself; a bare
            # `python foo.py` is a script-style test run (test_mcp.yml does this).
            if any(t.strip("\"'").endswith(".py") for t in rest):
                return True
    return False


def _candidate_path_tokens(tokens: Sequence[str]) -> List[str]:
    """Tokens that could be test paths: not flags, not values of a spaced flag."""
    # Flags whose value is a separate token and must not be read as a path.
    value_flags = {"-k", "-m", "-n", "-p", "--timeout", "--basetemp", "--rootdir"}
    candidates = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            if token in value_flags:
                skip_next = True
            continue
        candidates.append(token.strip("\"'"))
    return candidates


def _normalise(token: str) -> str:
    """Strip a pytest node id and normalise separators to posix."""
    path = token.split("::", 1)[0].replace("\\", "/")
    return path.rstrip("/")


def lane_paths_in_script(
    script: str, matrix: Dict[str, List[str]], roots: Sequence[str]
) -> Tuple[Set[str], Set[str]]:
    """Test paths a script runs, plus any path it names unresolvably.

    Returns (paths, unresolved). A path is kept only when it sits under one of
    `roots`, which keeps unrelated arguments out without guessing.
    """
    paths: Set[str] = set()
    unresolved: Set[str] = set()

    for variant in _expand_matrix(script, matrix):
        for command in _split_commands(variant):
            tokens = _tokenise(command)
            if not _is_test_invocation(tokens):
                continue
            for token in _candidate_path_tokens(tokens):
                candidate = _normalise(token)
                if not candidate:
                    continue
                if _EXPRESSION_RE.search(candidate):
                    # Cannot tell what this resolves to; report rather than drop.
                    if _may_be_test_path(candidate, roots):
                        unresolved.add(candidate)
                    continue
                if _under_any_root(candidate, roots):
                    paths.add(candidate)

    return paths, unresolved


def _may_be_test_path(candidate: str, roots: Sequence[str]) -> bool:
    """True when the literal head of an unexpanded path points into a test root.

    Matched against the text *before* the first `${{`, not the whole string —
    `${{ github.repository }}` contains "hub" and must not read as a hub suite.
    """
    head = candidate.split("${{", 1)[0]
    if not head:
        return False
    return any(
        root.startswith(head) or head.startswith(root.split("/")[0] + "/")
        for root in roots
    )


def _under_any_root(candidate: str, roots: Sequence[str]) -> bool:
    """True when `candidate` is one of the roots or lives inside one."""
    for root in roots:
        if candidate == root or candidate.startswith(root + "/"):
            return True
        # A glob root such as hub/agents/*/python/tests.
        if "*" in root and Path(candidate).match(root + "/*"):
            return True
        if "*" in root and Path(candidate).match(root):
            return True
    return False


# ---------------------------------------------------------------------------
# Repository scanning
# ---------------------------------------------------------------------------


def test_roots(repo_root: Path) -> List[str]:
    """Concrete, repo-relative test directories that exist right now."""
    found = []
    for pattern in TEST_ROOT_GLOBS:
        if "*" in pattern:
            found.extend(
                p.relative_to(repo_root).as_posix()
                for p in sorted(repo_root.glob(pattern))
                if p.is_dir()
            )
        elif (repo_root / pattern).is_dir():
            found.append(pattern)
    return found


def discover_test_files(repo_root: Path, roots: Sequence[str]) -> List[str]:
    """Every `test_*.py` under the given roots, repo-relative and sorted."""
    files: Set[str] = set()
    for root in roots:
        for path in (repo_root / root).rglob("test_*.py"):
            if "__pycache__" in path.parts:
                continue
            files.add(path.relative_to(repo_root).as_posix())
    return sorted(files)


def collect_lane_paths(
    workflow_dir: Path, action_dir: Path, roots: Sequence[str]
) -> Tuple[Set[str], Set[str], int]:
    """Union of test paths named across every workflow and composite action."""
    paths: Set[str] = set()
    unresolved: Set[str] = set()
    scanned = 0

    sources: List[Path] = []
    if workflow_dir.is_dir():
        sources.extend(
            p for p in sorted(workflow_dir.iterdir()) if p.suffix in (".yml", ".yaml")
        )
    if action_dir.is_dir():
        sources.extend(sorted(action_dir.glob("*/action.yml")))
        sources.extend(sorted(action_dir.glob("*/action.yaml")))

    for path in sources:
        scanned += 1
        for script, matrix in _iter_run_blocks(path):
            found, missing = lane_paths_in_script(script, matrix, roots)
            paths |= found
            unresolved |= {f"{path.name}: {m}" for m in missing}

    return paths, unresolved, scanned


def is_covered(test_file: str, lane_paths: Set[str]) -> bool:
    """True when a lane names this file directly or names a parent directory."""
    if test_file in lane_paths:
        return True
    return any(test_file.startswith(p + "/") for p in lane_paths)


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def load_allowlist(path: Path) -> Dict[str, str]:
    """Parse the allowlist. Every entry must carry a non-empty reason."""
    if not path.is_file():
        raise ValueError(
            f"allowlist not found at {path} — create it with a `files: {{}}` "
            f"mapping, or point ALLOWLIST_PATH at the real one."
        )

    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path.name}: expected a mapping at the top level")

    files = doc.get("files") or {}
    if not isinstance(files, dict):
        raise ValueError(f"{path.name}: `files:` must be a mapping of path -> reason")

    entries = {}
    for raw_path, reason in files.items():
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f"{path.name}: `{raw_path}` has no reason. Every allowlist entry "
                f"needs one line saying why no lane runs it (hardware, live "
                f"service, manual stress run)."
            )
        entries[str(raw_path).replace("\\", "/")] = reason.strip()
    return entries


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_check() -> int:
    """Report test files no lane runs. 0 on success, 1 on error."""
    if not WORKFLOW_DIR.is_dir():
        print(f"[!] {WORKFLOW_DIR} not found", file=sys.stderr)
        return 1

    roots = test_roots(REPO_ROOT)
    if not roots:
        print(f"[!] no test directories found under {REPO_ROOT}", file=sys.stderr)
        return 1

    try:
        lane_paths, unresolved, scanned = collect_lane_paths(
            WORKFLOW_DIR, ACTION_DIR, roots
        )
        allowlist = load_allowlist(ALLOWLIST_PATH)
    except ValueError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1

    test_files = discover_test_files(REPO_ROOT, roots)
    uncovered = [f for f in test_files if not is_covered(f, lane_paths)]

    offenders = [f for f in uncovered if f not in allowlist]
    covered_but_allowlisted = [
        f for f in allowlist if f in test_files and f not in uncovered
    ]
    missing_from_disk = [f for f in allowlist if f not in test_files]

    errors = False

    if offenders:
        errors = True
        print(
            f"[!] {len(offenders)} test file(s) are run by no CI lane. Each one "
            f"looks like a pass on every PR whether it works or not. Wire it into "
            f"a lane, or add it to {ALLOWLIST_PATH.name} with a one-line reason:",
            file=sys.stderr,
        )
        for name in offenders:
            print(f"    - {name}", file=sys.stderr)

    if unresolved:
        errors = True
        print(
            "[!] Test path(s) contain an unresolved ${{ }} expression, so "
            "coverage cannot be verified. Teach this checker to expand it:",
            file=sys.stderr,
        )
        for name in sorted(unresolved):
            print(f"    - {name}", file=sys.stderr)

    if covered_but_allowlisted:
        errors = True
        print(
            f"[!] {len(covered_but_allowlisted)} allowlist entr(ies) are now run "
            f"by a lane. Remove them so the allowlist keeps meaning something:",
            file=sys.stderr,
        )
        for name in sorted(covered_but_allowlisted):
            print(f"    - {name}", file=sys.stderr)

    if missing_from_disk:
        errors = True
        print(
            f"[!] {len(missing_from_disk)} allowlist entr(ies) name a file that "
            f"does not exist. Delete the stale entry:",
            file=sys.stderr,
        )
        for name in sorted(missing_from_disk):
            print(f"    - {name}", file=sys.stderr)

    if errors:
        return 1

    print(
        f"[OK] {len(test_files)} test file(s) across {len(roots)} root(s) are all "
        f"named by a lane or allowlisted ({scanned} workflow file(s) scanned, "
        f"{len(allowlist)} allowlisted)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_check())
