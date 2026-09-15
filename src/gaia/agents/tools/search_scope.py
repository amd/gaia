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

import sys
from pathlib import Path
from typing import Any, List, Tuple

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


def search_roots(host: Any) -> List[Path]:
    """Allowed paths, **most specific first**, then a working-directory fallback.

    Most specific first is load-bearing: it decides which root a relative
    directory like ``tui/internal`` is resolved against, and the deepest match
    is the one the user meant. Sorting by string put ``/a`` before ``/a/b``.

    The fallback is only for library use with no sandbox declared at all.
    """
    validator = path_validator_of(host)
    roots = [
        Path(root)
        for root in (getattr(validator, "allowed_paths", None) or [])
        if Path(root).exists()
    ]
    if not roots:
        return [Path.cwd().resolve()]
    return sorted(roots, key=lambda p: (-len(p.parts), str(p)))


def root_depth(root: Path, roots: List[Path]) -> int:
    """How deep to walk *root* given the whole set.

    The first (most specific) root is the project; the rest are approvals that
    accumulated, and are capped.
    """
    if roots and Path(root) == Path(roots[0]):
        return DEEP_ROOT_DEPTH
    return SHALLOW_ROOT_DEPTH


def _is_gaia_install_dir(path: Path) -> bool:
    """True when *path* is where GAIA itself lives, not where the user works.

    Covers the installed ``gaia`` package's parent (``src/`` or
    ``site-packages``), the running interpreter's prefix, and a hub agent's
    ``hub/agents/<id>/python`` directory — the cwd a dev-mode sidecar is
    spawned in.
    """
    import gaia

    own = [Path(gaia.__file__).resolve().parent.parent, Path(sys.prefix).resolve()]
    if any(path == d or d in path.parents for d in own):
        return True
    parts = path.parts
    return any(
        parts[i : i + 2] == ("hub", "agents") and parts[i + 3] == "python"
        for i in range(len(parts) - 3)
    )


def walk_plan(host: Any) -> List[Tuple[Path, int]]:
    """``(root, max_depth)`` pairs for a search with no ``directory`` given.

    Normally :func:`search_roots` with :func:`root_depth`. When the process cwd
    sits strictly inside an allowed root — the sandbox is ``$HOME`` and the user
    launched from a project under it — the cwd is walked deep first and the
    root containing it drops to :data:`SHALLOW_ROOT_DEPTH`, so a lookup cannot
    turn into a walk of the whole home folder (#3889). A cwd that is not inside
    the sandbox, or is GAIA's own install/package directory, is ignored: that
    is how the process was launched, not where the user's work is (#3576).
    """
    roots = search_roots(host)
    plan = [(root, root_depth(root, roots)) for root in roots]
    cwd = Path.cwd().resolve()
    resolved = [Path(r).resolve() for r in roots]
    if cwd in resolved:
        return plan
    container = next((r for r in resolved if r in cwd.parents), None)
    if container is None or _is_gaia_install_dir(cwd):
        return plan
    return [(cwd, DEEP_ROOT_DEPTH)] + [
        (root, SHALLOW_ROOT_DEPTH if res == container else depth)
        for (root, depth), res in zip(plan, resolved)
    ]
