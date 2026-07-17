# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Generic, spec-driven sidecar-agent supervision for the GAIA daemon.

Email is the first registered agent (see :func:`gaia.daemon.sidecars.spec.
builtin_specs`); this subpackage never imports ``gaia.ui`` — the daemon is the
one process that outlives the UI backend, so it cannot depend on it.
"""

from __future__ import annotations
