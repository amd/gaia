# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Guarded import of the optional ``keyring`` dependency (#1621).

``gaia connectors`` is a *base* CLI command, but ``keyring`` ships only in the
``[ui]``/``[api]``/``[dev]`` extras — never in base ``install_requires``. A bare
``pip install amd-gaia`` therefore reaches ``import keyring`` deep in the store
on the list/status/credential subcommands and dies with a raw
``ModuleNotFoundError`` that names neither the cause nor the fix.

Importing ``keyring`` through this module turns that into the actionable error
CLAUDE.md's "fail loudly" rule requires: it names what failed, what to install,
and where to read more. The re-exported name is the real ``keyring`` module
(with ``keyring.errors`` pre-imported), so callers use it exactly as before.

Backend-env normalization (#2441): ``PYTHON_KEYRING_BACKEND`` is a keyring
contract for a *fully-qualified* ``module.Class`` path. keyring resolves it via
``value.rpartition('.')``, so a **dotless** value (the common dev/test shorthand
``null``) yields an empty module name and ``__import__('')`` raises
``ValueError: Empty module name`` — which the email sidecar surfaces as an opaque
``502``. This shim rewrites the well-known no-op-store shorthands to keyring's
real null-backend class *before* keyring initializes, so the documented
"disable the credential store" workflow works instead of crashing.
"""

from __future__ import annotations

import logging
import os

from gaia.connectors.errors import ConnectorsError

logger = logging.getLogger(__name__)

_KEYRING_BACKEND_ENV_VAR = "PYTHON_KEYRING_BACKEND"
# keyring's real "store nothing" backend (all reads return None, writes no-op).
_NULL_KEYRING_BACKEND = "keyring.backends.null.Keyring"
# Friendly, dotless shorthands a dev/test harness reaches for to disable the OS
# credential store. Each unambiguously means "no store"; keyring cannot resolve
# any of them (no dot → empty module name), so mapping them to the real null
# backend matches intent rather than inventing behavior.
_NULL_KEYRING_ALIASES = frozenset({"null", "none", "off", "disabled"})


def normalize_keyring_backend_env() -> None:
    """Rewrite a shorthand ``PYTHON_KEYRING_BACKEND`` to keyring's real class path.

    Idempotent and a no-op when the var is unset or already a dotted path. MUST
    run before the first ``keyring.get_keyring()`` so keyring reads the corrected
    value (keyring caches the resolved backend on first use). Only the dotless
    no-op-store shorthands are rewritten; any other value is left untouched for
    keyring to resolve — a genuine resolution failure is turned into an
    actionable error by ``store.verify_keyring_backend``, never a silent
    fallback.
    """
    raw = os.environ.get(_KEYRING_BACKEND_ENV_VAR)
    if raw is None:
        return
    if raw.strip().lower() in _NULL_KEYRING_ALIASES:
        os.environ[_KEYRING_BACKEND_ENV_VAR] = _NULL_KEYRING_BACKEND
        logger.warning(
            "%s=%r is a shorthand for the no-op keyring backend; normalizing to "
            "%r. The OS credential store is DISABLED — OAuth tokens will not "
            "persist. This is intended for dev/testing only.",
            _KEYRING_BACKEND_ENV_VAR,
            raw,
            _NULL_KEYRING_BACKEND,
        )


# Correct the env before ``import keyring`` resolves any backend downstream.
normalize_keyring_backend_env()

try:
    import keyring
    import keyring.errors  # noqa: F401  # re-exported as ``keyring.errors``
except ImportError as e:  # pragma: no cover - exercised via reload in tests
    raise ConnectorsError(
        "gaia connectors needs the 'keyring' package, which is not installed. "
        'Install it with `pip install keyring` (or `pip install "amd-gaia[ui]"` '
        "for the full Agent UI install). "
        "See docs/sdk/infrastructure/connections.mdx."
    ) from e

__all__ = [
    "keyring",
    "normalize_keyring_backend_env",
    "_KEYRING_BACKEND_ENV_VAR",
    "_NULL_KEYRING_BACKEND",
    "_NULL_KEYRING_ALIASES",
]
