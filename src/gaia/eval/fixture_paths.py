# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Resolve committed repo fixtures (eval threshold manifests, corpora) so they
load whether gaia is installed editable or not.

The manifests live under ``tests/fixtures/`` in the source tree, not inside the
``gaia`` package. Resolving them as ``Path(__file__).parents[3]/tests/fixtures``
only works for an editable checkout; a non-editable install puts ``__file__`` in
``site-packages`` and ``parents[3]`` points at ``.venv/Lib`` — so the eval gate
loaders crashed with ``FileNotFoundError`` on the scorecard workflow (#2047).

``resolve_repo_fixture`` searches the editable-checkout layout first (unchanged
behavior for dev/CI checkouts), then a ``GAIA_REPO_ROOT`` override and the CWD,
which cover a non-editable install running from the repo checkout. It fails loud
with an actionable error if the fixture is absent from every candidate root.
"""

import os
from pathlib import Path

_FIXTURES_SUBPATH = ("tests", "fixtures")


def _candidate_roots() -> list[Path]:
    """Repo-root candidates to search, most-authoritative first."""
    # Editable checkout: src/gaia/eval/fixture_paths.py -> repo root.
    module_root = Path(__file__).resolve().parents[3]
    roots: list[Path] = [module_root]
    env_root = os.environ.get("GAIA_REPO_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())
    # A non-editable install still runs from the repo checkout in CI.
    roots.append(Path.cwd())
    return roots


def resolve_repo_fixture(*parts: str) -> Path:
    """Return the path to a committed fixture under ``tests/fixtures/``.

    ``parts`` is the path relative to ``tests/fixtures`` (e.g.
    ``("email", "drafting_gate_thresholds.json")``). Raises ``FileNotFoundError``
    naming what to fix if the fixture is missing from every candidate root.
    """
    tried: list[str] = []
    for root in _candidate_roots():
        candidate = root.joinpath(*_FIXTURES_SUBPATH, *parts)
        if candidate.is_file():
            return candidate
        tried.append(str(candidate))
    joined = "/".join((*_FIXTURES_SUBPATH, *parts))
    raise FileNotFoundError(
        f"Could not locate the committed fixture '{joined}'. gaia is likely "
        "installed non-editable, so tests/fixtures/ is not next to the module. "
        "Set GAIA_REPO_ROOT to the repo checkout, run the eval from the repo "
        "root, or reinstall gaia editable (`pip install -e .[dev,eval,api]`). "
        "Tried: " + ", ".join(tried)
    )
