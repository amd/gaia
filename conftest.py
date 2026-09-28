# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Root conftest — make the test run exercise THIS checkout.

``gaia`` and the hub agent packages are normally editable-installed. On a
machine with more than one clone or git worktree, that install points at
whichever one ran ``pip install -e`` last — so ``import gaia`` inside another
checkout's tests silently imports someone else's source. The tests pass or fail
against code that is not the code under review, which is worse than either
outcome on its own.

This file runs before collection and puts this repository's own source
directories at the front of ``sys.path``, so the checkout you are sitting in is
the one that gets tested. It also fails the session loudly if a core module
still resolves elsewhere, rather than letting a green run mean nothing.

Set ``GAIA_ALLOW_EXTERNAL_IMPORTS=1`` to opt out — testing an actually-installed
wheel is a legitimate thing to do, it just must not happen by accident.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def _source_roots():
    """Directories to shadow with, newest-wins order handled by the caller.

    For a hub agent the importable root is the directory CONTAINING the package
    (``hub/agents/gaia/python``), not the package itself — putting the package
    dir on sys.path makes ``import gaia_agent`` miss it entirely.

    A hub agent is included ONLY when its package already resolves somewhere.
    This redirects an existing install to this checkout; it must not *create*
    one. Tests for an agent nobody installed guard themselves with
    ``importorskip``, and forcing those onto the path turns a deliberate skip
    into a wall of failures for dependencies the developer never asked for.
    """
    roots = [REPO_ROOT / "src"]
    for init in sorted(REPO_ROOT.glob("hub/agents/*/python/*/__init__.py")):
        package = init.parent.name
        if importlib.util.find_spec(package) is not None:
            roots.append(init.parent.parent)
    return roots


_SOURCE_ROOTS = _source_roots()

#: Modules whose origin proves which checkout is under test. The hub agents are
#: listed too: they are separately editable-installed, so ``gaia`` can resolve
#: here while ``gaia_agent`` quietly comes from another worktree.
_ANCHOR_MODULES = ("gaia", "gaia_agent", "gaia_agent_chat")


def _prepend_source_roots() -> None:
    present = [str(root) for root in _SOURCE_ROOTS if root.is_dir()]
    for entry in reversed(present):
        if entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)

    # Tests that shell out to `python -c "import ..."` get a fresh interpreter
    # that inherits none of the above, so the child would resolve to whatever
    # the editable install points at — or find nothing at all.
    inherited = os.environ.get("PYTHONPATH", "")
    tail = [p for p in inherited.split(os.pathsep) if p and p not in present]
    os.environ["PYTHONPATH"] = os.pathsep.join(present + tail)


def _assert_local(session_warn) -> None:
    """Refuse to report results for a different checkout's source."""
    if os.environ.get("GAIA_ALLOW_EXTERNAL_IMPORTS") == "1":
        return

    strays = []
    for name in _ANCHOR_MODULES:
        try:
            module = __import__(name)
        except ImportError:
            continue
        origin = Path(getattr(module, "__file__", "") or "").resolve()
        if origin and REPO_ROOT not in origin.parents:
            strays.append(f"{name} -> {origin}")

    if strays:
        session_warn(
            "These modules resolved OUTSIDE this checkout, so the tests would "
            "not be testing this branch:\n  "
            + "\n  ".join(strays)
            + f"\n\nThis checkout: {REPO_ROOT}\n"
            "Usually an editable install pointing at another clone or git "
            "worktree. Re-run `uv pip install -e .` from here, or set "
            "GAIA_ALLOW_EXTERNAL_IMPORTS=1 if you really mean to test the "
            "installed package."
        )


_prepend_source_roots()


def pytest_sessionstart(session):  # noqa: D103 — pytest hook
    def _fail(message: str) -> None:
        raise RuntimeError(message)

    _assert_local(_fail)
