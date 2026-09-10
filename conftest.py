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

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

#: Source roots this repo owns, in the order they should shadow anything else.
_SOURCE_ROOTS = [REPO_ROOT / "src"] + sorted(
    p.parent for p in REPO_ROOT.glob("hub/agents/*/python/*/__init__.py")
)

#: Modules whose origin proves which checkout is under test.
_ANCHOR_MODULES = ("gaia",)


def _prepend_source_roots() -> None:
    for root in reversed(_SOURCE_ROOTS):
        entry = str(root)
        if root.is_dir():
            if entry in sys.path:
                sys.path.remove(entry)
            sys.path.insert(0, entry)


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
