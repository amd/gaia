# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Check that a CI lane triggers on every file that can change its result.

A `paths:` filter that misses a file the lane depends on is worse than no
filter: the PR touching that file shows a green checks page because the lane
never ran. This check derives, for every workflow with a `pull_request`
trigger, the files the workflow itself proves it depends on, and fails when its
filter would not fire on one of them:

- the workflow file, any reusable workflow it calls, and every local action it
  uses (`.github/actions/<name>/**`), recursively;
- every test path and script it runs (`pytest tests/x.py`, `python util/y.py`),
  expanded to the tracked files under a directory argument;
- the pytest session files those runs load: the root `conftest.py`, every
  `conftest.py` above a test path, and `pyproject.toml` (pytest config);
- the package manifests it installs (`setup.py`, `pyproject.toml`, and each
  `hub/agents/<id>/python` manifest it `pip install`s);
- the modules each test or script imports directly (`gaia.*`, the hub agent
  packages, `tests.*` helpers, and sibling `util/` modules, the last two
  followed transitively);
- the repository files a test or script names by exact literal path
  (`REPO_ROOT / "docs" / "docs.json"`, `".github/workflows/x.yml"`). A literal
  naming a directory or a glob is not expanded: the first is almost always a
  base the code joins a variable onto, the second a filter pattern under test.

It also fails when a tracked file under src/, hub/, tui/, installer/, util/ or
tests/ triggers no path-scoped pull_request workflow. A workflow with no filter
(the skill audit) runs on every PR without testing any file in particular, so it
does not count.

What it does not prove, stated so a pass is not over-read: imports a test only
reaches transitively (`gaia/__init__.py` pulls in ~100 core modules on any
import) are not required, because the hosted unit and import-every-module lanes
trigger on all of `src/**` and catch import-time breakage. A lane that drives the
`gaia` CLI end to end depends on code no import names; its filter is reviewed by
hand, and the comment in its `on:` block says why.

Run with no arguments to check (blocking in `util/lint.py --ci-paths` and
`--all`). Pass changed paths to list the workflows a PR would trigger:

    python util/check_ci_path_coverage.py --changed docs/guides/chat.mdx tui/main.go
    git diff --name-only origin/main... | python util/check_ci_path_coverage.py --changed -
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

import yaml

# Anchor to the repo root so the script works regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
ALLOWLIST_PATH = REPO_ROOT / "util" / "ci_path_allowlist.yml"

# Tracked files under these roots must each trigger at least one PR workflow.
ORPHAN_ROOTS = ("src", "hub", "tui", "installer", "util", "tests")

# Top-level directories a test may name by literal path. A literal outside
# these (a temp dir, a URL path) is not a repository dependency.
LITERAL_ROOTS = (
    ".github",
    "cpp",
    "data",
    "docs",
    "eval",
    "examples",
    "hub",
    "installer",
    "scripts",
    "src",
    "tests",
    "tui",
    "util",
    "website",
    "workers",
)
ROOT_FILES = ("setup.py", "pyproject.toml", "conftest.py", "MANIFEST.in")

