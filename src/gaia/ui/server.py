# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""FastAPI server for GAIA Agent UI.

Provides REST API endpoints for the chat desktop application:
- System status and health
- Session management (CRUD)
- Chat with streaming (SSE)
- Document library management
"""

import asyncio
import datetime
import hashlib
import json
import logging
import os
import platform
import shutil
import string
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .database import ChatDatabase
from .models import (
    AttachDocumentRequest,
    BrowseResponse,
    ChatRequest,
    ChatResponse,
    CreateSessionRequest,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadRequest,
    FileEntry,
    FilePreviewResponse,
    FileSearchResponse,
    FileSearchResult,
    IndexFolderRequest,
    IndexFolderResponse,
    MessageListResponse,
    MessageResponse,
    QuickLink,
    SessionListResponse,
    SessionResponse,
    SourceInfo,
    SystemStatus,
    UpdateSessionRequest,
)
from .sse_handler import _fix_double_escaped
from .tunnel import TunnelManager

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
        title="GAIA Agent UI API",
        description="Privacy-first local chat application API",
        version="0.1.0",
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
        allow_origin_regex=r"https://.*\.ngrok.*\.app",  # Allow ngrok tunnel origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Initialize database
    db = ChatDatabase(db_path)

    # Store db on app state so it's accessible
    app.state.db = db

    # Initialize tunnel manager for mobile access
    tunnel = TunnelManager(port=DEFAULT_PORT)
    app.state.tunnel = tunnel

    # Background indexing: track running tasks by document ID
    # so we can report status and cancel them.
    _indexing_tasks: dict = {}  # doc_id -> asyncio.Task
    _LARGE_FILE_THRESHOLD = 5 * 1024 * 1024  # 5 MB

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
                    status.model_loaded = health_data.get("model_loaded") or None

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
                            status.model_loaded = models[0].get("id", "unknown")
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
            "service": "gaia-agent-ui",
            "stats": stats,
        }

    # ── Session Endpoints ───────────────────────────────────────────────

    @app.get("/api/sessions", response_model=SessionListResponse)
    async def list_sessions(limit: int = 50, offset: int = 0):
        """List all chat sessions."""
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        sessions = db.list_sessions(limit=limit, offset=offset)
        total = db.count_sessions()
        return SessionListResponse(
            sessions=[_session_to_response(s) for s in sessions],
            total=total,
        )

    @app.post("/api/sessions", response_model=SessionResponse)
    async def create_session(request: CreateSessionRequest):
        """Create a new chat session."""
        try:
            session = db.create_session(
                title=request.title,
                model=request.model,
                system_prompt=request.system_prompt,
                document_ids=request.document_ids,
            )
            return _session_to_response(session)
        except Exception as e:
            logger.error("Failed to create session: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create session: {e}",
            )

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
        limit = max(1, min(limit, 10000))
        offset = max(0, offset)
        session = db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        messages = db.get_messages(session_id, limit=limit, offset=offset)
        total = db.count_messages(session_id)

        return MessageListResponse(
            messages=[_message_to_response(m) for m in messages],
            total=total,
        )

    @app.delete("/api/sessions/{session_id}/messages/{message_id}")
    async def delete_message(session_id: str, message_id: int):
        """Delete a single message from a session."""
        session = db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if not db.delete_message(session_id, message_id):
            raise HTTPException(status_code=404, detail="Message not found")
        return {"deleted": True}

    @app.delete("/api/sessions/{session_id}/messages/{message_id}/and-below")
    async def delete_messages_from(session_id: str, message_id: int):
        """Delete a message and all subsequent messages in the session.

        Used by the "resend" feature: removes the target user message and
        everything below it so the conversation can be replayed.
        """
        session = db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        count = db.delete_messages_from(session_id, message_id)
        if count == 0:
            raise HTTPException(status_code=404, detail="Message not found")
        return {"deleted": True, "count": count}

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str, format: str = "markdown"):  # noqa: A002
        """Export session to markdown or JSON."""
        export_format = format  # Avoid shadowing builtin in function body
        session = db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        messages = db.get_messages(session_id, limit=10000)

        if export_format == "markdown":
            lines = [f"# {session['title']}\n"]
            lines.append(f"*Created: {session['created_at']}*\n")
            lines.append(f"*Model: {session['model']}*\n\n---\n")

            for msg in messages:
                role_label = "User" if msg["role"] == "user" else "Assistant"
                lines.append(f"**{role_label}:**\n\n{msg['content']}\n\n---\n")

            content = "\n".join(lines)
            return {"content": content, "format": "markdown"}
        elif export_format == "json":
            return {"session": session, "messages": messages, "format": "json"}
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported format: {export_format}",
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
            msg_id = db.add_message(request.session_id, "assistant", response_text)
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
        """Index a document by file path (for Electron/local use).

        Small files (<5 MB) are indexed synchronously. Larger files are
        indexed in the background so the UI stays responsive; the returned
        document will have ``indexing_status='indexing'`` and the frontend
        can poll ``GET /api/documents/{id}/status`` for progress.
        """
        safe_filepath = _sanitize_document_path(request.filepath)

        if not safe_filepath.exists():
            raise HTTPException(status_code=404, detail="File not found")

        if not safe_filepath.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")

        file_hash = _compute_file_hash(safe_filepath)
        file_size = safe_filepath.stat().st_size

        if file_size <= _LARGE_FILE_THRESHOLD:
            # Small file: index synchronously (fast)
            chunk_count = await _index_document(safe_filepath)
            doc = db.add_document(
                filename=safe_filepath.name,
                filepath=str(safe_filepath),
                file_hash=file_hash,
                file_size=file_size,
                chunk_count=chunk_count,
            )
            return _doc_to_response(doc)

        # Large file: create a placeholder record and index in background
        doc = db.add_document(
            filename=safe_filepath.name,
            filepath=str(safe_filepath),
            file_hash=file_hash,
            file_size=file_size,
            chunk_count=0,
        )
        doc_id = doc["id"]
        db.update_document_status(doc_id, "indexing")

        async def _background_index(doc_id: str, filepath: Path):
            """Run indexing in background, updating DB status on completion."""
            try:
                logger.info(
                    "Background indexing started for %s (%s)", filepath.name, doc_id
                )
                chunk_count = await _index_document(filepath)
                # Check if task was cancelled while we were indexing
                if doc_id in _indexing_tasks:
                    db.update_document_status(
                        doc_id, "complete", chunk_count=chunk_count
                    )
                    logger.info(
                        "Background indexing complete for %s: %d chunks",
                        filepath.name,
                        chunk_count,
                    )
            except asyncio.CancelledError:
                db.update_document_status(doc_id, "cancelled")
                logger.info("Background indexing cancelled for %s", filepath.name)
            except Exception as e:
                db.update_document_status(doc_id, "failed")
                logger.error(
                    "Background indexing failed for %s: %s",
                    filepath.name,
                    e,
                    exc_info=True,
                )
            finally:
                _indexing_tasks.pop(doc_id, None)

        task = asyncio.create_task(_background_index(doc_id, safe_filepath))
        _indexing_tasks[doc_id] = task

        # Return immediately with indexing_status='indexing'
        doc["indexing_status"] = "indexing"
        return _doc_to_response(doc)

    @app.get("/api/documents/{doc_id}/status")
    async def get_document_status(doc_id: str):
        """Get current indexing status for a document."""
        doc = db.get_document(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        is_active = doc_id in _indexing_tasks
        return {
            "id": doc_id,
            "indexing_status": doc.get("indexing_status", "complete"),
            "chunk_count": doc.get("chunk_count", 0),
            "is_active": is_active,
        }

    @app.post("/api/documents/{doc_id}/cancel")
    async def cancel_indexing(doc_id: str):
        """Cancel a running background indexing task."""
        task = _indexing_tasks.get(doc_id)
        if not task:
            raise HTTPException(
                status_code=404, detail="No active indexing task for this document"
            )
        task.cancel()
        db.update_document_status(doc_id, "cancelled")
        _indexing_tasks.pop(doc_id, None)
        logger.info("Indexing cancelled by user for document %s", doc_id)
        return {"cancelled": True, "id": doc_id}

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

    # ── Mobile Access / Tunnel Endpoints ─────────────────────────────────

    @app.post("/api/tunnel/start")
    async def start_tunnel():
        """Start ngrok tunnel for mobile access."""
        try:
            logger.info("Starting mobile access tunnel...")
            status = await tunnel.start()
            return status
        except Exception as e:
            error_msg = str(e)
            logger.error("Failed to start tunnel: %s", error_msg)
            raise HTTPException(status_code=500, detail=error_msg)

    @app.post("/api/tunnel/stop")
    async def stop_tunnel():
        """Stop ngrok tunnel."""
        try:
            logger.info("Stopping mobile access tunnel...")
            await tunnel.stop()
            return {"active": False}
        except Exception as e:
            error_msg = str(e)
            logger.error("Failed to stop tunnel: %s", error_msg)
            raise HTTPException(status_code=500, detail=error_msg)

    @app.get("/api/tunnel/status")
    async def tunnel_status():
        """Get current tunnel status."""
        return tunnel.get_status()

    # ── File Browsing Endpoint ───────────────────────────────────────────

    @app.get("/api/files/browse", response_model=BrowseResponse)
    async def browse_files(path: Optional[str] = None):
        """Browse files and folders for the document picker.

        Lists folders (always shown) and files whose extension is in
        _ALLOWED_EXTENSIONS.  Results are sorted folders-first, then
        alphabetically by name.

        Args:
            path: Directory to browse. Defaults to user home directory.
                  On Windows, pass an empty string or "/" to list drive
                  letters.
        """
        quick_links = _build_quick_links()

        # On Windows, treat None / empty / "/" as "list drive letters"
        if platform.system() == "Windows" and (not path or path in ("/", "\\")):
            entries = _list_windows_drives()
            return BrowseResponse(
                current_path="/",
                parent_path=None,
                entries=entries,
                quick_links=quick_links,
            )

        # Default to home directory when no path is given
        if not path:
            path = str(Path.home())

        # Security: reject null bytes
        if "\x00" in path:
            raise HTTPException(status_code=400, detail="Invalid path")

        resolved = Path(path).resolve(strict=False)

        # Do not follow symlinks
        if resolved.is_symlink():
            raise HTTPException(
                status_code=400, detail="Symbolic links are not supported"
            )

        if not resolved.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")

        # Determine parent path
        parent_path: Optional[str] = None
        if resolved.parent != resolved:
            parent_path = str(resolved.parent)
        elif platform.system() == "Windows":
            # At a drive root (e.g. C:\) — go back to drive listing
            parent_path = "/"

        entries: List[FileEntry] = []
        try:
            for item in resolved.iterdir():
                # Skip symlinks for security
                if item.is_symlink():
                    continue

                try:
                    stat = item.stat()
                except (OSError, PermissionError):
                    continue

                if item.is_dir():
                    entries.append(
                        FileEntry(
                            name=item.name,
                            path=str(item),
                            type="folder",
                            size=0,
                            extension=None,
                            modified=datetime.datetime.fromtimestamp(
                                stat.st_mtime
                            ).isoformat(),
                        )
                    )
                elif item.is_file():
                    ext = item.suffix.lower()
                    if ext in _ALLOWED_EXTENSIONS:
                        entries.append(
                            FileEntry(
                                name=item.name,
                                path=str(item),
                                type="file",
                                size=stat.st_size,
                                extension=ext,
                                modified=datetime.datetime.fromtimestamp(
                                    stat.st_mtime
                                ).isoformat(),
                            )
                        )
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="Permission denied for this directory"
            )

        # Sort: folders first, then files, alphabetically within each group
        entries.sort(key=lambda e: (e.type != "folder", e.name.lower()))

        return BrowseResponse(
            current_path=str(resolved),
            parent_path=parent_path,
            entries=entries,
            quick_links=quick_links,
        )

    # ── File Search Endpoint ──────────────────────────────────────────────

    @app.get("/api/files/search", response_model=FileSearchResponse)
    async def search_files(
        query: str,
        file_types: Optional[str] = None,
        max_results: int = 20,
    ):
        """Search for files across the filesystem by name pattern.

        Searches common user directories (Documents, Downloads, Desktop)
        then expands to deeper search if needed. Results sorted by
        modification date (most recent first).

        Args:
            query: File name pattern to search for (partial matches supported).
            file_types: Comma-separated extensions to filter (e.g., 'csv,xlsx').
            max_results: Maximum results to return (1-100, default 20).
        """
        import time as _time

        if not query or not query.strip():
            raise HTTPException(status_code=400, detail="Search query is required")

        # Security: reject null bytes
        if "\x00" in query:
            raise HTTPException(status_code=400, detail="Invalid search query")

        query_lower = query.strip().lower()
        max_results = min(max(max_results, 1), 100)

        # Build extension filter
        extensions = None
        if file_types:
            extensions = {
                f".{ext.strip().lower()}"
                for ext in file_types.split(",")
                if ext.strip()
            }

        matching_files: list = []
        seen_paths: set = set()
        searched_locations: list = []
        start_time = _time.monotonic()

        def matches(file_path: Path) -> bool:
            """Check if file matches the search criteria."""
            name_match = query_lower in file_path.name.lower()
            if not name_match:
                return False
            if extensions:
                return file_path.suffix.lower() in extensions
            return True

        def scan_directory(directory: Path, max_depth: int = 5, depth: int = 0):
            """Recursively scan a directory for matching files."""
            if depth > max_depth or len(matching_files) >= max_results:
                return
            if not directory.exists() or not directory.is_dir():
                return

            searched_locations.append(str(directory))

            try:
                for item in directory.iterdir():
                    if len(matching_files) >= max_results:
                        return
                    # Skip hidden/system directories
                    if item.name.startswith((".", "$", "__")):
                        continue
                    if item.name in (
                        "node_modules",
                        ".git",
                        "Windows",
                        "Program Files",
                        "Program Files (x86)",
                        "ProgramData",
                        "AppData",
                    ):
                        continue
                    try:
                        if item.is_symlink():
                            continue
                        if item.is_file() and matches(item):
                            resolved_str = str(item.resolve())
                            if resolved_str in seen_paths:
                                continue
                            seen_paths.add(resolved_str)
                            stat = item.stat()
                            size = stat.st_size
                            matching_files.append(
                                {
                                    "name": item.name,
                                    "path": str(item),
                                    "size": size,
                                    "size_display": _format_size(size),
                                    "extension": item.suffix.lower(),
                                    "modified": datetime.datetime.fromtimestamp(
                                        stat.st_mtime
                                    ).isoformat(),
                                    "directory": str(item.parent),
                                }
                            )
                        elif item.is_dir() and depth < max_depth:
                            scan_directory(item, max_depth, depth + 1)
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                pass

        # Search common locations first
        home = Path.home()
        priority_locations = [
            home / "Documents",
            home / "Downloads",
            home / "Desktop",
            home / "OneDrive",
        ]

        for loc in priority_locations:
            if len(matching_files) >= max_results:
                break
            scan_directory(loc, max_depth=4)

        # If not enough results, search home directory more broadly
        if len(matching_files) < max_results:
            scan_directory(home, max_depth=3)

        # Sort by modification date (most recent first)
        matching_files.sort(key=lambda f: f["modified"], reverse=True)
        matching_files = matching_files[:max_results]

        elapsed = _time.monotonic() - start_time
        logger.info(
            "File search for '%s': %d results in %.2fs (%d locations)",
            query,
            len(matching_files),
            elapsed,
            len(searched_locations),
        )

        return FileSearchResponse(
            results=[FileSearchResult(**f) for f in matching_files],
            total=len(matching_files),
            query=query,
            searched_locations=searched_locations[:10],  # Limit for response size
        )

    # ── File Preview Endpoint ─────────────────────────────────────────────

    @app.get("/api/files/preview", response_model=FilePreviewResponse)
    async def preview_file(path: str, lines: int = 50):
        """Get a preview of a file's contents.

        For text files, returns the first N lines.
        For CSV/TSV, also returns column names and row count.
        For binary files, returns metadata only.

        Args:
            path: Absolute path to the file.
            lines: Number of lines to preview (default 50, max 200).
        """
        if not path:
            raise HTTPException(status_code=400, detail="File path is required")

        if "\x00" in path:
            raise HTTPException(status_code=400, detail="Invalid file path")

        resolved = Path(path).resolve(strict=False)

        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")

        if not resolved.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")

        if resolved.is_symlink():
            raise HTTPException(status_code=400, detail="Symbolic links not supported")

        lines = min(max(lines, 1), 200)
        stat = resolved.stat()
        ext = resolved.suffix.lower()

        result = {
            "path": str(resolved),
            "name": resolved.name,
            "size": stat.st_size,
            "size_display": _format_size(stat.st_size),
            "extension": ext,
            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "is_text": False,
            "preview_lines": [],
            "total_lines": None,
            "columns": None,
            "row_count": None,
            "encoding": None,
        }

        # Try to read as text
        text_extensions = {
            ".txt",
            ".md",
            ".csv",
            ".tsv",
            ".json",
            ".xml",
            ".yaml",
            ".yml",
            ".py",
            ".js",
            ".ts",
            ".html",
            ".css",
            ".log",
            ".ini",
            ".cfg",
            ".toml",
            ".sql",
            ".sh",
            ".bat",
            ".ps1",
            ".java",
            ".c",
            ".cpp",
            ".h",
            ".rs",
            ".go",
            ".rb",
        }

        if ext in text_extensions or stat.st_size < 1_000_000:  # Try text for < 1MB
            for encoding in ("utf-8", "latin-1", "cp1252"):
                try:
                    with open(resolved, "r", encoding=encoding) as f:
                        all_lines = f.readlines()
                    result["is_text"] = True
                    result["encoding"] = encoding
                    result["total_lines"] = len(all_lines)
                    result["preview_lines"] = [
                        line.rstrip("\n\r")[:500] for line in all_lines[:lines]
                    ]

                    # CSV/TSV specific info
                    if ext in (".csv", ".tsv"):
                        import csv as csv_mod

                        delimiter = "\t" if ext == ".tsv" else ","
                        try:
                            with open(resolved, "r", encoding=encoding) as cf:
                                reader = csv_mod.reader(cf, delimiter=delimiter)
                                header = next(reader, None)
                                if header:
                                    result["columns"] = header
                                    row_count = sum(1 for _ in reader)
                                    result["row_count"] = row_count
                        except Exception:
                            pass
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue

        return FilePreviewResponse(**result)

    # ── Folder Indexing Endpoint ─────────────────────────────────────────

    @app.post("/api/documents/index-folder", response_model=IndexFolderResponse)
    async def index_folder(request: IndexFolderRequest):
        """Index all supported documents in a folder.

        Scans the given folder for files with extensions in
        _ALLOWED_EXTENSIONS and indexes each one using the RAG pipeline.
        Indexing runs in a thread-pool executor to avoid blocking the
        event loop.

        Args:
            request: Contains folder_path and recursive flag.
        """
        folder_path = request.folder_path

        # Security: reject null bytes
        if "\x00" in folder_path:
            raise HTTPException(status_code=400, detail="Invalid folder path")

        resolved = Path(folder_path).resolve(strict=False)

        if not resolved.exists():
            raise HTTPException(status_code=404, detail="Folder not found")

        if not resolved.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")

        # Collect all candidate files
        candidate_files: List[Path] = []
        try:
            pattern_iter = (
                resolved.rglob("*") if request.recursive else resolved.iterdir()
            )
            for item in pattern_iter:
                if item.is_symlink():
                    continue
                if item.is_file() and item.suffix.lower() in _ALLOWED_EXTENSIONS:
                    candidate_files.append(item)
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail="Permission denied while scanning folder",
            )

        if not candidate_files:
            return IndexFolderResponse(indexed=0, failed=0, documents=[], errors=[])

        logger.info(
            "Indexing %d files from %s (recursive=%s)",
            len(candidate_files),
            resolved,
            request.recursive,
        )

        indexed_docs: List[DocumentResponse] = []
        errors: List[str] = []

        for filepath in candidate_files:
            try:
                file_hash = await asyncio.get_running_loop().run_in_executor(
                    None, _compute_file_hash, filepath
                )
                file_size = filepath.stat().st_size

                chunk_count = await _index_document(filepath)

                doc = db.add_document(
                    filename=filepath.name,
                    filepath=str(filepath),
                    file_hash=file_hash,
                    file_size=file_size,
                    chunk_count=chunk_count,
                )
                indexed_docs.append(_doc_to_response(doc))
            except Exception as e:
                error_msg = f"{filepath.name}: {e}"
                logger.warning("Failed to index %s: %s", filepath, e)
                errors.append(error_msg)

        logger.info(
            "Folder indexing complete: %d indexed, %d failed",
            len(indexed_docs),
            len(errors),
        )

        return IndexFolderResponse(
            indexed=len(indexed_docs),
            failed=len(errors),
            documents=indexed_docs,
            errors=errors,
        )

    # ── Serve Frontend Static Files ──────────────────────────────────────
    # Look for built frontend assets in the webui dist directory
    _webui_dist = Path(__file__).resolve().parent.parent / "apps" / "webui" / "dist"
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
    from .models import AgentStepResponse

    sources = None
    if msg.get("rag_sources"):
        try:
            raw_sources = msg["rag_sources"]
            if isinstance(raw_sources, str):
                raw_sources = json.loads(raw_sources)
            sources = [SourceInfo(**s) for s in raw_sources]
        except Exception:
            sources = None

    agent_steps = None
    if msg.get("agent_steps"):
        try:
            raw_steps = msg["agent_steps"]
            if isinstance(raw_steps, str):
                raw_steps = json.loads(raw_steps)
            agent_steps = [AgentStepResponse(**s) for s in raw_steps]
        except Exception:
            agent_steps = None

    return MessageResponse(
        id=msg["id"],
        session_id=msg["session_id"],
        role=msg["role"],
        content=msg["content"],
        created_at=msg["created_at"],
        rag_sources=sources,
        agent_steps=agent_steps,
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
        indexing_status=doc.get("indexing_status", "complete"),
    )


# Allowed document extensions for upload
_ALLOWED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".html",
        ".htm",
        ".xml",
        ".yaml",
        ".yml",
        ".py",
        ".js",
        ".ts",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".rs",
        ".go",
        ".rb",
        ".sh",
        ".bat",
        ".ps1",
        ".log",
        ".cfg",
        ".ini",
        ".toml",
    }
)


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


def _build_quick_links() -> list:
    """Build a list of common quick-access filesystem locations.

    Returns platform-appropriate links to Desktop, Documents, Downloads,
    and the user home directory.
    """
    home = Path.home()
    links = [
        QuickLink(name="Home", path=str(home), icon="home"),
    ]

    candidates = [
        ("Desktop", home / "Desktop", "desktop"),
        ("Documents", home / "Documents", "documents"),
        ("Downloads", home / "Downloads", "download"),
    ]

    for name, candidate_path, icon in candidates:
        if candidate_path.is_dir():
            links.append(QuickLink(name=name, path=str(candidate_path), icon=icon))

    return links


def _list_windows_drives() -> list:
    """List available Windows drive letters as FileEntry items.

    Iterates A-Z and returns an entry for each drive letter whose
    root directory exists on the system.
    """
    entries = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if Path(drive).exists():
            entries.append(
                FileEntry(
                    name=f"{letter}:",
                    path=drive,
                    type="folder",
                    size=0,
                    extension=None,
                    modified=None,
                )
            )
    return entries


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"


def _compute_file_hash(filepath: Path) -> str:
    """Compute SHA-256 hash of file contents."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            sha256.update(block)
    return sha256.hexdigest()


