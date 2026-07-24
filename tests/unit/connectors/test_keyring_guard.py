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
import os
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


# ---------------------------------------------------------------------------
# PYTHON_KEYRING_BACKEND normalization + loud failure (issue #2441)
#
# keyring resolves PYTHON_KEYRING_BACKEND as a fully-qualified 'module.Class'
# path (value.rpartition('.')). A dotless value — the common dev/test shorthand
# 'null' — yields an empty module name and __import__('') raises
# ``ValueError: Empty module name`` deep inside keyring, which the email sidecar
# surfaced as an opaque 502 on macOS dev mode.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shorthand", ["null", "none", "off", "disabled", "  NULL  "])
def test_normalize_rewrites_null_shorthand_to_real_backend(
    shorthand: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dotless no-op-store shorthand is rewritten to keyring's null backend
    class BEFORE keyring resolves it — so ``keyring.get_keyring()`` no longer
    raises ``Empty module name``."""
    from gaia.connectors._keyring import (
        _NULL_KEYRING_BACKEND,
        normalize_keyring_backend_env,
    )

    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", shorthand)
    normalize_keyring_backend_env()
    assert os.environ["PYTHON_KEYRING_BACKEND"] == _NULL_KEYRING_BACKEND

    # The rewritten value is a real, resolvable keyring backend (proves the
    # Empty-module-name crash is gone for the documented dev/test workflow).
    import keyring

    monkeypatch.setattr(keyring.core, "_keyring_backend", None, raising=False)
    backend = keyring.get_keyring()
    assert type(backend).__module__ == "keyring.backends.null"


def test_normalize_is_noop_for_unset_and_dotted_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully-qualified path (or an unset var) is left untouched — normalization
    only ever rewrites the dotless shorthands."""
    from gaia.connectors._keyring import normalize_keyring_backend_env

    monkeypatch.delenv("PYTHON_KEYRING_BACKEND", raising=False)
    normalize_keyring_backend_env()
    assert "PYTHON_KEYRING_BACKEND" not in os.environ

    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    normalize_keyring_backend_env()
    assert os.environ["PYTHON_KEYRING_BACKEND"] == "keyring.backends.fail.Keyring"


def test_verify_keyring_backend_raises_actionable_error_on_bad_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolvable (non-shorthand) PYTHON_KEYRING_BACKEND surfaces as an
    actionable ``ConnectorsError`` naming the offending value and the correct
    form — never keyring's opaque ``Empty module name`` / import error."""
    import keyring

    from gaia.connectors.store import verify_keyring_backend

    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "totally.bogus.Backend")
    monkeypatch.setattr(keyring.core, "_keyring_backend", None, raising=False)

    with pytest.raises(ConnectorsError) as excinfo:
        verify_keyring_backend()

    msg = str(excinfo.value)
    assert "totally.bogus.Backend" in msg  # names the offending value
    assert "keyring.backends.null.Keyring" in msg  # names the correct form
    # The opaque keyring message must be wrapped, not surfaced bare.
    assert "Could not initialize the keyring backend" in msg
