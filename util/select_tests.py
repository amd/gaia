# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pick the unit-test files a change can affect, so a PR runs those and not all.

**Narrowing is an allowlist.** A PR runs a subset only when every changed file
is a unit-test file, shipped ``.py`` code under a root the coverage map
measures (``src/gaia``, and ``hub/agents`` when the map includes it) with an
entry in the map, or a file tests only ever read (``read_only_class``: docs
pages, Agent UI sources, the Go TUI tree, hub data), which reaches just the
tests that name it or scan its folder. Everything else runs the whole suite:
other data files, pinned manifests and lockfiles, ``util/`` scripts, test
helpers and fixtures, ``eval/``, configs, workflows, any ``conftest.py``, a deleted or renamed file, a missing or stale map, this script
and the workflow that runs it. Tests load such files by path
(``spec_from_file_location``, ``runpy``, ``open``), and neither imports nor
coverage can follow what those scripts import or read. A selection over
``FULL_FRACTION`` of the suite also runs everything, since the saving is gone.

For a change that passes the allowlist, two sources say which tests it reaches:

**The coverage map** (``build-map``), from a full run with per-test coverage
contexts on main. It lists, for each source file, the test files that executed
it. This is what makes the selection tight: most of ``src/gaia`` imports most of
the rest inside function bodies, so static reading alone would run nearly
everything for nearly any change.

**Static rules**, added on top for what coverage can't see:

- Module-level imports, followed transitively. Lines that run at import time
  (constants, class bodies, decorators) carry no test context in coverage.
- Every import and module-naming string in tests and repo tooling, transitively:
  ``mocker.patch("gaia.llm.lemonade_client.X")``, ``python -c "import gaia.cli"``.
- A file named by a string is read by whoever names it, matched by its whole
  path where one is spelled out.
- Tests and tooling that list a directory (``rglob``, ``os.walk``) are affected
  by any file under a directory whose name they spell out.
- A ``conftest.py`` below the suite root reaches every file beneath it.
- A package ``__init__`` reaches everything under it when it changes, but the
  walk never continues *through* one: ``gaia/__init__`` imports the agent base
  eagerly, and following that would make every test depend on everything.
- Code run in a child process (``python -m gaia.daemon``), by a test or by
  shipped code, counts with all its imports, since coverage can't see inside.
- A test the map never ran (added since the nightly build) follows imports
  inside functions too.

**The workflow's own paths filter** (``--trigger-paths-from``) decides only
whether anything runs, as GitHub does for a pull request: a merge-queue group
has no paths filter, so a group none of whose files is in it (docs only) selects
nothing. It never drops a file from a group that does run. Unit tests read many
files outside the filter (``docs/``, ``.claude/``, the root instruction files),
so a code change shipped beside one of those runs everything, as on its PR.
The root instruction files, ``.claude/`` and ``scripts/`` also force the full
suite on their own, since the doc-path tests read them.

On a pull request the macOS lane runs only when ``os_reason`` finds a changed
file that touches the operating system. The ``ci:full`` label passes
``--force-full`` and runs everything on every OS.

``lane-scope`` makes the same kind of call for a whole workflow: build_tui.yml
runs one core freeze, not its Go and installer matrix, when only the Agent UI
changed.

This is a speed-up for pull requests and the merge queue, not the coverage of
record: test_unit.yml runs the full suite on Linux on every push to main, on
every dispatch (the release gate's run) and nightly on all three operating
systems, and the nightly run rebuilds the map.

Usage::

    python util/select_tests.py select --base origin/main --map test_map.json --out sel.txt
    python util/select_tests.py select --base origin/main --explain \
        --trigger-paths-from .github/workflows/test_unit.yml
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
_PATH_JOINERS = {"join", "joinpath", "Path", "PurePath", "PurePosixPath"}
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
    # Outside test_unit.yml's paths filter, but read by the doc-path tests
    # (test_ai_doc_paths_exist, test_amd_gaia_urls, test_project_instructions,
    # test_review_gates), so a merge-queue group of only these still runs them.
    "CLAUDE.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "REVIEW.md",
}
FULL_RUN_PREFIXES = (".claude/", "scripts/")
FULL_RUN_BASENAMES = {"conftest.py"}
# Pinned or generated files several lanes read; they keep forcing a full run
# even inside a folder that otherwise narrows.
PINNED_BASENAMES = {
    "binaries.lock.json",
    "package.json",
    "package-lock.json",
    "go.mod",
    "go.sum",
    "pyproject.toml",
    "setup.cfg",
}
WEBUI_ROOT = "src/gaia/apps/webui/"

