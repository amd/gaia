# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon's ``/host/v1/*`` custody API (design §0.31, breakdown V2-12).

The reverse contract: a sidecar agent calls *back* into the daemon to read and
write the user's custody data (memory / RAG / sessions / audit). Every route is
scoped to the calling agent — a sidecar authenticates with the per-spawn secret
bound to its agent id at mint (:mod:`gaia.daemon.custody.auth`) and can only
touch its own agent's rows.

Layering: this package never imports ``gaia.ui``. The store
(:mod:`gaia.daemon.custody.store`) is the daemon's own single writer over a
dedicated SQLite file; the UI routers migrating onto it is a later issue
(V2-12 follow-up). :class:`gaia.daemon.custody.provider.CustodyProvider` is the
sidecar-side abstraction so the custody home is swappable (§0.37).
"""

from __future__ import annotations