async def _index_document(filepath: Path) -> int:
    """Index a document using RAG SDK. Returns chunk count.

    Runs the synchronous RAG indexing in a thread pool executor
    to avoid blocking the async event loop.
    """

    def _do_index():
        from gaia.rag.sdk import RAGSDK, RAGConfig

        # Allow access to the file's directory (and user home) since the UI
        # explicitly selected this file via the file browser.
        allowed = [str(filepath.parent), str(Path.home())]
        config = RAGConfig(allowed_paths=allowed)
        rag = RAGSDK(config)
        result = rag.index_document(str(filepath))
        logger.info("RAG index_document result for %s: %s", filepath, result)
        if isinstance(result, dict):
            if result.get("error"):
                logger.warning(
                    "RAG returned error for %s: %s", filepath, result["error"]
                )
            if not result.get("success"):
                logger.warning(
                    "RAG indexing unsuccessful for %s (success=False)", filepath
                )
            # RAG SDK returns "num_chunks", not "chunk_count"
            chunks = result.get("num_chunks", 0) or result.get("chunk_count", 0)
            logger.info(
                "Indexed %s: %d chunks (success=%s)",
                filepath,
                chunks,
                result.get("success"),
            )
            return chunks
        logger.warning(
            "RAG index_document returned non-dict for %s: %r", filepath, result
        )
        return 0

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_index)
    except Exception as e:
        logger.error("Failed to index document %s: %s", filepath, e, exc_info=True)
        return 0


