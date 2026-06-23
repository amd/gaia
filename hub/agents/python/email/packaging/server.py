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
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gaia_agent_email.sidecar")

# Default sidecar bind. NOT 4001 (reserved). 8131 is unused here.
DEFAULT_PORT = 8131
DEFAULT_HOST = "127.0.0.1"


def build_app(with_connectors: bool = False):
    """Build the minimal FastAPI app hosting the email REST surface.

    ``with_connectors`` (playground mode) also mounts the flag-gated mailbox-
    connector routes so the playground can connect Gmail/Outlook and exercise
    live send. Off by default — a production sidecar stays connector-free (the
    consuming application owns the mailbox connection, milestone 40).
    """
    from fastapi import FastAPI
    from gaia_agent_email import __version__ as agent_version
    from gaia_agent_email.api_routes import router as email_router
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
    if with_connectors:
        # Playground mode only — reuse GAIA's connector framework (already
        # linked in via the send path) so the page can connect a mailbox.
        from gaia_agent_email.connector_routes import router as connector_router

        app.include_router(connector_router)
        log.info(
            "playground mode: mounted mailbox-connector routes at /v1/email/connectors"
        )
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
    parser.add_argument(
        "--playground",
        action="store_true",
        help=(
            "Mount the playground's mailbox-connector routes (/v1/email/connectors). "
            "Also enabled by GAIA_EMAIL_PLAYGROUND=1. Off by default — production "
            "sidecars stay connector-free."
        ),
    )
    args = parser.parse_args(argv)

    with_connectors = args.playground or os.environ.get(
        "GAIA_EMAIL_PLAYGROUND", ""
    ).strip().lower() in ("1", "true", "yes", "on")
    app = build_app(with_connectors=with_connectors)

    if args.print_openapi:
        print(json.dumps(app.openapi()))
        return 0

    import uvicorn

    log.info("Starting GAIA email sidecar on http://%s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
