# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
OpenAI-compatible API server for GAIA

This module provides a FastAPI server that exposes GAIA agents via
OpenAI-compatible endpoints, allowing VSCode and other tools to use
GAIA agents as if they were OpenAI models.

Endpoints:
    POST /v1/chat/completions - Create chat completion (streaming and non-streaming)
    GET /v1/models - List available models (GAIA agents)
    GET /health - Health check
"""

import asyncio
import importlib.util
import json
import logging
import os
import time
import uuid
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from gaia.agents.base.api_agent import ApiAgent

from .agent_proxy import build_agent_proxy_router
from .agent_registry import registry
from .schemas import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionResponseMessage,
    ModelListResponse,
    UsageInfo,
)

# Configure logging
logger = logging.getLogger(__name__)
_REDACTED_LOG_VALUE = "[redacted]"

# Set logger level based on debug flag
if os.environ.get("GAIA_API_DEBUG") == "1":
    logger.setLevel(logging.DEBUG)
    logger.info("Debug logging enabled for API server")


def _api_debug_enabled() -> bool:
    return os.environ.get("GAIA_API_DEBUG") == "1"


def _log_header_summary(headers) -> None:
    """Log header names and value sizes without persisting header values."""
    logger.debug("Headers:")
    for name, value in headers.items():
        logger.debug("  %s: %s (%d chars)", name, _REDACTED_LOG_VALUE, len(value))


def _log_message_summary(index: int, message) -> None:
    content_length = len(message.content or "")
    logger.debug("Message %d:", index)
    logger.debug("  Role: %s", message.role)
    logger.debug("  Content: %s (%d chars)", _REDACTED_LOG_VALUE, content_length)
    if message.tool_calls is not None:
        logger.debug("  Tool calls: %d", len(message.tool_calls))
    if message.tool_call_id is not None:
        logger.debug("  Tool call ID: %s", _REDACTED_LOG_VALUE)


def _log_request_parameter_summary(request: ChatCompletionRequest) -> None:
    logger.debug("Request parameters:")
    for field_name in ("temperature", "max_tokens", "top_p"):
        value = getattr(request, field_name, None)
        logger.debug("  %s: %s", field_name, "set" if value is not None else "not set")


def extract_workspace_root(messages):
    """
    Extract workspace root path from GitHub Copilot messages.

    GitHub Copilot includes workspace info in messages like:
    <workspace_info>
    I am working in a workspace with the following folders:
    - /Users/username/path/to/workspace
    </workspace_info>

    Args:
        messages: List of ChatMessage objects

    Returns:
        str: Workspace root path, or None if not found
    """
    import re

    for msg in messages:
        if msg.role == "user" and msg.content:
            # Look for workspace_info section
            workspace_match = re.search(
                r"<workspace_info>.*?following folders:\s*\n\s*-\s*([^\s\n]+)",
                msg.content,
                re.DOTALL,
            )
            if workspace_match:
                return workspace_match.group(1).strip()

    return None


# Initialize FastAPI app
app = FastAPI(
    title="GAIA OpenAI-Compatible API",
    description="OpenAI-compatible API for GAIA agents",
    version="1.0.0",
)

# CORS middleware - allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Email Triage Agent REST surface (#1229): POST /v1/email/{triage,draft,send}.
# Ships with the standalone ``gaia-agent-email`` wheel (#1102); mount it only
# when that wheel is installed so the core API server still starts without it.
# Gate on the wheel's presence via find_spec (no import side effect), then
# import the router OUTSIDE the gate so a genuine broken import inside an
# installed wheel fails loudly rather than being swallowed as "not installed"
# (no-silent-fallback rule).
if importlib.util.find_spec("gaia_agent_email") is None:
    logger.info(
        "Email REST routes unavailable: install the email agent "
        "(`pip install gaia-agent-email`) to enable POST /v1/email/*."
    )
else:
    from gaia_agent_email.agent_routes import router as email_agent_router
    from gaia_agent_email.api_routes import router as email_router

    app.include_router(email_router)
    # Stateful conversational surface (/v1/email/agent/*): session-scoped agent
    # with memory + tool-confirmation, for hosts driving the full agent over HTTP.
    app.include_router(email_agent_router)


# Raw request logging middleware (debug mode only)
@app.middleware("http")
async def log_raw_requests(request: Request, call_next):
    """
    Middleware to log raw HTTP requests when debug mode is enabled.
    For streaming endpoints, only log headers to avoid breaking SSE.
    """
    if _api_debug_enabled():
        logger.debug("=" * 80)
        logger.debug("📥 RAW HTTP REQUEST")
        logger.debug("=" * 80)
        logger.debug(f"Path: {request.url.path}")
        logger.debug(f"Method: {request.method}")
        _log_header_summary(request.headers)

        # DON'T read body for streaming endpoints - it breaks ASGI message flow
        # Per FastAPI docs: "Never read the request body in middleware for streaming responses"
        # Covers /v1/chat/completions AND the agent /query relay (#2178), whose
        # POST bodies feed a StreamingResponse.
        _p = request.url.path
        _is_streaming_post = request.method == "POST" and (
            _p == "/v1/chat/completions"
            or (_p.startswith("/v1/") and _p.endswith("/query"))
        )
        if _is_streaming_post:
            logger.debug(
                "Body: [Skipped for streaming endpoint - prevents ASGI message flow disruption]"
            )
        else:
            # Safe to read body for non-streaming endpoints
            body_bytes = await request.body()
            logger.debug(f"Body (raw bytes length): {len(body_bytes)}")
            if body_bytes:
                try:
                    body_bytes.decode("utf-8")
                    logger.debug("Body (decoded UTF-8): %s", _REDACTED_LOG_VALUE)
                except UnicodeDecodeError:
                    logger.debug("Body contains non-UTF-8 data")

        logger.debug("=" * 80)

    response = await call_next(request)
    return response


@app.post("/v1/chat/completions")
async def create_chat_completion(request: ChatCompletionRequest):
    """
    Create chat completion (OpenAI-compatible endpoint).

    Supports both streaming (SSE) and non-streaming responses.

    Args:
        request: Chat completion request with model, messages, and options

    Returns:
        For non-streaming: ChatCompletionResponse
        For streaming: StreamingResponse with SSE chunks

    Raises:
        HTTPException 404: Model not found
        HTTPException 400: No user message in request

    Example:
        Non-streaming:
        ```
        POST /v1/chat/completions
        {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Write hello world"}],
            "stream": false
        }
        ```

        Streaming:
        ```
        POST /v1/chat/completions
        {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Write hello world"}],
            "stream": true
        }
        ```
    """
    # Debug logging: trace incoming request
    if _api_debug_enabled():
        logger.debug("=" * 80)
        logger.debug("📥 INCOMING CHAT COMPLETION REQUEST")
        logger.debug("=" * 80)
        logger.debug(f"Model: {request.model}")
        logger.debug(f"Stream: {request.stream}")
        logger.debug(f"Message count: {len(request.messages)}")
        logger.debug("-" * 80)

        for i, msg in enumerate(request.messages):
            _log_message_summary(i, msg)
            logger.debug("-" * 40)

        _log_request_parameter_summary(request)
        logger.debug("=" * 80)

    # Validate model exists
    if not registry.model_exists(request.model):
        raise HTTPException(
            status_code=404, detail=f"Model '{request.model}' not found"
        )

    # Extract workspace root from messages (for converting relative paths to absolute)
    workspace_root = extract_workspace_root(request.messages)
    if _api_debug_enabled() and workspace_root:
        logger.debug("📁 Extracted workspace root: %s", _REDACTED_LOG_VALUE)

    # Extract user query from messages (get last user message)
    user_message = next(
        (m.content for m in reversed(request.messages) if m.role == "user"), None
    )

    if not user_message:
        raise HTTPException(
            status_code=400, detail="No user message found in messages array"
        )

    # Debug logging: show what we're passing to the agent
    if _api_debug_enabled():
        logger.debug("🔄 EXTRACTED FOR AGENT:")
        logger.debug(
            "Passing to agent: %s (%d chars)",
            _REDACTED_LOG_VALUE,
            len(user_message),
        )
        logger.debug("=" * 80)

    # Get agent instance for this model
    try:
        agent = registry.get_agent(request.model)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Handle streaming vs non-streaming
    if request.stream:
        # Debug logging for streaming mode
        if _api_debug_enabled():
            logger.debug("🌊 Using STREAMING mode")

        return StreamingResponse(
            create_sse_stream(
                agent, user_message, request.model, workspace_root=workspace_root
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable proxy buffering
            },
        )
    else:
        # Debug logging for non-streaming mode
        if _api_debug_enabled():
            logger.debug("📦 Using NON-STREAMING mode")

        # Process query synchronously with workspace root
        result = agent.process_query(user_message, workspace_root=workspace_root)

        # Debug logging: show what agent returned
        if _api_debug_enabled():
            logger.debug("=" * 80)
            logger.debug("📤 AGENT RESPONSE (NON-STREAMING)")
            logger.debug("=" * 80)
            logger.debug(f"Result type: {type(result)}")
            logger.debug(
                "Result key count: %s",
                len(result.keys()) if isinstance(result, dict) else "N/A",
            )
            logger.debug(
                f"Status: {result.get('status') if isinstance(result, dict) else 'N/A'}"
            )
            logger.debug(
                f"Steps taken: {result.get('steps_taken') if isinstance(result, dict) else 'N/A'}"
            )
            result_length = (
                len(str(result.get("result", "")))
                if isinstance(result, dict)
                else len(str(result))
            )
            logger.debug(
                "Result preview: %s (%d chars)",
                _REDACTED_LOG_VALUE,
                result_length,
            )
            logger.debug("=" * 80)

        # Extract content from result
        content = result.get("result", str(result))

        # Estimate tokens
        if isinstance(agent, ApiAgent):
            prompt_tokens = agent.estimate_tokens(user_message)
            completion_tokens = agent.estimate_tokens(content)
        else:
            prompt_tokens = len(user_message) // 4
            completion_tokens = len(content) // 4

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
            object="chat.completion",
            created=int(time.time()),
            model=request.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionResponseMessage(
                        role="assistant",
                        content=content,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )


async def create_sse_stream(
    agent, query: str, model: str, workspace_root: str = None
) -> AsyncGenerator[str, None]:
    """
    Create Server-Sent Events stream for chat completion.

    This function processes the agent query in a thread pool (to avoid blocking)
    and streams agent progress events in real-time via the SSEOutputHandler.

    Args:
        agent: Agent instance (with SSEOutputHandler)
        query: User query string
        model: Model ID
        workspace_root: Optional workspace root path for absolute file paths

    Yields:
        SSE-formatted chunks with "data: " prefix

    Example output:
        data: {"id":"chatcmpl-123","object":"chat.completion.chunk",...}
        data: {"id":"chatcmpl-123","object":"chat.completion.chunk",...}
        data: [DONE]
    """
    # Debug logging - FIRST LINE to confirm generator starts
    if _api_debug_enabled():
        logger.debug("🎬 Generator started! Client is consuming the stream.")

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    # First chunk with role
    first_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }
        ],
    }
    if _api_debug_enabled():
        logger.debug(f"📤 Sending first chunk: {json.dumps(first_chunk)}")
    yield f"data: {json.dumps(first_chunk)}\n\n"

    # Debug logging
    if _api_debug_enabled():
        logger.debug("🔄 Starting agent query processing in thread pool...")

    # Process query in thread pool to avoid blocking event loop
    loop = asyncio.get_event_loop()

    # Get the SSEOutputHandler from the agent (try output_handler first, fall back to console)
    output_handler = getattr(agent, "output_handler", None) or getattr(
        agent, "console", None
    )

    try:
        # Start processing in background
        task = loop.run_in_executor(
            None, lambda: agent.process_query(query, workspace_root=workspace_root)
        )

        # Stream events as they are generated
        while not task.done():
            # Check for new events from the output handler
            if hasattr(output_handler, "has_events") and output_handler.has_events():
                events = output_handler.get_events()

                for event in events:
                    event_type = event.get("type", "message")

                    # Check if this event should be streamed to client
                    if not output_handler.should_stream_as_content(event_type):
                        # Still log it in debug mode
                        if _api_debug_enabled():
                            logger.debug(f"📝 Skipping event: {event_type}")
                        continue

                    # Format event as clean content
                    content_text = output_handler.format_event_as_content(event)

                    # Skip empty content (filtered events)
                    if not content_text:
                        continue

                    content_chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": content_text},
                                "finish_reason": None,
                            }
                        ],
                    }

                    if _api_debug_enabled():
                        logger.debug(
                            "📤 Streaming event: %s -> %s (%d chars)",
                            event_type,
                            _REDACTED_LOG_VALUE,
                            len(content_text),
                        )

                    yield f"data: {json.dumps(content_chunk)}\n\n"

            # Small delay to avoid busy waiting
            await asyncio.sleep(0.1)

        # Get the final result
        result = await task

        # Get any remaining events
        if hasattr(output_handler, "has_events") and output_handler.has_events():
            events = output_handler.get_events()
            for event in events:
                event_type = event.get("type", "message")

                # Check if this event should be streamed
                if not output_handler.should_stream_as_content(event_type):
                    continue

                # Format event as clean content
                content_text = output_handler.format_event_as_content(event)

                # Skip empty content
                if not content_text:
                    continue

                content_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": content_text},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(content_chunk)}\n\n"

        # Debug logging: show what agent returned
        if _api_debug_enabled():
            logger.debug("=" * 80)
            logger.debug("📤 AGENT RESPONSE (STREAMING)")
            logger.debug("=" * 80)
            logger.debug(f"Result type: {type(result)}")
            logger.debug(
                "Result key count: %s",
                len(result.keys()) if isinstance(result, dict) else "N/A",
            )
            logger.debug(
                f"Status: {result.get('status') if isinstance(result, dict) else 'N/A'}"
            )
            logger.debug(
                f"Steps taken: {result.get('steps_taken') if isinstance(result, dict) else 'N/A'}"
            )
            result_length = (
                len(str(result.get("result", "")))
                if isinstance(result, dict)
                else len(str(result))
            )
            logger.debug(
                "Result preview: %s (%d chars)",
                _REDACTED_LOG_VALUE,
                result_length,
            )
            logger.debug("=" * 80)

    except Exception as e:
        # Log and re-raise errors
        logger.error(f"❌ Agent query processing failed: {e}", exc_info=True)
        raise

    # Final chunk with finish_reason
    final_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    if _api_debug_enabled():
        logger.debug("📤 Sending final chunk with finish_reason=stop")
    yield f"data: {json.dumps(final_chunk)}\n\n"

    # Done marker
    if _api_debug_enabled():
        logger.debug("✅ SSE stream complete. Sending [DONE] marker.")
    yield "data: [DONE]\n\n"


@app.get("/v1/models")
async def list_models() -> ModelListResponse:
    """
    List available models (OpenAI-compatible endpoint).

    Note: These are GAIA agents exposed as "models", not LLM models.
    Lemonade manages the actual LLM models underneath.

    Returns:
        ModelListResponse with list of available agent "models"

    Example:
        ```
        GET /v1/models
        {
            "object": "list",
            "data": [
                {
                    "id": "gaia-code",
                    "object": "model",
                    "created": 1234567890,
                    "owned_by": "amd-gaia"
                },
                ...
            ]
        }
        ```
    """
    return ModelListResponse(object="list", data=registry.list_models())


@app.get("/health")
async def health_check():
    """
    Health check endpoint.

    Returns:
        Status and service name

    Example:
        ```
        GET /health
        {
            "status": "ok",
            "service": "gaia-api"
        }
        ```
    """
    return {"status": "ok", "service": "gaia-api"}


# Agent /query surface (#2178 / V2-17): POST /v1/<agent>/query streams the
# agentic loop through the always-on daemon relay (#2150). Mounted after the
# OpenAI-compatible routes are declared; the router only claims the narrow
# /v1/<agent>/query surface, so it never shadows /v1/chat/completions or
# /v1/models (nor their 404/405). The API server stays a thin client — it
# forwards only the daemon client token, never a sidecar bearer. Gated by
# GAIA_API_KEY (§0.33), independent of the email wheel.
app.include_router(build_agent_proxy_router())
