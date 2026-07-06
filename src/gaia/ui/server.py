# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""FastAPI server for GAIA Agent UI.

Provides REST API endpoints for the chat desktop application:
- System status and health
- Session management (CRUD)
- Chat with streaming (SSE)
- Document library management

Endpoint implementations are split into router modules under
``gaia.ui.routers``.  This file is responsible for:
- FastAPI app creation and middleware configuration
- Lifespan (startup/shutdown) management
- Router registration
- Static file serving for the React SPA frontend
- Backward-compatible re-exports of helper functions used by tests
"""

import asyncio
import logging
import os
import shutil  # noqa: F401  # pylint: disable=unused-import
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# ── Backward-compatible re-exports ──────────────────────────────────────────
# Tests use @patch("gaia.ui.server._get_chat_response") etc., so we must
# expose these names at module level.  The canonical implementations live
# in ``_chat_helpers`` (shared by both server.py and the router modules).
# pylint: disable=unused-import
from ._chat_helpers import _attach_chat_stream  # noqa: F401
from ._chat_helpers import _build_history_pairs  # noqa: F401
from ._chat_helpers import _compute_allowed_paths  # noqa: F401
from ._chat_helpers import _get_chat_response  # noqa: F401
from ._chat_helpers import _index_document  # noqa: F401
from ._chat_helpers import _resolve_rag_paths  # noqa: F401
from ._chat_helpers import _stream_chat_response  # noqa: F401
from .agent_loop import agent_loop

# pylint: enable=unused-import
from .database import ChatDatabase
from .document_monitor import DocumentMonitor
from .routers import agents as agents_router_mod
from .routers import chat as chat_router_mod
from .routers import connectors as connectors_router_mod
from .routers import documents as documents_router_mod
from .routers import files as files_router_mod
from .routers import goals as goals_router_mod
from .routers import hub as hub_router_mod
from .routers import mcp as mcp_router_mod
from .routers import memory as memory_router_mod
from .routers import schedules as schedules_router_mod
from .routers import sessions as sessions_router_mod
from .routers import system as system_router_mod
from .routers import tunnel as tunnel_router_mod
from .tunnel import TunnelManager
from .utils import ALLOWED_EXTENSIONS as _ALLOWED_EXTENSIONS  # noqa: F401
from .utils import compute_file_hash as _compute_file_hash  # noqa: F401
from .utils import sanitize_document_path as _sanitize_document_path  # noqa: F401
from .utils import sanitize_static_path as _sanitize_static_path
from .utils import validate_file_path as _validate_file_path  # noqa: F401

logger = logging.getLogger(__name__)

# Default port for agent UI server
DEFAULT_PORT = 4200

# Localhost addresses that bypass tunnel authentication (Electron app)
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# API paths that bypass tunnel authentication (monitoring / preflight)
_AUTH_EXEMPT_PATHS = {"/api/health"}

# HttpOnly cookie name used to bootstrap tunnel auth from the QR-code URL.
# When a mobile browser opens ``https://<tunnel>/?token=<uuid>`` the SPA
# handler (``serve_spa``) sets this cookie on the response, so the React
# app's subsequent ``fetch('/api/...')`` calls carry it automatically
# (same-origin fetches include cookies by default).
_TUNNEL_COOKIE_NAME = "gaia_tunnel_token"


# ── Tunnel Auth Middleware ──────────────────────────────────────────────────


class TunnelAuthMiddleware(BaseHTTPMiddleware):
    """Validate tunnel auth token on API requests arriving through the ngrok tunnel.

    When the tunnel is active, every ``/api/*`` request whose source is
    *not* localhost must carry a valid token, provided via either:

    1. ``Authorization: Bearer <token>`` header (scriptable clients, curl)
    2. ``gaia_tunnel_token`` cookie (set by ``serve_spa`` when a mobile
       browser first opens the QR-code URL containing ``?token=<uuid>``)

    Local requests (from the Electron desktop app) and the ``/api/health``
    monitoring endpoint are always allowed through.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Gate /api/* routes and the /v1/connections forwarded-grant surface
        # (#1292) — the latter carries OAuth secrets, so remote/tunnel access
        # must present a valid token just like /api/*.
        if not (path.startswith("/api/") or path.startswith("/v1/")):
            return await call_next(request)

        # Always allow exempt paths (health check, etc.)
        if path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # Check whether the tunnel is active
        tunnel: TunnelManager = getattr(request.app.state, "tunnel", None)
        if tunnel is None or not tunnel.active:
            return await call_next(request)

        # ── Localhost bypass (Electron desktop app) ────────────────────
        # The bypass requires BOTH the raw TCP peer to be on localhost
        # AND the request to lack any ``X-Forwarded-*`` headers. The
        # second clause is what makes the bypass spoof-resistant: ngrok
        # always *adds* ``X-Forwarded-For`` / ``X-Forwarded-Host`` /
        # ``X-Forwarded-Proto`` to tunnelled requests, so if any of those
        # are present the request came in over the wire and must
        # authenticate — even if a remote attacker tried to set
        # ``X-Forwarded-For: 127.0.0.1`` to fake a localhost source.
        #
        # Note: ``request.client.host`` reflects the raw TCP peer because
        # the standalone runner in ``main()`` passes
        # ``forwarded_allow_ips=""`` to uvicorn, disabling the proxy-header
        # rewrite that would otherwise let the ``X-Forwarded-For`` value
        # take precedence.
        client_host = request.client.host if request.client else None
        has_forwarded_marker = any(
            h in request.headers
            for h in ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto")
        )
        if client_host in _LOCAL_HOSTS and not has_forwarded_marker:
            return await call_next(request)

        # ── Remote request through tunnel -- require valid token ─────────
        # Extract token from Authorization header OR cookie.
        token = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[len("bearer ") :].strip()  # noqa: E203
        if not token:
            token = request.cookies.get(_TUNNEL_COOKIE_NAME)

        if not token:
            logger.warning(
                "Tunnel auth: rejecting %s %s from %s (no header/cookie)",
                request.method,
                path,
                client_host,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        if not tunnel.validate_token(token):
            logger.warning(
                "Tunnel auth: rejecting %s %s from %s (invalid token)",
                request.method,
                path,
                client_host,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid tunnel authentication token"},
            )

        return await call_next(request)


# ── Application Factory ────────────────────────────────────────────────────


def create_app(db_path: str = None, webui_dist: str = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Path to SQLite database. None for default, ":memory:" for testing.
        webui_dist: Path to the pre-built frontend dist directory. When None,
            falls back to the default location relative to this package.

    Returns:
        Configured FastAPI application.
    """
    # Initialize database early so lifespan can access it
    db = ChatDatabase(db_path)

    # Background indexing: track running tasks by document ID
    # so we can report status and cancel them.
    indexing_tasks: dict = {}  # doc_id -> asyncio.Task

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage startup/shutdown lifecycle for background services."""
        from gaia.ui.dispatch import DispatchQueue

        # ── Boot-time initialization via DispatchQueue ──────────────────
        # Replaces the previous fire-and-forget asyncio.create_task() calls
        # with a tracked dispatch queue so the frontend can report progress.

        queue = DispatchQueue(max_workers=4)
        app.state.dispatch_queue = queue

        # ── Agent Registry ──────────────────────────────────────────────
        from gaia.agents.registry import AgentRegistry
        from gaia.ui._chat_helpers import set_agent_registry

        registry = AgentRegistry()
        registry.discover()
        app.state.agent_registry = registry
        set_agent_registry(registry)
        agent_ids = [r.id for r in registry.list()]
        logger.info(
            "server: Agent registry initialized with %d agents: %s",
            len(agent_ids),
            agent_ids,
        )

        def _check_lemonade():
            """Pre-warm LemonadeManager — check reachability only."""
            from gaia.llm.lemonade_manager import LemonadeManager

            LemonadeManager.ensure_ready(
                quiet=True,
                min_context_size=0,  # Only check reachability — don't trigger model reloads
            )

        def _import_modules():
            """Pre-import heavy pure-library modules so first-message imports are cached.

            ChatAgent/RAGSDK/MCPClientManager are intentionally excluded: their
            import trees pull in gaia.apps.* modules that instantiate AgentSDK
            at module level, which calls LemonadeManager.ensure_ready() and can
            trigger a model switch.
            """
            # pylint: disable=unused-import
            import sys

            import faiss  # noqa: F401
            import sentence_transformers  # noqa: F401

            # Log which SWIG backend faiss actually loaded.
            # Order matters: check most-optimized first.
            _swig_variants = [
                ("faiss.swigfaiss_avx512_spr", "AVX-512 SPR"),
                ("faiss.swigfaiss_avx512", "AVX-512"),
                ("faiss.swigfaiss_sve", "SVE"),
                ("faiss.swigfaiss_avx2", "AVX2"),
            ]
            opt = next(
                (label for mod, label in _swig_variants if mod in sys.modules),
                None,
            )
            if opt:
                logger.info("faiss: loaded with %s support", opt)
            else:
                logger.info(
                    "faiss: loaded (generic — no AVX2/AVX-512 SWIG module "
                    "in this wheel; vector search still works, just slower)"
                )

        def _load_model():
            """Pre-load the expected LLM model so the first prompt skips model loading.

            Uses the same model_load_lock as _maybe_load_expected_model() to
            prevent double loads if a chat request arrives during preload.
            """
            import httpx

            from gaia.llm.lemonade_client import (
                lemonade_auth_headers,
                resolve_lemonade_api_key,
            )
            from gaia.llm.lemonade_manager import DEFAULT_CONTEXT_SIZE, LemonadeManager
            from gaia.ui._chat_helpers import model_load_lock

            base_url = LemonadeManager.get_base_url() or "http://localhost:13305/api/v1"
            _auth = lemonade_auth_headers(resolve_lemonade_api_key())

            # Check if a chat model is already loaded.
            # Let exceptions propagate so the DispatchQueue marks the job as
            # FAILED (not DONE) — the frontend will show "degraded" state.
            resp = httpx.get(f"{base_url}/health", timeout=5.0, headers=_auth)
            if resp.status_code == 200:
                all_models = resp.json().get("all_models_loaded", [])
                if any(m.get("type") in ("llm", "vlm") for m in all_models):
                    return  # Already loaded — nothing to do

            from gaia.llm.lemonade_client import LemonadeClient

            with model_load_lock:
                # Double-check after acquiring the lock: another thread may have
                # loaded the model while we were waiting.
                try:
                    resp2 = httpx.get(f"{base_url}/health", timeout=5.0, headers=_auth)
                    if resp2.status_code == 200:
                        all_models2 = resp2.json().get("all_models_loaded", [])
                        if any(m.get("type") in ("llm", "vlm") for m in all_models2):
                            return
                except Exception:
                    pass  # proceed with load attempt

                from gaia.ui.routers.system import _DEFAULT_MODEL_NAME

                model_id = db.get_setting("custom_model") or _DEFAULT_MODEL_NAME
                LemonadeClient(verbose=False).load_model(
                    model_id, ctx_size=DEFAULT_CONTEXT_SIZE, prompt=False
                )

        # Dispatch startup tasks.  Jobs A and B run in parallel; Job C
        # waits for A (needs Lemonade reachable) before loading the model.
        lemonade_id = queue.dispatch(
            "Checking LLM server", _check_lemonade, visible=True
        )
        queue.dispatch("Loading ML libraries", _import_modules, visible=True)
        queue.dispatch(
            "Loading AI model",
            _load_model,
            visible=True,
            depends_on=lemonade_id,
        )

        # Start autonomous agent loop
        await agent_loop.start(db=db, app_state=app.state)
        logger.info("AgentLoop started")

        # ── Task scheduler (user-defined recurring tasks) ────────────────
        # Salvaged from #517. Scheduled tasks are explicit user automations
        # (cron analogy), so they run standalone rather than through the
        # GoalStore approval workflow — but they share AgentLoop's tunnel
        # gate (no background runs while a public tunnel is active).
        from gaia.ui.scheduler import Scheduler

        _sched_timeout = float(os.environ.get("GAIA_SCHEDULE_TIMEOUT", "300"))

        async def _schedule_executor(prompt: str) -> str:
            """Execute a scheduled prompt through a fresh ChatAgent.

            Constructs the agent inline rather than via the chat router's
            session cache: `_get_cached_agent` is a lookup keyed to live
            interactive sessions (SSE/doc plumbing); background runs are
            fresh-context by design.
            """
            tunnel = getattr(app.state, "tunnel", None)
            allow_tunnel = os.environ.get(
                "GAIA_AUTONOMOUS_ALLOW_TUNNEL", ""
            ).lower() in ("1", "true", "yes")
            if tunnel and tunnel.active and not allow_tunnel:
                raise RuntimeError(
                    "Scheduled run suspended: public tunnel is active. "
                    "Stop the tunnel or set GAIA_AUTONOMOUS_ALLOW_TUNNEL=1."
                )

            def _run() -> str:
                # ChatAgent ships as the standalone gaia-agent-chat wheel (#1102).
                try:
                    from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig
                except ImportError as e:
                    raise RuntimeError(
                        "The chat agent is not installed. Install it with "
                        "`pip install gaia-agent-chat` (or `pip install "
                        '"amd-gaia[agents]"` for all agents) to run scheduled '
                        "chat tasks."
                    ) from e

                # Beta dynamic tool loader (#1798). Inert here: scheduled runs
                # use the default "full" prompt profile and the loader only
                # activates on the "doc" profile — wired for future-proofing so
                # this path doesn't silently diverge if that ever changes.
                dynamic_tools = db.get_setting("dynamic_tools", "false") == "true"
                config = ChatAgentConfig(
                    max_steps=5,
                    silent_mode=True,
                    debug=False,
                    dynamic_tools=dynamic_tools,
                )
                agent = ChatAgent(config)
                result = agent.process_query(prompt)
                if isinstance(result, dict):
                    val = result.get("result")
                    return val if val is not None else result.get("answer", "")
                return str(result) if result else ""

            loop = asyncio.get_running_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, _run), timeout=_sched_timeout
            )

        scheduler = Scheduler(db=db, executor=_schedule_executor)
        app.state.scheduler = scheduler
        await scheduler.start()
        logger.info("Task scheduler started (timeout=%ss)", _sched_timeout)

        # Start document file monitor for auto re-indexing
        monitor = DocumentMonitor(
            db=db,
            index_fn=_index_document,
            interval=30.0,
            active_tasks=indexing_tasks,
        )
        app.state.document_monitor = monitor
        await monitor.start()
        logger.info("Document file monitor started (30s polling interval)")

        # ── Connector activation watcher (issue #1226) ──────────────────
        # CLI/SDK activation writes land in the activations ledger from a
        # separate process; poll it and emit connector.activation.changed so
        # an open Settings tab reflects them live without a manual refresh.
        from gaia.connectors.activation_watcher import start_watcher

        start_watcher()
        logger.info("Connector activation watcher started")

        # ── Connections (issue #915) ────────────────────────────────────
        # Eager tripwire sweep so a rotated OAuth client_id surfaces in
        # the server logs at boot (and clears stale entries) BEFORE any
        # SSE client connects. Per plan amendment A3, missing
        # GAIA_GOOGLE_CLIENT_ID logs a loud warning but does NOT crash
        # the lifespan — chat/documents/files/tunnel/mcp routers stay
        # available; only /api/connections returns 503 until the env
        # var is set.
        try:
            from gaia.connectors.api import tripwire_check

            tripwire_check()
            logger.info("connections: tripwire sweep complete")
        except Exception as e:  # noqa: BLE001 — defense in depth
            logger.warning(
                "connections: tripwire sweep failed (%s); proceeding "
                "without it. /api/connections endpoints may surface "
                "stale-credential errors at first call instead.",
                e,
            )

        # ── Grant key migration (#1592) ────────────────────────────────
        # Migrate orphaned legacy grant keys (e.g. builtin:email ->
        # installed:email from the #1520 hub rename) so users who granted
        # permissions under the old key don't silently lose access.
        try:
            from gaia.connectors.grants import migrate_legacy_agent_grants

            migrate_legacy_agent_grants()
            logger.info("connections: legacy grant migration complete")
        except Exception as e:  # noqa: BLE001 — defence in depth
            logger.warning(
                "connections: legacy grant migration failed (%s); "
                "users may need to re-grant permissions manually.",
                e,
            )

        # ── Connectors live-reload (issue #1004) ────────────────────────
        # Wire the McpServerHandler.reload_callback so a Settings →
        # Connectors enable/disable/configure/disconnect from the UI
        # broadcasts a reload to every cached chat-session agent's
        # per-instance MCPClientManager. Without this, toggling an MCP
        # only takes effect after GAIA restart.
        try:
            from gaia.connectors.handler import _HANDLER_REGISTRY
            from gaia.ui._chat_helpers import reload_all_session_agents_mcp

            mcp_handler = _HANDLER_REGISTRY.get("mcp_server")
            if mcp_handler is not None:
                mcp_handler.set_reload_callback(reload_all_session_agents_mcp)
                logger.info(
                    "connectors: McpServerHandler reload_callback wired to "
                    "reload_all_session_agents_mcp"
                )
            else:
                logger.warning(
                    "connectors: McpServerHandler not registered; live "
                    "reload of toggled connectors will be deferred until "
                    "GAIA restart"
                )
        except Exception as e:  # noqa: BLE001 — defense in depth
            logger.warning(
                "connectors: failed to wire McpServerHandler reload_callback "
                "(%s); live reload of toggled connectors will be deferred "
                "until GAIA restart",
                e,
            )

        yield

        # Shutdown
        await scheduler.shutdown()
        logger.info("Task scheduler stopped")
        await agent_loop.stop()
        logger.info("AgentLoop stopped")
        await queue.shutdown()
        await monitor.stop()
        logger.info("Document file monitor stopped")
        from gaia.connectors.activation_watcher import stop_watcher

        await stop_watcher()
        logger.info("Connector activation watcher stopped")
        db.close()
        logger.info("Database connection closed")
        memory_router_mod.close_store()
        logger.info("Memory store connection closed")
        goals_router_mod.close_store()
        logger.info("Goal store connection closed")
        sidecar_mgr = getattr(app.state, "email_sidecar_manager", None)
        if sidecar_mgr is not None:
            from starlette.concurrency import run_in_threadpool

            await run_in_threadpool(sidecar_mgr.shutdown)
            logger.info("Email sidecar stopped")

    app = FastAPI(
        title="GAIA Agent UI API",
        description="Privacy-first local chat application API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS - allow local origins and tunnel URLs for mobile access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:4200",
            "http://127.0.0.1:4200",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_origin_regex=r"https://[a-zA-Z0-9-]+\.(ngrok-free\.app|use\.devtunnels\.ms)",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Tunnel authentication -- reject unauthenticated remote requests when
    # the ngrok tunnel is active.  Must be added *after* CORSMiddleware so
    # that CORS preflight (OPTIONS) responses are handled first.
    app.add_middleware(TunnelAuthMiddleware)

    # Store shared state on app.state so routers can access via Depends
    app.state.db = db
    app.state.indexing_tasks = indexing_tasks
    app.state.max_indexed_files = int(os.environ.get("GAIA_MAX_INDEXED_FILES", "0"))

    # Initialize tunnel manager for mobile access
    tunnel = TunnelManager(port=DEFAULT_PORT)
    app.state.tunnel = tunnel

    # Concurrency control for /api/chat/send
    # ChatAgent is expensive (LLM connection, RAG indexing), so we limit
    # the number of concurrent chat requests to avoid resource exhaustion.
    app.state.chat_semaphore = asyncio.Semaphore(
        1
    )  # serialize: _TOOL_REGISTRY is global
    # Per-session locks prevent the same session from having multiple
    # concurrent requests, which would corrupt conversation state.
    app.state.session_locks: dict = {}  # session_id -> asyncio.Lock
    app.state.upload_locks: dict = {}  # resolved filepath -> asyncio.Lock

    # ── Global Exception Handler ────────────────────────────────────────
    # Prevent stack traces from leaking to external users (CodeQL
    # py/stack-trace-exposure).  Log the full traceback server-side
    # for debugging, but return only a generic error message.
    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        logger.error(
            "Unhandled exception on %s %s: %s\n%s",
            request.method,
            request.url.path,
            exc,
            traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    # ── Include Routers ──────────────────────────────────────────────────
    app.include_router(system_router_mod.router)
    # Hub routes (catalog/install/...) MUST precede the agents router: that
    # router has a greedy GET /api/agents/{agent_id:path} that would otherwise
    # capture /api/agents/catalog and /api/agents/{id}/install-status.
    app.include_router(hub_router_mod.router)
    app.include_router(agents_router_mod.router)
    app.include_router(sessions_router_mod.router)
    app.include_router(chat_router_mod.router)
    app.include_router(documents_router_mod.router)
    app.include_router(files_router_mod.router)
    app.include_router(tunnel_router_mod.router)
    app.include_router(goals_router_mod.router)
    app.include_router(memory_router_mod.router)
    app.include_router(schedules_router_mod.router)
    app.include_router(mcp_router_mod.router)
    # Issue #915 — OAuth connections (Settings page + agent grants).
    app.include_router(connectors_router_mod.router)
    # Issue #1292 — forwarded pre-authenticated connections (/v1/connections).
    app.include_router(connectors_router_mod.forwarded_router)
    # Email REST surface (/v1/email/*) — out-of-process sidecar ONLY (#1767
    # cutover / design decision 4). The core backend never imports the email
    # wheel: it stays lightweight, crash-isolated, and dogfoods the exact binary
    # we ship. GAIA_EMAIL_AGENT_MODE selects the backend process — user (default,
    # frozen binary, lazy-fetched + SHA-verified) or dev (uvicorn from source);
    # unset means user. The sidecar is the SOLE /v1/email surface — there is no
    # in-process fallback (a missing/unpublished binary fails loudly with a
    # remedy, never silently re-mounts the wheel).
    from gaia.ui.email_sidecar.manager import get_shared_manager
    from gaia.ui.email_sidecar.router import router as email_sidecar_router

    # Lazily spawned on the first /v1/email request (not at startup), so users
    # who never use email never pay for a sidecar. The shared manager is the SAME
    # one the in-app email chat agent (agent_type=email) drives, so the REST
    # surface and the chat agent share one sidecar process. The contract MAJOR is
    # pinned inside get_shared_manager() so a breaking upgrade fails loudly.
    app.state.email_sidecar_manager = get_shared_manager()
    app.include_router(email_sidecar_router)
    logger.info(
        "Email REST surface served by out-of-process sidecar "
        "(GAIA_EMAIL_AGENT_MODE=%s).",
        os.environ.get("GAIA_EMAIL_AGENT_MODE", "user"),
    )

    # ── Serve Uploaded Files ─────────────────────────────────────────────
    # Mount the uploads directory so uploaded files can be served by URL.
    _uploads_dir = Path.home() / ".gaia" / "chat" / "uploads"
    _uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/api/files/uploads",
        StaticFiles(directory=str(_uploads_dir)),
        name="uploaded-files",
    )

    # ── Serve Frontend Static Files ──────────────────────────────────────
    # Look for built frontend assets in the webui dist directory.
    # The dist path is resolved per-request so a build that completes after
    # startup is served immediately on the next refresh without restarting
    # the server (issue #1088).
    _default_dist = Path(__file__).resolve().parent.parent / "apps" / "webui" / "dist"
    _webui_dist = Path(webui_dist) if webui_dist else _default_dist

    # Warn at startup when the dist dir is absent so that users can diagnose
    # a wheel install without frontend assets. The warning is advisory only --
    # the per-request handler will serve the fallback page until a build appears.
    if not (_webui_dist / "index.html").is_file():
        try:
            _resolved_for_log = _webui_dist.resolve()
        except OSError:
            _resolved_for_log = _webui_dist
        logger.warning(
            "No frontend build found at %s (resolved from gaia.apps.webui). "
            "If you installed via pip, the wheel may have shipped without "
            "dist/ — diagnose with: "
            "python -c 'import gaia.apps.webui as m; "
            "from pathlib import Path; "
            'print(Path(m.__file__).parent / "dist")\'. '
            "If you installed from source, run 'npm ci && npm run build' "
            "in src/gaia/apps/webui/.",
            _resolved_for_log,
        )
    else:
        logger.info("Serving frontend from %s", _webui_dist)

    from fastapi.responses import FileResponse

    # Prevent browsers and tunnel proxies from caching index.html so
    # that rebuilt assets (with new content hashes) are always picked up.
    # ``Referrer-Policy: no-referrer`` ensures that even if a token
    # transiently appears in the URL (the QR-code landing path), it is
    # never leaked to outbound requests via the ``Referer`` header.
    _NO_CACHE = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "Referrer-Policy": "no-referrer",
    }

    _FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GAIA Agent UI &mdash; Backend API</title>
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 Helvetica, Arial, sans-serif;
    max-width: 640px;
    margin: 4rem auto;
    padding: 0 1.5rem;
    line-height: 1.55;
    color: #1f2328;
    background: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
    body { color: #e6edf3; background: #0d1117; }
    a { color: #58a6ff; }
    code { background: #161b22; }
  }
  h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
  p  { margin: 0.75rem 0; }
  ul { padding-left: 1.25rem; }
  li { margin: 0.25rem 0; }
  code {
    background: #f3f4f6;
    padding: 0.1rem 0.35rem;
    border-radius: 4px;
    font-size: 0.95em;
  }
  .muted { color: #656d76; font-size: 0.9rem; margin-top: 2rem; }
</style>
</head>
<body>
  <h1>This is the GAIA backend API</h1>
  <p>
    To use the GAIA interface, open the GAIA desktop app
    (download at
    <a href="https://github.com/amd/gaia/releases">github.com/amd/gaia/releases</a>).
    For browser-mode setup and troubleshooting, see
    <a href="https://amd-gaia.ai/docs/guides/agent-ui">amd-gaia.ai/docs/guides/agent-ui</a>.
  </p>
  <ul>
    <li><a href="/docs">API documentation</a> (<code>/docs</code>)</li>
    <li><a href="/api/health">Health endpoint</a> (<code>/api/health</code>)</li>
  </ul>
  <p class="muted">
    GAIA Agent UI backend is running, but no frontend build was found.
  </p>
</body>
</html>
"""

    def _maybe_bootstrap_tunnel_cookie(request: Request):
        """Validate ``?token=<uuid>`` and return a token-stripping redirect.

        When a mobile browser first opens the QR-code URL
        ``https://<tunnel>/?token=<uuid>``, we validate the token against
        the active tunnel and:

        1. Set a ``HttpOnly``, ``SameSite=Strict``, ``Secure`` cookie so
           the SPA's subsequent same-origin ``fetch('/api/...')`` calls
           authenticate automatically -- no frontend token-plumbing.
        2. Redirect (303) to the same path with ``token`` stripped so the
           token doesn't linger in the address bar, browser history, or
           outbound ``Referer`` headers.

        ``SameSite=Strict`` is the cookie-side defence against CSRF on
        state-changing endpoints reached via the cookie path -- modern
        browsers refuse to attach the cookie on any cross-site request.

        Returns the redirect response if a cookie was bootstrapped, or
        ``None`` if no token was present / valid (caller serves the
        requested file normally).
        """
        tunnel_mgr = getattr(request.app.state, "tunnel", None)
        qs_token = request.query_params.get("token")
        if not (
            tunnel_mgr is not None
            and tunnel_mgr.active
            and qs_token
            and tunnel_mgr.validate_token(qs_token)
        ):
            return None

        # ngrok terminates TLS and forwards plain HTTP, so direct
        # request.url.scheme is often "http".  Trust X-Forwarded-Proto
        # when present so the Secure flag is set on real tunnel requests.
        fwd_proto = request.headers.get("x-forwarded-proto", "").lower()
        is_https = request.url.scheme == "https" or fwd_proto == "https"

        # Build the redirect target: same path, all query params except
        # ``token``. Preserves friendly params like ``?session=...``.
        stripped_qs = urlencode(
            [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
        )
        target = request.url.path + (f"?{stripped_qs}" if stripped_qs else "")

        redirect = RedirectResponse(url=target, status_code=303)
        redirect.set_cookie(
            key=_TUNNEL_COOKIE_NAME,
            value=qs_token,
            httponly=True,
            secure=is_https,
            samesite="strict",
            path="/",
        )
        logger.info(
            "Tunnel auth: bootstrapped cookie for client %s (secure=%s, target=%s)",
            request.client.host if request.client else "unknown",
            is_https,
            request.url.path,
        )
        return redirect

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """Serve the React SPA for all non-API routes.

        Resolves the dist directory on every request so a build that
        completes after startup is picked up immediately without a
        process restart (issue #1088).
        """
        resolved_dist = _webui_dist.resolve()
        index_html = resolved_dist / "index.html"

        if not index_html.is_file():
            # Frontend build not present yet -- serve the actionable fallback.
            return HTMLResponse(content=_FALLBACK_HTML, status_code=200)

        # 1. Token bootstrap path: only fires for the index-html case
        #    (token always lands on ``/`` from the QR code). On any
        #    static asset path we ignore the token entirely so the
        #    cookie can't be planted via ``GET /favicon.png?token=...``.
        #
        # 2. Static asset path: use the shared ``sanitize_static_path``
        #    utility -- it explicitly returns ``None`` for traversal
        #    attempts, so CodeQL can trace the validation through to
        #    the ``FileResponse`` call.
        sanitized = _sanitize_static_path(resolved_dist, full_path)

        if sanitized is not None and sanitized.is_file():
            # Static asset (JS, CSS, image) -- never bootstrap a cookie
            # off this path; only the SPA index does that.
            return FileResponse(str(sanitized))

        # A missing file under /assets/ is a real 404 -- don't mask a broken
        # hashed chunk with index.html, or the browser parses the HTML as JS
        # (``Uncaught SyntaxError: Unexpected token '<'``). SPA fallback is for
        # route paths only.
        if full_path.startswith("assets/"):
            return HTMLResponse(content="Not Found", status_code=404)

        # SPA fallback: serve index.html. Bootstrap the auth cookie
        # if a valid ?token= is present (returns a 303 redirect that
        # strips the token from the URL).
        redirect = _maybe_bootstrap_tunnel_cookie(request)
        if redirect is not None:
            return redirect
        return FileResponse(str(index_html), headers=_NO_CACHE)

    return app


# ── Standalone runner ───────────────────────────────────────────────────────


def main():
    """Run the Agent UI server."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="GAIA Agent UI Server")
    parser.add_argument("--host", default="localhost", help="Host (default: localhost)")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--ui-dist",
        default=None,
        help="Path to pre-built Agent UI frontend dist directory",
    )
    args = parser.parse_args()

    log_level = "debug" if args.debug else "info"
    print(f"Starting GAIA Agent UI server on http://{args.host}:{args.port}")
    server_app = create_app(webui_dist=args.ui_dist)
    uvicorn.run(
        server_app,
        host=args.host,
        port=args.port,
        log_level=log_level,
        access_log=args.debug,  # Only show HTTP access logs in debug mode
        # SECURITY: do NOT trust ``X-Forwarded-For`` / ``X-Forwarded-Proto``
        # to rewrite ``request.client.host``. ngrok forwards from the
        # local agent (127.0.0.1), so uvicorn's default of trusting
        # forwarded headers from 127.0.0.1 would let a remote attacker
        # send ``X-Forwarded-For: 127.0.0.1`` through the tunnel and
        # impersonate the Electron app. The localhost-bypass check in
        # ``TunnelAuthMiddleware`` separately requires the request to
        # carry no ``X-Forwarded-*`` headers, giving us a spoof-resistant
        # distinction between Electron-direct and ngrok-tunnelled traffic.
        proxy_headers=False,
        forwarded_allow_ips="",
    )


if __name__ == "__main__":
    # When run via `python -m gaia.ui.server`, the module is __main__ not
    # gaia.ui.server.  Register it under its canonical name so that
    # sys.modules["gaia.ui.server"] lookups (used by router modules for
    # test-patchable function resolution) succeed.
    import sys as _sys

    _sys.modules.setdefault("gaia.ui.server", _sys.modules[__name__])
    main()
