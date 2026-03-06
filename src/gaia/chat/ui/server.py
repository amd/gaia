# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""FastAPI server for GAIA Chat UI.

Provides REST API endpoints for the chat desktop application:
- System status and health
- Session management (CRUD)
- Chat with streaming (SSE)
- Document library management
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .database import ChatDatabase
from .models import (
    AttachDocumentRequest,
    ChatRequest,
    ChatResponse,
    CreateSessionRequest,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadRequest,
    MessageListResponse,
    MessageResponse,
    SessionListResponse,
    SessionResponse,
    SourceInfo,
    SystemStatus,
    UpdateSessionRequest,
)

logger = logging.getLogger(__name__)

# Default port for chat UI server
DEFAULT_PORT = 4200


def create_app(db_path: str = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Path to SQLite database. None for default, ":memory:" for testing.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(
        title="GAIA Chat UI API",
        description="Privacy-first local chat application API",
        version="0.1.0",
    )

    # CORS - allow local connections
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Initialize database
    db = ChatDatabase(db_path)

    # Store db on app state so it's accessible
    app.state.db = db

    # ── System Endpoints ────────────────────────────────────────────────

    @app.get("/api/system/status", response_model=SystemStatus)
    async def system_status():
        """Check system readiness (Lemonade, models, disk space)."""
        status = SystemStatus()

        # Check Lemonade Server
        try:
            import httpx

            async with httpx.AsyncClient(timeout=3.0) as client:
                base_url = os.environ.get(
                    "LEMONADE_BASE_URL", "http://localhost:8000/api/v1"
                )

                # Use /health endpoint to get the actually loaded model
                # (not /models which returns the full catalog of available models)
                health_resp = await client.get(f"{base_url}/health")
                if health_resp.status_code == 200:
                    status.lemonade_running = True
                    health_data = health_resp.json()
                    status.model_loaded = health_data.get(
                        "model_loaded"
                    ) or None

                    # Check loaded models list for embedding model
                    for m in health_data.get("all_models_loaded", []):
                        if m.get("type") == "embedding":
                            status.embedding_model_loaded = True
                            break

                    # If no embedding found in loaded models,
                    # fall back to checking the model catalog
                    if not status.embedding_model_loaded:
                        models_resp = await client.get(f"{base_url}/models")
                        if models_resp.status_code == 200:
                            for m in models_resp.json().get("data", []):
                                if "embed" in m.get("id", "").lower():
                                    status.embedding_model_loaded = True
                                    break
                else:
                    # Fall back to /models if /health isn't available
                    resp = await client.get(f"{base_url}/models")
                    if resp.status_code == 200:
                        status.lemonade_running = True
                        data = resp.json()
                        models = data.get("data", [])
                        if models:
                            status.model_loaded = models[0].get(
                                "id", "unknown"
                            )
                        for m in models:
                            if "embed" in m.get("id", "").lower():
                                status.embedding_model_loaded = True
                                break
        except Exception:
            status.lemonade_running = False

        # Disk space
        try:
            usage = shutil.disk_usage(Path.home())
            status.disk_space_gb = round(usage.free / (1024**3), 1)
        except Exception:
            pass

        # Memory
        try:
            import psutil

            mem = psutil.virtual_memory()
            status.memory_available_gb = round(mem.available / (1024**3), 1)
        except ImportError:
            pass

        # Initialized check
        init_marker = Path.home() / ".gaia" / "chat" / "initialized"
        status.initialized = init_marker.exists()

        return status

    @app.get("/api/health")
    async def health():
        """Health check endpoint."""
        stats = db.get_stats()
        return {
            "status": "ok",
            "service": "gaia-chat-ui",
            "stats": stats,
        }

    # ── Session Endpoints ───────────────────────────────────────────────

    @app.get("/api/sessions", response_model=SessionListResponse)
    async def list_sessions(limit: int = 50, offset: int = 0):
        """List all chat sessions."""
        sessions = db.list_sessions(limit=limit, offset=offset)
        total = db.count_sessions()
        return SessionListResponse(
            sessions=[_session_to_response(s) for s in sessions],
            total=total,
        )

    @app.post("/api/sessions", response_model=SessionResponse)
    async def create_session(request: CreateSessionRequest):
        """Create a new chat session."""
        session = db.create_session(
            title=request.title,
            model=request.model,
            system_prompt=request.system_prompt,
            document_ids=request.document_ids,
        )
        return _session_to_response(session)

    @app.get("/api/sessions/{session_id}", response_model=SessionResponse)
    async def get_session(session_id: str):
        """Get session details."""
        session = db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return _session_to_response(session)

    @app.put("/api/sessions/{session_id}", response_model=SessionResponse)
    async def update_session(session_id: str, request: UpdateSessionRequest):
        """Update session title or system prompt."""
        session = db.update_session(
            session_id, title=request.title, system_prompt=request.system_prompt
        )
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return _session_to_response(session)

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        """Delete a session and its messages."""
        if not db.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"deleted": True}

    @app.get("/api/sessions/{session_id}/messages", response_model=MessageListResponse)
    async def get_messages(session_id: str, limit: int = 100, offset: int = 0):
        """Get messages for a session."""
        session = db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        messages = db.get_messages(session_id, limit=limit, offset=offset)
        total = db.count_messages(session_id)

        return MessageListResponse(
            messages=[_message_to_response(m) for m in messages],
            total=total,
        )

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str, format: str = "markdown"):
        """Export session to markdown."""
        session = db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        messages = db.get_messages(session_id, limit=10000)

        if format == "markdown":
            lines = [f"# {session['title']}\n"]
            lines.append(f"*Created: {session['created_at']}*\n")
            lines.append(f"*Model: {session['model']}*\n\n---\n")

            for msg in messages:
                role_label = "User" if msg["role"] == "user" else "Assistant"
                lines.append(f"**{role_label}:**\n\n{msg['content']}\n\n---\n")

            content = "\n".join(lines)
            return {"content": content, "format": "markdown"}
        elif format == "json":
            return {"session": session, "messages": messages, "format": "json"}
        else:
            raise HTTPException(
                status_code=400, detail=f"Unsupported format: {format}"
            )

    # ── Chat Endpoint ───────────────────────────────────────────────────

    @app.post("/api/chat/send")
    async def send_message(request: ChatRequest):
        """Send a message and get a response (streaming or non-streaming)."""
        # Verify session exists
        session = db.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Save user message
        db.add_message(request.session_id, "user", request.message)

        if request.stream:
            return StreamingResponse(
                _stream_chat_response(db, session, request),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            # Non-streaming response
            response_text = await _get_chat_response(db, session, request)
            msg_id = db.add_message(
                request.session_id, "assistant", response_text
            )
            return ChatResponse(
                message_id=msg_id,
                content=response_text,
                sources=[],
            )

    # ── Document Endpoints ──────────────────────────────────────────────

    @app.get("/api/documents", response_model=DocumentListResponse)
    async def list_documents():
        """List all documents in the library."""
        docs = db.list_documents()
        total_size = sum(d.get("file_size", 0) for d in docs)
        total_chunks = sum(d.get("chunk_count", 0) for d in docs)

        return DocumentListResponse(
            documents=[_doc_to_response(d) for d in docs],
            total=len(docs),
            total_size_bytes=total_size,
            total_chunks=total_chunks,
        )

    @app.post("/api/documents/upload-path", response_model=DocumentResponse)
    async def upload_by_path(request: DocumentUploadRequest):
        """Index a document by file path (for Electron/local use)."""
        # Validate and sanitize the user-provided path
        safe_filepath = _sanitize_document_path(request.filepath)

        if not safe_filepath.exists():
            raise HTTPException(status_code=404, detail="File not found")

        if not safe_filepath.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")

        # Compute file hash
        file_hash = _compute_file_hash(safe_filepath)
        file_size = safe_filepath.stat().st_size

        # Index the document with RAG
        chunk_count = await _index_document(safe_filepath)

        doc = db.add_document(
            filename=safe_filepath.name,
            filepath=str(safe_filepath),
            file_hash=file_hash,
            file_size=file_size,
            chunk_count=chunk_count,
        )

        return _doc_to_response(doc)

    @app.delete("/api/documents/{doc_id}")
    async def delete_document(doc_id: str):
        """Remove a document from the library."""
        if not db.delete_document(doc_id):
            raise HTTPException(status_code=404, detail="Document not found")
        return {"deleted": True}

    # ── Session-Document Attachments ────────────────────────────────────

    @app.post("/api/sessions/{session_id}/documents")
    async def attach_document(session_id: str, request: AttachDocumentRequest):
        """Attach a document to a session."""
        session = db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        doc = db.get_document(request.document_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        db.attach_document(session_id, request.document_id)
        return {"attached": True}

    @app.delete("/api/sessions/{session_id}/documents/{doc_id}")
    async def detach_document(session_id: str, doc_id: str):
        """Detach a document from a session."""
        db.detach_document(session_id, doc_id)
        return {"detached": True}

    # ── Serve Frontend Static Files ──────────────────────────────────────
    # Look for built frontend assets in the webui dist directory
    _webui_dist = Path(__file__).resolve().parent.parent.parent / "apps" / "chat" / "webui" / "dist"
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
        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """Serve the React SPA for all non-API routes."""
            # Sanitize the path to prevent directory traversal
            safe_path = _sanitize_static_path(_webui_dist, full_path)
            if safe_path is not None and safe_path.is_file():
                return FileResponse(str(safe_path))
            # Default to index.html for SPA routing
            return FileResponse(str(_webui_dist / "index.html"))
    else:
        logger.info("No frontend build found at %s. Run 'npm run build' in the webui directory.", _webui_dist)

        @app.get("/")
        async def no_frontend():
            """Inform user that frontend needs to be built."""
            return {
                "message": "GAIA Chat API is running. Frontend not built yet.",
                "hint": "Run 'npm run build' in src/gaia/apps/chat/webui/ to build the frontend.",
            }

    return app


# ── Helper Functions ────────────────────────────────────────────────────────


def _session_to_response(session: dict) -> SessionResponse:
    """Convert database session dict to response model."""
    return SessionResponse(
        id=session["id"],
        title=session["title"],
        created_at=session["created_at"],
        updated_at=session["updated_at"],
        model=session["model"],
        system_prompt=session.get("system_prompt"),
        message_count=session.get("message_count", 0),
        document_ids=session.get("document_ids", []),
    )


def _message_to_response(msg: dict) -> MessageResponse:
    """Convert database message dict to response model."""
    sources = None
    if msg.get("rag_sources"):
        try:
            raw_sources = msg["rag_sources"]
            if isinstance(raw_sources, str):
                raw_sources = json.loads(raw_sources)
            sources = [SourceInfo(**s) for s in raw_sources]
        except (json.JSONDecodeError, TypeError, KeyError):
            sources = None

    return MessageResponse(
        id=msg["id"],
        session_id=msg["session_id"],
        role=msg["role"],
        content=msg["content"],
        created_at=msg["created_at"],
        rag_sources=sources,
    )


def _doc_to_response(doc: dict) -> DocumentResponse:
    """Convert database document dict to response model."""
    return DocumentResponse(
        id=doc["id"],
        filename=doc["filename"],
        filepath=doc["filepath"],
        file_size=doc.get("file_size", 0),
        chunk_count=doc.get("chunk_count", 0),
        indexed_at=doc["indexed_at"],
        last_accessed_at=doc.get("last_accessed_at"),
        sessions_using=doc.get("sessions_using", 0),
    )


# Allowed document extensions for upload
_ALLOWED_EXTENSIONS = frozenset({
    ".pdf", ".txt", ".md", ".csv", ".json",
    ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".html", ".htm", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h",
    ".rs", ".go", ".rb", ".sh", ".bat", ".ps1",
    ".log", ".cfg", ".ini", ".toml",
})


def _sanitize_document_path(user_path: str) -> Path:
    """Sanitize a user-provided file path for document upload.

    Resolves the path, validates it is absolute, checks for null bytes,
    and enforces an extension allowlist. Returns a safe Path object
    that has been fully validated.

    Args:
        user_path: Raw file path string from user input.

    Returns:
        A resolved, validated Path object safe for filesystem operations.

    Raises:
        HTTPException: If the path is invalid, contains traversal, or
            has a disallowed extension.
    """
    # Reject null bytes early (before any path operations)
    if "\x00" in user_path:
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Resolve to absolute canonical path (eliminates .., symlinks, etc.)
    resolved = Path(user_path).resolve()

    # Verify the path is absolute
    if not resolved.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Check file extension against allowlist
    ext = resolved.suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}",
        )

    return resolved


