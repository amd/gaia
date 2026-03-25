# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Chat and document-indexing helper functions for GAIA Agent UI.

These functions are extracted into their own module so that both
``server.py`` (for backward-compatible ``@patch`` targets) and the
router modules can import from the same canonical location.

Tests may patch ``gaia.ui.server._get_chat_response`` etc. because
``server.py`` re-exports these names.  The router endpoints access
them through ``gaia.ui.server`` as well (via lazy import) so the
patches take effect.
"""

import asyncio
import json
import logging
import os
import re as _re
import threading
import time as _time
from pathlib import Path

from .database import ChatDatabase
from .models import ChatRequest
from .sse_handler import (
    _ANSWER_JSON_SUB_RE,
    _RAG_RESULT_JSON_SUB_RE,
    _THOUGHT_JSON_SUB_RE,
    _TOOL_CALL_JSON_SUB_RE,
    _clean_answer_json,
    _fix_double_escaped,
)

logger = logging.getLogger(__name__)

# Active SSE handlers keyed by session_id.  The /api/chat/confirm-tool
# endpoint looks up the handler here to resolve a pending confirmation.
_active_sse_handlers: dict = {}  # session_id -> SSEOutputHandler

# ── Per-session ChatAgent cache ───────────────────────────────────────────────
# Constructing a fresh ChatAgent on every message is expensive: it initialises
# RAGSDK, MCPClientManager, runs LemonadeManager.ensure_ready() (HTTP calls),
# registers all tools, composes the system prompt, and re-indexes session docs
# even when nothing has changed.  Caching the agent per session_id lets us skip
# all of that on follow-up turns.
#
# Thread-safety: the global chat_semaphore(1) in server.py serialises all chat
# requests, and the per-session session_lock prevents concurrent turns within
# the same session.  Together they guarantee the cache dict and each agent are
# accessed by at most one thread at a time — no per-entry locking needed.
_agent_cache: dict = (
    {}
)  # session_id -> {"agent": ChatAgent, "model_id": str, "document_ids": list}
_agent_cache_lock = threading.Lock()
_MAX_CACHED_AGENTS = 10

# Last known MCP runtime status — updated after each agent setup so
# GET /api/mcp/status can return it without needing a running chat.
_mcp_status_cache: list = []
_mcp_status_lock = threading.Lock()


def get_cached_mcp_status() -> list:
    """Return the last known MCP server connection status from any cached agent."""
    with _mcp_status_lock:
        return list(_mcp_status_cache)


def _get_cached_agent(session_id: str, model_id: str):
    """Return the cached ChatAgent for *session_id* if the model matches, else None.

    Evicts the entry when the model has changed so a fresh agent is created.
    """
    with _agent_cache_lock:
        entry = _agent_cache.get(session_id)
        if entry is None:
            return None
        if entry["model_id"] != model_id:
            # Model changed — the cached agent used a different LLM; discard it.
            del _agent_cache[session_id]
            logger.debug(
                "Agent cache miss (model change) for session %s", session_id[:8]
            )
            return None
        return entry["agent"]


def _store_agent(session_id: str, model_id: str, document_ids: list, agent) -> None:
    """Cache *agent* for *session_id*.  Evicts the oldest entry if over the limit."""
    with _agent_cache_lock:
        if session_id not in _agent_cache and len(_agent_cache) >= _MAX_CACHED_AGENTS:
            oldest = next(iter(_agent_cache))
            del _agent_cache[oldest]
            logger.debug("Agent cache full; evicted session %s", oldest[:8])
        _agent_cache[session_id] = {
            "model_id": model_id,
            "document_ids": list(document_ids or []),
            "agent": agent,
        }
        logger.debug(
            "Cached agent for session %s (cache size: %d)",
            session_id[:8],
            len(_agent_cache),
        )


def _index_rag_with_progress(
    agent, fpath_list, sse_handler, *, rebuild_per_doc=False, label="document(s)"
):
    """Index *fpath_list* with SSE progress events.

    Emits tool_start, per-doc status, and tool_result events.
    When *rebuild_per_doc* is True, calls agent.rebuild_system_prompt() after
    each successfully indexed document (used for cache-hit incremental updates).
    """
    n = len(fpath_list)
    sse_handler._emit(
        {
            "type": "tool_start",
            "tool": "index_documents",
            "detail": f"Indexing {n} {label} for RAG",
        }
    )
    idx_start = _time.time()
    doc_stats = []
    total_chunks = 0
    for i, fpath in enumerate(fpath_list, 1):
        doc_name = Path(fpath).name
        sse_handler._emit(
            {
                "type": "status",
                "status": "info",
                "message": f"Indexing [{i}/{n}]: {doc_name}",
            }
        )
        try:
            result = agent.rag.index_document(fpath)
            n_chunks = result.get("num_chunks", 0)
            error = result.get("error")
            if error:
                logger.warning("RAG error for %s: %s", fpath, error)
                doc_stats.append(f"  {doc_name} — ERROR: {error}")
                sse_handler._emit(
                    {
                        "type": "status",
                        "status": "warning",
                        "message": f"Error indexing {doc_name}: {error}",
                    }
                )
            else:
                agent.indexed_files.add(fpath)
                total_chunks += n_chunks
                size_mb = result.get("file_size_mb", 0) or 0
                file_size_bytes = int(size_mb * 1024 * 1024)
                if size_mb >= 1:
                    size_str = f"{size_mb:.1f} MB"
                elif file_size_bytes >= 1024:
                    size_str = f"{file_size_bytes // 1024} KB"
                else:
                    size_str = f"{file_size_bytes} B"
                from_cache = result.get("from_cache", False)
                doc_stats.append(
                    f"  {doc_name} — {n_chunks} chunks, {size_str}"
                    + (" (cached)" if from_cache else "")
                )
                if rebuild_per_doc:
                    agent.rebuild_system_prompt()
        except Exception as idx_err:
            logger.warning("Failed to index %s: %s", fpath, idx_err)
            doc_stats.append(f"  {doc_name} — FAILED: {idx_err}")
            sse_handler._emit(
                {
                    "type": "status",
                    "status": "warning",
                    "message": f"Failed to index {doc_name}: {idx_err}",
                }
            )
    idx_elapsed = round(_time.time() - idx_start, 1)
    summary_lines = [
        f"Indexed {n} {label} in {idx_elapsed}s",
        f"Total: {total_chunks} chunks in index",
        "",
    ] + doc_stats
    sse_handler._emit(
        {
            "type": "tool_result",
            "title": "Index Documents",
            "summary": "\n".join(summary_lines),
            "success": True,
        }
    )


def evict_session_agent(session_id: str) -> None:
    """Remove a session's cached agent (call on session deletion or clear)."""
    with _agent_cache_lock:
        if _agent_cache.pop(session_id, None) is not None:
            logger.debug("Evicted cached agent for session %s", session_id[:8])