def _build_history_pairs(
    messages: list,
) -> list:
    """Build user/assistant conversation pairs from message history.

    Iterates messages sequentially and pairs adjacent user→assistant messages.
    Unpaired messages (e.g., a user message without a following assistant reply
    due to a prior streaming error) are safely skipped without misaligning
    subsequent pairs.

    Returns:
        List of (user_content, assistant_content) tuples.
    """
    pairs = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg["role"] == "user" and i + 1 < len(messages):
            next_msg = messages[i + 1]
            if next_msg["role"] == "assistant":
                pairs.append((msg["content"], next_msg["content"]))
                i += 2
                continue
        # Skip unpaired or system messages
        i += 1
    return pairs


def _resolve_rag_paths(db: ChatDatabase, document_ids: list) -> list:
    """Resolve document IDs to file paths for RAG.

    If the session has specific documents attached (document_ids non-empty),
    resolves those IDs to file paths.  Otherwise falls back to ALL indexed
    documents in the global library so that newly-indexed files are
    immediately available to the agent without manual attachment.

    Returns:
        List of file-path strings suitable for ChatAgentConfig.rag_documents.
    """
    rag_file_paths = []
    if document_ids:
        for doc_id in document_ids:
            doc = db.get_document(doc_id)
            if doc and doc.get("filepath"):
                rag_file_paths.append(doc["filepath"])
            else:
                logger.warning("Document %s not found in database, skipping", doc_id)
    else:
        # No specific docs attached — use entire library
        all_docs = db.list_documents()
        for doc in all_docs:
            if doc.get("filepath"):
                rag_file_paths.append(doc["filepath"])
    return rag_file_paths


