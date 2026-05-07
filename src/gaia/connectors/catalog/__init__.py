# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Connector catalog — registers all built-in ConnectorSpecs and their handlers.

Importing this package triggers registration of every built-in connector
into ``REGISTRY`` and every handler into ``_HANDLER_REGISTRY``.  Application
entry-points (FastAPI routers, CLI, Agent UI) must import this package
before they call ``get_credential`` / ``configure`` / ``health_check``.

Each sub-module is responsible for:
  1. Calling ``REGISTRY.register(spec)`` for every ConnectorSpec it owns.
  2. Importing the type handler module (e.g. ``gaia.connectors.oauth_pkce``)
     so ``register_handler`` fires at import time.

New connectors: add a module under ``catalog/`` that does the above two
things, then add an import here.
"""

from gaia.connectors.catalog import google  # noqa: F401
from gaia.connectors.catalog import mcp_servers  # noqa: F401

__all__ = ["google", "mcp_servers"]
