# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression test for issue #1621.

``gaia connectors`` is a *base* CLI command, but ``keyring`` ships only in the
``[ui]``/``[api]``/``[dev]`` extras. A bare ``pip install amd-gaia`` reaches
``import keyring`` deep in ``gaia.connectors.store`` on the list/status/credential
subcommands and used to die with a raw ``ModuleNotFoundError`` that named neither
the cause nor the fix.

``gaia.connectors._keyring`` guards that import and re-raises a ``ConnectorsError``
naming the exact install command — CLAUDE.md's "fail loudly / actionable errors"
rule. The CLI handler (``gaia.connectors.cli.handle``) catches ``ConnectorsError``
and prints it to stderr, so the user sees the install hint instead of a traceback.

The promotion of ``keyring`` to a base dep vs. a dedicated extra is a separate
maintainer packaging decision; this guard is the defense-in-depth half that holds
regardless of where the dependency ultimately lives.
"""

from __future__ import annotations

import builtins
import importlib
import sys

import pytest

from gaia.connectors.errors import ConnectorsError


def test_keyring_guard_raises_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing the keyring shim without ``keyring`` installed must raise an
    actionable ``ConnectorsError`` (not a bare ``ModuleNotFoundError``)."""
    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "keyring" or name.startswith("keyring."):
            raise ModuleNotFoundError("No module named 'keyring'")
        return real_import(name, *args, **kwargs)

    # Drop any cached keyring + shim modules so the guarded import re-runs.
    for mod in list(sys.modules):
        if (
            mod == "keyring"
            or mod.startswith("keyring.")
            or mod == "gaia.connectors._keyring"
        ):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    with pytest.raises(ConnectorsError) as excinfo:
        importlib.import_module("gaia.connectors._keyring")

    msg = str(excinfo.value)
    assert "keyring" in msg
    assert "pip install" in msg

    # Restore a clean shim for the rest of the suite (keyring is available again
    # now that the monkeypatch is being torn down at function exit).
    sys.modules.pop("gaia.connectors._keyring", None)


def test_keyring_shim_reexports_real_module() -> None:
    """When ``keyring`` IS installed the shim re-exports the real module,
    so ``store``/``mcp_server`` keep using ``keyring`` / ``keyring.errors``."""
    shim = importlib.import_module("gaia.connectors._keyring")
    real = importlib.import_module("keyring")
    assert shim.keyring is real
    assert hasattr(shim.keyring, "errors")
