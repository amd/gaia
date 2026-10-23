# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pick the unit-test files a change can affect, so a PR runs those and not all.

Two sources say which tests a file reaches:

**The coverage map** (``build-map``), from a full run with per-test coverage
contexts on main. It lists, for each source file, the test files that executed
it. This is what makes the selection tight: most of ``src/gaia`` imports most of
the rest inside function bodies, so static reading alone would run nearly
everything for nearly any change.

**Static rules**, for what coverage can't see:

- Module-level imports, followed transitively. Lines that run at import time
  (constants, class bodies, decorators) carry no test context in coverage.
- Every import and module-naming string in tests and repo tooling, transitively:
  ``mocker.patch("gaia.llm.lemonade_client.X")``, ``python -c "import gaia.cli"``,
  ``import check_test_lane_coverage`` after a ``sys.path`` insert.
- A file named by a string (``"ci_tiers.json"``) is read by whoever names it.
- Tests and tooling that list a directory (``rglob``, ``os.walk``) are affected
  by any file under a directory whose name they spell out.
- A ``conftest.py`` below the suite root reaches every file beneath it.
- A package ``__init__`` reaches everything under it when it changes, but the
  walk never continues *through* one: ``gaia/__init__`` imports the agent base
  eagerly, and following that would make every test depend on everything.

Without a map, imports inside functions are followed too: more tests, never
fewer, and the summary says so.

Some changes run the whole suite, because their reach can't be read at all: a
top-level ``conftest.py``, packaging or pytest config, a deleted or renamed
``.py`` file, this script, or the workflow that runs it. So does a selection
over ``FULL_FRACTION`` of the suite, where the saving is gone anyway.

This is a speed-up for pull requests, not the coverage of record: test_unit.yml
runs the full suite on every push to main and nightly on all three operating
systems, and the nightly run rebuilds the map.

Usage::

    python util/select_tests.py select --base origin/main --map test_map.json --out sel.txt
    python util/select_tests.py select --changed src/gaia/security.py --explain
    python util/select_tests.py build-map --coverage .coverage --out test_map.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_ROOT = "tests/unit"
MAP_VERSION = 1
# The nightly run rebuilds the map; older than this means it stopped.
MAX_MAP_AGE = timedelta(days=3)
FULL_FRACTION = 0.7
# A basename shared by more files than this ("agent.py") says nothing about
# which one a string means.
MAX_BASENAME_FANOUT = 25
_IGNORED_BASENAMES = {"__init__.py", "README.md", "LICENSE", ".gitignore"}
_SCANNER_RE = re.compile(r"\.rglob\(|\.glob\(|os\.walk\(|\.iterdir\(|os\.listdir\(")
_DOTTED_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+(:[A-Za-z_]\w*)?$")
_PATH_SPLIT_RE = re.compile(r"[\\/]")
_ISOLATION_RE = re.compile(r"isolate|guard|restore|block|forbid")
_SPAWN_RE = re.compile(r"sys\.executable|\bsubprocess\b|\bPopen\b|create_subprocess")

FULL_RUN_FILES = {
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "pytest.ini",
    "tox.ini",
    "util/select_tests.py",
    ".github/workflows/test_unit.yml",
}
FULL_RUN_BASENAMES = {"conftest.py"}

# Windows and macOS run only the selected tests that touch the operating system.
PLATFORM_RE = re.compile(
    r"\bsubprocess\b|\bos\.name\b|\bsys\.platform\b|\bplatform\.(?:system|"
    r"machine|release|mac_ver|win32)|\bsignal\.|\bpsutil\b|\bmultiprocessing\b|"
    r"\bos\.(?:replace|rename|kill|chmod|symlink|getpid|killpg|setsid|startfile|"
    r"sep|pathsep|linesep)\b|\bfcntl\b|\bmsvcrt\b|\bwin32|\bctypes\b|\bsocket\b|"
    r"\bPermissionError\b|\bshutil\.(?:which|rmtree|move)\b|\bsymlink_to\b|"
    r"\bchmod\b|\bdarwin\b|\b[Ww]indows\b|\bmacOS\b|\bposix\b|\bexpanduser\b|"
    r"\bPopen\b|\bselectors\b|\bcreate_subprocess|\bPureWindowsPath\b|\bntpath\b"
)
PLATFORM_MODULE_PREFIXES = (
    "gaia.daemon",
    "gaia.sidecar",
    "gaia.installer",
    "gaia.security",
    "gaia.filesystem",
    "gaia.utils.file_lock",
    "gaia.utils.file_watcher",
    "gaia.llm.lemonade_launcher",
    "gaia.agents.tools.shell_tools",
    "gaia.agents.tools.file_io_tools",
    "gaia.electron",
    "gaia.audio",
)


