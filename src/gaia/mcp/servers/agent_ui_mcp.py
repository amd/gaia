# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""MCP server that wraps the GAIA Agent UI REST API.

Allows MCP clients (like Claude Code) to interact with the GAIA Chat Agent
through the same backend that powers the webapp, so conversations and tool
activity are visible in the browser UI in real time.

Usage:
    uv run python -m gaia.mcp.servers.agent_ui_mcp
    uv run python -m gaia.mcp.servers.agent_ui_mcp --port 8765
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import webbrowser
from typing import TYPE_CHECKING, Any, Dict, Optional

import requests

from gaia.ui.sse_handler import (
    _RAG_RESULT_JSON_SUB_RE,
    _THINK_TAG_SUB_RE,
    _THOUGHT_JSON_SUB_RE,
    _TOOL_CALL_JSON_SUB_RE,
    _TRAILING_CODE_FENCE_RE,
)

if TYPE_CHECKING:  # import only for type checking; runtime import is lazy (#1750)
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Default GAIA Agent UI backend URL
DEFAULT_BACKEND = "http://localhost:4200"
MCP_DEFAULT_PORT = 8765
MCP_DEFAULT_HOST = "localhost"


def _normalize_error(
    e: Exception, base_url: str, status_code: Optional[int] = None
) -> Dict[str, Any]:
    """Normalize an exception into a structured error dict without leaking URLs.

    Returns {"status": "error", "detail": "<clean message>"}.
    """
    if isinstance(e, requests.exceptions.ConnectionError):
        return {
            "status": "error",
            "detail": "Cannot connect to GAIA backend. Is it running?",
        }

    if isinstance(e, requests.exceptions.HTTPError):
        resp = e.response
        code = resp.status_code if resp is not None else (status_code or 0)
        detail = ""
        if resp is not None:
            try:
                body = resp.json()
                detail = body.get("detail", "") if isinstance(body, dict) else ""
            except (ValueError, KeyError):
                detail = resp.text[:200] if resp.text else ""
        if not detail:
            detail = f"HTTP {code}"
        detail = detail.replace(base_url, "")
        return {"status": "error", "detail": detail}

    msg = str(e)[:200].replace(base_url, "")
    return {"status": "error", "detail": msg}


def _is_error(result: Any) -> bool:
    """True if a tool result is a structured error envelope.

    All boundary errors flow through ``_normalize_error`` which returns
    ``{"status": "error", ...}``. Callers must key on this shape, not the
    legacy ``{"error": ...}`` one, or a backend failure silently passes as
    success.
    """
    return isinstance(result, dict) and result.get("status") == "error"


def _api(base_url: str, method: str, path: str, **kwargs) -> Dict[str, Any]:
    """Make an API request to the GAIA Agent UI backend."""
    url = f"{base_url}/api{path}"
    try:
        r = getattr(requests, method)(url, timeout=120, **kwargs)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError as e:
        return _normalize_error(e, base_url)
    except requests.exceptions.HTTPError as e:
        return _normalize_error(e, base_url)
    except Exception as e:
        return _normalize_error(e, base_url)


