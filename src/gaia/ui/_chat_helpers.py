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

        config = ChatAgentConfig(
            model_id=model_id,
            max_steps=10,
            silent_mode=True,
            debug=False,
            rag_documents=rag_file_paths,
            library_documents=library_paths,
            allowed_paths=allowed,
            ui_session_id=request.session_id,
        )
        agent = ChatAgent(config)

        # Restore conversation history (limited to prevent context overflow).
        # 5 pairs × 2 msgs × ~500 tokens ≈ 5 000 tokens — well within 32K.
        # 2000-char truncation preserves enough assistant context for cross-turn
        # recall, pronoun resolution, and multi-step planning.
        _MAX_PAIRS = 5
        _MAX_CHARS = 2000
        for user_msg, assistant_msg in history_pairs[-_MAX_PAIRS:]:
            if hasattr(agent, "conversation_history"):
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
        # Apply a 120-second timeout to prevent indefinite hangs when the
        # LLM gets stuck in a tool loop or Lemonade becomes unresponsive
        return await asyncio.wait_for(
            loop.run_in_executor(None, _do_chat),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        logger.error("Chat response timed out after 120 seconds")
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
    import threading

    from gaia.ui.sse_handler import SSEOutputHandler

    session_id = request.session_id
    try:
        # Create SSE handler first and emit immediate feedback BEFORE the
        # slow ChatAgent construction (RAG indexing, LLM connection can take 10-30s)
        sse_handler = SSEOutputHandler()
        # Register so /api/chat/confirm-tool can find this handler.
        _active_sse_handlers[session_id] = sse_handler
        sse_handler._emit(
            {"type": "status", "status": "info", "message": "Connecting to LLM..."}
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
            import time as _time

            try:
                from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

                # -- Phase 1: Configure --
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

                # -- Phase 2: LLM connection --
                agent = ChatAgent(config)
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
                    sse_handler._emit(
                        {
                            "type": "tool_start",
                            "tool": "index_documents",
                            "detail": f"Indexing {len(rag_file_paths)} document(s) for RAG",
                        }
                    )
                    idx_start = _time.time()
                    doc_stats = []
                    total_chunks = 0
                    for i, fpath in enumerate(rag_file_paths, 1):
                        doc_name = Path(fpath).name
                        sse_handler._emit(
                            {
                                "type": "status",
                                "status": "info",
                                "message": f"Indexing [{i}/{len(rag_file_paths)}]: {doc_name}",
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
                                # Collect per-doc stats
                                size_mb = result.get("file_size_mb", 0) or 0
                                file_size_bytes = int(size_mb * 1024 * 1024)
                                if size_mb >= 1:
                                    size_str = f"{size_mb:.1f} MB"
                                elif file_size_bytes >= 1024:
                                    size_str = f"{file_size_bytes // 1024} KB"
                                else:
                                    size_str = f"{file_size_bytes} B"
                                cached = result.get("from_cache", False)
                                doc_stats.append(
                                    f"  {doc_name} — {n_chunks} chunks, {size_str}"
                                    + (" (cached)" if cached else "")
                                )
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
                        f"Indexed {len(rag_file_paths)} document(s) in {idx_elapsed}s",
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
                                "Library pre-index skipped for %s: %s", fpath, lib_err
                            )
                    if preindexed:
                        agent.rebuild_system_prompt()
                        logger.info(
                            "Pre-indexed %d library doc(s) from cache", preindexed
                        )

                # -- Phase 4: Conversation history --
                # Limit history to prevent context window overflow.
                # With RAG chunks + tools + system prompt, the 32K context
                # fills fast.  Keep the last 5 exchanges and truncate long
                # assistant messages to ~2000 chars each.
                # NOTE: Increasing from (2, 500) → (5, 2000) unblocks multi-turn
                # scenarios: cross_turn_file_recall, pronoun_resolution,
                # multi_doc_context, conversation_summary, multi_step_plan,
                # vague_request_clarification, topic_switch.
                # 5 pairs × 2 msgs × ~500 tokens ≈ 5 000 tokens — well within 32K.
                _MAX_HISTORY_PAIRS = 5
                _MAX_MSG_CHARS = 2000
                if history_pairs:
                    recent = history_pairs[-_MAX_HISTORY_PAIRS:]
                    sse_handler._emit(
                        {
                            "type": "status",
                            "status": "info",
                            "message": f"Restoring {len(recent)} previous message(s)",
                        }
                    )
                    for user_msg, assistant_msg in recent:
                        if hasattr(agent, "conversation_history"):
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
                result = agent.process_query(request.message)
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
        import time as _loop_time

        _stream_start = _loop_time.time()
        _STREAM_TIMEOUT = 180  # 3 minutes max for entire streaming response
        while True:
            # Guard: total timeout for the streaming response
            if _loop_time.time() - _stream_start > _STREAM_TIMEOUT:
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

                yield f"data: {json.dumps(event)}\n\n"

            except queue.Empty:
                if not producer.is_alive():
                    break
                # Send SSE comment as keepalive every ~5s (25 cycles x 0.2s)
                # to prevent proxies/browsers from closing idle connections
                idle_cycles += 1
                if idle_cycles % 25 == 0:
                    yield ": keepalive\n\n"
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
            msg_id = db.add_message(
                request.session_id,
                "assistant",
                full_response,
                agent_steps=captured_steps if captured_steps else None,
            )
            done_event: dict = {
                "type": "done",
                "message_id": msg_id,
                "content": full_response,
            }
            # Fetch last inference stats from Lemonade (non-blocking)
            try:
                import httpx

                base_url = os.environ.get(
                    "LEMONADE_BASE_URL", "http://localhost:8000/api/v1"
                )
                async with httpx.AsyncClient(timeout=3.0) as stats_client:
                    stats_resp = await stats_client.get(f"{base_url}/stats")
                    if stats_resp.status_code == 200:
                        stats_data = stats_resp.json()
                        done_event["stats"] = {
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
            done_data = json.dumps(done_event)
            yield f"data: {done_data}\n\n"
        else:
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