# ── Chat Helpers ─────────────────────────────────────────────────────────────


def _build_history_pairs(messages: list) -> list:
    """Build user/assistant conversation pairs from message history.

    Iterates messages sequentially and pairs adjacent user->assistant messages.
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


def _resolve_rag_paths(db: ChatDatabase, document_ids: list) -> tuple:
    """Resolve document IDs to file paths for RAG.

    If the session has specific documents attached (document_ids non-empty),
    resolves those IDs to file paths for auto-indexing.  Otherwise returns
    them as library documents (available but not auto-indexed) so the agent
    can index on demand based on the user's request.

    Returns:
        Tuple of (rag_file_paths, library_file_paths).
        - rag_file_paths: Docs to auto-index (session-specific attachments).
        - library_file_paths: Docs available for on-demand indexing (entire library).
    """
    if document_ids:
        # Session has specific documents attached -- auto-index these
        rag_file_paths = []
        for doc_id in document_ids:
            doc = db.get_document(doc_id)
            if doc and doc.get("filepath"):
                rag_file_paths.append(doc["filepath"])
            else:
                logger.warning("Document %s not found in database, skipping", doc_id)
        return rag_file_paths, []
    else:
        # No session-specific documents attached — return empty lists.
        # Previously this exposed ALL global library documents, causing
        # cross-session contamination: documents from unrelated sessions
        # would appear in the system prompt and list_indexed_documents,
        # confusing the agent about what's actually available in the
        # current session.  Users who want a document available must
        # explicitly index it and link it to their session via document_ids.
        return [], []


def _compute_allowed_paths(rag_file_paths: list) -> list:
    """Derive allowed filesystem paths from document locations.

    Collects the unique parent directories of all RAG document paths.
    Falls back to the current working directory when no document paths
    are provided, to avoid granting unnecessarily broad access across
    unrelated projects on the same machine.
    """
    dirs = set()
    for fp in rag_file_paths:
        dirs.add(str(Path(fp).parent))
    if not dirs:
        dirs.add(str(Path.cwd()))
    return list(dirs)


def _find_last_tool_step(steps: list) -> dict | None:
    """Find the last tool step in captured_steps, searching backwards."""
    for i in range(len(steps) - 1, -1, -1):
        if steps[i].get("type") == "tool":
            return steps[i]
    return None


# ── Non-streaming Chat ───────────────────────────────────────────────────────


async def _get_chat_response(
    db: ChatDatabase, session: dict, request: ChatRequest
) -> str:
    """Get a non-streaming chat response from the ChatAgent.

    Uses the full ChatAgent (with tools) instead of plain AgentSDK
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
        # Session-specific docs get auto-indexed; library docs are available
        # for on-demand indexing by the agent based on user's query.
        document_ids = session.get("document_ids", [])
        rag_file_paths, library_paths = _resolve_rag_paths(db, document_ids)

        all_doc_paths = rag_file_paths + library_paths
        if all_doc_paths:
            logger.info(
                "Chat: %d auto-index doc(s), %d library doc(s)",
                len(rag_file_paths),
                len(library_paths),
            )

        allowed = _compute_allowed_paths(all_doc_paths)

        # Use custom model override if set in user settings,
        # otherwise fall back to the session's model.
        model_id = session.get("model")
        custom_model = db.get_setting("custom_model")
        if custom_model:
            logger.info(
                "Using custom model override: %s (session default: %s)",
                custom_model,
                model_id,
            )
            model_id = custom_model

        # ── Agent cache ──────────────────────────────────────────────────────
        # Reuse an existing ChatAgent rather than constructing a fresh one.
        # On a cache hit we still re-register tools (so _TOOL_REGISTRY points
        # to this agent's self after another session may have overwritten it)
        # but skip the heavyweight __init__ work: RAGSDK construction,
        # MCPClientManager init, LemonadeManager.ensure_ready() HTTP calls,
        # system-prompt composition, and session-doc re-indexing.
        session_id = request.session_id
        cached_agent = _get_cached_agent(session_id, model_id)

        if cached_agent is not None:
            agent = cached_agent
            # Re-register tools so _TOOL_REGISTRY points at this agent's self.
            agent._register_tools()
            # Index any session docs that were attached since the agent was cached.
            if rag_file_paths and agent.rag:
                new_paths = [p for p in rag_file_paths if p not in agent.indexed_files]
                for fpath in new_paths:
                    try:
                        result_idx = agent.rag.index_document(fpath)
                        if result_idx.get("success"):
                            agent.indexed_files.add(fpath)
                            agent.rebuild_system_prompt()
                    except Exception as _idx_err:
                        logger.warning("Failed to index %s: %s", fpath, _idx_err)
            logger.debug(
                "Agent cache hit (non-streaming) for session %s", session_id[:8]
            )
        else:
            config = ChatAgentConfig(
                model_id=model_id,
                max_steps=10,
                silent_mode=True,
                debug=False,
                rag_documents=rag_file_paths,
                library_documents=library_paths,
                allowed_paths=allowed,
                ui_session_id=session_id,
            )
            agent = ChatAgent(config)
            _store_agent(session_id, model_id, document_ids, agent)

        # Restore conversation history (limited to prevent context overflow).
        # Always re-inject from DB so the history is consistent with what was
        # persisted — regardless of whether the agent was cached or fresh.
        # 5 pairs × 2 msgs × ~500 tokens ≈ 5 000 tokens — well within 32K.
        # 2000-char truncation preserves enough assistant context for cross-turn
        # recall, pronoun resolution, and multi-step planning.
        _MAX_PAIRS = 5
        _MAX_CHARS = 2000
        agent.conversation_history = []
        for user_msg, assistant_msg in history_pairs[-_MAX_PAIRS:]:
            u = user_msg[:_MAX_CHARS]
            a = assistant_msg[:_MAX_CHARS]
            if len(assistant_msg) > _MAX_CHARS:
                a += "... (truncated)"
            agent.conversation_history.append({"role": "user", "content": u})
            agent.conversation_history.append({"role": "assistant", "content": a})

        result = agent.process_query(request.message)
        if isinstance(result, dict):
            # process_query returns {"result": "...", "status": "...", ...}
            # Use explicit None check so an intentional empty string isn't
            # overridden by fallback to "answer".
            val = result.get("result")
            return val if val is not None else result.get("answer", "")
        return str(result) if result else ""

    try:
        loop = asyncio.get_running_loop()
        # Apply a 600-second timeout to prevent indefinite hangs when the
        # LLM gets stuck in a tool loop or Lemonade becomes unresponsive
        return await asyncio.wait_for(
            loop.run_in_executor(None, _do_chat),
            timeout=600.0,
        )
    except asyncio.TimeoutError:
        logger.error("Chat response timed out after 600 seconds")
        return "I took too long thinking about that one. Try breaking your question into simpler parts and I'll do my best."
    except Exception as e:
        logger.error("Chat error: %s", e, exc_info=True)
        return (
            "I'm having trouble connecting to the language model right now. "
            "Please make sure Lemonade Server is running and try again."
        )


