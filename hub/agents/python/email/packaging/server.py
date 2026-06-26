# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Frozen-binary entrypoint for the GAIA Email Triage agent REST sidecar
(milestone #49, Phase 2 of email-agent-packaging).

This is the module PyInstaller freezes (see ``freeze.py`` /
``release_agent_email.yml``). It boots a **minimal** FastAPI app that mounts ONLY
the email REST router (``/v1/email/*``) plus two dependency-free probes the
sidecar lifecycle handshake needs:

    GET /health   -> {"status": "ok", "service": "gaia-agent-email"}
    GET /version  -> {"apiVersion": <contract SCHEMA_VERSION>,
                      "agentVersion": <package __version__>}

Why a minimal app instead of freezing the whole ``gaia api`` (openai_server)
app: the full app eagerly imports every registered agent (RAG, code, etc.),
which balloons the frozen binary and multiplies the freeze-time hidden-import
surface. The sidecar only needs the email surface, so we mount just that router.
The router import chain is identical to what ``openai_server`` mounts, so the
served contract is byte-for-byte the same.

Triage uses the real local Lemonade model. If Lemonade is unreachable,
``POST /v1/email/triage`` returns HTTP 502 (``local LLM triage failed``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gaia_agent_email.sidecar")

# Default sidecar bind. NOT 4001 (reserved). 8131 is unused here.
DEFAULT_PORT = 8131
DEFAULT_HOST = "127.0.0.1"


def build_app():
    """Build the minimal FastAPI app hosting the email REST surface.

    Also mounts the playground's mailbox-connector routes
    (``/v1/email/connectors*``) so the always-served playground page can connect
    Gmail/Outlook and exercise live send. They reuse GAIA's connector framework
    (already linked in via the send path) and are excluded from the OpenAPI
    contract — a playground convenience, not part of the frozen email REST
    contract.
    """
    from fastapi import FastAPI
    from gaia_agent_email import __version__ as agent_version
    from gaia_agent_email.api_routes import router as email_router
    from gaia_agent_email.connector_routes import router as connector_router
    from gaia_agent_email.contract import SCHEMA_VERSION

    app = FastAPI(
        title="GAIA Email Agent Sidecar",
        version=agent_version,
        description="Frozen-binary email triage REST sidecar.",
    )

    @app.get("/health", include_in_schema=True)
    async def health() -> dict:
        return {"status": "ok", "service": "gaia-agent-email"}

    @app.get("/version", include_in_schema=True)
    async def version() -> dict:
        # apiVersion is the host-facing REST contract version (the frozen
        # request/response schema); agentVersion is the package build.
        return {"apiVersion": SCHEMA_VERSION, "agentVersion": agent_version}

    app.include_router(email_router)
    app.include_router(connector_router)
    return app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="GAIA Email Triage REST sidecar (frozen binary entrypoint)."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    parser.add_argument(
        "--print-openapi",
        action="store_true",
        help="Print the OpenAPI JSON to stdout and exit (no server).",
    )
    args = parser.parse_args(argv)

    app = build_app()

    if args.print_openapi:
        print(json.dumps(app.openapi()))
        return 0

    import uvicorn

    log.info("Starting GAIA email sidecar on http://%s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
