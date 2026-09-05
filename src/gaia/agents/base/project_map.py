# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Task-start orientation: a project map injected into the system prompt.

The agent otherwise opens every task blind — it guesses directory names, guesses
which commands exist, and guesses which shell it is talking to. Each wrong guess
costs a full round trip to learn something one orientation pass establishes once.

What the map carries, and why each part is there:

* **Directory shape and likely entry points** — the genuinely new half. Nothing
  else in GAIA states them without the model choosing to call ``tree``.
* **Which binaries are present** — from ``system_context.probe_binaries``, the
  same probe that backs day-0 memory, widened to the developer toolchain and to
  the shell tool's own allowlist. Absent commands are named too: "do not run
  ``cargo``" saves the round trip that "command not found" would have cost.
* **Three platform quirks** — path separator, quoting for spaces, shell dialect.
  Exactly three, enumerated in :class:`PlatformQuirks`.

Budget: :data:`PROJECT_MAP_TOKEN_BUDGET` tokens, enforced by
:func:`render_project_map` on every render. The figure is sized against the
smaller of the two device profiles (``NPU_CTX_SIZE`` = 32,768), not the 64K one.

Caching: :func:`build_project_map` is memoised per root and invalidated by a
filesystem fingerprint (see :func:`_fingerprint`), so it is rebuilt when the
project changes rather than on every query.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gaia.agents.base.system_context import DEV_TOOL_PROBES, probe_binaries
from gaia.agents.base.turn_metrics import count_tokens
from gaia.logger import get_logger

logger = get_logger(__name__)

# ── budget ────────────────────────────────────────────────────────────────

#: Ceiling on the rendered map, measured with the shared estimator in
#: :mod:`gaia.agents.base.turn_metrics` (cl100k, or a char ratio when tiktoken
#: is absent) — so it bounds the map to within that estimator's error of the
#: model's own count, not to the exact token.
#:
#: 600 tokens is 1.8% of the NPU profile's 32,768-token window (``NPU_CTX_SIZE``)
#: and 0.9% of the GPU profile's 65,536. Sized against the NPU deliberately: the
#: smaller window is the one that has to survive the addition, and a budget that
#: only holds on 64K is not a budget.
PROJECT_MAP_TOKEN_BUDGET = 600

#: Per-section sub-caps, as a fraction of the total budget. Without these the
#: directory listing — the one unbounded section — eats the whole allowance and
#: the platform quirks fall off the end.
_DIR_SHAPE_SHARE = 0.40
_COMMANDS_SHARE = 0.30

# ── the "code repository" predicate ───────────────────────────────────────

#: A version-control directory at the root. First half of the predicate.
VCS_DIRS: Tuple[str, ...] = (".git", ".hg", ".svn")

#: A recognised build/package manifest at the root. Second half of the predicate.
PROJECT_MANIFESTS: Tuple[str, ...] = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "CMakeLists.txt",
    "Makefile",
    "Gemfile",
    "composer.json",
)


def is_code_repository(root: os.PathLike | str) -> bool:
    """Is *root* a code repository?

    The predicate, stated so it can be checked rather than judged: **a directory
    from** :data:`VCS_DIRS` **is present at the root, or a file from**
    :data:`PROJECT_MANIFESTS` **is present at the root.** Non-recursive, so a
    home directory that happens to contain repositories is not itself one.
    """
    path = Path(root)
    try:
        if not path.is_dir():
            return False
        return any((path / d).is_dir() for d in VCS_DIRS) or any(
            (path / f).is_file() for f in PROJECT_MANIFESTS
        )
    except OSError as e:
        logger.debug("is_code_repository(%s) could not stat the path: %s", root, e)
        return False


# ── platform quirks: exactly three, enumerated ────────────────────────────


@dataclass(frozen=True)
class PlatformQuirks:
    """The closed set of platform differences that change command syntax.

    Three, and only three. Each is something the model gets wrong by default on
    the other platform, and each changes the text of a command it emits.
    """

    #: 1. Separator between path components — ``\\`` or ``/``.
    path_separator: str
    #: 2. How to quote a path containing spaces.
    path_quoting: str
    #: 3. Which shell interprets the command line.
    shell_dialect: str


