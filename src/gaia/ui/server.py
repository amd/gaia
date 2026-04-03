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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# ── Backward-compatible re-exports ──────────────────────────────────────────
# Tests use @patch("gaia.ui.server._get_chat_response") etc., so we must
# expose these names at module level.  The canonical implementations live
# in ``_chat_helpers`` (shared by both server.py and the router modules).
# pylint: disable=unused-import
from ._chat_helpers import _build_history_pairs  # noqa: F401
from ._chat_helpers import _compute_allowed_paths  # noqa: F401
from ._chat_helpers import _get_chat_response  # noqa: F401
from ._chat_helpers import _index_document  # noqa: F401
from ._chat_helpers import _resolve_rag_paths  # noqa: F401
from ._chat_helpers import _stream_chat_response  # noqa: F401

# pylint: enable=unused-import
from .database import ChatDatabase
from .document_monitor import DocumentMonitor
from .routers import chat as chat_router_mod
from .routers import documents as documents_router_mod
from .routers import files as files_router_mod
from .routers import mcp as mcp_router_mod
from .routers import sessions as sessions_router_mod
from .routers import system as system_router_mod
from .routers import tunnel as tunnel_router_mod
from .tunnel import TunnelManager
from .utils import ALLOWED_EXTENSIONS as _ALLOWED_EXTENSIONS  # noqa: F401
from .utils import compute_file_hash as _compute_file_hash  # noqa: F401
from .utils import sanitize_document_path as _sanitize_document_path  # noqa: F401
from .utils import sanitize_static_path as _sanitize_static_path  # noqa: F401
from .utils import validate_file_path as _validate_file_path  # noqa: F401

logger = logging.getLogger(__name__)

# Default port for agent UI server
DEFAULT_PORT = 4200

# Localhost addresses that bypass tunnel authentication (Electron app)
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# API paths that bypass tunnel authentication (monitoring / preflight)
_AUTH_EXEMPT_PATHS = {"/api/health"}


# ── Tunnel Auth Middleware ──────────────────────────────────────────────────