def _stream_chat(base_url: str, session_id: str, message: str) -> Dict[str, Any]:
    """Send a message via SSE stream and collect the full response."""
    url = f"{base_url}/api/chat/send"
    # Pin the model to GAIA's canonical chat LLM so the UI backend can't
    # fall back to its installer-time ``default_model_name`` (currently
    # ``Qwen3.5-4B-GGUF``) and force Lemonade to swap models on every
    # call. Without this, an eval scenario that defaults the session
    # ends up thrashing Lemonade between Qwen↔Gemma for each turn, which
    # routinely blew the per-scenario 930s budget (rag_quality
    # ``negation_handling`` was the canary). Pin is harmless for users
    # who haven't customised their default — the agent layer was already
    # going to dispatch to Gemma anyway.
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    payload = {
        "session_id": session_id,
        "message": message,
        "stream": True,
        "model": DEFAULT_MODEL_NAME,
    }

    try:
        r = requests.post(url, json=payload, stream=True, timeout=180)
        r.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        return _normalize_error(e, base_url)
    except requests.exceptions.HTTPError as e:
        return _normalize_error(e, base_url)
    except Exception as e:
        return _normalize_error(e, base_url)

    full_content = ""
    agent_steps = []
    event_log = []
    current_tool = None
    inference_stats = None

    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            break

        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        etype = event.get("type", "")

        if etype == "chunk":
            full_content += event.get("content", "")

        elif etype == "thinking":
            event_log.append(f"[thinking] {event.get('content', '')[:150]}")

        elif etype == "tool_start":
            tool = event.get("tool", "?")
            current_tool = tool
            event_log.append(f"[tool] {tool}")

        elif etype == "tool_args":
            detail = event.get("detail", "")[:200]
            if detail:
                event_log.append(f"  args: {detail}")

        elif etype == "tool_result":
            summary = event.get("summary", "")[:300]
            success = event.get("success", True)
            cmd = event.get("command_output")
            step_info = {
                "tool": current_tool,
                "success": success,
                "summary": summary,
            }
            if cmd:
                step_info["command"] = cmd.get("command", "")
                if cmd.get("stdout"):
                    step_info["stdout"] = cmd["stdout"][:500]
                if cmd.get("stderr"):
                    step_info["stderr"] = cmd["stderr"][:300]
                if cmd.get("return_code", 0) != 0:
                    step_info["exit_code"] = cmd["return_code"]
            agent_steps.append(step_info)
            icon = "OK" if success else "ERR"
            event_log.append(f"  result [{icon}]: {summary[:150]}")

        elif etype == "plan":
            steps = event.get("steps", [])
            event_log.append(f"[plan] {len(steps)} steps: {', '.join(steps[:5])}")

        elif etype == "answer":
            # Use the answer event content to override accumulated dirty chunks.
            # The streaming filter (Case 1b in print_streaming_text) extracts a
            # clean answer from {"answer": "..."} JSON; print_final_answer also
            # fires at the end.  Both should carry clean extracted text, so the
            # last non-empty answer wins over whatever chunk accumulation happened.
            answer_content = event.get("content", "")
            if answer_content:
                full_content = answer_content

        elif etype == "agent_error":
            event_log.append(f"[error] {event.get('content', '')}")

        elif etype == "done":
            stats = event.get("stats")
            if stats:
                inference_stats = stats
                event_log.append(
                    f"[perf] {stats.get('tokens_per_second') or 0} tok/s | "
                    f"{(stats.get('time_to_first_token') or 0)*1000:.0f}ms TTFT | "
                    f"{stats.get('input_tokens') or 0} → "
                    f"{stats.get('output_tokens') or 0} tokens"
                )
            # done content takes final priority over answer/chunk accumulation
            done_content = event.get("content", "")
            if done_content:
                full_content = done_content

        elif etype == "status":
            msg = event.get("message", "")
            if msg:
                event_log.append(f"[status] {msg}")

    # Clean LLM noise from content using shared patterns from sse_handler.
    # The SSE handler already filters these during streaming, but the MCP
    # server reads the raw SSE stream so it needs to clean up as well.
    full_content = _TOOL_CALL_JSON_SUB_RE.sub("", full_content)
    full_content = _THOUGHT_JSON_SUB_RE.sub("", full_content)
    full_content = _RAG_RESULT_JSON_SUB_RE.sub("", full_content)
    full_content = _TRAILING_CODE_FENCE_RE.sub("", full_content)
    full_content = _THINK_TAG_SUB_RE.sub("", full_content)
    import re as _re

    full_content = _re.sub(r"^[}\s`]+", "", full_content)
    full_content = full_content.strip()

    result = {
        "content": full_content,
        "agent_steps": agent_steps,
        "event_log": event_log,
    }
    if inference_stats:
        result["stats"] = inference_stats
    return result