def detect_platform_quirks() -> PlatformQuirks:
    """Resolve the three quirks for the running host."""
    if platform.system() == "Windows":
        comspec = os.environ.get("COMSPEC", "")
        dialect = "PowerShell" if "powershell" in comspec.lower() else "cmd.exe"
        return PlatformQuirks(
            path_separator="\\",
            path_quoting='wrap in double quotes: "C:\\Program Files\\app"',
            shell_dialect=dialect,
        )
    shell = os.environ.get("SHELL", "")
    return PlatformQuirks(
        path_separator="/",
        path_quoting="wrap in single quotes: '/home/me/My Docs'",
        shell_dialect=Path(shell).name if shell else "sh",
    )


# ── directory shape and entry points ──────────────────────────────────────

#: Directories never worth a slot in the map — build output, caches, vendored
#: dependencies. Closed list.
IGNORED_DIRS: frozenset = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".next",
        ".gaia",
        "__pycache__",
        "node_modules",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        "coverage",
        "htmlcov",
        "site-packages",
        "vendor",
    }
)

#: Files that are, when present, a likely way to start or drive the project.
#: Closed list — checked as literal relative paths, no globbing.
ENTRY_POINT_CANDIDATES: Tuple[str, ...] = (
    "main.py",
    "__main__.py",
    "app.py",
    "cli.py",
    "run.py",
    "manage.py",
    "src/main.py",
    "src/cli.py",
    "src/app.py",
    "index.js",
    "index.ts",
    "server.js",
    "src/index.js",
    "src/index.ts",
    "main.go",
    "cmd/main.go",
    "src/main.rs",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
)

_MAX_TOP_LEVEL_DIRS = 24
_MAX_SUBDIRS_PER_DIR = 8
_MAX_EXPANDED_DIRS = 6

#: Only a few directories can afford a second level within the budget. These
#: names get it first — alphabetical order would spend the whole allowance on
#: ``cpp/``, ``data/``, ``docs/`` and never reach ``src/``.
_EXPAND_FIRST: Tuple[str, ...] = (
    "src",
    "lib",
    "app",
    "apps",
    "packages",
    "pkg",
    "cmd",
    "internal",
    "hub",
    "tests",
    "test",
)


@dataclass
class ProjectMap:
    """One project's shape, as of the fingerprint it was built at."""

    root: str
    is_repository: bool
    vcs: Optional[str]
    manifests: List[str] = field(default_factory=list)
    top_level_dirs: List[str] = field(default_factory=list)
    subdirs: Dict[str, List[str]] = field(default_factory=dict)
    entry_points: List[str] = field(default_factory=list)
    #: Toolchain binaries found on PATH, and those looked for and not found.
    tools_present: List[str] = field(default_factory=list)
    tools_absent: List[str] = field(default_factory=list)
    #: The subset of the above that ``run_shell_command`` will actually accept.
    shell_commands: List[str] = field(default_factory=list)
    quirks: PlatformQuirks = field(default_factory=detect_platform_quirks)
    fingerprint: str = ""


def _shell_allowlisted_commands() -> Tuple[str, ...]:
    """Commands ``run_shell_command`` will accept, as a sorted tuple.

    Imported lazily to keep ``agents/base`` free of an import-time dependency
    on ``agents/tools``.
    """
    from gaia.agents.tools.shell_tools import ALLOWED_COMMANDS

    return tuple(sorted(ALLOWED_COMMANDS))


def _fingerprint(root: Path) -> str:
    """Cheap change detector for *root*.

    Covers the three things that make a map stale: the top-level listing (a new
    directory appears), any manifest's content (dependencies or scripts change),
    and the VCS head (a branch switch rewrites the tree). Reading the whole tree
    to detect a change would cost as much as rebuilding the map.
    """
    parts: List[str] = [",".join(sorted(e.name for e in os.scandir(root)))]

    for name in PROJECT_MANIFESTS:
        p = root / name
        try:
            st = p.stat()
            parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            continue

    for vcs in VCS_DIRS:
        head = root / vcs / "HEAD"
        try:
            parts.append(f"{vcs}:{head.stat().st_mtime_ns}")
        except OSError:
            continue

    parts.append(os.environ.get("PATH", ""))
    return "|".join(parts)


#: ``absolute root -> (fingerprint, map)``. Module-level so several agents over
#: the same project in one process pay for the walk once.
_MAP_CACHE: Dict[str, Tuple[str, ProjectMap]] = {}


def clear_project_map_cache() -> None:
    """Drop every cached map. For tests and for an explicit re-orientation."""
    _MAP_CACHE.clear()