def _compute_allowed_paths(rag_file_paths: list) -> list:
    """Derive allowed filesystem paths from document locations.

    Collects the unique parent directories of all RAG document paths
    plus the user home directory, so the agent (and its RAG SDK) are
    permitted to read the indexed files.
    """
    dirs = {str(Path.home())}
    for fp in rag_file_paths:
        parent = str(Path(fp).parent)
        dirs.add(parent)
    return list(dirs)


async def _get_chat_response(
    db: ChatDatabase, session: dict, request: ChatRequest
) -> str:
    """Get a non-streaming chat response from the ChatAgent.

    Uses the full ChatAgent (with tools) instead of plain ChatSDK
    so non-streaming mode also has agentic capabilities.

    Runs the synchronous agent in a thread pool executor
    to avoid blocking the async event loop.
    """

    def _do_chat():
        from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

        # Build conversation history from database
        messages = db.get_messages(request.session_id, limit=20)
        history_pairs = _build_history_pairs(messages)

        # Resolve document IDs to file paths.
        # If the session has specific documents attached, use those.
        # Otherwise, use ALL documents from the global library so newly
        # indexed documents are immediately available to the agent.
        document_ids = session.get("document_ids", [])
        rag_file_paths = _resolve_rag_paths(db, document_ids)

        if rag_file_paths:
            logger.info("Chat using %d document(s) for RAG", len(rag_file_paths))

        allowed = _compute_allowed_paths(rag_file_paths)
        config = ChatAgentConfig(
            model_id=session.get("model"),
            max_steps=10,
            silent_mode=True,
            debug=False,
            rag_documents=rag_file_paths,
            allowed_paths=allowed,
        )
        agent = ChatAgent(config)

        # Restore conversation history
        for user_msg, assistant_msg in history_pairs[-4:]:
            if hasattr(agent, "conversation_history"):
                agent.conversation_history.append({"role": "user", "content": user_msg})
                agent.conversation_history.append(
                    {"role": "assistant", "content": assistant_msg}
                )

        result = agent.process_query(request.message)
        if isinstance(result, dict):
            # process_query returns {"result": "...", "status": "...", ...}
            return result.get("result", "") or result.get("answer", "")
        return str(result) if result else ""

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_chat)
    except Exception as e:
        logger.error("Chat error: %s", e, exc_info=True)
        return "Error: Could not get response from LLM. Is Lemonade Server running? Check server logs for details."