sys.path.insert(0, str(Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
from check_test_lane_coverage import (  # noqa: E402
    _expand_matrix,
    _matrix_values,
    _normalise,
    _split_commands,
    _tokenise,
)

# ---------------------------------------------------------------------------
# GitHub path-filter matching
# ---------------------------------------------------------------------------


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Compile a GitHub Actions path glob.

    `*` stops at `/`, `**` crosses it, `**/` may match nothing, and `?` / `+`
    quantify the preceding character — GitHub's filter cheat sheet, which is
    not shell globbing.
    """
    out: List[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char in "?+":
            out.append(char)
            i += 1
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(char))
                i += 1
            else:
                out.append(pattern[i : end + 1])
                i = end + 1
        elif char == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
        else:
            out.append(re.escape(char))
            i += 1
    return re.compile("".join(out))


@dataclass(frozen=True)
class PathFilter:
    """A workflow's pull_request trigger, reduced to what decides if it runs."""

    # "all" (no filter), "paths" or "paths-ignore".
    kind: str
    patterns: Tuple[str, ...] = ()

    def _last_match_positive(self, path: str) -> bool:
        result = False
        for raw in self.patterns:
            negated = raw.startswith("!")
            body = raw[1:] if negated else raw
            if _compiled(body).fullmatch(path):
                result = not negated
        return result

    def fires_on(self, path: str) -> bool:
        """True when a PR changing only `path` triggers the workflow."""
        if self.kind == "all":
            return True
        if self.kind == "paths":
            return self._last_match_positive(path)
        return not self._last_match_positive(path)

    def fires_on_any(self, paths: Iterable[str]) -> bool:
        return any(self.fires_on(p) for p in paths)


@lru_cache(maxsize=None)
def _compiled(pattern: str) -> "re.Pattern[str]":
    return glob_to_regex(pattern)


def _triggers(doc: Any) -> Dict[Any, Any]:
    # PyYAML reads a bare `on:` key as the boolean True (YAML 1.1).
    if not isinstance(doc, dict):
        return {}
    triggers = doc.get("on", doc.get(True))
    if isinstance(triggers, str):
        return {triggers: None}
    if isinstance(triggers, list):
        return {t: None for t in triggers}
    return triggers if isinstance(triggers, dict) else {}


def pr_filter(doc: Any) -> Optional[PathFilter]:
    """The workflow's pull_request filter, or None when PRs do not trigger it."""
    triggers = _triggers(doc)
    if "pull_request" not in triggers:
        return None
    trigger = triggers["pull_request"]
    if not isinstance(trigger, dict):
        return PathFilter("all")
    if "paths" in trigger:
        return PathFilter("paths", tuple(str(p) for p in trigger["paths"] or ()))
    if "paths-ignore" in trigger:
        return PathFilter(
            "paths-ignore", tuple(str(p) for p in trigger["paths-ignore"] or ())
        )
    return PathFilter("all")


# ---------------------------------------------------------------------------
# Repository model
# ---------------------------------------------------------------------------


@dataclass
class Repo:
    """Tracked files plus a module index, built once per run."""

    root: Path
    files: Set[str] = field(default_factory=set)
    modules: Dict[str, str] = field(default_factory=dict)
    source_cache: Dict[str, Tuple[Tuple[str, str], ...]] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path, files: Optional[Iterable[str]] = None) -> "Repo":
        if files is None:
            files = _git_ls_files(root)
        repo = cls(root, set(files))
        repo._index_modules()
        return repo

    def _index_modules(self) -> None:
        # Package roots: src/ for gaia, each hub agent's python/ dir, and the
        # repo root for `tests.*` helpers.
        package_roots = ["src"] + sorted(
            {
                "/".join(f.split("/")[:4])
                for f in self.files
                if re.match(r"hub/agents/[^/]+/python/", f)
            }
        )
        for path in sorted(self.files):
            if not path.endswith(".py"):
                continue
            for root in package_roots + [""]:
                prefix = root + "/" if root else ""
                if not path.startswith(prefix):
                    continue
                parts = path[len(prefix) : -3].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                if parts and (root or parts[0] == "tests"):
                    self.modules.setdefault(".".join(parts), path)
                break

    def under(self, directory: str) -> List[str]:
        """Tracked files at or under `directory` (a file path returns itself)."""
        directory = directory.rstrip("/")
        if directory in self.files:
            return [directory]
        prefix = directory + "/"
        return sorted(f for f in self.files if f.startswith(prefix))

    def resolve_module(self, name: str, script_dir: Optional[str]) -> List[str]:
        """The file `import name` loads, longest resolvable prefix first.

        Parent-package `__init__` files are deliberately left out: they are the
        import-time core every lane shares, covered by the unit lanes.
        """
        found: List[str] = []
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            key = ".".join(parts[:i])
            if key in self.modules:
                found.append(self.modules[key])
                return found
        # A bare `import sibling` resolves next to the importing file, or in
        # util/, which tests put on sys.path to import the script they test.
        if script_dir is not None:
            for directory in (script_dir, "util"):
                candidate = (
                    f"{directory}/{parts[0]}.py" if directory else f"{parts[0]}.py"
                )
                if candidate in self.files:
                    found.append(candidate)
                    break
        return found


def _git_ls_files(root: Path) -> List[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"`git ls-files` failed in {root}: {exc}. Run this check from a git "
            f"checkout of the repository."
        ) from exc
    return [p for p in out.decode("utf-8").split("\0") if p]


# ---------------------------------------------------------------------------
# What a workflow depends on
# ---------------------------------------------------------------------------

_PYTHON_RE = re.compile(r"^python[0-9.]*(?:\.exe)?$", re.IGNORECASE)
_INSTALL_SELF_RE = re.compile(
    r"""(?:^|\s)(?:-e\s+)?["']?\.(?:\[[^\]]*\])?["']?(?=\s|$)"""
)
_HUB_INSTALL_RE = re.compile(r"(hub/agents/[A-Za-z0-9_.\-]+/python)\b")


@dataclass
class Dependencies:
    """Repo paths a workflow depends on, each with the reason it does."""

    reasons: Dict[str, str] = field(default_factory=dict)

    def add(self, path: str, reason: str) -> None:
        self.reasons.setdefault(path, reason)


def _workflow_doc(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path.name}: failed to parse: {exc}") from exc


def _steps(doc: Any) -> Iterator[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Yield (step, job) for workflow jobs and composite-action steps."""
    if not isinstance(doc, dict):
        return
    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        for job in jobs.values():
            if isinstance(job, dict):
                for step in job.get("steps") or []:
                    if isinstance(step, dict):
                        yield step, job
    runs = doc.get("runs")
    if isinstance(runs, dict):
        for step in runs.get("steps") or []:
            if isinstance(step, dict):
                yield step, {}


def _local_uses(doc: Any) -> Tuple[Set[str], Set[str]]:
    """(local action dirs, local reusable workflow files) a workflow references."""
    actions: Set[str] = set()
    workflows: Set[str] = set()
    for step, _job in _steps(doc):
        uses = step.get("uses")
        if isinstance(uses, str) and uses.startswith("./.github/actions/"):
            actions.add(uses[2:].rstrip("/"))
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if isinstance(jobs, dict):
        for job in jobs.values():
            uses = job.get("uses") if isinstance(job, dict) else None
            if isinstance(uses, str) and uses.startswith("./.github/workflows/"):
                workflows.add(uses[2:])
    return actions, workflows


def _command_paths(
    command: str, repo: Repo
) -> Tuple[List[str], List[str], bool, bool, Set[str]]:
    """Classify one shell command.

    Returns (test_paths, script_paths, runs_pytest, installs_self, hub_installs).
    """
    tokens = _tokenise(command)
    bare = [t.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1] for t in tokens]
    runs_pytest = any(b in ("pytest", "pytest.exe") for b in bare) or (
        "-m" in tokens and "pytest" in tokens
    )
    runs_python = any(_PYTHON_RE.match(b) for b in bare)
    tests: List[str] = []
    scripts: List[str] = []
    if runs_pytest or runs_python:
        for token in tokens:
            if token.startswith("-"):
                continue
            candidate = _normalise(token.strip("\"'"))
            candidate = candidate.removeprefix("./")
            if "${{" in candidate or not candidate:
                continue
            if candidate in repo.files or repo.under(candidate):
                if runs_pytest:
                    tests.append(candidate)
                elif candidate.endswith(".py"):
                    scripts.append(candidate)
    is_install = "pip" in bare and "install" in tokens
    installs_self = is_install and bool(_INSTALL_SELF_RE.search(command))
    hubs = set(_HUB_INSTALL_RE.findall(command)) if is_install else set()
    return tests, scripts, runs_pytest, installs_self, hubs


def workflow_dependencies(path: Path, repo: Repo) -> Dependencies:
    """Every repo path `path` depends on, following called workflows and actions."""
    deps = Dependencies()
    seen: Set[str] = set()
    _collect(path.relative_to(repo.root).as_posix(), repo, deps, seen)
    return deps


def _collect(rel: str, repo: Repo, deps: Dependencies, seen: Set[str]) -> None:
    if rel in seen:
        return
    seen.add(rel)
    is_action = rel.startswith(".github/actions/")
    if is_action:
        for f in repo.under(rel):
            deps.add(f, f"local action `{rel}`")
        files = [
            f for f in repo.under(rel) if f.endswith(("action.yml", "action.yaml"))
        ]
        doc_path = repo.root / files[0] if files else None
    else:
        deps.add(rel, "the workflow file")
        doc_path = repo.root / rel
    if doc_path is None or not doc_path.is_file():
        return
    doc = _workflow_doc(doc_path)

    actions, called = _local_uses(doc)
    for action in sorted(actions):
        _collect(action, repo, deps, seen)
    for workflow in sorted(called):
        if workflow in repo.files:
            deps.add(workflow, f"reusable workflow called by `{rel}`")
            _collect(workflow, repo, deps, seen)

    for step, job in _steps(doc):
        script = step.get("run")
        if not isinstance(script, str):
            continue
        matrix = _job_matrix(job)
        workdir = step.get("working-directory") or (
            (job.get("defaults") or {}).get("run", {}).get("working-directory")
            if isinstance(job, dict)
            else None
        )
        for variant in _expand_matrix(script, matrix):
            for command in _split_commands(variant):
                _command_deps(command, workdir, repo, deps, rel)


def _job_matrix(job: Dict[str, Any]) -> Dict[str, List[str]]:
    return _matrix_values(job) if isinstance(job, dict) and job else {}


_MESSAGE_COMMANDS = {
    "echo",
    "write-host",
    "write-output",
    "printf",
    "::error",
    "::warning",
}
_TOOLCHAIN_RE = re.compile(r"^(?:npm|npx|node|yarn|pnpm|go)(?:\.exe|\.cmd)?$")


def _command_deps(
    command: str, workdir: Any, repo: Repo, deps: Dependencies, rel: str
) -> None:
    tokens = _tokenise(command)
    if not tokens or tokens[0].startswith("#") or tokens[0].upper() in ("REM", "::"):
        return
    in_subdir = (
        isinstance(workdir, str) and workdir.strip("./") != "" and "${{" not in workdir
    )
    if _TOOLCHAIN_RE.match(tokens[0]):
        # A Node or Go build reads its whole project tree.
        if in_subdir:
            tree = workdir.strip().removeprefix("./").rstrip("/")
            for f in repo.under(tree):
                deps.add(f, f"`{rel}` runs `{tokens[0]}` in `{tree}`")
        elif tokens[0].startswith(("npm", "npx")) and not {"-g", "--global"} & set(
            tokens
        ):
            for manifest in ("package.json", "package-lock.json"):
                if manifest in repo.files:
                    deps.add(manifest, f"`{rel}` runs `{tokens[0]}` at the repo root")
        return
    tests, scripts, runs_pytest, installs_self, hubs = _command_paths(command, repo)
    if in_subdir and (tests or scripts):
        # Paths in a working-directory step are relative to it; skip rather
        # than guess.
        return
    if not in_subdir and tokens[0].lower() not in _MESSAGE_COMMANDS:
        # Any other tracked file a command names (`pwsh -File x.ps1`, `bash
        # y.sh`, a config passed by path) is read by the step.
        for token in tokens:
            named = _normalise(token.strip("\"'")).removeprefix("./")
            if named in repo.files and named not in tests:
                deps.add(named, f"`{rel}` names `{named}` in a run step")
    for test_path in tests:
        for f in repo.under(test_path):
            deps.add(f, f"`{rel}` runs pytest on `{test_path}`")
        _conftest_chain(test_path, repo, deps, rel)
    if runs_pytest:
        deps.add("pyproject.toml", f"`{rel}` runs pytest (config in pyproject.toml)")
        if "conftest.py" in repo.files:
            deps.add("conftest.py", f"`{rel}` runs pytest from the repo root")
    for script_path in scripts:
        deps.add(script_path, f"`{rel}` runs `python {script_path}`")
    if installs_self:
        for manifest in ("setup.py", "pyproject.toml"):
            if manifest in repo.files:
                deps.add(manifest, f"`{rel}` installs the gaia package")
    for hub in hubs:
        for manifest in ("pyproject.toml", "setup.py"):
            if f"{hub}/{manifest}" in repo.files:
                deps.add(f"{hub}/{manifest}", f"`{rel}` installs `{hub}`")
    executed = [f for t in tests for f in repo.under(t) if f.endswith(".py")]
    executed += scripts
    for source in executed:
        for dep, why in source_dependencies(source, repo):
            deps.add(dep, f"{why} (run by `{rel}`)")


def _conftest_chain(test_path: str, repo: Repo, deps: Dependencies, rel: str) -> None:
    parts = test_path.split("/")
    for i in range(1, len(parts) + 1):
        candidate = "/".join(parts[:i] + ["conftest.py"])
        if candidate in repo.files:
            deps.add(candidate, f"pytest loads it for `{test_path}` in `{rel}`")


# ---------------------------------------------------------------------------
# What a test or script file depends on
# ---------------------------------------------------------------------------


def source_dependencies(source: str, repo: Repo) -> List[Tuple[str, str]]:
    """(path, reason) pairs a Python test or script depends on directly.

    Imports of `gaia.*` and hub packages are taken one level deep; `tests.*`
    helpers and bare sibling imports (util/ scripts) are followed, because they
    are part of the test rather than the code under test.
    """
    if source not in repo.source_cache:
        repo.source_cache[source] = _source_dependencies(source, repo)
    return list(repo.source_cache[source])


def _source_dependencies(source: str, repo: Repo) -> Tuple[Tuple[str, str], ...]:
    found: Dict[str, str] = {}
    pending = [source]
    visited: Set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        names, literals = _scan(repo.root / current, current)
        script_dir = current.rsplit("/", 1)[0] if "/" in current else ""
        for name in sorted(names):
            for dep in repo.resolve_module(name, script_dir):
                if dep == current:
                    continue
                found.setdefault(dep, f"`{current}` imports `{name}`")
                local_helper = dep.startswith("tests/") or (
                    dep.startswith(script_dir + "/")
                    and not dep.startswith("src/")
                    and not dep.startswith("hub/")
                )
                if local_helper:
                    pending.append(dep)
        if current != source and not current.startswith("tests/"):
            # A util helper reached by import may read a file in a function
            # the caller never runs; only the executed file and test helpers
            # are held to their literals.
            continue
        for literal in sorted(literals):
            for dep in _literal_targets(literal, repo):
                found.setdefault(dep, f"`{current}` reads `{literal}`")
    found.pop(source, None)
    return tuple(sorted(found.items()))


@lru_cache(maxsize=None)
def _scan(path: Path, current: str) -> Tuple[frozenset, frozenset]:
    """(imported module names, repo path literals) of one file, in one walk."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError, ValueError):
        return frozenset(), frozenset()
    package = current.rsplit("/", 1)[0].replace("/", ".") if "/" in current else ""
    if package.startswith("src."):
        package = package[4:]
    names: Set[str] = set()
    literals: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            if module:
                names.add(module)
                names.update(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            called = getattr(func, "attr", getattr(func, "id", None))
            if (
                called == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.add(node.args[0].value)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            parts = _div_chain(node)
            if parts:
                literals.add("/".join(parts))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.replace("\\", "/")
            if "/" in value and not any(c.isspace() for c in value):
                literals.add(value)
    kept = {
        lit.strip("/")
        for lit in literals
        if lit.split("/", 1)[0] in LITERAL_ROOTS or lit in ROOT_FILES
    }
    return frozenset(names), frozenset(kept)


def _div_chain(node: ast.AST) -> List[str]:
    """`ROOT / "a" / "b"` -> ["a", "b"]; [] when any segment is not a literal."""
    parts: List[str] = []
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        right = node.right
        if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
            return []
        parts.insert(0, right.value.replace("\\", "/").strip("/"))
        node = node.left
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        parts.insert(0, node.value.replace("\\", "/").strip("/"))
    return parts if len(parts) >= 1 else []


def _literal_targets(literal: str, repo: Repo) -> List[str]:
    """The tracked file a literal names, if it names exactly one.

    A directory literal names nothing: it is almost always a base the code
    joins a variable onto (`FIXTURES = ROOT / "tests" / "fixtures"`). A glob
    names nothing either: in a test it is far more often a filter pattern the
    test asserts on than a set of files it reads.
    """
    return [literal] if literal in repo.files else []


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Allowlist:
    """Declared exceptions: deps a lane may skip, files no PR lane covers."""

    # workflow name -> {path glob: reason}
    deps: Dict[str, Dict[str, str]]
    # path glob -> reason
    orphans: Dict[str, str]

    def dep_entry(self, workflow: str, path: str) -> Optional[Tuple[str, str]]:
        """The (scope, glob) entry that excuses `path` for `workflow`, if any."""
        for scope in (workflow, "*"):
            for glob in self.deps.get(scope, {}):
                if _compiled(glob).fullmatch(path):
                    return scope, glob
        return None

    def orphan_entry(self, path: str) -> Optional[str]:
        """The orphan glob that excuses `path`, if any."""
        for glob in self.orphans:
            if _compiled(glob).fullmatch(path):
                return glob
        return None

    def entries(self) -> List[Tuple[str, str]]:
        """Every (scope, glob) entry; orphan globs use the scope `orphans`."""
        listed = [(scope, glob) for scope, globs in self.deps.items() for glob in globs]
        return listed + [("orphans", glob) for glob in self.orphans]


def load_allowlist(path: Path) -> Allowlist:
    """Parse the allowlist. Every entry must carry a non-empty reason."""
    if not path.is_file():
        raise ValueError(
            f"allowlist not found at {path} — create it with empty "
            f"`dependencies: {{}}` and `orphans: {{}}` mappings."
        )
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path.name}: expected a mapping at the top level")

    def _reasons(mapping: Any, where: str) -> Dict[str, str]:
        if not isinstance(mapping, dict):
            raise ValueError(f"{path.name}: `{where}` must map path glob -> reason")
        for glob, reason in mapping.items():
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(
                    f"{path.name}: `{where}.{glob}` has no reason. Say why the "
                    f"lane cannot be affected by it."
                )
        return {str(k): str(v) for k, v in mapping.items()}

    deps_raw = doc.get("dependencies") or {}
    if not isinstance(deps_raw, dict):
        raise ValueError(f"{path.name}: `dependencies` must map workflow -> globs")
    deps = {str(wf): _reasons(m, f"dependencies.{wf}") for wf, m in deps_raw.items()}
    orphans = _reasons(doc.get("orphans") or {}, "orphans")
    return Allowlist(deps, orphans)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def load_filters(workflow_dir: Path) -> Dict[str, PathFilter]:
    """Workflow file name -> pull_request filter, for PR-triggered workflows."""
    filters: Dict[str, PathFilter] = {}
    for path in sorted(workflow_dir.iterdir()):
        if path.suffix not in (".yml", ".yaml"):
            continue
        flt = pr_filter(_workflow_doc(path))
        if flt is not None:
            filters[path.name] = flt
    return filters


def triggered_workflows(
    filters: Dict[str, PathFilter], changed: Sequence[str]
) -> List[str]:
    return sorted(name for name, flt in filters.items() if flt.fires_on_any(changed))


@dataclass
class Gaps:
    """What the check found, as printable lines."""

    missed: List[str] = field(default_factory=list)
    orphans: List[str] = field(default_factory=list)
    stale: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.missed or self.orphans or self.stale)


def find_gaps(repo: Repo, workflow_dir: Path, allowlist: Allowlist) -> Gaps:
    """Missed dependencies, orphan files and allowlist entries that excuse nothing."""
    filters = load_filters(workflow_dir)
    gaps = Gaps()
    used: Set[Tuple[str, str]] = set()
    for name, flt in filters.items():
        if flt.kind == "all":
            continue
        deps = workflow_dependencies(workflow_dir / name, repo)
        for dep, why in sorted(deps.reasons.items()):
            if dep not in repo.files or flt.fires_on(dep):
                continue
            entry = allowlist.dep_entry(name, dep)
            if entry is not None:
                used.add(entry)
                continue
            gaps.missed.append(f"{name}: does not trigger on `{dep}` — {why}")

    # A catch-all workflow (the skill audit) runs on every PR without testing
    # any file in particular, so it cannot be what covers one.
    scoped = [flt for flt in filters.values() if flt.kind != "all"]
    for f in sorted(repo.files):
        if f.split("/", 1)[0] not in ORPHAN_ROOTS:
            continue
        if any(flt.fires_on(f) for flt in scoped):
            continue
        glob = allowlist.orphan_entry(f)
        if glob is not None:
            used.add(("orphans", glob))
            continue
        gaps.orphans.append(f)

    gaps.stale = [
        f"{scope}: `{glob}`"
        for scope, glob in allowlist.entries()
        if (scope, glob) not in used
    ]
    return gaps


def run_check(repo: Optional[Repo] = None) -> int:
    """0 when every lane triggers on what it depends on, 1 otherwise."""
    if not WORKFLOW_DIR.is_dir():
        print(f"[!] {WORKFLOW_DIR} not found", file=sys.stderr)
        return 1
    try:
        repo = repo or Repo.load(REPO_ROOT)
        allowlist = load_allowlist(ALLOWLIST_PATH)
        gaps = find_gaps(repo, WORKFLOW_DIR, allowlist)
    except (ValueError, RuntimeError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    missed, orphans = gaps.missed, gaps.orphans

    if missed:
        print(
            f"[!] {len(missed)} file(s) a CI lane depends on would not trigger it, "
            f"so a PR changing only that file merges without the lane running. "
            f"Add the path to the workflow's `on.pull_request.paths` (and "
            f"`on.push.paths`), or list it under `dependencies:` in "
            f"{ALLOWLIST_PATH.name} with the reason the lane cannot be affected:",
            file=sys.stderr,
        )
        for line in missed:
            print(f"    - {line}", file=sys.stderr)
    if orphans:
        print(
            f"[!] {len(orphans)} tracked file(s) trigger no path-scoped "
            f"pull_request workflow, so a PR changing only them runs no test lane. "
            f"Add them to the filter of "
            f"the lane that tests them, or to `orphans:` in "
            f"{ALLOWLIST_PATH.name} with a reason:",
            file=sys.stderr,
        )
        for line in orphans:
            print(f"    - {line}", file=sys.stderr)
    if gaps.stale:
        print(
            f"[!] {len(gaps.stale)} entr(ies) in {ALLOWLIST_PATH.name} excuse "
            f"nothing any more. Delete them so the allowlist keeps meaning "
            f"something:",
            file=sys.stderr,
        )
        for line in gaps.stale:
            print(f"    - {line}", file=sys.stderr)
    if gaps:
        return 1

    print(
        f"[OK] every pull_request lane triggers on the files it runs, imports, "
        f"installs and reads; {len(repo.files)} tracked files checked for orphans."
    )
    return 0


def _read_changed(values: Sequence[str]) -> List[str]:
    if values == ["-"]:
        return [line.strip() for line in sys.stdin if line.strip()]
    return [v.replace("\\", "/") for v in values]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--changed",
        nargs="+",
        metavar="PATH",
        help="List the pull_request workflows these paths trigger ('-' reads stdin)",
    )
    args = parser.parse_args(argv)
    if args.changed:
        changed = _read_changed(args.changed)
        filters = load_filters(WORKFLOW_DIR)
        fired = triggered_workflows(filters, changed)
        for name in fired:
            print(name)
        print(
            f"{len(fired)} of {len(filters)} pull_request workflow(s) trigger on "
            f"{len(changed)} changed path(s).",
            file=sys.stderr,
        )
        return 0
    return run_check()


if __name__ == "__main__":
    sys.exit(main())