def _sanitize_static_path(base_dir: Path, user_path: str) -> Optional[Path]:
    """Sanitize a URL path for static file serving.

    Ensures the resolved path stays within the base directory.
    Returns None if the path would escape the base directory.

    Args:
        base_dir: The root directory for static files (must be resolved).
        user_path: The URL path component from the request.

    Returns:
        A safe resolved Path within base_dir, or None if invalid.
    """
    if not user_path:
        return None

    # Reject null bytes and obvious traversal patterns
    if "\x00" in user_path or ".." in user_path:
        return None

    # Build and resolve the candidate path
    resolved_base = base_dir.resolve()
    candidate = (resolved_base / user_path).resolve()

    # Verify the candidate is within the base directory
    try:
        candidate.relative_to(resolved_base)
    except ValueError:
        return None

    return candidate


def _validate_file_path(filepath: Path) -> None:
    """Validate that a file path is safe to access.

    Checks:
    - Path is absolute (after resolve)
    - Path does not contain null bytes
    - File extension is in allowed set

    Raises:
        HTTPException: If the path is invalid or unsafe.
    """
    # Check for null bytes (path injection)
    if "\x00" in str(filepath):
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Verify the path is absolute (resolve() makes it absolute)
    if not filepath.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Check file extension
    ext = filepath.suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}",
        )


