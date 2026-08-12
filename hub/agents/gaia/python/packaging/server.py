# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Frozen-binary / dev-mode entrypoint for the GAIA flagship agent sidecar.

A thin re-export of :mod:`gaia_agent_gaia.server`, which is the single source of
truth for the app wiring. Kept as a separate top-level module because two
launchers reach the sidecar by *module path* rather than by import:

- the daemon's dev mode runs ``uvicorn server:app --app-dir <this dir>``
  (``AgentSidecarSpec.dev_app_dir`` / ``dev_module``),
- PyInstaller freezes this file as the binary entry.

Both must therefore resolve a module-level ``app``. Because the frozen binary
and a source ``uvicorn gaia_agent_gaia.server:app`` share that one app object,
they serve a byte-for-byte identical ``/v1/gaia/*`` contract.

    GET /health   -> {"status": "ok", "service": "gaia-agent-gaia"}
    GET /version  -> {"apiVersion": <wire contract>, "agentVersion": <package>}
"""

from __future__ import annotations

import sys

from gaia_agent_gaia.server import (  # noqa: F401  (re-exported for the freeze/daemon)
    DEFAULT_HOST,
    DEFAULT_PORT,
    app,
    build_app,
    main,
)

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