def _find_last_tool_step(steps: list) -> dict | None:
    """Find the last tool step in captured_steps, searching backwards."""
    for i in range(len(steps) - 1, -1, -1):
        if steps[i].get("type") == "tool":
            return steps[i]
    return steps[-1] if steps else None


async def _stream_chat_response(db: ChatDatabase, session: dict, request: ChatRequest):
    """Stream chat response as Server-Sent Events.

    Uses ChatAgent with SSEOutputHandler to emit agent activity events
    (steps, tool calls, thinking) alongside text chunks, giving the
    frontend visibility into what the agent is doing.
    """
    import queue
    import threading

    from gaia.ui.sse_handler import SSEOutputHandler

    try:
        from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

        # Build conversation history for agent context
        messages = db.get_messages(request.session_id, limit=20)
        history_pairs = _build_history_pairs(messages)

        # Create SSE output handler to capture agent events
        sse_handler = SSEOutputHandler()

        # Create ChatAgent with SSE handler
        # Resolve document IDs to file paths (falls back to all indexed docs)
        document_ids = session.get("document_ids", [])
        rag_file_paths = _resolve_rag_paths(db, document_ids)

        if rag_file_paths:
            logger.info(
                "Streaming chat using %d document(s) for RAG", len(rag_file_paths)
            )

        allowed = _compute_allowed_paths(rag_file_paths)
        config = ChatAgentConfig(
            model_id=session.get("model"),
            max_steps=10,
            streaming=False,  # Keep False so raw LLM JSON isn't streamed to frontend
            silent_mode=False,
            debug=False,
            rag_documents=rag_file_paths,
            allowed_paths=allowed,
        )
        agent = ChatAgent(config)
        agent.console = sse_handler  # Replace console with SSE handler

        # Restore conversation history
        for user_msg, assistant_msg in history_pairs[-4:]:
            if hasattr(agent, "conversation_history"):
                agent.conversation_history.append({"role": "user", "content": user_msg})
                agent.conversation_history.append(
                    {"role": "assistant", "content": assistant_msg}
                )

        # Run agent in background thread
        result_holder = {"answer": "", "error": None}

        def _run_agent():
            try:
                result = agent.process_query(request.message)
                if isinstance(result, dict):
                    # process_query returns {"result": "...", "status": "...", ...}
                    result_holder["answer"] = result.get("result", "") or result.get(
                        "answer", ""
                    )
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
        # Also capture agent steps for persistence
        full_response = ""
        captured_steps = []  # Collect agent steps for DB persistence
        step_id = 0
        idle_cycles = 0
        while True:
            try:
                event = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: sse_handler.event_queue.get(timeout=0.2)
                )
                idle_cycles = 0
                if event is None:
                    # Sentinel - agent is done
                    break

                event_type = event.get("type", "")

                # Capture answer content for DB storage
                if event_type == "answer":
                    full_response = event.get("content", "")
                elif event_type == "chunk":
                    full_response += event.get("content", "")

                # Capture agent steps for persistence
                if event_type == "thinking":
                    step_id += 1
                    # Deactivate previous steps
                    for s in captured_steps:
                        s["active"] = False
                    captured_steps.append(
                        {
                            "id": step_id,
                            "type": "thinking",
                            "label": "Thinking",
                            "detail": event.get("content"),
                            "active": True,
                            "timestamp": int(asyncio.get_running_loop().time() * 1000),
                        }
                    )
                elif event_type == "tool_start":
                    step_id += 1
                    for s in captured_steps:
                        s["active"] = False
                    captured_steps.append(
                        {
                            "id": step_id,
                            "type": "tool",
                            "label": f"Using {event.get('tool', 'tool')}",
                            "tool": event.get("tool"),
                            "detail": event.get("detail"),
                            "active": True,
                            "timestamp": int(asyncio.get_running_loop().time() * 1000),
                        }
                    )
                elif event_type == "tool_args" and captured_steps:
                    # Update the last TOOL step (not just last step, since thinking
                    # events may have been interleaved during tool execution)
                    tool_step = _find_last_tool_step(captured_steps)
                    if tool_step is not None:
                        tool_step["detail"] = event.get("detail", "")
                elif event_type == "tool_end" and captured_steps:
                    tool_step = _find_last_tool_step(captured_steps)
                    if tool_step is not None:
                        tool_step["active"] = False
                        tool_step["success"] = event.get("success", True)
                elif event_type == "tool_result" and captured_steps:
                    tool_step = _find_last_tool_step(captured_steps)
                    if tool_step is not None:
                        tool_step["active"] = False
                        tool_step["result"] = (
                            event.get("summary") or event.get("title") or "Done"
                        )
                        tool_step["success"] = event.get("success", True)
                        # Persist structured command output for terminal rendering
                        if event.get("command_output"):
                            tool_step["commandOutput"] = event["command_output"]
                elif event_type == "plan":
                    step_id += 1
                    for s in captured_steps:
                        s["active"] = False
                    captured_steps.append(
                        {
                            "id": step_id,
                            "type": "plan",
                            "label": "Created plan",
                            "planSteps": event.get("steps"),
                            "active": False,
                            "success": True,
                            "timestamp": int(asyncio.get_running_loop().time() * 1000),
                        }
                    )
                elif event_type == "agent_error":
                    step_id += 1
                    for s in captured_steps:
                        s["active"] = False
                    captured_steps.append(
                        {
                            "id": step_id,
                            "type": "error",
                            "label": "Error",
                            "detail": event.get("content"),
                            "active": False,
                            "success": False,
                            "timestamp": int(asyncio.get_running_loop().time() * 1000),
                        }
                    )

                yield f"data: {json.dumps(event)}\n\n"

            except queue.Empty:
                if not producer.is_alive():
                    break
                # Send SSE comment as keepalive every ~5s (25 cycles × 0.2s)
                # to prevent proxies/browsers from closing idle connections
                idle_cycles += 1
                if idle_cycles % 25 == 0:
                    yield ": keepalive\n\n"
                continue

        # Finalize all captured steps (mark as inactive)
        for s in captured_steps:
            s["active"] = False

        # Check for errors from the agent thread
        if result_holder["error"]:
            error_msg = f"Agent error: {result_holder['error']}"
            if not full_response:
                full_response = error_msg
            else:
                # Partial response exists — append error notice so user knows
                # the response may be incomplete
                full_response += f"\n\n[Error: {result_holder['error']}]"
            error_data = json.dumps({"type": "error", "content": error_msg})
            yield f"data: {error_data}\n\n"

        # Use agent result if no streamed answer was captured
        if not full_response and result_holder["answer"]:
            full_response = result_holder["answer"]
            # Send as answer event since it wasn't streamed
            yield f"data: {json.dumps({'type': 'answer', 'content': full_response})}\n\n"

        # Clean double-escaped newlines before DB storage
        if full_response:
            full_response = _fix_double_escaped(full_response)

        # Save complete response to DB (including captured agent steps)
        if full_response:
            msg_id = db.add_message(
                request.session_id,
                "assistant",
                full_response,
                agent_steps=captured_steps if captured_steps else None,
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

    parser = argparse.ArgumentParser(description="GAIA Agent UI Server")
    parser.add_argument("--host", default="localhost", help="Host (default: localhost)")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    log_level = "debug" if args.debug else "info"
    print(f"Starting GAIA Agent UI server on http://{args.host}:{args.port}")
    server_app = create_app()
    uvicorn.run(
        server_app,
        host=args.host,
        port=args.port,
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