class TunnelAuthMiddleware(BaseHTTPMiddleware):
    """Validate Bearer token on API requests arriving through the ngrok tunnel.

    When the tunnel is active, every ``/api/*`` request whose source is
    *not* localhost must carry a valid ``Authorization: Bearer <token>``
    header.  Local requests (from the Electron desktop app) and the
    ``/api/health`` monitoring endpoint are always allowed through.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Only gate /api/* routes
        if not path.startswith("/api/"):
            return await call_next(request)

        # Always allow exempt paths (health check, etc.)
        if path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # Check whether the tunnel is active
        tunnel: TunnelManager = getattr(request.app.state, "tunnel", None)
        if tunnel is None or not tunnel.active:
            return await call_next(request)

        # Allow requests originating from localhost (Electron app)
        client_host = request.client.host if request.client else None
        if client_host in _LOCAL_HOSTS:
            return await call_next(request)

        # ── Remote request through tunnel -- require Bearer token ────────
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        token = auth_header[len("bearer ") :].strip()  # noqa: E203
        if not tunnel.validate_token(token):
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
            import faiss  # noqa: F401
            import sentence_transformers  # noqa: F401

        def _load_model():
            """Pre-load the expected LLM model so the first prompt skips model loading.

            Uses the same model_load_lock as _maybe_load_expected_model() to
            prevent double loads if a chat request arrives during preload.
            """
            import httpx

            from gaia.llm.lemonade_manager import DEFAULT_CONTEXT_SIZE, LemonadeManager
            from gaia.ui._chat_helpers import model_load_lock

            base_url = LemonadeManager.get_base_url() or "http://localhost:8000/api/v1"

            # Check if a chat model is already loaded.
            # Let exceptions propagate so the DispatchQueue marks the job as
            # FAILED (not DONE) — the frontend will show "degraded" state.
            resp = httpx.get(f"{base_url}/health", timeout=5.0)
            if resp.status_code == 200:
                all_models = resp.json().get("all_models_loaded", [])
                if any(m.get("type") in ("llm", "vlm") for m in all_models):
                    return  # Already loaded — nothing to do

            from gaia.llm.lemonade_client import LemonadeClient

            with model_load_lock:
                # Double-check after acquiring the lock: another thread may have
                # loaded the model while we were waiting.
                try:
                    resp2 = httpx.get(f"{base_url}/health", timeout=5.0)
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

        def _warmup_prompt_cache():
            """Send a warmup inference to populate Lemonade's prompt cache.

            Constructs a ChatAgent to generate the full system prompt
            (including tool descriptions), sends a single-token completion,
            then discards the agent.  Lemonade's LCP-based prompt cache
            stores the KV state so the first real user message only
            processes the delta (~30 new tokens instead of ~7,400).

            Safe to import ChatAgent here because _load_model has already
            completed (dependency chain enforced by DispatchQueue).  The
            model is loaded, so LemonadeManager.ensure_ready() is a no-op
            — see the exclusion comment in _import_modules above.
            """
            from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig
            from gaia.llm.lemonade_client import LemonadeClient
            from gaia.ui.routers.system import _DEFAULT_MODEL_NAME

            model_id = db.get_setting("custom_model") or _DEFAULT_MODEL_NAME

            config = ChatAgentConfig(
                model_id=model_id,
                streaming=False,
                silent_mode=True,
                debug=False,
                rag_documents=[],
            )
            agent = ChatAgent(config)
            system_prompt = agent.system_prompt

            try:
                LemonadeClient(verbose=False).chat_completions(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "Hi"},
                    ],
                    max_completion_tokens=1,
                    stream=False,
                )
            finally:
                # Explicitly clean up MCP connections — ChatAgent.__del__
                # only stops file watchers, not MCP subprocesses.
                if hasattr(agent, "_mcp_manager") and agent._mcp_manager:
                    agent._mcp_manager.disconnect_all()

        # Dispatch startup tasks.  Jobs A and B run in parallel; Job C
        # waits for A (needs Lemonade reachable) before loading the model;
        # Job D waits for C before warming the prompt cache.
        lemonade_id = queue.dispatch(
            "Checking LLM server", _check_lemonade, visible=True
        )
        queue.dispatch("Loading ML libraries", _import_modules, visible=True)
        load_model_id = queue.dispatch(
            "Loading AI model",
            _load_model,
            visible=True,
            depends_on=lemonade_id,
        )
        queue.dispatch(
            "Preparing AI assistant",
            _warmup_prompt_cache,
            visible=True,
            depends_on=load_model_id,
        )

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

        yield

        # Shutdown
        await queue.shutdown()
        await monitor.stop()
        logger.info("Document file monitor stopped")
        db.close()
        logger.info("Database connection closed")

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
    app.include_router(sessions_router_mod.router)
    app.include_router(chat_router_mod.router)
    app.include_router(documents_router_mod.router)
    app.include_router(files_router_mod.router)
    app.include_router(tunnel_router_mod.router)
    app.include_router(mcp_router_mod.router)

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
    # Look for built frontend assets in the webui dist directory
    _default_dist = Path(__file__).resolve().parent.parent / "apps" / "webui" / "dist"
    _webui_dist = Path(webui_dist) if webui_dist else _default_dist
    if _webui_dist.is_dir():
        logger.info("Serving frontend from %s", _webui_dist)

        from fastapi.responses import FileResponse

        # Mount static assets (JS, CSS, etc.)
        app.mount(
            "/assets",
            StaticFiles(directory=str(_webui_dist / "assets")),
            name="static-assets",
        )

        # Serve index.html for all non-API routes (SPA fallback)
        _resolved_dist = _webui_dist.resolve()
        _index_html = str(_resolved_dist / "index.html")
        # Prevent browsers and tunnel proxies from caching index.html so
        # that rebuilt assets (with new content hashes) are always picked up.
        # Hashed files under /assets/ are cached normally by StaticFiles.
        _NO_CACHE = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """Serve the React SPA for all non-API routes."""
            # Inline path sanitization (prevents directory traversal).
            # Checks are explicit so static analysis (CodeQL) can verify
            # the user-controlled ``full_path`` is properly constrained.
            if not full_path or "\x00" in full_path or ".." in full_path:
                return FileResponse(_index_html, headers=_NO_CACHE)

            candidate = (_resolved_dist / full_path).resolve()

            # Verify candidate stays within the dist directory
            try:
                candidate.relative_to(_resolved_dist)
            except ValueError:
                return FileResponse(_index_html, headers=_NO_CACHE)

            if candidate.is_file():
                return FileResponse(str(candidate))

            # Default to index.html for SPA routing
            return FileResponse(_index_html, headers=_NO_CACHE)

    else:
        logger.info(
            "No frontend build found at %s. Run 'npm run build' in the webui directory.",
            _webui_dist,
        )

        @app.get("/")
        async def no_frontend():
            """Inform user that frontend needs to be built."""
            return {
                "message": "GAIA Agent UI API is running. Frontend not built yet.",
                "hint": "Run 'npm run build' in src/gaia/apps/webui/ to build the frontend.",
            }

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
    )


if __name__ == "__main__":
    # When run via `python -m gaia.ui.server`, the module is __main__ not
    # gaia.ui.server.  Register it under its canonical name so that
    # sys.modules["gaia.ui.server"] lookups (used by router modules for
    # test-patchable function resolution) succeed.
    import sys as _sys

    _sys.modules.setdefault("gaia.ui.server", _sys.modules[__name__])
    main()
