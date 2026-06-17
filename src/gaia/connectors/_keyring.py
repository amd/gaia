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
"""

from __future__ import annotations

from gaia.connectors.errors import ConnectorsError

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

__all__ = ["keyring"]
