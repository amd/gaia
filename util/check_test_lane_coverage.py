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
  covered here. `util/check_module_skips.py` catches that inside the lane
  itself (#4206); this check only keeps its `module_skips:` allowlist honest:
  each entry's `runs_in` workflow must exist, name the path, and run that guard.
- Every workflow counts equally, including release-only ones. A file named only
  by a `workflow_dispatch` lane passes this check while never running on a PR.

Blocking in `util/lint.py` (`--test-lanes`, and part of `--all`). Run directly
with `python util/check_test_lane_coverage.py`.
"""

from __future__ import annotations

import itertools
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import yaml

# Anchor to the repo root so the script works regardless of CWD — matches the
# convention in util/check_workflow_ancestor_skip.py.
REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
ACTION_DIR = REPO_ROOT / ".github" / "actions"
ALLOWLIST_PATH = REPO_ROOT / "util" / "test_lane_allowlist.yml"
MODULE_SKIP_GUARD = "check_module_skips.py"

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


def _load_yaml(path: Path) -> Any:
    """Parse a workflow file, raising on YAML this cannot read."""
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path.name}: failed to parse: {exc}") from exc


def _job_run_blocks(job: Any) -> Iterator[Tuple[str, Dict[str, List[str]]]]:
    """Yield (run_script, matrix_values) for every `run:` step of one job."""
    if not isinstance(job, dict):
        return
    matrix = _matrix_values(job)
    for step in job.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("run"), str):
            yield step["run"], matrix


def job_run_blocks(path: Path, job_id: str) -> List[Tuple[str, Dict[str, List[str]]]]:
    """The `run:` steps of one named job. Raises when the job does not exist."""
    doc = _load_yaml(path)
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict) or job_id not in jobs:
        known = ", ".join(sorted(jobs)) if isinstance(jobs, dict) else "none"
        raise ValueError(f"{path.name}: no job `{job_id}` (jobs: {known})")
    return list(_job_run_blocks(jobs[job_id]))


def _iter_run_blocks(path: Path) -> Iterator[Tuple[str, Dict[str, List[str]]]]:
    """Yield (run_script, matrix_values) for every `run:` step in a YAML file.

    Handles both workflow files (`jobs.<id>.steps`) and composite actions
    (`runs.steps`). Raises on unparseable YAML — a workflow this cannot read is
    a hole in the audit, not something to skip past.
    """
    doc = _load_yaml(path)
    if not isinstance(doc, dict):
        return

    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        for job in jobs.values():
            yield from _job_run_blocks(job)

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


def pytest_commands_in_script(
    script: str, matrix: Dict[str, List[str]], roots: Sequence[str]
) -> Tuple[List[List[str]], Set[str]]:
    """The test paths of each test command in a script, one list per command.

    Returns (commands, unresolved). A path is kept only when it sits under one
    of `roots`, which keeps unrelated arguments out without guessing. A command
    that names no test path (`python util/foo.py`) is left out.
    """
    commands: List[List[str]] = []
    unresolved: Set[str] = set()

    for variant in _expand_matrix(script, matrix):
        for command in _split_commands(variant):
            tokens = _tokenise(command)
            if not _is_test_invocation(tokens):
                continue
            paths: List[str] = []
            for token in _candidate_path_tokens(tokens):
                candidate = _normalise(token)
                if not candidate:
                    continue
                if _EXPRESSION_RE.search(candidate):
                    # Cannot tell what this resolves to; report rather than drop.
                    if _may_be_test_path(candidate, roots):
                        unresolved.add(candidate)
                    continue
                if _under_any_root(candidate, roots) and candidate not in paths:
                    paths.append(candidate)
            if paths and paths not in commands:
                commands.append(paths)

    return commands, unresolved


def lane_paths_in_script(
    script: str, matrix: Dict[str, List[str]], roots: Sequence[str]
) -> Tuple[Set[str], Set[str]]:
    """Test paths a script runs, plus any path it names unresolvably."""
    commands, unresolved = pytest_commands_in_script(script, matrix, roots)
    return {path for command in commands for path in command}, unresolved


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


@dataclass(frozen=True)
class ModuleSkip:
    """A test file (or directory) allowed to skip wholesale in some lane."""

    path: str
    reason: str
    # The workflow that installs what the file needs and so must run it. None
    # means no lane runs it, which the reason has to justify.
    runs_in: Optional[str]


def load_module_skips(path: Path) -> List[ModuleSkip]:
    """Parse the `module_skips:` groups of the allowlist.

    Each group is `{reason, runs_in (optional), paths}`. Raises on a group with
    no reason or no paths, and on a path listed twice.
    """
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path.name}: expected a mapping at the top level")
    groups = doc.get("module_skips") or []
    if not isinstance(groups, list):
        raise ValueError(f"{path.name}: `module_skips:` must be a list of groups")

    entries: List[ModuleSkip] = []
    seen: Set[str] = set()
    for index, group in enumerate(groups):
        where = f"{path.name}: module_skips[{index}]"
        if not isinstance(group, dict):
            raise ValueError(f"{where} must be a mapping")
        reason = group.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f"{where} has no reason. Say what the lane lacks (a hub wheel, "
                f"an extra) or why no lane can run the file."
            )
        runs_in = group.get("runs_in")
        if runs_in is not None and not isinstance(runs_in, str):
            raise ValueError(f"{where}: `runs_in` must be a workflow file name")
        paths = group.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"{where} lists no paths")
        for raw in paths:
            entry = str(raw).replace("\\", "/").rstrip("/")
            if entry in seen:
                raise ValueError(f"{path.name}: `{entry}` is listed twice")
            seen.add(entry)
            entries.append(ModuleSkip(entry, reason.strip(), runs_in))
    return entries


def find_module_skip(
    test_file: str, entries: Sequence[ModuleSkip]
) -> Optional[ModuleSkip]:
    """The most specific entry covering `test_file`, or None."""
    matches = [
        e for e in entries if test_file == e.path or test_file.startswith(e.path + "/")
    ]
    return max(matches, key=lambda e: len(e.path)) if matches else None


def module_skip_errors(
    entries: Sequence[ModuleSkip],
    test_files: Sequence[str],
    repo_root: Path,
    workflow_dir: Path,
    roots: Sequence[str],
) -> List[str]:
    """Entries that no longer describe reality.

    A `runs_in` workflow must exist, must name the path, and must run the
    module-skip guard — otherwise "runs elsewhere" is an unchecked promise.
    """
    errors: List[str] = []
    workflows: Dict[str, Tuple[Set[str], bool]] = {}

    for entry in entries:
        exists = entry.path in test_files or (
            (repo_root / entry.path).is_dir()
            and any(f.startswith(entry.path + "/") for f in test_files)
        )
        if not exists:
            errors.append(f"{entry.path}: no such test file or directory")
            continue
        if entry.runs_in is None:
            continue

        if entry.runs_in not in workflows:
            wf_path = workflow_dir / entry.runs_in
            if not wf_path.is_file():
                errors.append(f"{entry.path}: runs_in `{entry.runs_in}` does not exist")
                continue
            paths: Set[str] = set()
            guarded = False
            for script, matrix in _iter_run_blocks(wf_path):
                found, _ = lane_paths_in_script(script, matrix, roots)
                paths |= found
                guarded = guarded or MODULE_SKIP_GUARD in script
            workflows[entry.runs_in] = (paths, guarded)

        paths, guarded = workflows[entry.runs_in]
        if not is_covered(entry.path, paths):
            errors.append(f"{entry.path}: `{entry.runs_in}` does not run it")
        if not guarded:
            errors.append(
                f"{entry.path}: `{entry.runs_in}` never runs {MODULE_SKIP_GUARD}, "
                f"so nothing checks the file stops skipping there"
            )
    return errors


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
        module_skips = load_module_skips(ALLOWLIST_PATH)
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

    stale_skips = module_skip_errors(
        module_skips, test_files, REPO_ROOT, WORKFLOW_DIR, roots
    )
    if stale_skips:
        errors = True
        print(
            f"[!] {len(stale_skips)} `module_skips` entr(ies) in "
            f"{ALLOWLIST_PATH.name} are stale. Fix the entry or the lane:",
            file=sys.stderr,
        )
        for problem in stale_skips:
            print(f"    - {problem}", file=sys.stderr)

    if errors:
        return 1

    print(
        f"[OK] {len(test_files)} test file(s) across {len(roots)} root(s) are all "
        f"named by a lane or allowlisted ({scanned} workflow file(s) scanned, "
        f"{len(allowlist)} allowlisted, {len(module_skips)} module-skip "
        f"entr(ies) verified)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_check())