def build_project_map(root: os.PathLike | str) -> ProjectMap:
    """Build (or return the cached) map for *root*.

    Cached per absolute root and invalidated when :func:`_fingerprint` changes,
    so repeated queries against an unchanged project reuse one walk.
    """
    path = Path(root).expanduser().resolve()
    key = str(path)
    fp = _fingerprint(path)

    cached = _MAP_CACHE.get(key)
    if cached is not None and cached[0] == fp:
        return cached[1]

    pm = _collect(path, fp)
    _MAP_CACHE[key] = (fp, pm)
    logger.debug(
        "[project-map] built for %s (%d top-level dirs, %d entry points)",
        key,
        len(pm.top_level_dirs),
        len(pm.entry_points),
    )
    return pm


def _collect(path: Path, fingerprint: str) -> ProjectMap:
    """Walk *path* two levels deep and probe the toolchain."""
    vcs = next((d for d in VCS_DIRS if (path / d).is_dir()), None)
    manifests = [f for f in PROJECT_MANIFESTS if (path / f).is_file()]

    top_dirs: List[str] = []
    for entry in sorted(os.scandir(path), key=lambda e: e.name):
        if len(top_dirs) >= _MAX_TOP_LEVEL_DIRS:
            break
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name in IGNORED_DIRS or entry.name.startswith("."):
            continue
        top_dirs.append(entry.name)

    expand_order = sorted(
        top_dirs,
        key=lambda n: (
            _EXPAND_FIRST.index(n) if n in _EXPAND_FIRST else len(_EXPAND_FIRST),
            n,
        ),
    )
    subdirs: Dict[str, List[str]] = {}
    for name in expand_order[:_MAX_EXPANDED_DIRS]:
        children: List[str] = []
        try:
            for entry in sorted(os.scandir(path / name), key=lambda e: e.name):
                if len(children) >= _MAX_SUBDIRS_PER_DIR:
                    break
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.name in IGNORED_DIRS or entry.name.startswith("."):
                    continue
                children.append(entry.name)
        except OSError as e:
            # One unreadable subdirectory renders with no children rather than
            # sinking the whole map.
            logger.debug("[project-map] cannot list %s: %s", path / name, e)
            continue
        if children:
            subdirs[name] = children

    entry_points = [c for c in ENTRY_POINT_CANDIDATES if (path / c).is_file()]
    entry_points.extend(_declared_npm_scripts(path))

    allowlist = _shell_allowlisted_commands()
    probed = probe_binaries(set(DEV_TOOL_PROBES) | set(allowlist))
    tools_present = sorted(n for n in DEV_TOOL_PROBES if probed.get(n))
    # Only toolchain absences are reported: that list is closed and bounded,
    # whereas "every allowlisted command not installed" is mostly platform noise
    # (a Windows box is never going to have ``lspci``).
    tools_absent = sorted(n for n in DEV_TOOL_PROBES if not probed.get(n))
    # Installed AND allowlisted. Kept separate from ``tools_present`` because
    # most of the toolchain is not allowlisted — ``uv`` and ``npm`` are on this
    # machine and ``run_shell_command`` refuses both.
    shell_commands = sorted(n for n in allowlist if probed.get(n))

    return ProjectMap(
        root=str(path),
        is_repository=bool(vcs) or bool(manifests),
        vcs=vcs,
        manifests=manifests,
        top_level_dirs=top_dirs,
        subdirs=subdirs,
        entry_points=entry_points,
        tools_present=tools_present,
        tools_absent=tools_absent,
        shell_commands=shell_commands,
        quirks=detect_platform_quirks(),
        fingerprint=fingerprint,
    )


def _declared_npm_scripts(path: Path) -> List[str]:
    """``npm run <name>`` targets declared in ``package.json``, capped at 8."""
    manifest = path / "package.json"
    if not manifest.is_file():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as e:
        logger.warning("[project-map] %s is not readable JSON: %s", manifest, e)
        return []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return []
    return [f"npm run {name}" for name in sorted(scripts)[:8]]


# ── project-root resolution ───────────────────────────────────────────────

#: Overrides the resolved root. Set by a host that knows the workspace.
PROJECT_ROOT_ENV = "GAIA_PROJECT_ROOT"

#: How far up from the working directory to look for a repository root.
_MAX_ASCEND = 4


