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
import os
import sys
import uuid
from pathlib import Path

# How --skill-set reaches the per-request agent sessions (#2466): via the env var
# EmailAgentConfig.skill_set reads. Imported so the two cannot drift.
from gaia_agent_email.config import SKILL_SET_ENV

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


def install_email_unhandled_exception_handler(app):
    """Return structured JSON for unexpected /v1/email/* exceptions (#3000).

    Registered for ``Exception``, so it runs as ``ServerErrorMiddleware``'s
    handler — outside the ``ExceptionMiddleware`` that already converts
    route-raised HTTPException and validation errors. Those never reach here;
    the passthrough branches cover the same errors raised from outer middleware.
    Anything else is a 500 with an error_id; the traceback is logged only.
    """
    from fastapi.exception_handlers import request_validation_exception_handler
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(Exception)
    async def _unhandled_email_exception(request, exc):
        # fastapi.HTTPException subclasses the Starlette one, so this covers both.
        if isinstance(exc, StarletteHTTPException):
            return JSONResponse(
                status_code=exc.status_code, content={"detail": exc.detail}
            )
        if isinstance(exc, RequestValidationError):
            return await request_validation_exception_handler(request, exc)
        error_id = uuid.uuid4().hex[:12]
        log.exception(
            "Unhandled email API exception on %s %s error_id=%s",
            request.method,
            request.url.path,
            error_id,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "error_id": error_id},
        )


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
    from gaia_agent_email.autonomy_scheduler import (
        AutonomyScheduleConfig,
        AutonomyScheduler,
    )
    from gaia_agent_email.briefing import BriefingScheduleConfig, BriefingScheduler
    from gaia_agent_email.connection_intake_routes import (
        router as connection_intake_router,
    )
    from gaia_agent_email.connector_routes import router as connector_router
    from gaia_agent_email.contract import SCHEMA_VERSION
    from gaia_agent_email.supervision import is_daemon_supervised

    # Daily inbox briefing (#1608) — env config is read at build time so an
    # invalid value aborts startup loudly, not at the first scheduled fire.
    # Off by default: without the env opt-in no scheduler task is created.
    briefing_config = BriefingScheduleConfig.from_env()
    # Full-autonomy cycle driver (#1115). Off by default; env config read at
    # build time so an invalid level/interval aborts startup loudly.
    autonomy_config = AutonomyScheduleConfig.from_env()

    @asynccontextmanager
    async def lifespan(_app):
        # V2-15 (#2156): under daemon supervision the daemon drives the brief
        # AND the autonomy cycle from its single reconciled clock, so the
        # embedded timers stay dark — running both over one store is a
        # double-run. Standalone / bare integrator runs (no supervision env)
        # keep the embedded timers live.
        if is_daemon_supervised():
            log.info(
                "Email sidecar under daemon supervision: embedded "
                "BriefingScheduler and AutonomyScheduler gated off (the daemon "
                "drives both from its reconciled clock)."
            )
            yield
            return
        briefing_scheduler = BriefingScheduler(briefing_config)
        briefing_scheduler.start()
        autonomy_scheduler = AutonomyScheduler(autonomy_config)
        autonomy_scheduler.start()
        try:
            yield
        finally:
            await briefing_scheduler.stop()
            await autonomy_scheduler.stop()

    app = FastAPI(
        title="GAIA Email Agent Sidecar",
        version=agent_version,
        description="Email triage REST sidecar.",
        lifespan=lifespan,
    )
    install_email_unhandled_exception_handler(app)

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
    # OAuth forward-out intake (#2154): the daemon POSTs short-lived access
    # tokens here. Token-gated like every mailbox-touching router — only the
    # daemon holding the sidecar bearer can forward a credential.
    app.include_router(connection_intake_router, dependencies=token_gate)
    # Stateful agent surface (/v1/email/agent/*): hosts a session-scoped
    # EmailTriageAgent with memory + tool-confirmation so the Agent UI can drive
    # the full conversational agent over HTTP instead of importing it in-process.
    # Router import is light; the heavy agent/memory imports are deferred to the
    # first session build.
    app.include_router(agent_router, dependencies=token_gate)

    # require_caller_token is a plain Request dependency (not a
    # fastapi.security class), so FastAPI never emits securitySchemes for
    # it (#2993) — overlay the real, conditional (bearer-or-none) posture.
    caller_auth.install_openapi_security(app)
    return app