def create_agent_ui_mcp(backend_url: str = DEFAULT_BACKEND) -> "FastMCP":
    """Create the MCP server with tools for interacting with GAIA Agent UI."""
    # Imported lazily so the pure helpers above (_normalize_error, _api,
    # _stream_chat) stay importable without the optional ``mcp`` dependency,
    # which the unit-test job does not install (issue #1750).
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(name="GAIA Agent UI")

    # ── System ─────────────────────────────────────────────────────

    @mcp.tool()
    def system_status() -> Dict[str, Any]:
        """Check the GAIA system status (LLM server, model, memory, etc.)."""
        return _api(backend_url, "get", "/system/status")

    # ── Sessions ───────────────────────────────────────────────────

    @mcp.tool()
    def list_sessions() -> Dict[str, Any]:
        """List all chat sessions. Returns session IDs, titles, and message counts."""
        return _api(backend_url, "get", "/sessions")

    @mcp.tool()
    def create_session(
        title: str = "New Chat", agent_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new chat session. Returns the session object with its ID.

        Args:
            title: Display title for the session.
            agent_type: Optional agent registration ID (e.g. "chat", "gaia-lite").
                When omitted, the backend's default agent is used. Pass the
                desired agent ID here to route this session through a specific
                registered agent (matches the IDs returned by GET /api/agents).
        """
        payload: Dict[str, Any] = {"title": title}
        if agent_type:
            payload["agent_type"] = agent_type
        return _api(backend_url, "post", "/sessions", json=payload)

    @mcp.tool()
    def get_session(session_id: str) -> Dict[str, Any]:
        """Get details of a specific chat session."""
        return _api(backend_url, "get", f"/sessions/{session_id}")

    @mcp.tool()
    def delete_session(session_id: str) -> Dict[str, Any]:
        """Delete a chat session and all its messages."""
        try:
            r = requests.delete(f"{backend_url}/api/sessions/{session_id}", timeout=30)
            r.raise_for_status()
            return {"deleted": True, "session_id": session_id}
        except Exception as e:
            return _normalize_error(e, backend_url)

    # ── Messages ───────────────────────────────────────────────────

    @mcp.tool()
    def get_messages(session_id: str) -> Dict[str, Any]:
        """Get all messages in a session (with agent steps and tool outputs)."""
        data = _api(backend_url, "get", f"/sessions/{session_id}/messages")
        if _is_error(data):
            return data
        # Simplify for readability
        messages = []
        for m in data.get("messages", []):
            msg = {
                "role": m["role"],
                "content": m["content"][:2000],
            }
            steps = m.get("agent_steps") or []
            if steps:
                msg["agent_steps"] = [
                    {
                        "type": s.get("type"),
                        "tool": s.get("tool"),
                        "label": s.get("label"),
                        "result": (s.get("result") or "")[:300],
                        "success": s.get("success"),
                    }
                    for s in steps
                ]
            stats = m.get("stats")
            if stats:
                msg["stats"] = stats
            messages.append(msg)
        return {"messages": messages, "total": data.get("total", len(messages))}

    @mcp.tool()
    def send_message(session_id: str, message: str) -> Dict[str, Any]:
        """Send a message to the GAIA agent in a session and wait for the response.
        The conversation is stored and will be visible when the session is viewed.
        Returns the agent's response, tool outputs, and an event log.

        Use list_sessions() first to get a session ID, or create_session() to make one.
        To make the session visible in the browser, call open_session_in_browser() first.
        """
        return _stream_chat(backend_url, session_id, message)

    # ── Documents ──────────────────────────────────────────────────

    @mcp.tool()
    def list_documents() -> Dict[str, Any]:
        """List all indexed documents in the document library."""
        return _api(backend_url, "get", "/documents")

    @mcp.tool()
    def index_document(filepath: str, session_id: str = "") -> Dict[str, Any]:
        """Index a document file for RAG (supports PDF, TXT, CSV, XLSX, etc.).

        If session_id is provided, the document is also linked to that session so
        the agent automatically loads it as a session document on every turn.
        Without session_id the document is indexed globally (library mode) but the
        agent won't treat it as session-specific.
        """
        result = _api(
            backend_url, "post", "/documents/upload-path", json={"filepath": filepath}
        )
        # If a session was specified, link the newly-indexed document to it so
        # the agent sees it as a session document (not just a library document).
        # Use POST /sessions/{id}/documents (attach_document endpoint) which
        # correctly writes to the session_documents join table.
        if session_id and isinstance(result, dict):
            doc_id = result.get("id") or result.get("result", {}).get("id")
            if doc_id:
                attach_result = _api(
                    backend_url,
                    "post",
                    f"/sessions/{session_id}/documents",
                    json={"document_id": doc_id},
                )
                if _is_error(attach_result):
                    logger.warning(
                        "Failed to link doc %s to session %s: %s",
                        doc_id,
                        session_id,
                        attach_result.get("detail"),
                    )
                    result["link_error"] = attach_result.get("detail")
                else:
                    result["linked_to_session"] = session_id
        return result

    @mcp.tool()
    def index_folder(folder_path: str, recursive: bool = True) -> Dict[str, Any]:
        """Index all supported documents in a folder for RAG."""
        return _api(
            backend_url,
            "post",
            "/documents/index-folder",
            json={"folder_path": folder_path, "recursive": recursive},
        )

    # ── File Browsing ──────────────────────────────────────────────

    @mcp.tool()
    def browse_files(path: str = "") -> Dict[str, Any]:
        """Browse files and folders at the given path. Returns entries with
        name, path, type (file/folder), size, and quick links."""
        params = {"path": path} if path else {}
        return _api(backend_url, "get", "/files/browse", params=params)

    @mcp.tool()
    def search_files(
        query: str, file_types: str = "", max_results: int = 20
    ) -> Dict[str, Any]:
        """Search for files across the filesystem by name pattern.
        file_types: comma-separated extensions (e.g. 'pdf,csv,xlsx').
        """
        payload: Dict[str, Any] = {"query": query, "max_results": max_results}
        if file_types:
            payload["file_types"] = file_types
        return _api(backend_url, "get", "/files/search", params=payload)

    @mcp.tool()
    def preview_file(filepath: str) -> Dict[str, Any]:
        """Preview the contents of a file (first lines for text, metadata for binary)."""
        return _api(backend_url, "get", "/files/preview", params={"path": filepath})

    # ── Screenshot ──────────────────────────────────────────────

    def _find_browser_window(title_substring: str = "GAIA Agent UI"):
        """Find a browser window containing the given title text."""
        try:
            import win32gui
        except ImportError:
            return None

        result = []

        def enum_cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title_substring.lower() in title.lower():
                    result.append(hwnd)

        win32gui.EnumWindows(enum_cb, None)
        return result[0] if result else None

    @mcp.tool()
    def take_screenshot(
        output_path: str = "",
        max_width: int = 1280,
        quality: int = 55,
        full_screen: bool = False,
    ) -> Dict[str, Any]:
        """Take a screenshot of the GAIA Agent UI browser window.
        Automatically finds the browser window by title, captures it,
        resizes for efficiency, and compresses as JPEG to minimize tokens.

        After calling this, use the Read tool on the returned path to view it.

        Args:
            output_path: Where to save the image. Defaults to a temp file.
            max_width: Max pixel width to resize to (default 1280). Smaller = fewer tokens.
            quality: JPEG quality 1-95 (default 55). Lower = smaller file.
            full_screen: If True, capture the entire screen instead of just the browser.

        Returns:
            path: Absolute path to the saved screenshot.
            size: Image dimensions as [width, height].
            file_size_kb: File size in KB.
        """
        try:
            from PIL import Image, ImageGrab
        except ImportError:
            return {
                "status": "error",
                "detail": "Pillow not installed. Run: pip install Pillow",
            }

        try:
            bbox = None

            if not full_screen:
                hwnd = _find_browser_window("GAIA Agent UI")
                if not hwnd:
                    # Fallback: try common browser titles
                    for title in [
                        "GAIA",
                        "localhost:4200",
                        "Chrome",
                        "Edge",
                        "Firefox",
                    ]:
                        hwnd = _find_browser_window(title)
                        if hwnd:
                            break

                if hwnd:
                    import win32gui

                    # Bring window to front so it's not occluded
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                    except Exception:
                        pass  # May fail if window is minimized

                    rect = win32gui.GetWindowRect(hwnd)
                    # rect = (left, top, right, bottom)
                    bbox = rect
                    logger.info(f"Found browser window at {rect}")
                else:
                    logger.warning("Browser window not found, capturing full screen")

            img = ImageGrab.grab(bbox=bbox, all_screens=False)

            # Resize to max_width maintaining aspect ratio
            w, h = img.size
            if w > max_width:
                ratio = max_width / w
                new_size = (max_width, int(h * ratio))
                img = img.resize(new_size, Image.LANCZOS)

            # Save as compressed JPEG
            if not output_path:
                tmp_dir = os.path.join(tempfile.gettempdir(), "gaia_screenshots")
                os.makedirs(tmp_dir, exist_ok=True)
                output_path = os.path.join(tmp_dir, "screenshot.jpg")

            img.save(output_path, format="JPEG", quality=quality, optimize=True)
            final_w, final_h = img.size
            file_size_kb = round(os.path.getsize(output_path) / 1024, 1)

            return {
                "path": os.path.abspath(output_path),
                "size": [final_w, final_h],
                "file_size_kb": file_size_kb,
            }
        except Exception as e:
            return {"status": "error", "detail": f"Screenshot failed: {e}"}

    # ── Memory ────────────────────────────────────────────────────────
    #
    # Read tools register when EITHER:
    #   * env GAIA_MEMORY_MCP_ALWAYS=1   (eval runner sets this), OR
    #   * UI setting mcp_memory_enabled=True  (Memory Dashboard toggle)
    #
    # Admin tools (memory_clear, memory_seed) ALSO require env
    # GAIA_MEMORY_ADMIN=1, mirroring the REST router gate.  Without that env
    # var on the backend process, the admin REST endpoints return 403 even
    # if the MCP tool is registered.  We register the tool anyway so the
    # error surfaces to the eval simulator with an actionable message.

    _mcp_always = os.environ.get("GAIA_MEMORY_MCP_ALWAYS") == "1"
    _admin_enabled = os.environ.get("GAIA_MEMORY_ADMIN") == "1"
    _ui_settings_enabled = False
    if not _mcp_always:
        _mem_settings = _api(backend_url, "get", "/memory/settings")
        _ui_settings_enabled = isinstance(_mem_settings, dict) and _mem_settings.get(
            "mcp_memory_enabled"
        )
    _memory_read_enabled = _mcp_always or _ui_settings_enabled

    if _memory_read_enabled:

        @mcp.tool()
        def memory_stats() -> Dict[str, Any]:
            """Return aggregate statistics for the agent's persistent memory store.

            Shows knowledge counts (total, by category), retrieval counts,
            embedding coverage, and conversation session counts.  Use this as
            a quick sanity check during eval — total_items=0 after a clear,
            >0 after a seed/remember, etc.
            """
            return _api(backend_url, "get", "/memory/stats")

        @mcp.tool()
        def memory_list(
            category: str = "",
            context: str = "",
            entity: str = "",
            search: str = "",
            include_sensitive: bool = False,
            include_superseded: bool = False,
            limit: int = 20,
        ) -> Dict[str, Any]:
            """Browse stored memories. All parameters are optional filters.

            Args:
                category: Filter by category (fact, preference, skill, error,
                    note, reminder).
                context: Filter by context scope (global, work, personal, or
                    custom).
                entity: Filter by linked entity (e.g. ``person:alice``,
                    ``app:chrome``).  Use to verify entity-tagged storage.
                search: Full-text search query.
                include_sensitive: Include items with ``sensitive=true``.
                    Default False to mirror the dashboard.
                include_superseded: Include items replaced by newer knowledge.
                    Default False; set True to inspect supersession chains
                    (used by adversarial-poisoning eval scenarios).
                limit: Max results to return (default 20, max 200).
            """
            params: Dict[str, Any] = {
                "limit": limit,
                "order": "desc",
                "sort_by": "updated_at",
                "include_sensitive": str(bool(include_sensitive)).lower(),
                "include_superseded": str(bool(include_superseded)).lower(),
            }
            if category:
                params["category"] = category
            if context:
                params["context"] = context
            if entity:
                params["entity"] = entity
            if search:
                params["search"] = search
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return _api(backend_url, "get", f"/memory/knowledge?{qs}")

        @mcp.tool()
        def memory_recall(query: str, limit: int = 10) -> Dict[str, Any]:
            """Search the agent's memory by keyword or concept.

            Hybrid keyword + semantic search via the dashboard's
            ``/memory/knowledge?search=...`` endpoint.  Returns the same
            shape as ``memory_list``.

            Args:
                query: Search query (supports natural language).
                limit: Max results (default 10).
            """
            import urllib.parse

            qs = f"search={urllib.parse.quote(query)}&limit={limit}&order=desc"
            return _api(backend_url, "get", f"/memory/knowledge?{qs}")

        @mcp.tool()
        def memory_get(knowledge_id: str) -> Dict[str, Any]:
            """Fetch one knowledge entry by ID, including superseded rows.

            Critical for adversarial / contradiction scenarios where the
            judge needs to inspect ``superseded_by``, ``sensitive``, or
            ``confidence`` on a specific row.  Unlike ``memory_list``, this
            does NOT hide superseded items.

            Args:
                knowledge_id: UUID returned by an earlier remember / seed call.

            Returns the full knowledge dict, or ``{"error": "..."}`` on 404.
            """
            return _api(backend_url, "get", f"/memory/knowledge/{knowledge_id}")

        @mcp.tool()
        def memory_get_by_entity(entity: str, limit: int = 20) -> Dict[str, Any]:
            """List all active knowledge linked to one entity.

            Wrapper around ``GET /api/memory/entities/{entity}``.  Use this
            to verify that the agent's ``remember()`` call wrote the right
            ``entity=person:name`` / ``entity=app:name`` tag.  Returns a list
            of knowledge dicts (excludes superseded rows).

            Args:
                entity: The exact entity tag, e.g. ``person:alice``.
                limit: Max rows to return (default 20).
            """
            return _api(
                backend_url,
                "get",
                f"/memory/entities/{entity}?limit={limit}",
            )

        @mcp.tool()
        def memory_get_conversation_turns(
            session_id: str, limit: int = 50
        ) -> Dict[str, Any]:
            """List conversation turns persisted for a session.

            Wraps ``GET /api/memory/conversations/{session_id}``.  Used to
            verify that the agent's ``_after_process_query`` hook stored
            user/assistant turns correctly — important for journaling and
            session-continuity scenarios.

            Args:
                session_id: Memory session ID (different from the chat
                    session ID returned by ``create_session``).  Read this
                    from ``memory_stats()`` if you don't already have it.
                limit: Max turns to return (default 50).
            """
            return _api(
                backend_url,
                "get",
                f"/memory/conversations/{session_id}?limit={limit}",
            )

    if _admin_enabled:

        @mcp.tool()
        def memory_clear(scope: str = "all") -> Dict[str, Any]:
            """**EVAL ONLY.** Wipe memory for a clean scenario start.

            Calls ``POST /api/memory/admin/clear`` which is disabled in
            production unless the backend is started with
            ``GAIA_MEMORY_ADMIN=1``.

            Args:
                scope: ``"all"`` (default), ``"knowledge"``, or
                    ``"conversations"``.  ``"all"`` wipes everything;
                    the partials let cross-session-persistence tests keep
                    knowledge while clearing conversations or vice versa.

            Returns ``{"scope": ..., "cleared": {<table>: <rows>}}`` on
            success or ``{"error": "..."}`` if the backend rejects.
            """
            return _api(
                backend_url,
                "post",
                "/memory/admin/clear",
                json={"scope": scope},
            )

        @mcp.tool()
        def memory_seed(items: list) -> Dict[str, Any]:
            """**EVAL ONLY.** Bulk-insert known knowledge rows for stress tests.

            Bypasses dedup so two near-identical items become two rows —
            the whole point is to plant a known cluttered state.  Calls
            ``POST /api/memory/admin/seed`` which requires
            ``GAIA_MEMORY_ADMIN=1`` on the backend.

            Args:
                items: List of dicts.  Each must have ``content`` (non-empty
                    string) and may include ``category``, ``context``,
                    ``domain``, ``entity``, ``sensitive``, ``confidence``,
                    ``source``, ``due_at``.  Validation is whole-batch — a
                    malformed item rejects all of them.

            Returns ``{"count": N, "ids": [...]}`` on success or
            ``{"error": "..."}`` on validation failure.
            """
            return _api(
                backend_url,
                "post",
                "/memory/admin/seed",
                json={"items": items},
            )

    # ── Browser Navigation ────────────────────────────────────────

    # Track last opened URL to avoid duplicate tabs
    _last_opened_url = {"url": ""}

    @mcp.tool()
    def open_session_in_browser(session_id: str) -> Dict[str, Any]:
        """Open a chat session in the user's default browser.
        This navigates the browser to the GAIA Agent UI with the session selected
        and signals the frontend to switch to that session via the activate endpoint.
        Won't open a duplicate tab if the same session is already open.

        Args:
            session_id: The session ID to open.
        """
        # Try dev server first (5174/5173), fall back to backend URL.
        # Use hash-based navigation so the SPA router picks up the session.
        dev_ports = [5174, 5173]
        base = None
        for port in dev_ports:
            try:
                r = requests.get(f"http://localhost:{port}/", timeout=2)
                if r.status_code == 200:
                    base = f"http://localhost:{port}"
                    break
            except Exception:
                continue

        if not base:
            base = backend_url

        target_url = f"{base}/#{session_id}"

        # Signal the frontend to switch to this session via the activate endpoint.
        # This emits a set_active_session SSE event that App.tsx consumes.
        _api(backend_url, "post", f"/sessions/{session_id}/activate")

        # Skip opening a duplicate tab for the same URL.
        if _last_opened_url["url"] == target_url:
            return {
                "opened": False,
                "url": target_url,
                "note": "Already open in browser",
            }

        try:
            webbrowser.open(target_url)
            _last_opened_url["url"] = target_url
            return {"opened": True, "url": target_url}
        except Exception as e:
            return {
                "opened": False,
                "url": target_url,
                "note": f"Failed to open browser: {e}",
            }

    return mcp


def main():
    parser = argparse.ArgumentParser(description="GAIA Agent UI MCP Server")
    parser.add_argument(
        "--port",
        type=int,
        default=MCP_DEFAULT_PORT,
        help=f"MCP server port (default: {MCP_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        default=MCP_DEFAULT_HOST,
        help=f"MCP server host (default: {MCP_DEFAULT_HOST})",
    )
    parser.add_argument(
        "--backend",
        default=DEFAULT_BACKEND,
        help=f"GAIA Agent UI backend URL (default: {DEFAULT_BACKEND})",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Use stdio transport instead of HTTP (for Claude Code integration)",
    )
    args = parser.parse_args()

    mcp = create_agent_ui_mcp(backend_url=args.backend)

    if args.stdio:
        print("Starting GAIA Agent UI MCP Server (stdio mode)...", file=sys.stderr)
        mcp.run(transport="stdio")
    else:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        print("\n🚀 GAIA Agent UI MCP Server")
        print(f"   Backend: {args.backend}")
        print(f"   MCP: http://{args.host}:{args.port}/mcp")
        tool_count = len(mcp._tool_manager._tools)  # pylint: disable=protected-access
        print(f"   Tools: {tool_count} registered\n")
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