def is_agent_own_source(root: os.PathLike | str) -> bool:
    """Does *root* contain the ``gaia`` package this process is running from?

    The daemon launches the agent sidecar with its working directory set to the
    GAIA checkout in dev mode, so a working-directory-derived root there is the
    agent's own source tree, not the user's project — and auto-indexing it would
    embed thousands of files nobody asked about. An explicitly configured root
    is never subject to this check: pointing GAIA at GAIA is legitimate when you
    mean it.
    """
    import gaia

    package = Path(gaia.__file__).resolve().parent
    path = Path(root).resolve()
    return path == package or path in package.parents


def resolve_project_root(explicit: Optional[str] = None) -> Optional[str]:
    """The project this task is about, or ``None`` when there isn't one.

    Order: *explicit* argument, then ``GAIA_PROJECT_ROOT``, then the working
    directory or the nearest repository above it (at most :data:`_MAX_ASCEND`
    levels, never the home directory and never :func:`is_agent_own_source`).

    ``None`` is a real answer, not a degraded one — an agent answering questions
    from a home directory is not in a project, and inventing a map of ``~``
    would cost tokens to describe nothing.
    """
    for candidate in (explicit, os.environ.get(PROJECT_ROOT_ENV)):
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if not path.is_dir():
            raise ValueError(
                f"Project root {candidate!r} is not a directory. "
                f"Point {PROJECT_ROOT_ENV} (or the agent's project_root config) "
                f"at an existing directory, or unset it to use the working "
                f"directory."
            )
        return str(path.resolve())

    cwd = Path.cwd().resolve()
    home = Path.home().resolve()
    for ancestor in [cwd, *list(cwd.parents)[: _MAX_ASCEND - 1]]:
        if ancestor == home or ancestor == ancestor.parent:
            break
        if not is_code_repository(ancestor):
            continue
        if is_agent_own_source(ancestor):
            logger.info(
                "[project-map] %s is GAIA's own source tree — no map. Set %s to "
                "the project you want mapped.",
                ancestor,
                PROJECT_ROOT_ENV,
            )
            return None
        return str(ancestor)
    return None


# ── rendering, under budget ───────────────────────────────────────────────


def _fit(text: str, token_cap: int) -> str:
    """Trim *text* to *token_cap* estimated tokens, whole lines first."""
    if count_tokens(text) <= token_cap:
        return text
    lines = text.splitlines()
    while lines and count_tokens("\n".join(lines)) > token_cap:
        lines.pop()
    return "\n".join(lines)


def render_project_map(
    pm: ProjectMap,
    index_status: Optional[str] = None,
    token_budget: int = PROJECT_MAP_TOKEN_BUDGET,
    has_shell_tool: bool = True,
) -> str:
    """Render *pm* as a system-prompt block of at most *token_budget* tokens.

    Sections are emitted in priority order and a section that would overflow
    stops the render, so what survives truncation is always the highest-value
    text rather than whatever happened to come first.

    Pass ``has_shell_tool=False`` for an agent without ``run_shell_command`` —
    naming a tool it does not have buys a guaranteed failed call.
    """
    q = pm.quirks
    header = [
        "==== PROJECT MAP ====",
        f"Root: {pm.root}",
    ]
    if pm.vcs or pm.manifests:
        detail = ", ".join(filter(None, [pm.vcs, ", ".join(pm.manifests[:4])]))
        header.append(f"Code repository: yes ({detail})")
    else:
        header.append("Code repository: no (no VCS directory, no known manifest)")

    quirks = [
        "Platform (these three change the commands you write):",
        f"- Path separator: {q.path_separator}",
        f"- Paths with spaces: {q.path_quoting}",
        f"- Shell dialect: {q.shell_dialect}",
    ]

    shape: List[str] = []
    if pm.top_level_dirs:
        shape.append("Directories:")
        for name in pm.top_level_dirs:
            children = pm.subdirs.get(name)
            suffix = f"  ({', '.join(children)})" if children else ""
            shape.append(f"- {name}/{suffix}")

    entries: List[str] = []
    if pm.entry_points:
        entries.append(f"Entry points: {', '.join(pm.entry_points)}")

    # Absences first: they are the line that prevents a wasted round trip, so
    # they must survive the sub-cap even when the longer lists do not.
    commands: List[str] = []
    if pm.tools_absent:
        commands.append(f"NOT installed, do not invoke: {', '.join(pm.tools_absent)}")
    if has_shell_tool:
        allowed = set(pm.shell_commands)
        if pm.shell_commands:
            commands.append(
                f"run_shell_command accepts: {', '.join(pm.shell_commands)}"
            )
        off_limits = [t for t in pm.tools_present if t not in allowed]
        if off_limits:
            commands.append(
                "Installed but run_shell_command refuses them — use a tool, not "
                f"the shell: {', '.join(off_limits)}"
            )
    elif pm.tools_present:
        commands.append(f"Installed: {', '.join(pm.tools_present)}")

    index: List[str] = [f"Code index: {index_status}"] if index_status else []

    # The header always ships — a map that says nothing but "you are in
    # /x/y, it is a git repo" is still worth more than an empty block — so it
    # is clamped to the budget rather than dropped by it.
    head = _fit("\n".join(header), token_budget)
    out: List[str] = [head]
    used = count_tokens(head)

    optional: Sequence[str] = (
        "\n".join(quirks),
        _fit("\n".join(shape), int(token_budget * _DIR_SHAPE_SHARE)),
        "\n".join(entries),
        _fit("\n".join(commands), int(token_budget * _COMMANDS_SHARE)),
        "\n".join(index),
    )
    for text in optional:
        if not text:
            continue
        cost = count_tokens(text) + 1  # +1 for the blank-line join
        if used + cost > token_budget:
            logger.debug(
                "[project-map] budget of %d tokens reached, dropping the tail",
                token_budget,
            )
            break
        out.append(text)
        used += cost

    return "\n\n".join(out)