def _edges() -> Dict[str, Set[str]]:
    return defaultdict(set)


@dataclass
class Graph:
    files: Set[str]
    repo: Path = REPO_ROOT
    # Followed transitively, always.
    deps: Dict[str, Set[str]] = field(default_factory=_edges)
    # Imports inside function bodies of package code: followed only without a map.
    lazy_deps: Dict[str, Set[str]] = field(default_factory=_edges)
    # Parent-package __init__ files: followed one hop, from a changed __init__.
    init_deps: Dict[str, Set[str]] = field(default_factory=_edges)
    # Files named by a string: followed one hop, from the changed file.
    read_deps: Dict[str, Set[str]] = field(default_factory=_edges)
    scanner_tokens: Dict[str, Set[str]] = field(default_factory=dict)
    # Modules a test runs in a child process (`python -m gaia.cli`): coverage
    # can't see inside it, so their function-level imports always count.
    subprocess_targets: Dict[str, Set[str]] = field(default_factory=_edges)
    imported_modules: Dict[str, Set[str]] = field(default_factory=_edges)
    sources: Dict[str, str] = field(default_factory=dict)


@dataclass
class Selection:
    full: bool
    reason: str
    tests: List[str]
    total: int


def _reverse(edges: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    rev: Dict[str, Set[str]] = defaultdict(set)
    for src, targets in edges.items():
        for target in targets:
            rev[target].add(src)
    return rev


def tracked_files(repo: Path = REPO_ROOT) -> List[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def _module_roots(files: Iterable[str]) -> List[str]:
    """Directories whose children are importable top-level packages."""
    roots = {"", "src"}
    for path in files:
        parts = PurePosixPath(path).parts
        if len(parts) >= 5 and parts[:2] == ("hub", "agents") and parts[3] == "python":
            roots.add("/".join(parts[:4]))
    return sorted(roots, key=len, reverse=True)


def _module_name(path: str, root: str) -> Optional[str]:
    if root and not path.startswith(root + "/"):
        return None
    rel = path[len(root) + 1 :] if root else path
    if not rel.endswith(".py"):
        return None
    parts = list(PurePosixPath(rel[:-3]).parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def is_package_code(path: str, roots: List[str]) -> bool:
    """Shipped code under src/ or a hub agent package, not its tests."""
    if "/tests/" in f"/{path}":
        return False
    return path.startswith("src/") or any(
        root and path.startswith(root + "/") for root in roots
    )


def is_test_file(path: str, test_root: str = DEFAULT_TEST_ROOT) -> bool:
    p = PurePosixPath(path)
    return (
        path.startswith(test_root.rstrip("/") + "/")
        and p.suffix == ".py"
        and (p.name.startswith("test_") or p.name.endswith("_test.py"))
    )


class _Imports(ast.NodeVisitor):
    """Collect imports, split by whether they run at import time."""

    def __init__(self) -> None:
        self.module_level: List[ast.AST] = []
        self.in_function: List[ast.AST] = []
        self.strings: List[str] = []
        self._depth = 0

    def _function(self, node: ast.AST) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _function

    def _import(self, node: ast.AST) -> None:
        (self.in_function if self._depth else self.module_level).append(node)

    visit_Import = visit_ImportFrom = _import

    def visit_If(self, node: ast.If) -> None:
        # `if TYPE_CHECKING:` imports never run.
        test = node.test
        name = getattr(test, "id", None) or getattr(test, "attr", None)
        if name == "TYPE_CHECKING":
            for stmt in node.orelse:
                self.visit(stmt)
            return
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.strings.append(node.value)


def build_graph(repo: Path = REPO_ROOT, files: Optional[List[str]] = None) -> Graph:
    files = files if files is not None else tracked_files(repo)
    roots = _module_roots(files)
    module_files: Dict[str, str] = {}
    stems: Dict[str, Set[str]] = defaultdict(set)
    basenames: Dict[str, Set[str]] = defaultdict(set)
    for path in files:
        basenames[PurePosixPath(path).name].add(path)
        if path.endswith(".py"):
            stems[PurePosixPath(path).stem].add(path)
            for root in roots:
                name = _module_name(path, root)
                if name is not None:
                    # src/ and the hub roots win over the repo root ("src.gaia.x").
                    module_files.setdefault(name, path)
    graph = Graph(files=set(files), repo=repo)
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    packages = sorted(
        name
        for name, path in module_files.items()
        if "." not in name
        and path.endswith("__init__.py")
        and not path.startswith("tests/")
        and name not in stdlib
    )
    # "setup.py" looks dotted too; only names under a real package count.
    dotted_roots = set(packages) | {"tests"}
    embedded = re.compile(
        r"\b(?:%s)(?:\.[A-Za-z_]\w*)+" % "|".join(map(re.escape, packages))
    )

    def resolve(name: str) -> Optional[str]:
        while name:
            if name in module_files:
                return name
            name = name.rpartition(".")[0]
        return None

    def link(src: str, name: str, edges: Dict[str, Set[str]], parent_only=False):
        found = resolve(name)
        if found is None:
            # A script imported by bare name after a sys.path insert.
            if "." not in name and name not in stdlib:
                edges[src].update(t for t in stems.get(name, ()) if t != src)
            return
        graph.imported_modules[src].add(found)
        target = module_files[found]
        if target != src:
            if parent_only and target.endswith("__init__.py"):
                graph.init_deps[src].add(target)
            else:
                edges[src].add(target)
        parts = found.split(".")
        for i in range(1, len(parts)):
            parent = module_files.get(".".join(parts[:i]))
            if parent and parent.endswith("__init__.py") and parent != src:
                graph.init_deps[src].add(parent)

    def import_names(node: ast.AST, package: Optional[str]) -> List[Tuple[str, bool]]:
        """``(module, parent_only)`` for each module an import statement loads."""
        if isinstance(node, ast.Import):
            return [(alias.name, False) for alias in node.names]
        base = node.module or ""
        if node.level:
            pkg = package.split(".") if package else []
            keep = len(pkg) - (node.level - 1)
            if keep < 0:
                return []
            prefix = ".".join(pkg[:keep])
            base = f"{prefix}.{base}".strip(".") if base else prefix
        if not base:
            return []
        subs = [
            f"{base}.{a.name}" for a in node.names if f"{base}.{a.name}" in module_files
        ]
        # `from pkg import submodule` needs the submodule, not all of pkg.
        return [(base, len(subs) == len(node.names))] + [(s, False) for s in subs]

    def link_import(src: str, node: ast.AST, package: Optional[str], edges) -> None:
        for name, parent_only in import_names(node, package):
            link(src, name, edges, parent_only=parent_only)

    def import_files(node: ast.AST, package: Optional[str]) -> Set[str]:
        named = import_names(node, package)
        found = (resolve(name) for name, parent_only in named if not parent_only)
        return {module_files[f] for f in found if f}

    for path in files:
        if not path.endswith(".py"):
            continue
        try:
            source = (repo / path).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=path)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
            raise SystemExit(
                f"select_tests: cannot parse {path} ({exc}). Fix the file, or "
                "run the full suite."
            ) from exc
        graph.sources[path] = source
        package_code = is_package_code(path, roots)
        spawns = not package_code and _SPAWN_RE.search(source) is not None
        package = None
        for root in roots:
            name = _module_name(path, root)
            if name is not None:
                package = (
                    name if path.endswith("__init__.py") else name.rpartition(".")[0]
                )
                break
        found = _Imports()
        found.visit(tree)
        for node in found.module_level:
            link_import(path, node, package, graph.deps)
        lazy = graph.lazy_deps if package_code else graph.deps
        for node in found.in_function:
            link_import(path, node, package, lazy)

        tokens: Set[str] = set()
        for text in found.strings:
            value = text.strip()
            if not package_code:
                head = value.split(".")[0]
                named: List[str] = []
                if _DOTTED_RE.match(value) and head in dotted_roots:
                    # A patch target like "gaia.llm" names a package, not its __init__.
                    named = [value.split(":")[0]]
                elif packages and "." in value:
                    # Code in a string: `python -c "import gaia.audio.whisper_asr"`.
                    named = embedded.findall(value)
                for name in named:
                    link(path, name, graph.deps, parent_only=True)
                    found = resolve(name)
                    if spawns and found:
                        graph.subprocess_targets[path].add(module_files[found])
            pieces = [p for p in _PATH_SPLIT_RE.split(value) if p]
            if not pieces:
                continue
            tokens.update(p for p in pieces if "\n" not in p and len(p) < 80)
            name = pieces[-1]
            # Shipped code naming a script ("run util/lint.py") points the user
            # at it; only the data files it names are reads.
            if name in _IGNORED_BASENAMES or (package_code and name.endswith(".py")):
                continue
            targets = basenames.get(name, ())
            if 0 < len(targets) <= MAX_BASENAME_FANOUT:
                graph.read_deps[path].update(t for t in targets if t != path)
        # Shipped code that lists a directory is linked by the names it reads.
        if not package_code and _SCANNER_RE.search(source):
            graph.scanner_tokens[path] = tokens

    # A conftest's fixtures reach every file below it. The top-level ones mostly
    # stub shared services, so there a test depends on the fixtures it asks for
    # by name (``ui_api_client`` builds the whole Agent UI app), transitively,
    # and one hop on what each autouse fixture imports: a change to a module an
    # autouse fixture patches reaches every test, but that module's own imports
    # don't.
    for conftest in (f for f in files if PurePosixPath(f).name == "conftest.py"):
        folder = PurePosixPath(conftest).parent
        prefix = "" if folder.as_posix() == "." else folder.as_posix() + "/"
        below = [
            p
            for p in files
            if p.startswith(prefix) and p.endswith(".py") and p != conftest
        ]
        if len(folder.parts) >= 3:
            for path in below:
                graph.deps[path].add(conftest)
            continue
        fixtures, autouse = _fixture_imports(graph.sources.get(conftest, ""), conftest)
        if not fixtures:
            continue
        package = _module_name(conftest, "")
        package = package.rpartition(".")[0] if package else None
        patched: Set[str] = set()
        for name in autouse:
            for node in fixtures.get(name, ()):
                patched |= import_files(node, package)
        for path in below:
            graph.read_deps[path] |= patched - {path}
            used = _requested_fixtures(graph.sources.get(path, ""))
            for name in used & fixtures.keys():
                for node in fixtures[name]:
                    link_import(path, node, package, graph.deps)
    return graph


def _fixture_imports(
    source: str, path: str
) -> Tuple[Dict[str, List[ast.AST]], Set[str]]:
    """Each fixture in a conftest with the imports it and the fixtures it asks
    for make, and the names of the autouse ones."""
    if not source:
        return {}, set()
    own: Dict[str, List[ast.AST]] = {}
    wants: Dict[str, Set[str]] = {}
    autouse: Set[str] = set()
    for node in ast.walk(ast.parse(source, filename=path)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = [ast.unparse(d) for d in node.decorator_list]
        if not any("fixture" in d for d in decorators):
            continue
        # Only the isolation guards: if a change breaks a fixture that stubs a
        # service, the service's own (selected) tests fail too, but a broken
        # isolation fixture lets every other test leak into the real home.
        if any("autouse=True" in d for d in decorators) and _ISOLATION_RE.search(
            node.name
        ):
            autouse.add(node.name)
        own[node.name] = [
            n for n in ast.walk(node) if isinstance(n, (ast.Import, ast.ImportFrom))
        ]
        wants[node.name] = {a.arg for a in node.args.args + node.args.kwonlyargs}
    fixtures: Dict[str, List[ast.AST]] = {}
    for name in own:
        seen, stack, imports = {name}, [name], []
        while stack:
            current = stack.pop()
            imports.extend(own.get(current, ()))
            for dep in wants.get(current, ()):
                if dep in own and dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        if imports:
            fixtures[name] = imports
    return fixtures, autouse


def _requested_fixtures(source: str) -> Set[str]:
    """Argument names of every function in a test file: the fixtures it asks for."""
    if not source:
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.update(a.arg for a in node.args.args + node.args.kwonlyargs)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # @pytest.mark.usefixtures("name")
            if node.value.isidentifier():
                names.add(node.value)
    return names


def load_map(path: Path) -> Dict[str, Set[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != MAP_VERSION:
        raise SystemExit(
            f"select_tests: {path} is map version {data.get('version')}, this "
            f"script reads {MAP_VERSION}. Rebuild it with `build-map`."
        )
    built = datetime.fromisoformat(data["built_at"])
    age = datetime.now(timezone.utc) - built
    if age > MAX_MAP_AGE:
        print(
            f"::warning::select_tests: the coverage map is {age.days} days old "
            "(built by the nightly unit run). Tests added to code since then are "
            "found only through imports. Check why the nightly stopped saving it.",
            file=sys.stderr,
        )
    tests = data["tests"]
    return {src: {tests[i] for i in idx} for src, idx in data["files"].items()}


def build_map(coverage_file: Path, repo: Path = REPO_ROOT) -> dict:
    from coverage import CoverageData

    data = CoverageData(basename=str(coverage_file))
    data.read()
    tests: Dict[str, int] = {}
    files: Dict[str, Set[int]] = {}
    for measured in data.measured_files():
        try:
            rel = Path(measured).resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            continue
        hit: Set[int] = set()
        for contexts in (data.contexts_by_lineno(measured) or {}).values():
            for context in contexts:
                test = context.split("::", 1)[0]
                if not test.endswith(".py"):
                    continue
                hit.add(tests.setdefault(test.replace("\\", "/"), len(tests)))
        if hit:
            files[rel] = hit
    names = sorted(tests, key=tests.get)
    return {
        "version": MAP_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "tests": names,
        "files": {f: sorted(ix) for f, ix in sorted(files.items())},
    }


def affected_files(
    graph: Graph, changed: Iterable[str], coverage: Optional[Dict[str, Set[str]]]
) -> Set[str]:
    changed = set(changed)
    affected: Set[str] = set(changed)
    rev_read = _reverse(graph.read_deps)
    rev_init = _reverse(graph.init_deps)
    for path in changed:
        affected.update(rev_read.get(path, ()))
        if path.endswith("__init__.py"):
            affected.update(rev_init.get(path, ()))
        dirs = PurePosixPath(path).parts[:-1]
        for scanner, tokens in graph.scanner_tokens.items():
            if any(d in tokens for d in dirs):
                affected.add(scanner)
    edges = dict(graph.deps)
    if coverage is None:
        for src, targets in graph.lazy_deps.items():
            edges[src] = set(edges.get(src, ())) | targets
    rev = _reverse(edges)
    stack = list(affected)
    while stack:
        node = stack.pop()
        for dependant in rev.get(node, ()):
            if dependant not in affected:
                affected.add(dependant)
                stack.append(dependant)
    if coverage is not None:
        for node in list(affected):
            affected.update(coverage.get(node, ()))
        # Coverage never sees inside a child process, so a test that runs
        # `python -m gaia.cli` depends on everything the CLI imports, lazily too.
        every = {src: set(targets) for src, targets in graph.deps.items()}
        for src, targets in graph.lazy_deps.items():
            every.setdefault(src, set()).update(targets)
        reach: Dict[str, Set[str]] = {}
        for test, targets in graph.subprocess_targets.items():
            if test in affected:
                continue
            for target in targets:
                if target not in reach:
                    reach[target] = _forward(every, target)
                if reach[target] & affected:
                    affected.add(test)
                    break
    return affected


def _forward(edges: Dict[str, Set[str]], start: str) -> Set[str]:
    seen, stack = {start}, [start]
    while stack:
        for target in edges.get(stack.pop(), ()):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


def full_run_reason(
    changed: Iterable[str], graph: Graph, repo: Path = REPO_ROOT
) -> Optional[str]:
    for path in sorted(changed):
        if path in FULL_RUN_FILES or PurePosixPath(path).name in FULL_RUN_BASENAMES:
            return f"{path} changed"
        # A test may still name the old path or module; nothing on disk does.
        if not (repo / path).exists():
            return f"{path} was deleted or renamed"
        if path.endswith(".py") and path not in graph.files:
            return f"{path} is not tracked by git, so its imports were not read"
    return None


def is_platform_sensitive(graph: Graph, test: str) -> bool:
    if PLATFORM_RE.search(graph.sources.get(test, "")):
        return True
    return any(
        mod == prefix or mod.startswith(prefix + ".")
        for mod in graph.imported_modules.get(test, ())
        for prefix in PLATFORM_MODULE_PREFIXES
    )


def select(
    graph: Graph,
    changed: Iterable[str],
    coverage: Optional[Dict[str, Set[str]]] = None,
    test_root: str = DEFAULT_TEST_ROOT,
    platform_only: bool = False,
) -> Selection:
    changed = sorted(set(changed))
    all_tests = sorted(p for p in graph.files if is_test_file(p, test_root))
    reason = full_run_reason(changed, graph, graph.repo)
    tests = all_tests
    if reason is None:
        hit = affected_files(graph, changed, coverage)
        tests = [p for p in all_tests if p in hit]
        if len(tests) > FULL_FRACTION * len(all_tests):
            reason = f"{len(tests)} of {len(all_tests)} test files affected"
            tests = all_tests
    if platform_only:
        tests = [p for p in tests if is_platform_sensitive(graph, p)]
    detail = reason or f"{len(changed)} changed file(s)"
    if coverage is None and reason is None:
        detail += "; no coverage map, so imports inside functions were followed too"
    return Selection(
        full=reason is not None and not platform_only,
        reason=detail,
        tests=tests,
        total=len(all_tests),
    )


def changed_since(base: str, repo: Path = REPO_ROOT) -> List[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"select_tests: `git diff {base}...HEAD` failed: {result.stderr.strip()}. "
            "Fetch the base branch first (actions/checkout with fetch-depth: 0)."
        )
    return [line for line in result.stdout.splitlines() if line]


def _cmd_select(args: argparse.Namespace) -> int:
    changed = changed_since(args.base) if args.base else args.changed
    coverage = None
    if args.map:
        if Path(args.map).is_file():
            coverage = load_map(Path(args.map))
        else:
            print(
                f"::warning::select_tests: no coverage map at {args.map}; "
                "selecting from imports alone, which runs more tests.",
                file=sys.stderr,
            )
    graph = build_graph()
    sel = select(graph, changed, coverage, args.test_root, args.platform_only)
    kind = "full suite" if sel.full else "selected"
    summary = f"{kind}: {len(sel.tests)} of {sel.total} test files ({sel.reason})"
    print(f"select_tests: {summary}", file=sys.stderr)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(f"**Test selection** — {summary}\n\n")
    if args.explain:
        print("\n".join(sel.tests))
    if args.out:
        Path(args.out).write_text("".join(f"{t}\n" for t in sel.tests), "utf-8")
    if args.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"{args.github_output}_full={str(sel.full).lower()}\n")
            fh.write(f"{args.github_output}_count={len(sel.tests)}\n")
    return 0


def _cmd_build_map(args: argparse.Namespace) -> int:
    result = build_map(Path(args.coverage))
    if not result["files"]:
        raise SystemExit(
            f"select_tests: {args.coverage} has no per-test contexts. Run pytest "
            "with --cov-context=test."
        )
    Path(args.out).write_text(json.dumps(result, separators=(",", ":")), "utf-8")
    print(
        f"select_tests: mapped {len(result['files'])} source files to "
        f"{len(result['tests'])} test files -> {args.out}",
        file=sys.stderr,
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sel = sub.add_parser("select", help="list the test files a change affects")
    source = sel.add_mutually_exclusive_group(required=True)
    source.add_argument("--base", help="git ref to diff HEAD against")
    source.add_argument("--changed", nargs="+", help="changed paths, repo-relative")
    sel.add_argument("--map", help="coverage map from build-map")
    sel.add_argument("--test-root", default=DEFAULT_TEST_ROOT)
    sel.add_argument(
        "--platform-only",
        action="store_true",
        help="keep only tests that touch the operating system",
    )
    sel.add_argument("--out", help="write one test path per line here")
    sel.add_argument("--explain", action="store_true", help="print the selection")
    sel.add_argument(
        "--github-output",
        metavar="PREFIX",
        help="write PREFIX_full and PREFIX_count to $GITHUB_OUTPUT",
    )
    sel.set_defaults(func=_cmd_select)

    bm = sub.add_parser("build-map", help="turn a coverage file into a test map")
    bm.add_argument("--coverage", required=True, help="a .coverage data file")
    bm.add_argument("--out", required=True)
    bm.set_defaults(func=_cmd_build_map)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