# Module-level app for uvicorn's import-string form (dev mode's `--reload`) and
# for the ``packaging/server.py`` freeze shim. Built exactly once per process.
app = build_app()


def _read_declared_skill_sets() -> list[str]:
    """Skill-set names this build declares. Raises if the manifest is unreadable."""
    from gaia.hub.manifest import parse as parse_manifest

    from gaia_agent_email.agent import EmailTriageAgent

    return list(parse_manifest(EmailTriageAgent.SKILL_MANIFEST).skill_sets.set_names)


def _declared_skill_sets() -> list[str]:
    """Declared set names for the ``--skill-set`` help string, or ``[]``.

    Help-text only, so an unreadable manifest degrades the wording rather than
    breaking ``--help``. **Never use this to validate a requested name** — a
    swallowed read error would turn validation into "accept anything". Use
    :func:`_read_declared_skill_sets`, which raises.
    """
    try:
        return _read_declared_skill_sets()
    except Exception as e:  # noqa: BLE001 — help text only
        log.debug("Could not read declared skill sets for --skill-set help: %s", e)
        return []


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
        "--skill-set",
        default=None,
        metavar="NAME",
        help="Activate this bundled skill set for every agent session instead "
        "of the one the connected mailbox's account type selects. Valid names "
        "come from gaia-agent.yaml's skill_sets: block "
        f"({', '.join(_declared_skill_sets()) or 'none declared'}).",
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

    # Validate a pinned skill set here, at startup, rather than letting the first
    # session fail — and validate the env-var form identically to the flag, since
    # the docs tell integrators the two are equivalent. The flag wins when both
    # are set.
    requested_set = args.skill_set or os.environ.get(SKILL_SET_ENV, "").strip()
    if requested_set:
        source = (
            "--skill-set" if args.skill_set else f"{SKILL_SET_ENV} in the environment"
        )
        try:
            declared = _read_declared_skill_sets()
        except Exception as exc:  # noqa: BLE001 — re-raised as a CLI error below
            parser.error(
                f"{source} requested skill set {requested_set!r}, but this "
                f"build's declared sets could not be read: {exc}"
            )
        if not declared:
            # This build ships with gaia-agent.yaml's skill_sets: commented out,
            # so there is nothing to pin. Say that, rather than "Valid sets: ".
            parser.error(
                f"{source} requested skill set {requested_set!r}, but this "
                "agent declares no skill sets — Agent Skills are switched off "
                "in this build. Drop the option, or uncomment the 'skill_sets:' "
                "and 'default_skill_set:' blocks in gaia-agent.yaml."
            )
        if requested_set not in declared:
            parser.error(
                f"{source} requested skill set {requested_set!r}, which this "
                f"agent does not declare. Valid sets: {', '.join(declared)}."
            )
        # Every agent session is built per-request inside the app (which is
        # constructed at import time), so the override travels by env var —
        # inherited by the --reload child process too.
        os.environ[SKILL_SET_ENV] = requested_set
        log.info(
            "Email sidecar: skill set pinned to %r via %s for every session "
            "(overrides mailbox account-type selection).",
            requested_set,
            source,
        )

    reload = bool(args.reload or args.dev)
    if reload and getattr(sys, "frozen", False):
        # The frozen binary has no source tree to watch, and uvicorn's reloader
        # would re-exec the frozen exe. Fail loud — reload is a source-checkout
        # feature.
        parser.error(
            "--reload/--dev is not supported in the frozen binary (no source to "
            "watch). Run it from a source checkout instead: "
            "`gaia-agent-email serve --reload`."
        )

    import uvicorn

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