# Filter syntax beyond `*` and `**`, refused rather than misread.
_UNSUPPORTED_GLOB_RE = re.compile(r"[!?+\[\]]")

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
# Changed code that touches the operating system, so the macOS lane runs on a
# PR: processes, shells, paths, locks, ports, keyring and the installer. Push,
# merge queue and nightly runs ignore this and still run macOS.
OS_MODULE_PREFIXES = PLATFORM_MODULE_PREFIXES + (
    "gaia.agents.tools.shell_session",
    "gaia.agents.tools.shell_deletes",
    "gaia.agents.tools.path_access",
    "gaia.agents.tools.workspace_paths",
    "gaia.agents.base.credential_reads",
    "gaia.connectors",
    "gaia.mcp.client.transports.stdio",
    "gaia.mcp.ports",
    "gaia.messaging.discord.credentials",
    "gaia.messaging.slack.credentials",
    "gaia.schedule.lock",
    "gaia.skills.lock",
    "gaia.skills.binaries",
    "gaia.skills.binary_setup",
    "gaia.utils.paths",
    "gaia.utils.process_owner",
    "gaia.utils.process_tree",
    "gaia.ports",
    "gaia.env",
    "gaia.device",
    "gaia.login_path",
    "gaia.log_rotation",
    "gaia.program_lookup",
    "gaia.cli",
    "gaia.llm.lemonade_client",
    "gaia.llm.lemonade_embedded",
    "gaia.llm.lemonade_tls",
    "gaia.llm.gpu_memory",
    "gaia.llm.cloud_keys",
    "gaia.hub.native_launcher",
    "gaia.hub.compatibility",
    "gaia.agents.registry",
    "gaia.agents.base.console",
    "gaia.agents.base.discovery",
    "gaia.agents.base.memory",
    "gaia.agents.base.system_context",
    "gaia.agents.tools.protected_folders",
    "gaia.agents.tools.screenshot_tools",
    "gaia.utils.user_folders",
    "gaia.utils.terminal",
    "gaia.ui.routers.files",
    "gaia.ui.tunnel",
    "gaia.ui.devices.listener",
    "gaia.mcp.client.mcp_client",
    "gaia.eval.bench.config",
    "gaia.eval.bench.sandbox",
)
OS_PATH_PREFIXES = ("installer/",)
# A changed test that spawns processes, sends signals or reads the platform.
OS_CALL_RE = re.compile(
    r"\bsubprocess\b|\bPopen\b|create_subprocess|\bpsutil\b|\bmultiprocessing\b|"
    r"\bos\.(?:kill|killpg|setsid|chmod|symlink|startfile|name)\b|\bsignal\.\w|"
    r"\bfcntl\b|\bmsvcrt\b|\bctypes\b|\bwinreg\b|\bkeyring\b|\bsymlink_to\b|"
    r"\bsys\.platform\b|\bplatform\.system\b"
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
    # Folders a test names by path and reads a file from by a computed name
    # (`LOCALES / f"{locale}.ts"`): any non-Python file under one reaches it.
    folder_reads: Dict[str, Set[str]] = field(default_factory=_edges)
    # Modules a test runs in a child process (`python -m gaia.cli`): coverage
    # can't see inside it, so their function-level imports always count.
    subprocess_targets: Dict[str, Set[str]] = field(default_factory=_edges)
    # Modules shipped code runs in a child process (`-m gaia.daemon`): the
    # tests that run the spawning file depend on them, and coverage can't tell.
    spawn_deps: Dict[str, Set[str]] = field(default_factory=_edges)
    imported_modules: Dict[str, Set[str]] = field(default_factory=_edges)
    sources: Dict[str, str] = field(default_factory=dict)


@dataclass
class Selection:
    full: bool
    reason: str
    tests: List[str]
    total: int
    # False: no changed file is in the workflow's paths filter, so nothing runs.
    triggered: bool = True


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
        # Pieces of a joined path: "SKILL.md" there is not a bare file name.
        self.path_pieces: List[str] = []
        self._joined: Set[int] = set()
        # Constant folders a joined path continues past with a computed name
        # (`ROOT / "hub" / "skills" / name`).
        self.folder_strings: List[str] = []
        # Inner links of a `/` chain, already read with the whole chain.
        self._chained: Set[int] = set()
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
            if id(node) in self._joined:
                self.path_pieces.append(node.value)
            else:
                self.strings.append(node.value)

    def _add_path(self, parts: List[ast.AST]) -> None:
        """Record the constant pieces that end a path as one joined string."""
        tail: List[ast.Constant] = []
        for part in reversed(parts):
            if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
                break
            tail.append(part)
        if len(tail) > 1:
            self.strings.append("/".join(c.value for c in reversed(tail)))
            self._joined.update(id(c) for c in tail)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Div) and id(node) not in self._chained:
            parts: List[ast.AST] = []
            current: ast.AST = node
            while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
                parts.insert(0, current.right)
                self._chained.add(id(current))
                current = current.left
            if _joins_a_path(current):
                parts = list(current.args) + parts
            self._add_path(parts)
            self._add_folders(parts)
        self.generic_visit(node)

    def _add_folders(self, parts: List[ast.AST]) -> None:
        """Record each run of constant pieces a computed piece follows."""
        run: List[str] = []
        for part in parts:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                run.append(part.value)
                continue
            if len(run) > 1:
                self.folder_strings.append("/".join(run))
            run = []

    def visit_Call(self, node: ast.Call) -> None:
        if _joins_a_path(node):
            self._add_path(node.args)
        self.generic_visit(node)


