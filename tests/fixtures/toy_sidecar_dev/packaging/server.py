# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Minimal real ASGI toy-dev sidecar for the #2588 dev-mode-anchor integration
contract.

Unlike ``tests/fixtures/toy_sidecar.py`` (a raw stdlib ``BaseHTTPRequestHandler``
binary with no ASGI ``app``, used for the "user mode" frozen-binary spawn
path), this fixture answers the exact dev-mode spawn command
:class:`gaia.daemon.sidecars.manager.AgentSidecarManager` issues:

    uvicorn server:app --app-dir <dev_src_dir>/packaging --host H --port P

Loaded as the TOP-LEVEL module ``server`` (this directory has no
``__init__.py`` by design, mirroring the real email agent's ``packaging/``
layout -- ``packaging.server:app`` would resolve to the unrelated PyPI
``packaging`` library).

Answers the same ``/health``/``/version`` contract the manager probes
(``_wait_for_health`` / ``_check_version``):

    GET /health   -> {"status": "ok", "service": "gaia-agent-toy-dev"}
    GET /version  -> {"apiVersion": "1.0", "agentVersion": "0.0.1-toy-dev"}

Whichever checkout's ``dev_src_dir`` the daemon actually spawns from is the
tree this file lives in -- that is the whole point of the #2588 headline
integration test: proving on unmodified code that a caller in a DIFFERENT
checkout gets silently served this fixture's answers anyway.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "gaia-agent-toy-dev"}


@app.get("/version")
def version() -> dict:
    return {"apiVersion": "1.0", "agentVersion": "0.0.1-toy-dev"}