def _compute_file_hash(filepath: Path) -> str:
    """Compute SHA-256 hash of file contents."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            sha256.update(block)
    return sha256.hexdigest()


async def _index_document(filepath: Path) -> int:
    """Index a document using RAG SDK. Returns chunk count."""
    try:
        from gaia.rag.sdk import RAGSDK, RAGConfig

        config = RAGConfig()
        rag = RAGSDK(config)
        result = rag.index_file(str(filepath))
        return result.get("chunk_count", 0) if isinstance(result, dict) else 0
    except Exception as e:
        logger.warning("Failed to index document %s: %s", filepath, e)
        return 0


async def _get_chat_response(
    db: ChatDatabase, session: dict, request: ChatRequest
) -> str:
    """Get a non-streaming chat response from the LLM."""
    try:
        from gaia.chat.sdk import ChatConfig, ChatSDK

        # Build conversation history from database
        messages = db.get_messages(request.session_id, limit=20)
        history_pairs = []
        for i in range(0, len(messages) - 1, 2):
            if (
                i + 1 < len(messages)
                and messages[i]["role"] == "user"
                and messages[i + 1]["role"] == "assistant"
            ):
                history_pairs.append(
                    (messages[i]["content"], messages[i + 1]["content"])
                )

        config = ChatConfig(
            model=session.get("model", "Qwen3-Coder-30B-A3B-Instruct-GGUF"),
            system_prompt=session.get("system_prompt"),
        )
        chat = ChatSDK(config)

        # Restore history
        for user_msg, assistant_msg in history_pairs[-4:]:
            chat.chat_history.append(f"user: {user_msg}")
            chat.chat_history.append(f"assistant: {assistant_msg}")

        response = chat.send(request.message)
        return response.text

    except Exception as e:
        logger.error("Chat error: %s", e, exc_info=True)
        return "Error: Could not get response from LLM. Is Lemonade Server running? Check server logs for details."


async def _stream_chat_response(db: ChatDatabase, session: dict, request: ChatRequest):
    """Stream chat response as Server-Sent Events.

    Uses ChatAgent with SSEOutputHandler to emit agent activity events
    (steps, tool calls, thinking) alongside text chunks, giving the
    frontend visibility into what the agent is doing.
    """
    import queue
    import threading

    from gaia.chat.ui.sse_handler import SSEOutputHandler

    try:
        from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

        # Build conversation history for agent context
        messages = db.get_messages(request.session_id, limit=20)
        history_pairs = []
        for i in range(0, len(messages) - 1, 2):
            if (
                i + 1 < len(messages)
                and messages[i]["role"] == "user"
                and messages[i + 1]["role"] == "assistant"
            ):
                history_pairs.append(
                    (messages[i]["content"], messages[i + 1]["content"])
                )

        # Create SSE output handler to capture agent events
        sse_handler = SSEOutputHandler()

        # Create ChatAgent with SSE handler
        config = ChatAgentConfig(
            model_id=session.get("model"),
            max_steps=10,
            silent_mode=False,
            debug=False,
        )
        agent = ChatAgent(config)
        agent.console = sse_handler  # Replace console with SSE handler

        # Restore conversation history
        for user_msg, assistant_msg in history_pairs[-4:]:
            if hasattr(agent, "conversation_history"):
                agent.conversation_history.append(
                    {"role": "user", "content": user_msg}
                )
                agent.conversation_history.append(
                    {"role": "assistant", "content": assistant_msg}
                )

        # Run agent in background thread
        result_holder = {"answer": "", "error": None}

        def _run_agent():
            try:
                result = agent.process_query(request.message)
                if isinstance(result, dict):
                    result_holder["answer"] = result.get("answer", "")
                else:
                    result_holder["answer"] = str(result) if result else ""
            except Exception as e:
                logger.error("Agent error: %s", e, exc_info=True)
                result_holder["error"] = str(e)
            finally:
                sse_handler.signal_done()

        producer = threading.Thread(target=_run_agent, daemon=True)
        producer.start()

        # Yield SSE events from the handler's queue
        full_response = ""
        while True:
            try:
                event = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: sse_handler.event_queue.get(timeout=0.2)
                )
                if event is None:
                    # Sentinel - agent is done
                    break

                # Capture answer content for DB storage
                if event.get("type") == "answer":
                    full_response = event.get("content", "")
                elif event.get("type") == "chunk":
                    full_response += event.get("content", "")

                yield f"data: {json.dumps(event)}\n\n"

            except queue.Empty:
                if not producer.is_alive():
                    break
                continue

        # Check for errors from the agent thread
        if result_holder["error"]:
            error_msg = f"Agent error: {result_holder['error']}"
            if not full_response:
                full_response = error_msg
                error_data = json.dumps({"type": "error", "content": error_msg})
                yield f"data: {error_data}\n\n"

        # Use agent result if no streamed answer was captured
        if not full_response and result_holder["answer"]:
            full_response = result_holder["answer"]
            # Send as answer event since it wasn't streamed
            yield f"data: {json.dumps({'type': 'answer', 'content': full_response})}\n\n"

        # Save complete response to DB
        if full_response:
            msg_id = db.add_message(
                request.session_id, "assistant", full_response
            )
            done_data = json.dumps(
                {"type": "done", "message_id": msg_id, "content": full_response}
            )
            yield f"data: {done_data}\n\n"
        else:
            error_msg = "No response received from agent. Is Lemonade Server running?"
            db.add_message(request.session_id, "assistant", error_msg)
            error_data = json.dumps({"type": "error", "content": error_msg})
            yield f"data: {error_data}\n\n"

    except Exception as e:
        logger.error("Chat streaming error: %s", e, exc_info=True)
        error_msg = "Error: Could not get response from LLM. Is Lemonade Server running? Check server logs for details."
        try:
            db.add_message(request.session_id, "assistant", error_msg)
        except Exception:
            pass
        error_data = json.dumps({"type": "error", "content": error_msg})
        yield f"data: {error_data}\n\n"


# ── Standalone runner ───────────────────────────────────────────────────────


def main():
    """Run the Chat UI server."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="GAIA Chat UI Server")
    parser.add_argument("--host", default="localhost", help="Host (default: localhost)")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    log_level = "debug" if args.debug else "info"
    print(f"Starting GAIA Chat UI server on http://{args.host}:{args.port}")
    server_app = create_app()
    uvicorn.run(
        server_app,
        host=args.host,
        port=args.port,
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