# ── agent mixin ───────────────────────────────────────────────────────────

#: Truthy/falsy override for the task-start ``index_codebase`` trigger.
AUTO_INDEX_ENV = "GAIA_PROJECT_MAP_AUTO_INDEX"


def auto_index_env_override() -> Optional[bool]:
    """``GAIA_PROJECT_MAP_AUTO_INDEX`` as a bool, or ``None`` when unset."""
    raw = os.environ.get(AUTO_INDEX_ENV)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


#: States of the task-start index trigger. All three post-``idle`` states are
#: terminal for the session: the trigger fires at most once, and ``failed``
#: exists so an index that died does not leave the prompt saying "building".
_IDLE, _RUNNING, _DONE, _FAILED = "idle", "running", "done", "failed"


class ProjectMapMixin:
    """Injects a project map into the system prompt and triggers indexing.

    Consumer responsibilities:

    * List this mixin **before** the base agent in the bases — ``Agent``'s no-op
      ``_on_task_start`` otherwise wins the MRO and the trigger never fires.
      ``__init_subclass__`` raises if you get it wrong.
    * Compose :class:`CodeIndexToolsMixin` too if the ``index_codebase`` trigger
      is wanted; without it the map still renders, minus the index line.
    * Optionally give the config a ``project_root`` field; otherwise the root
      comes from ``GAIA_PROJECT_ROOT`` or the working directory.
    """

    def __init_subclass__(cls, **kwargs) -> None:
        """Fail at class definition when the MRO would silence the hook.

        The prompt fragment is found by ``dir()`` and would still render, so a
        wrong base order otherwise produces a map with no index trigger and no
        symptom at all.
        """
        super().__init_subclass__(**kwargs)
        from gaia.agents.base.agent import Agent

        mro = cls.__mro__
        if Agent in mro and mro.index(ProjectMapMixin) > mro.index(Agent):
            raise TypeError(
                f"{cls.__name__} lists ProjectMapMixin after Agent, so "
                f"Agent._on_task_start shadows it and the project map never "
                f"triggers indexing. Put ProjectMapMixin first in the bases: "
                f"class {cls.__name__}(ProjectMapMixin, ...)."
            )

    # ── the map ───────────────────────────────────────────────────────────

    def _project_map_root(self) -> Optional[str]:
        """This session's project root, resolved once.

        Resolved once rather than per turn so the map and the code index can
        never end up describing two different trees after a ``chdir``.
        """
        if not hasattr(self, "_project_map_root_cache"):
            explicit = getattr(getattr(self, "config", None), "project_root", None)
            self._project_map_root_cache = resolve_project_root(explicit)
        return self._project_map_root_cache

    def materialize_project_map(self) -> Optional[ProjectMap]:
        """This task's map, or ``None`` when the task is not in a project."""
        root = self._project_map_root()
        return build_project_map(root) if root else None

    def get_project_map_system_prompt(self) -> str:
        """Auto-discovered by ``Agent._get_mixin_prompts``."""
        pm = self.materialize_project_map()
        if pm is None:
            return ""
        return render_project_map(
            pm,
            index_status=self._code_index_status(pm),
            has_shell_tool="run_shell_command" in (self._tool_names()),
        )

    def _tool_names(self) -> Dict[str, Any]:
        return getattr(self, "_tools_registry", {}) or {}

    # ── code index ────────────────────────────────────────────────────────

    def _code_index_status(self, pm: ProjectMap) -> Optional[str]:
        """One line on the semantic index, or ``None`` when it does not apply."""
        if not pm.is_repository:
            return None
        indexed = self._code_index_is_built()
        if indexed is None:
            return None
        if indexed:
            return "built — use search_code_index before grepping"
        state = getattr(self, "_project_map_index_state", _IDLE)
        if state == _RUNNING:
            return "building now in the background; grep until it lands"
        if state == _FAILED:
            return "build FAILED — grep instead, or call index_codebase to see why"
        return "not built — call index_codebase to enable semantic code search"

    def _code_index_is_built(self) -> Optional[bool]:
        """``True``/``False``, or ``None`` when this agent has no code index.

        Reads the index at *the agent's* configured repo path, which the trigger
        below also indexes, so the two can never disagree about which tree they
        mean. ``GaiaAgent`` points both at :func:`resolve_project_root`.
        """
        getter = getattr(self, "_get_code_index_sdk", None)
        if getter is None:
            return None
        sdk = getter()
        if sdk is None:
            return None
        # Presence check, not get_status(): this runs on every prompt
        # composition and get_status parses every indexed chunk.
        return bool(sdk.is_indexed())

    def _auto_index_enabled(self) -> bool:
        override = auto_index_env_override()
        if override is not None:
            return override
        return bool(getattr(getattr(self, "config", None), "auto_index", True))

    def _on_task_start(self, user_input: str) -> None:
        """Materialize the map and, if warranted, kick off ``index_codebase``."""
        super()._on_task_start(user_input)
        pm = self.materialize_project_map()
        if pm is not None:
            self._maybe_start_background_index(pm)

    def _maybe_start_background_index(self, pm: ProjectMap) -> None:
        """Start ``index_codebase`` in a background thread, at most once."""
        if getattr(self, "_project_map_index_state", _IDLE) != _IDLE:
            return
        if not pm.is_repository or not self._auto_index_enabled():
            return
        if self._code_index_is_built() is not False:
            return
        index_tool = self._tool_names().get("index_codebase")
        if index_tool is None:
            return

        self._project_map_index_state = _RUNNING
        import threading

        def _fail(detail: str) -> None:
            # Background work has no caller to raise into, and a swallowed
            # failure surfaces only as an empty search_code_index later.
            self._project_map_index_state = _FAILED
            logger.error(
                "[project-map] background index of %s failed: %s. "
                "Call index_codebase directly to see the full error.",
                pm.root,
                detail,
            )

        def _run() -> None:
            logger.info("[project-map] indexing %s in the background", pm.root)
            try:
                # No repo_path: the tool defaults to the agent's configured
                # code-index root, the one the status above was read from.
                raw = index_tool["function"]()
            except Exception as e:
                _fail(str(e))
                return
            # The tool reports refusals and internal errors as JSON rather than
            # by raising, so "no exception" is not "it worked".
            error = _tool_error(raw)
            if error:
                _fail(error)
            else:
                self._project_map_index_state = _DONE
                logger.info("[project-map] background index of %s done", pm.root)

        threading.Thread(
            target=_run, name="gaia-project-map-index", daemon=True
        ).start()


def _tool_error(raw: Any) -> Optional[str]:
    """The ``error`` a code-index tool reported as JSON, if any."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed.get("error") if isinstance(parsed, dict) else None


__all__ = [
    "AUTO_INDEX_ENV",
    "ENTRY_POINT_CANDIDATES",
    "IGNORED_DIRS",
    "PROJECT_MANIFESTS",
    "PROJECT_MAP_TOKEN_BUDGET",
    "PROJECT_ROOT_ENV",
    "PlatformQuirks",
    "ProjectMap",
    "ProjectMapMixin",
    "VCS_DIRS",
    "auto_index_env_override",
    "build_project_map",
    "clear_project_map_cache",
    "detect_platform_quirks",
    "is_code_repository",
    "render_project_map",
    "resolve_project_root",
]
