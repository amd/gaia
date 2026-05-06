# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
ConnectorRegistry — the catalog of all known connectors.

The registry is a process-level singleton (``REGISTRY``) populated during
module import by each catalog module under ``gaia.connectors.catalog.*``.
After the last catalog import ``REGISTRY.freeze()`` is called; any
subsequent ``register()`` call raises ``RuntimeError``.

Design constraints (plan amendment A7):
- ``register()`` raises ``ValueError`` on duplicate ``connector_id``.
- Catalog is frozen at module import — no runtime mutation API.
- POST endpoints accept only ``connector_id`` (a lookup key); they never
  accept ``command`` / ``args`` / ``mcp_command`` from the request body.

Tests should call ``REGISTRY.clear()`` in their teardown to reset the
singleton between test runs.
"""

from __future__ import annotations

import threading
from typing import Iterator

from gaia.connectors.spec import ConnectorSpec


class ConnectorRegistry:
    """Thread-safe, id-unique registry of ``ConnectorSpec`` entries."""

    def __init__(self) -> None:
        self._specs: dict[str, ConnectorSpec] = {}
        self._frozen = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Write path (used only at module-load time)
    # ------------------------------------------------------------------

    def register(self, spec: ConnectorSpec) -> None:
        """
        Add a spec to the registry.

        Raises ``ValueError`` if ``spec.id`` is already registered.
        Raises ``RuntimeError`` if the registry has been frozen.
        """
        with self._lock:
            if self._frozen:
                raise RuntimeError(
                    f"ConnectorRegistry is frozen; cannot register {spec.id!r} "
                    "after module load. Add catalog entries before calling freeze()."
                )
            if spec.id in self._specs:
                existing = self._specs[spec.id]
                raise ValueError(
                    f"Duplicate connector id {spec.id!r} — already registered as "
                    f"{existing.display_name!r}. Each connector id must be unique "
                    "across the entire catalog."
                )
            self._specs[spec.id] = spec

    def freeze(self) -> None:
        """Prevent further registrations. Called after catalog discovery."""
        with self._lock:
            self._frozen = True

    # ------------------------------------------------------------------
    # Read path (safe after freeze)
    # ------------------------------------------------------------------

    def get(self, connector_id: str) -> ConnectorSpec:
        """
        Return the spec for ``connector_id``.

        Raises ``KeyError`` with an actionable message (lists known ids) if
        the id is not found.
        """
        try:
            return self._specs[connector_id]
        except KeyError:
            known = sorted(self._specs)
            raise KeyError(
                f"Unknown connector {connector_id!r}. Known ids: {known!r}. "
                "Register the spec in a catalog module under "
                "gaia/connectors/catalog/ before looking it up."
            ) from None

    def all(self) -> list[ConnectorSpec]:
        """Return all registered specs, ordered by (tier, id)."""
        return sorted(self._specs.values(), key=lambda s: (s.tier, s.id))

    def __contains__(self, connector_id: str) -> bool:
        return connector_id in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    def __iter__(self) -> Iterator[ConnectorSpec]:
        return iter(self.all())

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset the registry. For use in test teardown only."""
        with self._lock:
            self._specs.clear()
            self._frozen = False


# Module-level singleton — populated by catalog/*.py at import time.
REGISTRY = ConnectorRegistry()