# ── Streaming Chat ───────────────────────────────────────────────────────────


async def _stream_chat_response(db: ChatDatabase, session: dict, request: ChatRequest):
    """Stream chat response as Server-Sent Events.

    Uses ChatAgent with SSEOutputHandler to emit agent activity events
    (steps, tool calls, thinking) alongside text chunks, giving the
    frontend visibility into what the agent is doing.
    """
    import queue

    from gaia.ui.sse_handler import SSEOutputHandler

    session_id = request.session_id
    try:
        # Create SSE handler for streaming events
        sse_handler = SSEOutputHandler()
        # Register so /api/chat/confirm-tool can find this handler.
        _active_sse_handlers[session_id] = sse_handler

        # ── Immediate browser feedback ────────────────────────────────────
        # Yield "Connecting to LLM..." directly (not via the queue) so the
        # browser sees it *before* the producer thread starts — giving instant
        # visual feedback even if agent construction or LemonadeManager take
        # several seconds on first turn.
        #
        # The padding comment that follows forces Chromium / Electron to flush
        # its internal receive buffer.  With small SSE events (< ~512 bytes),
        # Chromium's fetch ReadableStream holds chunks until the buffer fills or
        # the stream closes.  Without this, the browser sees nothing for the
        # entire duration and then gets a batch-dump of all events at the end.
        yield (
            'data: {"type":"status","status":"info","message":"Connecting to LLM..."}\n\n'
            ": " + "x" * 512 + "\n\n"
        )

        # Build conversation history
        messages = db.get_messages(request.session_id, limit=20)
        history_pairs = _build_history_pairs(messages)

        # Resolve document IDs to file paths.
        # Session-specific docs get auto-indexed; library docs are available
        # for on-demand indexing by the agent based on user's query.
        document_ids = session.get("document_ids", [])
        rag_file_paths, library_paths = _resolve_rag_paths(db, document_ids)

        all_doc_paths = rag_file_paths + library_paths
        if all_doc_paths:
            logger.info(
                "Streaming chat: %d auto-index doc(s), %d library doc(s)",
                len(rag_file_paths),
                len(library_paths),
            )

        allowed = _compute_allowed_paths(all_doc_paths)
        model_id = session.get("model")

        # Use custom model override if set in user settings
        custom_model = db.get_setting("custom_model")
        if custom_model:
            logger.info(
                "Streaming: using custom model override: %s (session default: %s)",
                custom_model,
                model_id,
            )
            model_id = custom_model

        # Move ALL slow work (ChatAgent constructor + process_query) into the
        # background thread so the SSE generator can yield the thinking event
        # immediately instead of blocking for 10-30s during initialization
        result_holder = {"answer": "", "error": None}

        def _run_agent():
            try:
                from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

                t0 = _time.monotonic()

                # ── Agent cache check ─────────────────────────────────────────
                # Reuse an existing ChatAgent if one exists for this session.
                # On a cache hit we still call _register_tools() so _TOOL_REGISTRY
                # points at this agent's self (another session may have overwritten
                # it between turns).  We skip the heavyweight parts: ChatAgent
                # __init__, RAGSDK construction, MCPClientManager init,
                # LemonadeManager.ensure_ready() HTTP calls, system-prompt
                # composition, and session-doc re-indexing for unchanged files.
                cached_agent = _get_cached_agent(session_id, model_id)

                if cached_agent is not None:
                    # -- Cache hit --
                    agent = cached_agent
                    agent.console = sse_handler

                    # Re-register tools: another session may have overwritten
                    # _TOOL_REGISTRY with its own self-bound closures.
                    agent._register_tools()

                    # Early-exit if consumer disconnected
                    if sse_handler.cancelled.is_set():
                        return

                    # Index any session docs newly attached since last turn.
                    new_rag_paths = [
                        p for p in rag_file_paths if p not in agent.indexed_files
                    ]
                    if new_rag_paths and agent.rag:
                        _index_rag_with_progress(
                            agent,
                            new_rag_paths,
                            sse_handler,
                            rebuild_per_doc=True,
                            label="new document(s)",
                        )

                    logger.info(
                        "PERF agent cache hit (streaming) session=%s setup=%.3fs",
                        session_id[:8],
                        _time.monotonic() - t0,
                    )
                    sse_handler._emit(
                        {
                            "type": "status",
                            "status": "info",
                            "message": "Sending to model...",
                        }
                    )

                else:
                    # -- Cache miss: full construction --
                    # Build config: session-specific docs auto-index,
                    # library docs passed as metadata for on-demand indexing.
                    config = ChatAgentConfig(
                        model_id=model_id,
                        max_steps=10,
                        streaming=True,
                        silent_mode=False,
                        debug=False,
                        rag_documents=[],  # Index manually below (session docs only)
                        library_documents=library_paths,  # Available for on-demand indexing
                        allowed_paths=allowed,
                        ui_session_id=session_id,
                    )

                    t_construct = _time.monotonic()
                    agent = ChatAgent(config)
                    logger.info(
                        "PERF ChatAgent constructed session=%s took=%.3fs",
                        session_id[:8],
                        _time.monotonic() - t_construct,
                    )
                    agent.console = sse_handler  # Assign early so tool events flow

                    # Early-exit if consumer disconnected
                    if sse_handler.cancelled.is_set():
                        return

                    # -- Phase 3: RAG indexing --
                    # Session-attached docs are indexed with full SSE progress events.
                    # Library docs are silently pre-indexed from disk cache so the
                    # system prompt shows them as "already indexed" — preventing the
                    # LLM from calling index_document again on unchanged files.
                    # The hash-based cache (RAGSDK) guarantees no re-processing
                    # unless file content has actually changed.
                    if rag_file_paths and agent.rag:
                        t_rag = _time.monotonic()
                        _index_rag_with_progress(agent, rag_file_paths, sse_handler)
                        logger.info(
                            "PERF RAG indexing session=%s took=%.3fs",
                            session_id[:8],
                            _time.monotonic() - t_rag,
                        )

                    # -- Phase 3b: Silently pre-index library docs from cache --
                    # Library docs that are already on disk are loaded from the
                    # hash-based RAG cache (no LLM/embedding re-computation for
                    # unchanged files).  Adding them to agent.indexed_files causes
                    # rebuild_system_prompt() to emit the ANTI-RE-INDEX RULE, so
                    # the LLM will query them directly instead of re-indexing.
                    if library_paths and agent.rag:
                        preindexed = 0
                        for fpath in library_paths:
                            try:
                                result = agent.rag.index_document(fpath)
                                if result.get("success") and not result.get("error"):
                                    agent.indexed_files.add(fpath)
                                    preindexed += 1
                            except Exception as lib_err:
                                logger.debug(
                                    "Library pre-index skipped for %s: %s",
                                    fpath,
                                    lib_err,
                                )
                        if preindexed:
                            agent.rebuild_system_prompt()
                            logger.info(
                                "Pre-indexed %d library doc(s) from cache", preindexed
                            )

                    # Cache the agent for subsequent turns in this session.
                    _store_agent(session_id, model_id, document_ids, agent)
                    logger.info(
                        "PERF total setup (cache miss) session=%s took=%.3fs",
                        session_id[:8],
                        _time.monotonic() - t0,
                    )
                    sse_handler._emit(
                        {
                            "type": "status",
                            "status": "info",
                            "message": "Sending to model...",
                        }
                    )

                # -- Emit MCP runtime status (once per request, after agent setup) --
                if hasattr(agent, "get_mcp_status_report"):
                    mcp_report = agent.get_mcp_status_report()
                    with _mcp_status_lock:
                        _mcp_status_cache[:] = mcp_report
                    if mcp_report:
                        sse_handler._emit({"type": "mcp_status", "servers": mcp_report})

                # Early-exit if consumer disconnected
                if sse_handler.cancelled.is_set():
                    return

                # -- Phase 4: Conversation history --
                # Always re-inject from DB so history is consistent regardless of
                # whether the agent was cached or freshly constructed.  Clears any
                # stale history accumulated in prior turns of a cached agent.
                # 5 pairs × 2 msgs × ~500 tokens ≈ 5 000 tokens — well within 32K.
                _MAX_HISTORY_PAIRS = 5
                _MAX_MSG_CHARS = 2000
                agent.conversation_history = []
                if history_pairs:
                    recent = history_pairs[-_MAX_HISTORY_PAIRS:]
                    for user_msg, assistant_msg in recent:
                        # Truncate to keep context manageable
                        u = user_msg[:_MAX_MSG_CHARS]
                        a = assistant_msg[:_MAX_MSG_CHARS]
                        if len(assistant_msg) > _MAX_MSG_CHARS:
                            a += "... (truncated)"
                        agent.conversation_history.append(
                            {"role": "user", "content": u}
                        )
                        agent.conversation_history.append(
                            {"role": "assistant", "content": a}
                        )

                # Early-exit if consumer disconnected
                if sse_handler.cancelled.is_set():
                    return

                # -- Phase 5: Query processing --
                t_query = _time.monotonic()
                result = agent.process_query(request.message)
                logger.info(
                    "PERF process_query session=%s took=%.3fs",
                    session_id[:8],
                    _time.monotonic() - t_query,
                )
                if isinstance(result, dict):
                    val = result.get("result")
                    result_holder["answer"] = (
                        val if val is not None else result.get("answer", "")
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
        _stream_start = _time.time()
        _STREAM_TIMEOUT = 600  # 10 minutes — large system prompts need time
        while True:
            # Guard: total timeout for the streaming response
            if _time.time() - _stream_start > _STREAM_TIMEOUT:
                logger.error("Streaming response timed out after %ds", _STREAM_TIMEOUT)
                timeout_event = json.dumps(
                    {
                        "type": "agent_error",
                        "content": f"Response timed out after {_STREAM_TIMEOUT}s. "
                        "Try a simpler query or break it into smaller questions.",
                    }
                )
                yield f"data: {timeout_event}\n\n"
                break
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
                    # Always use the answer event to override accumulated chunks.
                    # print_final_answer emits a clean, artifact-free final answer,
                    # while chunks include all intermediate streaming text (planning
                    # sentences, tool call noise, etc.).  Using the answer event
                    # ensures DB storage matches what the MCP client receives.
                    answer_content = event.get("content", "")
                    if answer_content:
                        full_response = answer_content
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
                        # Persist file list for rich file list rendering
                        result_data = event.get("result_data", {})
                        if result_data.get("type") == "file_list":
                            tool_step["fileList"] = {
                                "files": result_data.get("files", []),
                                "total": result_data.get("total", 0),
                            }
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

                # Pad each event so Chromium's receive buffer flushes immediately.
                # Events < 512 bytes are held by Chromium until the buffer fills.
                event_data = f"data: {json.dumps(event)}\n\n"
                if len(event_data) < 512:
                    event_data += ": " + "x" * (512 - len(event_data) - 4) + "\n\n"
                yield event_data

            except queue.Empty:
                if not producer.is_alive():
                    break
                idle_cycles += 1
                # Send a padded keepalive every ~5s (25 cycles × 0.2s).
                # The padding flushes Chromium's receive buffer so any events
                # already sent but not yet dispatched arrive immediately.
                if idle_cycles % 25 == 0:
                    yield ": keepalive " + "x" * 490 + "\n\n"
                # Every 15s (75 cycles) emit a visible status so the user knows
                # the model is still processing (prompt prefill is silent).
                # Use status='working' so active=true; consecutive events merge
                # into a single updating step on the frontend.
                if idle_cycles % 75 == 0:
                    elapsed = int(_time.time() - _stream_start)
                    status_evt = json.dumps(
                        {
                            "type": "status",
                            "status": "working",
                            "message": f"Model is processing... ({elapsed}s)",
                        }
                    )
                    status_data = f"data: {status_evt}\n\n"
                    if len(status_data) < 512:
                        status_data += (
                            ": " + "x" * (512 - len(status_data) - 4) + "\n\n"
                        )
                    yield status_data
                continue

        # Signal cancellation (handles client disconnect) then wait for producer
        sse_handler.cancelled.set()
        _active_sse_handlers.pop(session_id, None)
        producer.join(timeout=5.0)
        if producer.is_alive():
            logger.warning("Producer thread still running after stream ended")

        # Finalize all captured steps (mark as inactive)
        for s in captured_steps:
            s["active"] = False

        # Check for errors from the agent thread
        if result_holder["error"]:
            error_msg = f"Agent error: {result_holder['error']}"
            if not full_response:
                full_response = error_msg
            else:
                # Partial response exists -- append error notice so user knows
                # the response may be incomplete
                full_response += f"\n\n[Error: {result_holder['error']}]"
            error_data = json.dumps({"type": "error", "content": error_msg})
            yield f"data: {error_data}\n\n"

        # Use agent result if no streamed answer was captured
        if not full_response and result_holder["answer"]:
            full_response = result_holder["answer"]
            # Send as answer event since it wasn't streamed
            yield f"data: {json.dumps({'type': 'answer', 'content': full_response})}\n\n"

        # Clean LLM output artifacts before DB storage.
        # Apply all canonical patterns so stored content is always clean
        # regardless of which streaming path was taken.
        # Order matters: strip embedded JSON blobs BEFORE _clean_answer_json so
        # that stray closing braces from tool/RAG JSON don't confuse the answer extractor.
        if full_response:
            full_response = _TOOL_CALL_JSON_SUB_RE.sub("", full_response)
            full_response = _THOUGHT_JSON_SUB_RE.sub("", full_response)
            full_response = _RAG_RESULT_JSON_SUB_RE.sub("", full_response)
            # _clean_answer_json handles pure {"answer": "..."} responses (whole string).
            full_response = _clean_answer_json(full_response)
            # _ANSWER_JSON_SUB_RE handles mixed content where {"answer": "..."} is
            # embedded after plain text — strips the duplicate JSON wrapper.
            full_response = _ANSWER_JSON_SUB_RE.sub("", full_response)
            full_response = _fix_double_escaped(full_response)
            # Strip trailing JSON artifact sequences (3+ closing braces = nested tool result leak)
            full_response = _re.sub(r"\}{3,}\s*$", "", full_response).strip()
            # Strip trailing code-fence artifacts (e.g. "}\n```" left after JSON extraction)
            full_response = _re.sub(r"[\n\s]*`{3,}\s*$", "", full_response).strip()
            full_response = full_response.strip()

        # Guard: if cleaning reduced the response to JSON/code artifacts only
        # (e.g. "}", "}}", "}\n", "}\n```", backtick-only), fall back to the agent's
        # direct result which is unaffected by streaming fragmentation.
        if full_response and _re.fullmatch(r'[\s{}\[\]",:` ]+', full_response):
            logger.warning(
                "Streaming response reduced to JSON artifacts %r — using agent result",
                full_response[:40],
            )
            full_response = result_holder.get("answer", "") or ""

        # Save complete response to DB (including captured agent steps)
        if full_response:
            # Fetch last inference stats from Lemonade (non-blocking)
            inference_stats = None
            try:
                import httpx

                base_url = os.environ.get(
                    "LEMONADE_BASE_URL", "http://localhost:8000/api/v1"
                )
                async with httpx.AsyncClient(timeout=3.0) as stats_client:
                    stats_resp = await stats_client.get(f"{base_url}/stats")
                    if stats_resp.status_code == 200:
                        stats_data = stats_resp.json()
                        inference_stats = {
                            "tokens_per_second": round(
                                stats_data.get("tokens_per_second", 0), 1
                            ),
                            "time_to_first_token": round(
                                stats_data.get("time_to_first_token", 0), 3
                            ),
                            "input_tokens": stats_data.get("input_tokens", 0),
                            "output_tokens": stats_data.get("output_tokens", 0),
                        }
            except Exception:
                pass

            msg_id = db.add_message(
                request.session_id,
                "assistant",
                full_response,
                agent_steps=captured_steps if captured_steps else None,
                inference_stats=inference_stats,
            )
            done_event: dict = {
                "type": "done",
                "message_id": msg_id,
                "content": full_response,
            }
            if inference_stats:
                done_event["stats"] = inference_stats
            done_data = json.dumps(done_event)
            yield f"data: {done_data}\n\n"
        else:
            # Log details to help diagnose: cold start, empty LLM response, filtered artifacts
            logger.warning(
                "Empty response for session %s — result_holder answer=%r error=%r captured_steps=%d",
                session_id[:8],
                (
                    result_holder.get("answer", "")[:80]
                    if result_holder.get("answer")
                    else None
                ),
                result_holder.get("error"),
                len(captured_steps),
            )
            error_msg = "I wasn't able to generate a response. Please make sure Lemonade Server is running and try again."
            db.add_message(request.session_id, "assistant", error_msg)
            error_data = json.dumps({"type": "error", "content": error_msg})
            yield f"data: {error_data}\n\n"

    except Exception as e:
        logger.error("Chat streaming error: %s", e, exc_info=True)
        _active_sse_handlers.pop(session_id, None)
        error_msg = "Sorry, something went wrong on my end. This is usually a temporary issue — try sending your message again."
        try:
            db.add_message(request.session_id, "assistant", error_msg)
        except Exception:
            pass
        error_data = json.dumps({"type": "error", "content": error_msg})
        yield f"data: {error_data}\n\n"


# ── Document Indexing ────────────────────────────────────────────────────────


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
