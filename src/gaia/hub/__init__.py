# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Agent Hub — packaging, distribution, and discovery primitives.

The Agent Hub turns every agent (builtin, custom, community) into a
self-describing package.  This module owns the shared package format used
across packaging (#1093), init scaffolding (#1098), and registry discovery.

Public surface:
    - :class:`gaia.hub.manifest.AgentManifest` — parsed ``gaia-agent.yaml``
    - :func:`gaia.hub.manifest.parse` — load + validate a manifest file
    - :class:`gaia.hub.manifest.ManifestError` — actionable validation error
"""

from gaia.hub.manifest import AgentManifest, ManifestError, parse

__all__ = ["AgentManifest", "ManifestError", "parse"]
