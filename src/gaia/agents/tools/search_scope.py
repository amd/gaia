# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where a filesystem search should look, and how deep (#3576).

Both file-search mixins rooted their searches at ``Path.cwd()``. A sidecar is
spawned by the daemon in its own package directory, so that is a fact about how
the process was launched, not about where the user's work is — the agent
answered "zero .go files" for a repo holding 203.

The answer is the agent's declared sandbox. That set is not only the project,
though: ``PathValidator`` merges every path the user has ever approved out of
``~/.gaia/cache/allowed_paths.json``, so it grows with use. Walking all of it to
unlimited depth on every miss is what :data:`SHALLOW_ROOT_DEPTH` exists to
prevent.

One module rather than a method on each mixin, so the two cannot drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

#: Depth the primary root is walked to — effectively unlimited. The project the
#: user is working in is the one place worth an exhaustive walk.
DEEP_ROOT_DEPTH = 999

#: Depth for every other root. An approved ``~/Documents`` is a legitimate
#: search location and a bad thing to traverse exhaustively on every search
#: that finds nothing; the old code capped the same folders at 5 for the same
#: reason.
SHALLOW_ROOT_DEPTH = 5

#: Wall-clock budget for one search walk. Well under the 180 s tool watchdog,
#: so the model gets partial results and a hint instead of an abandoned call.
SEARCH_TIME_BUDGET_S = 20.0

#: Directory entries one search walk may examine before it stops (#3889).
SEARCH_ENTRY_BUDGET = 200_000


def path_validator_of(host: Any) -> Any:
    """The host's PathValidator under either of the two attribute names."""
    return getattr(host, "path_validator", None) or getattr(
        host, "_path_validator", None
    )


def confine_roots(host: Any, roots: List[Any]) -> List[Path]:
    """*roots* cut down to ``GAIA_ALLOWED_PATHS``, for walks wider than the allowlist.

    A name search over Documents or Downloads returns only paths, which is why
    it may look past the allowed folders; a run confined to one folder must
    not learn the names of files outside it either.
    """
    validator = path_validator_of(host)
    if validator is None or not hasattr(validator, "confine_roots"):
        return [Path(r) for r in roots]
    return validator.confine_roots(roots)


def in_env_scope(host: Any, path: Any) -> bool:
    """False only when ``GAIA_ALLOWED_PATHS`` excludes *path*."""
    validator = path_validator_of(host)
    if validator is None or not hasattr(validator, "in_env_scope"):
        return True
    return validator.in_env_scope(path)


def is_broad_root(root: Path) -> bool:
    """The filesystem root or the user's home — a boundary, never a project.

    Full access grants ``/``; the flagship's default grants ``~``. Either walked
    exhaustively is a whole-disk crawl.

    Deliberately just those two. A container of homes (``/home``, ``C:\\Users``)
    is as expensive to walk, but only reaches the approved set by a user
    approving it by name — and demoting a root someone chose on purpose is the
    bug this module exists to avoid.
    """
    resolved = Path(root).expanduser().resolve()
    return resolved == Path(resolved.anchor) or resolved == Path.home().resolve()


def session_workspace(host: Any) -> Optional[Path]:
    """Where this session is working: its project root, else the process cwd.

    ``None`` when that is a broad root, or GAIA's own source tree — the cwd a
    dev-mode sidecar is spawned in, which says nothing about the user's work.
    """
    project_root = getattr(host, "_project_map_root", None)
    recorded = project_root() if callable(project_root) else None
    if recorded:
        workspace = Path(recorded).resolve()
    else:
        from gaia.agents.base.project_map import is_agent_own_source

        workspace = Path.cwd().resolve()
        if is_agent_own_source(workspace):
            return None
    return None if is_broad_root(workspace) else workspace


def search_roots(host: Any) -> List[Path]:
    """Allowed paths, **most specific first**, then a working-directory fallback.

    Most specific first is load-bearing: it decides which root a relative
    directory like ``tui/internal`` is resolved against, and the deepest match
    is the one the user meant. Sorting by string put ``/a`` before ``/a/b``.

    The allowed paths are a permission boundary, not a search scope. When the
    only ones holding the session's workspace are broad (``/`` under full
    access), the workspace goes first — otherwise the "project" is the disk.

    The fallback is only for library use with no sandbox declared at all.
    """
    validator = path_validator_of(host)
    # The scratch dir often nests deeper than the project and would outrank it.
    scratch_dir = getattr(validator, "scratch_dir", None)
    roots = [
        Path(root)
        for root in (getattr(validator, "allowed_paths", None) or [])
        if Path(root).exists() and Path(root) != scratch_dir
    ]
    if not roots:
        # Under GAIA_ALLOWED_PATHS an empty scope means nothing, not the cwd.
        if isinstance(getattr(validator, "env_scope", None), list):
            return []
        return [Path.cwd().resolve()]
    roots = sorted(roots, key=lambda p: (-len(p.parts), str(p)))
    workspace = session_workspace(host)
    if workspace is None or workspace in roots:
        return roots
    holders = [r for r in roots if r.resolve() in workspace.parents]
    if holders and all(is_broad_root(r) for r in holders):
        return [workspace, *roots]
    return roots


def _hidden_below(path: Path, root: Path, skipped: set[str]) -> bool:
    """True when a walk from *root* skips a folder on the way down to *path*."""
    return any(
        part.startswith(".") or part in skipped for part in path.relative_to(root).parts
    )


def hidden_granted_roots(
    host: Any, roots: List[Any], skipped: set[str] | frozenset[str] = frozenset()
) -> List[Path]:
    """GAIA's documents folder and the chat's folders a walk of *roots* misses.

    Walks skip dot-folders, so ``~/.gaia/documents`` — where GAIA saves files —
    was never searched. Only folders inside *roots* are returned: this widens
    where a walk looks, never what it may reach.

    Args:
        host: The agent whose validator lists the chat's allowed paths.
        roots: The folders the search walks.
        skipped: Folder names the walk skips besides dot-folders.

    Returns:
        Folders to walk as extra roots, outermost first.
    """
    from gaia.config import gaia_home

    validator = path_validator_of(host)
    scratch_dir = getattr(validator, "scratch_dir", None)
    candidates = {gaia_home() / "documents"}
    candidates.update(
        Path(p)
        for p in (getattr(validator, "allowed_paths", None) or [])
        if Path(p) != scratch_dir
    )
    walked = [Path(r).expanduser().resolve() for r in roots]
    extra: List[Path] = []
    for folder in sorted(
        {c.expanduser().resolve() for c in candidates if c.is_dir()},
        key=lambda p: (len(p.parts), str(p)),
    ):
        holders = [r for r in walked if r == folder or r in folder.parents]
        if holders and all(
            r != folder and _hidden_below(folder, r, skipped) for r in holders
        ):
            extra.append(folder)
            walked.append(folder)
    return extra


def root_depth(root: Path, roots: List[Path]) -> int:
    """How deep to walk *root* given the whole set.

    The first (most specific) root is the project; the rest are approvals that
    accumulated, and are capped. A broad root is capped even when first.
    """
    if is_broad_root(root):
        return SHALLOW_ROOT_DEPTH
    if roots and Path(root) == Path(roots[0]):
        return DEEP_ROOT_DEPTH
    return SHALLOW_ROOT_DEPTH
