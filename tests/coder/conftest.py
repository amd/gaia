# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pytest configuration for ``tests/coder/``.

The sibling ``feature/gaia-coder-scaffold`` branch owns ``gaia.coder.__init__``,
``gaia.coder.base``, and ``gaia.coder.loop``. While that work is in flight the
top-level ``gaia.coder`` package may transiently reference names that are not
yet defined (``CoderAgent``, ``DEFAULT_LOOP``, …) — which breaks imports of
``gaia.coder.stores.*`` even though our store modules don't depend on any of
that.

This conftest isolates the stores tests from that churn by:

1. Trying a normal ``import gaia.coder`` first. If it succeeds the sibling
   scaffold is present and working — we do nothing and let the real modules
   run.
2. If the import fails for any reason, we pre-populate ``sys.modules`` with
   minimal stubs of the offending sub-modules so a fresh import of
   ``gaia.coder`` succeeds against them.

Once the sibling branch lands, step 1 stays green forever and this file
becomes effectively inert.
"""

from __future__ import annotations

import sys
import types


def _try_real_import() -> bool:
    """Attempt the real import; return True on success."""
    try:
        import gaia.coder  # noqa: F401
    except Exception:
        return False
    return True


def _install_stubs() -> None:
    """Replace ``gaia.coder`` and its sibling-owned submodules with stubs."""
    # Remove any half-imported entries so the stubs take effect cleanly.
    for key in list(sys.modules):
        if key == "gaia.coder" or key.startswith("gaia.coder."):
            if key == "gaia.coder.stores" or key.startswith("gaia.coder.stores"):
                # Preserve any already-loaded stores modules — they are ours.
                continue
            del sys.modules[key]

    coder_mod = types.ModuleType("gaia.coder")
    # Make ``gaia.coder`` behave as a package so submodule imports work.
    import os

    coder_mod.__path__ = [os.path.join(os.path.dirname(__file__), "..", "..", "src", "gaia", "coder")]  # type: ignore[attr-defined]
    sys.modules["gaia.coder"] = coder_mod

    base_mod = types.ModuleType("gaia.coder.base")

    class _CoderAgentStub:  # pragma: no cover - placeholder only
        """Placeholder; real implementation lands in feature/gaia-coder-scaffold."""

    base_mod.CoderAgent = _CoderAgentStub  # type: ignore[attr-defined]
    sys.modules["gaia.coder.base"] = base_mod

    loop_mod = types.ModuleType("gaia.coder.loop")
    for attr in ("DEFAULT_LOOP", "Loop", "State", "Transition"):
        setattr(loop_mod, attr, object())
    sys.modules["gaia.coder.loop"] = loop_mod


if not _try_real_import():
    _install_stubs()