def _joins_a_path(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
    return name in _PATH_JOINERS


def build_graph(repo: Path = REPO_ROOT, files: Optional[List[str]] = None) -> Graph:
    files = files if files is not None else tracked_files(repo)
    roots = _module_roots(files)
    module_files: Dict[str, str] = {}
    stems: Dict[str, Set[str]] = defaultdict(set)
    basenames: Dict[str, Set[str]] = defaultdict(set)
    path_suffixes: Dict[str, Set[str]] = defaultdict(set)
    dir_suffixes: Dict[str, Set[str]] = defaultdict(set)
    for path in files:
        basenames[PurePosixPath(path).name].add(path)
        parts = PurePosixPath(path).parts
        for start in range(len(parts) - 1):
            path_suffixes["/".join(parts[start:])].add(path)
        for end in range(1, len(parts)):
            folder = "/".join(parts[:end])
            for start in range(end):
                dir_suffixes["/".join(parts[start:end])].add(folder)
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

    def read_targets(pieces: List[str], package_code: bool) -> Set[str]:
        """The tracked files a string naming a path can mean."""
        name = pieces[-1]
        # Shipped code naming a script ("run util/lint.py") points the user at
        # it; only the data files it names are reads.
        if package_code and name.endswith(".py"):
            return set()
        for start in range(len(pieces) - 1):
            found = path_suffixes.get("/".join(pieces[start:]))
            if found and len(found) <= MAX_BASENAME_FANOUT:
                return found
        targets = basenames.get(name, set())
        # A test naming SKILL.md under an unknown folder may read any of them,
        # and coverage records only .py files, so nothing else would catch it.
        if not package_code and not name.endswith(".py"):
            return targets
        if name in _IGNORED_BASENAMES or len(targets) > MAX_BASENAME_FANOUT:
            return set()
        return targets

    def folder_targets(pieces: List[str]) -> Set[str]:
        """The tracked folders a string of two or more path pieces can mean."""
        # One piece alone ("src", "docs") says nothing about which folder.
        for start in range(len(pieces) - 1):
            found = dir_suffixes.get("/".join(pieces[start:]))
            if found and len(found) <= MAX_BASENAME_FANOUT:
                return found
        return set()

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
        spawns = _SPAWN_RE.search(source) is not None
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

        tokens: Set[str] = {
            p for text in found.path_pieces for p in _PATH_SPLIT_RE.split(text) if p
        }
        if not package_code:
            for text in found.folder_strings:
                folders = folder_targets([p for p in _PATH_SPLIT_RE.split(text) if p])
                graph.folder_reads[path].update(folders)
        for text in found.strings:
            value = text.strip()
            if package_code and spawns and "." in value:
                for name in embedded.findall(value):
                    target = resolve(name)
                    if target and module_files[target] != path:
                        graph.spawn_deps[path].add(module_files[target])
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
            targets = read_targets(pieces, package_code)
            graph.read_deps[path].update(t for t in targets if t != path)
            if not package_code and not targets:
                graph.folder_reads[path].update(folder_targets(pieces))
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


def load_map(path: Path) -> Tuple[Dict[str, Set[str]], bool]:
    """The map, and whether it is too old to be trusted on its own."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != MAP_VERSION:
        raise SystemExit(
            f"select_tests: {path} is map version {data.get('version')}, this "
            f"script reads {MAP_VERSION}. Rebuild it with `build-map`."
        )
    built = datetime.fromisoformat(data["built_at"])
    age = datetime.now(timezone.utc) - built
    stale = age > MAX_MAP_AGE
    if stale:
        print(
            f"::warning::select_tests: the coverage map is {age.days} days old "
            "(built by the nightly unit run), so the full suite runs. Check why "
            "the nightly stopped saving it.",
            file=sys.stderr,
        )
    tests = data["tests"]
    files = {src: {tests[i] for i in idx} for src, idx in data["files"].items()}
    return files, stale


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
    graph: Graph,
    changed: Iterable[str],
    coverage: Optional[Dict[str, Set[str]]],
) -> Set[str]:
    changed = set(changed)
    affected: Set[str] = set(changed)
    rev_read = _reverse(graph.read_deps)
    rev_init = _reverse(graph.init_deps)
    for path in changed:
        affected.update(rev_read.get(path, ()))
        if path.endswith("__init__.py"):
            affected.update(rev_init.get(path, ()))
        # Python reaches its readers through imports; a folder read is how a
        # test finds a data file by a name it computes.
        if not path.endswith(".py"):
            affected.update(
                reader
                for reader, folders in graph.folder_reads.items()
                if any(path.startswith(folder + "/") for folder in folders)
            )
        dirs = PurePosixPath(path).parts[:-1]
        for scanner, tokens in graph.scanner_tokens.items():
            if any(d in tokens for d in dirs):
                affected.add(scanner)
    every = {src: set(targets) for src, targets in graph.deps.items()}
    for extra in (graph.lazy_deps, graph.spawn_deps):
        for src, targets in extra.items():
            every.setdefault(src, set()).update(targets)
    rev_every = _reverse(every)
    seeds = set(affected)
    if coverage is None:
        return _closure(rev_every, seeds)
    affected = _closure(_reverse(graph.deps), seeds)
    for node in list(affected):
        affected.update(coverage.get(node, ()))
    # Coverage never sees inside a child process, so a file that runs
    # `python -m gaia.cli` depends on everything the CLI imports, lazily too,
    # and so does every test that ran that file.
    reach: Dict[str, Set[str]] = {}
    spawners = list(graph.subprocess_targets.items()) + list(graph.spawn_deps.items())
    for src, targets in spawners:
        if src in affected:
            continue
        for target in targets:
            if target not in reach:
                reach[target] = _forward(every, target)
            if reach[target] & affected:
                affected.add(src)
                affected.update(coverage.get(src, ()))
                break
    # Where the map can't speak, follow every import, as with no map at all:
    # tests it never ran (added since it was built) and source it never saw.
    loose = _closure(rev_every, seeds)
    mapped_tests = set().union(*coverage.values())
    affected.update(f for f in loose if f not in mapped_tests)
    unmapped = {f for f in changed if f.endswith(".py") and f not in coverage}
    if unmapped:
        affected |= _closure(rev_every, unmapped)
    return affected


def _closure(rev: Dict[str, Set[str]], start: Set[str]) -> Set[str]:
    seen, stack = set(start), list(start)
    while stack:
        for dependant in rev.get(stack.pop(), ()):
            if dependant not in seen:
                seen.add(dependant)
                stack.append(dependant)
    return seen


def _forward(edges: Dict[str, Set[str]], start: str) -> Set[str]:
    seen, stack = {start}, [start]
    while stack:
        for target in edges.get(stack.pop(), ()):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


def is_measured(path: str, coverage: Dict[str, Set[str]]) -> bool:
    """Shipped .py code under a root the coverage map measures."""
    if not path.endswith(".py") or "/tests/" in f"/{path}":
        return False
    if path.startswith("src/gaia/"):
        return True
    # hub/agents is measured only when the map was built with it.
    return path.startswith("hub/agents/") and any(
        f.startswith("hub/agents/") for f in coverage
    )


def full_run_reason(
    changed: Iterable[str],
    graph: Graph,
    repo: Path = REPO_ROOT,
    coverage: Optional[Dict[str, Set[str]]] = None,
    stale: bool = False,
    test_root: str = DEFAULT_TEST_ROOT,
) -> Optional[str]:
    """Why the whole suite must run, or None if the selection can narrow.

    Narrowing is an allowlist: every changed file must be a unit-test file,
    shipped .py code the coverage map measures and has an entry for, or a
    read-only file (``read_only_class``). Anything else (other data files, util/ scripts, test helpers and fixtures, eval/, configs,
    workflows) can be loaded by path or read by a script, which neither imports
    nor coverage can follow, so it runs everything.
    """
    if coverage is None:
        return "no coverage map"
    if stale:
        return "the coverage map is stale"
    for path in sorted(changed):
        if forces_full_run(path):
            return f"{path} changed"
        # A test may still name the old path or module; nothing on disk does.
        if not (repo / path).exists():
            return f"{path} was deleted or renamed"
        if path not in graph.files:
            return f"{path} is not tracked by git, so its imports were not read"
        if is_test_file(path, test_root):
            continue
        # Read by path, never imported: the tests that name the file or scan
        # its folder are the ones it reaches (affected_files follows read_deps).
        if read_only_class(path):
            continue
        if not is_measured(path, coverage):
            return f"{path} is outside the code the coverage map measures"
        if path not in coverage:
            return f"{path} has no entry in the coverage map"
    return None


def forces_full_run(path: str) -> bool:
    return (
        path in FULL_RUN_FILES
        or path.startswith(FULL_RUN_PREFIXES)
        or PurePosixPath(path).name in FULL_RUN_BASENAMES
    )


def read_only_class(path: str) -> Optional[str]:
    """The kind of non-Python file that only reaches the tests reading it, or None.

    Docs pages, Agent UI sources, the Go TUI tree and hub data are never
    imported by a test; a test reads them by a path it spells out or by
    scanning their folder, and the graph records both. Anything else that
    isn't Python (fixtures, configs, eval data) still runs the full suite, and
    so do skills and the data inside a hub agent's Python package: shipped
    code loads those by scanning a folder, which the graph can't follow.
    """
    p = PurePosixPath(path)
    if p.suffix == ".py" or p.name in PINNED_BASENAMES or forces_full_run(path):
        return None
    if path.startswith("tests/"):
        return None
    if path.startswith(WEBUI_ROOT):
        return "webui"
    if path.startswith("tui/"):
        return "tui"
    if path.startswith("hub/skills/") or re.match(r"hub/agents/[^/]+/python/", path):
        return None
    if path.startswith("hub/"):
        return "hub data"
    # eval/ prompts and corpora and the installer tree are inputs, not pages.
    if p.suffix in {".md", ".mdx"} and not path.startswith(
        ("src/", "eval/", "installer/")
    ):
        return "docs"
    return None


def is_os_touching_path(path: str) -> bool:
    """Whether a path is in the installer tree or an OS_MODULE_PREFIXES module."""
    module = _module_name(path, "src") or ""
    return path.startswith(OS_PATH_PREFIXES) or any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in OS_MODULE_PREFIXES
    )


def os_reason(changed: Iterable[str], graph: Graph) -> Optional[str]:
    """Why a change needs the macOS lane on a pull request, or None.

    It does for OS-touching code (OS_MODULE_PREFIXES, the installer tree), a
    test that makes OS calls itself, a file that forces a full run, or Python
    whose source can't be read. Anything else runs on Linux and Windows only;
    a macOS-only break in it surfaces on the push to main instead.
    """
    for path in sorted(changed):
        if forces_full_run(path):
            return f"{path} changed"
        if is_os_touching_path(path):
            return f"{path} is OS-touching code"
        if not path.endswith(".py"):
            continue
        source = graph.sources.get(path)
        if source is None:
            return f"{path} was deleted or is untracked, so its OS calls can't be read"
        if is_test_file(path) and OS_CALL_RE.search(source):
            return f"{path} is a test that makes OS calls"
    return None


def workflow_trigger_paths(workflow: Path) -> List[str]:
    """The ``on.pull_request.paths`` filter of a workflow file."""
    import yaml

    data = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
    # YAML 1.1 reads a bare `on:` key as True.
    on = data.get("on", data.get(True))
    pull_request = on.get("pull_request") if isinstance(on, dict) else None
    paths = pull_request.get("paths") if isinstance(pull_request, dict) else None
    if not paths:
        raise SystemExit(
            f"select_tests: {workflow} has no on.pull_request.paths filter to "
            "match changes against. Drop --trigger-paths-from, or add the filter."
        )
    return [str(p) for p in paths]


def path_filter(patterns: Iterable[str]) -> "re.Pattern[str]":
    """One regex for a list of GitHub Actions ``paths`` patterns."""
    parts = []
    for pattern in patterns:
        if _UNSUPPORTED_GLOB_RE.search(pattern):
            raise SystemExit(
                f"select_tests: paths pattern {pattern!r} uses filter syntax this "
                "script does not read (only `*` and `**`). Extend path_filter() "
                "before relying on it."
            )
        out, i = [], 0
        while i < len(pattern):
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
            elif pattern[i] == "*":
                out.append("[^/]*")
                i += 1
            else:
                out.append(re.escape(pattern[i]))
                i += 1
        parts.append("".join(out))
    if not parts:
        raise SystemExit("select_tests: an empty paths filter matches nothing.")
    return re.compile(r"(?:%s)\Z" % "|".join(parts))


def triggering_changes(changed: Iterable[str], patterns: Iterable[str]) -> List[str]:
    """The changed files the paths filter matches, plus any that force a full run."""
    rx = path_filter(patterns)
    return [p for p in changed if rx.match(p) or forces_full_run(p)]


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
    stale: bool = False,
    trigger_paths: Optional[List[str]] = None,
    force_full: Optional[str] = None,
) -> Selection:
    changed = sorted(set(changed))
    all_tests = sorted(p for p in graph.files if is_test_file(p, test_root))
    if force_full:
        return Selection(
            full=True, reason=force_full, tests=all_tests, total=len(all_tests)
        )
    # The filter only gates whether anything runs: tests read files outside it.
    if trigger_paths is not None and not triggering_changes(changed, trigger_paths):
        return Selection(
            full=False,
            reason="no changed file is in the workflow's paths filter",
            tests=[],
            total=len(all_tests),
            triggered=False,
        )
    reason = full_run_reason(changed, graph, graph.repo, coverage, stale, test_root)
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
    stale = False
    if args.map:
        if Path(args.map).is_file():
            coverage, stale = load_map(Path(args.map))
        else:
            print(
                f"::warning::select_tests: no coverage map at {args.map}; "
                "running the full suite.",
                file=sys.stderr,
            )
    trigger_paths = None
    if args.trigger_paths_from:
        trigger_paths = workflow_trigger_paths(Path(args.trigger_paths_from))
    graph = build_graph()
    sel = select(
        graph,
        changed,
        coverage,
        args.test_root,
        args.platform_only,
        stale,
        trigger_paths,
        args.force_full,
    )
    kind = "full suite" if sel.full else "selected"
    summary = f"{kind}: {len(sel.tests)} of {sel.total} test files ({sel.reason})"
    touches_os = args.force_full or os_reason(changed, graph)
    if args.platform_only:
        summary += (
            f"; macOS on a PR: runs ({touches_os})"
            if touches_os
            else "; macOS on a PR: skipped (no OS-touching file changed)"
        )
    _report(summary, "Test selection")
    if args.explain:
        print("\n".join(sel.tests))
    if args.out:
        Path(args.out).write_text("".join(f"{t}\n" for t in sel.tests), "utf-8")
    if args.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"{args.github_output}_full={str(sel.full).lower()}\n")
            fh.write(f"{args.github_output}_count={len(sel.tests)}\n")
            fh.write(f"{args.github_output}_triggered={str(sel.triggered).lower()}\n")
            fh.write(f"{args.github_output}_os={str(bool(touches_os)).lower()}\n")
    return 0


def _report(summary: str, title: str) -> None:
    print(f"select_tests: {summary}", file=sys.stderr)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(f"**{title}** — {summary}\n\n")


def lane_scope(
    changed: Iterable[str],
    patterns: Iterable[str],
    narrow_prefixes: Iterable[str],
    force_full: Optional[str] = None,
) -> Tuple[bool, str]:
    """``(full, reason)``: whether a workflow needs its full job set.

    Narrow only when every changed file the workflow's paths filter matches is
    under one of ``narrow_prefixes``; files outside the filter never triggered
    the workflow, so they don't decide its scope.
    """
    if force_full:
        return True, force_full
    rx = path_filter(patterns)
    matched = sorted(p for p in changed if rx.match(p))
    if not matched:
        return True, "no changed file matches the paths filter, so nothing narrows"
    prefixes = tuple(narrow_prefixes)
    for path in matched:
        if not path.startswith(prefixes):
            return True, f"{path} needs the full job set"
    return False, f"only {', '.join(prefixes)} changed ({len(matched)} file(s))"


def _cmd_lane_scope(args: argparse.Namespace) -> int:
    changed = changed_since(args.base) if args.base else args.changed
    full, reason = lane_scope(
        changed,
        workflow_trigger_paths(Path(args.workflow)),
        args.narrow_prefix,
        args.force_full,
    )
    _report(f"{'full' if full else 'narrow'}: {reason}", "Job scope")
    if args.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"{args.github_output}_full={str(full).lower()}\n")
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
    sel.add_argument(
        "--trigger-paths-from",
        metavar="WORKFLOW",
        help="drop changed files outside this workflow's pull_request paths filter",
    )
    sel.add_argument("--out", help="write one test path per line here")
    sel.add_argument("--explain", action="store_true", help="print the selection")
    sel.add_argument(
        "--force-full",
        metavar="REASON",
        help="run the whole suite for this logged reason (the ci:full label)",
    )
    sel.add_argument(
        "--github-output",
        metavar="PREFIX",
        help="write PREFIX_full, PREFIX_count, PREFIX_triggered and PREFIX_os "
        "to $GITHUB_OUTPUT",
    )
    sel.set_defaults(func=_cmd_select)

    scope = sub.add_parser(
        "lane-scope", help="decide whether a workflow needs its full job set"
    )
    scope_src = scope.add_mutually_exclusive_group(required=True)
    scope_src.add_argument("--base", help="git ref to diff HEAD against")
    scope_src.add_argument("--changed", nargs="+", help="changed paths, repo-relative")
    scope.add_argument(
        "--workflow", required=True, help="workflow whose pull_request paths to read"
    )
    scope.add_argument(
        "--narrow-prefix",
        action="append",
        required=True,
        help="a path prefix that alone needs only the narrow jobs (repeatable)",
    )
    scope.add_argument("--force-full", metavar="REASON")
    scope.add_argument(
        "--github-output", metavar="PREFIX", help="write PREFIX_full to $GITHUB_OUTPUT"
    )
    scope.set_defaults(func=_cmd_lane_scope)

    bm = sub.add_parser("build-map", help="turn a coverage file into a test map")
    bm.add_argument("--coverage", required=True, help="a .coverage data file")
    bm.add_argument("--out", required=True)
    bm.set_defaults(func=_cmd_build_map)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
