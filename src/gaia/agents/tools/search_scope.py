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
from typing import Any, List

#: Depth the primary root is walked to — effectively unlimited. The project the
#: user is working in is the one place worth an exhaustive walk.
DEEP_ROOT_DEPTH = 999

#: Depth for every other root. An approved ``~/Documents`` is a legitimate
#: search location and a bad thing to traverse exhaustively on every search
#: that finds nothing; the old code capped the same folders at 5 for the same
#: reason.
SHALLOW_ROOT_DEPTH = 5


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
