# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
In-package, runnable email REST sidecar — the single source of truth for the
sidecar app wiring, importable from an installed wheel **and** an editable
checkout.

``packaging/server.py`` (the PyInstaller freeze entry) is a thin re-export of
this module, so the frozen binary and a source ``uvicorn gaia_agent_email.server:app``
serve a byte-for-byte identical ``/v1/email/*`` contract.

Two ways to run it:

    # Production-shape: the frozen binary (or a plain source run).
    gaia-agent-email serve --port 8131

    # Fast dev loop: auto-reload on source edits, caller-token off for local dev.
    gaia-agent-email serve --reload            # watches the package dir
    gaia-agent-email serve --dev               # reload + explicit dev banner

The dev loop pairs with the ``@amd-gaia/agent-email`` npm client's
``connectSidecar({ baseUrl })`` (attach mode) — start this server from source,
attach the shipped client, edit Python, and the next call hits the reloaded code.

Triage uses the real local Lemonade model. If Lemonade is unreachable,
``POST /v1/email/triage`` returns HTTP 502 (``local LLM triage failed``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gaia_agent_email.sidecar")

# Default sidecar bind. NOT 4001 (reserved). 8131 is unused here.
DEFAULT_PORT = 8131
DEFAULT_HOST = "127.0.0.1"

# Import string uvicorn's reloader needs — reload requires an import-string app,
# not a pre-built object (which is what ``uvicorn.run(app, ...)`` uses).
_APP_IMPORT_STRING = "gaia_agent_email.server:app"


def build_app():
    """Build the minimal FastAPI app hosting the email REST surface.

    Mounts the same routers the product server (``gaia.api.openai_server``) and
    the frozen freeze entry mount, so the served contract is identical:

    - the email REST router (``/v1/email/*``),
    - the playground's mailbox-connector routes (``/v1/email/connectors*``) so
      the always-served playground page can connect Gmail/Outlook and exercise a
      live send — reuses GAIA's connector framework and is excluded from the
      OpenAPI contract (a playground convenience, not part of the frozen email
      REST contract),
    - the stateful, session-scoped agent surface (``/v1/email/agent/*``),
    - two dependency-free probes the sidecar lifecycle handshake needs
      (``GET /health``, ``GET /version``).

    A minimal app (vs. freezing the whole ``gaia api`` app) keeps the frozen
    binary lean: the full app eagerly imports every registered agent (RAG, code,
    …), ballooning the binary and the freeze-time hidden-import surface.
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
        description="Email triage REST sidecar.",
        lifespan=lifespan,
    )

    # Caller authentication (#1706). The sidecar binds 127.0.0.1 and exposes
    # draft/send, so it MUST authenticate its caller — a no-auth localhost API is
    # reachable by any other local process and (via DNS-rebinding) by the user's
    # browser. The spawning parent passes a per-session bearer token over the
    # private GAIA_EMAIL_SIDECAR_TOKEN env channel; the Host/Origin middleware
    # closes rebinding / drive-by-webpage access regardless. This is wired ONLY
    # here — the product server (gaia.api.openai_server) mounts the same router
    # unchanged.
    auth_config = caller_auth.config_from_env()
    caller_auth.configure(auth_config)
    app.add_middleware(caller_auth.HostOriginMiddleware)
    if auth_config.token:
        log.info(
            "Email sidecar: caller authentication ENABLED "
            "(per-session bearer token required on /v1/email/* requests)."
        )
    else:
        log.warning(
            "Email sidecar: caller authentication DISABLED — no %s in the "
            "environment. This is intended for LOCAL DEVELOPMENT only; the "
            "shipped product spawns the sidecar with a per-session token. "
            "Host/Origin protection is still enforced.",
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


# Module-level app for uvicorn's import-string form (dev mode's `--reload`) and
# for the ``packaging/server.py`` freeze shim. Built exactly once per process.
app = build_app()


def _add_serve_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload on source edits (fast dev loop). Uses uvicorn's reloader.",
    )
    parser.add_argument(
        "--reload-dir",
        action="append",
        default=None,
        dest="reload_dirs",
        metavar="DIR",
        help="Extra directory to watch in --reload mode (repeatable). "
        "Defaults to the gaia_agent_email package dir; add your core src "
        "checkout to pick up edits there.",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Developer mode: implies --reload and logs the caller-token-off "
        "banner. For local iteration only — never ship this.",
    )
    parser.add_argument(
        "--print-openapi",
        action="store_true",
        help="Print the OpenAPI JSON to stdout and exit (no server).",
    )


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `serve` is the only (and default) subcommand: `gaia-agent-email --reload`
    # and `gaia-agent-email serve --reload` behave identically. Inject `serve`
    # when the first token is a flag or nothing was passed.
    if not argv or argv[0].startswith("-"):
        argv = ["serve", *argv]

    parser = argparse.ArgumentParser(
        prog="gaia-agent-email",
        description="GAIA Email Triage REST sidecar.",
    )
    sub = parser.add_subparsers(dest="command")
    serve_parser = sub.add_parser("serve", help="Run the email REST sidecar.")
    _add_serve_args(serve_parser)
    args = parser.parse_args(argv)

    if args.print_openapi:
        print(json.dumps(app.openapi()))
        return 0

    if args.port == 4001:
        parser.error("port 4001 is reserved and must never be used")

    import uvicorn

    reload = bool(args.reload or args.dev)
    if args.dev:
        log.warning(
            "Email sidecar: --dev — auto-reload ON, caller token off unless "
            "%s is set. Local iteration only; do not ship this.",
            "GAIA_EMAIL_SIDECAR_TOKEN",
        )

    if reload:
        # Reload needs an import-string app (not a pre-built object). Watch the
        # package dir by default so editing any gaia_agent_email module reloads;
        # callers can add their core src checkout with --reload-dir.
        reload_dirs = [str(Path(__file__).resolve().parent)]
        if args.reload_dirs:
            reload_dirs.extend(args.reload_dirs)
        log.info(
            "Starting GAIA email sidecar (reload) on http://%s:%d — watching %s",
            args.host,
            args.port,
            ", ".join(reload_dirs),
        )
        uvicorn.run(
            _APP_IMPORT_STRING,
            host=args.host,
            port=args.port,
            log_level="info",
            reload=True,
            reload_dirs=reload_dirs,
        )
        return 0

    log.info("Starting GAIA email sidecar on http://%s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
