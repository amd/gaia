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


def build_app():
    """Build the minimal FastAPI app hosting the email REST surface.

    Also mounts the playground's mailbox-connector routes
    (``/v1/email/connectors*``) so the always-served playground page can connect
    Gmail/Outlook and exercise live send. They reuse GAIA's connector framework
    (already linked in via the send path) and are excluded from the OpenAPI
    contract — a playground convenience, not part of the frozen email REST
    contract.
    """
    from contextlib import asynccontextmanager

    from fastapi import Depends, FastAPI
    from gaia_agent_email import __version__ as agent_version
    from gaia_agent_email import caller_auth
    from gaia_agent_email.agent_routes import router as agent_router
    from gaia_agent_email.api_routes import require_caller_token
    from gaia_agent_email.api_routes import router as email_router
    from gaia_agent_email.briefing import BriefingScheduleConfig, BriefingScheduler
    from gaia_agent_email.connector_routes import router as connector_router
    from gaia_agent_email.contract import SCHEMA_VERSION

    # Daily inbox briefing (#1608) — env config is read at build time so an
    # invalid value aborts startup loudly, not at the first scheduled fire.
    # Off by default: without the env opt-in no scheduler task is created.
    briefing_config = BriefingScheduleConfig.from_env()

    @asynccontextmanager
    async def lifespan(_app):
        scheduler = BriefingScheduler(briefing_config)
        scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    app = FastAPI(
        title="GAIA Email Agent Sidecar",
        version=agent_version,
        description="Frozen-binary email triage REST sidecar.",
        lifespan=lifespan,
    )

    # Caller authentication (#1706). The sidecar binds 127.0.0.1 and exposes
    # draft/send, so it MUST authenticate its caller — a no-auth localhost API is
    # reachable by any other local process and (via DNS-rebinding) by the user's
    # browser. The spawning parent hands over a per-session bearer token — via a
    # 0600 secret file (GAIA_EMAIL_SIDECAR_TOKEN_FILE, preferred #2149) or the
    # legacy GAIA_EMAIL_SIDECAR_TOKEN env var; the Host/Origin middleware closes
    # rebinding / drive-by-webpage access regardless. This is wired ONLY here —
    # the product server (gaia.api.openai_server) mounts the same router
    # unchanged.
    auth_config = caller_auth.config_from_env()
    caller_auth.configure(auth_config)
    app.add_middleware(caller_auth.HostOriginMiddleware)
    if auth_config.token:
        channel = (
            f"0600 secret file ({caller_auth.TOKEN_FILE_ENV_VAR})"
            if os.environ.get(caller_auth.TOKEN_FILE_ENV_VAR)
            else f"{caller_auth.TOKEN_ENV_VAR} env var (legacy delivery)"
        )
        log.info(
            "Email sidecar: caller authentication ENABLED via %s "
            "(per-session bearer token required on /v1/email/* requests).",
            channel,
        )
    else:
        log.warning(
            "Email sidecar: caller authentication DISABLED — neither %s nor %s "
            "is in the environment. This is intended for LOCAL DEVELOPMENT "
            "only; the shipped product spawns the sidecar with a per-session "
            "token. Host/Origin protection is still enforced.",
            caller_auth.TOKEN_FILE_ENV_VAR,
            caller_auth.TOKEN_ENV_VAR,
        )

    @app.get("/health", include_in_schema=True)
    async def health() -> dict:
        return {"status": "ok", "service": "gaia-agent-email"}

    @app.get("/version", include_in_schema=True)
    async def version() -> dict:
        # apiVersion is the host-facing REST contract version (the frozen
        # request/response schema); agentVersion is the package build.
        return {"apiVersion": SCHEMA_VERSION, "agentVersion": agent_version}

    # The token gate applies to EVERY mailbox-touching router (the exempt
    # probe/HTML paths are skipped inside the dependency). Connector routes
    # (configure / complete-OAuth / disconnect) and the stateful agent surface
    # can both act on the mailbox connection, so they are gated too — a local
    # process must present the session token to reach them (#1706).
    token_gate = [Depends(require_caller_token)]
    app.include_router(email_router, dependencies=token_gate)
    app.include_router(connector_router, dependencies=token_gate)
    # Stateful agent surface (/v1/email/agent/*): hosts a session-scoped
    # EmailTriageAgent with memory + tool-confirmation so the Agent UI can drive
    # the full conversational agent over HTTP instead of importing it in-process.
    # Router import is light; the heavy agent/memory imports are deferred to the
    # first session build.
    app.include_router(agent_router, dependencies=token_gate)
    return app


# Module-level app for uvicorn's import-string form (dev mode's `--reload`).
# Loaded as the TOP-LEVEL module `server` via `uvicorn server:app --app-dir
# <this dir>` — NOT `packaging.server:app`, which would resolve to the PyPI
# `packaging` library (this dir has no __init__.py by design). main() reuses this
# same instance, so the app is built exactly once per process.
app = build_app()


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

    if args.print_openapi:
        print(json.dumps(app.openapi()))
        return 0

    import uvicorn

    log.info("Starting GAIA email sidecar on http://%s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
