# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
OpenAI API-compatible Pydantic schemas

These schemas define the request and response structures for the
OpenAI-compatible API endpoints.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """
    Chat message in OpenAI format.

    Supports standard chat roles plus 'tool' for tool call results.

    Example:
        >>> msg = ChatMessage(role="user", content="Hello")
        >>> msg.model_dump()
        {'role': 'user', 'content': 'Hello'}

        >>> tool_msg = ChatMessage(role="tool", tool_call_id="call_123", content="Result")
        >>> tool_msg.model_dump()
        {'role': 'tool', 'content': 'Result', 'tool_call_id': 'call_123'}
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Tool calls in the message (for assistant messages)"
    )
    tool_call_id: Optional[str] = Field(
        default=None, description="Tool call ID (for tool role messages)"
    )


class ChatCompletionRequest(BaseModel):
    """
    POST /v1/chat/completions request schema.

    Example:
        >>> request = ChatCompletionRequest(
        ...     model="gaia-code",
        ...     messages=[{"role": "user", "content": "Hello"}]
        ... )
    """

    model: str = Field(..., description="Model ID (e.g., gaia-code, gaia-jira)")
    messages: List[ChatMessage] = Field(..., description="Array of chat messages")
    stream: bool = Field(default=False, description="Enable SSE streaming")
    temperature: Optional[float] = Field(
        default=0.7, ge=0, le=2, description="Sampling temperature"
    )
    max_tokens: Optional[int] = Field(
        default=None, gt=0, description="Maximum tokens to generate"
    )
    top_p: Optional[float] = Field(
        default=1.0, ge=0, le=1, description="Nucleus sampling parameter"
    )


class ChatCompletionResponseMessage(BaseModel):
    """
    Response message from chat completion.

    Example:
        >>> msg = ChatCompletionResponseMessage(
        ...     role="assistant",
        ...     content="Hello! How can I help?"
        ... )
    """

    role: Literal["assistant"]
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Tool calls for file operations (OpenAI-compatible format)",
    )


class ChatCompletionChoice(BaseModel):
    """
    A single completion choice.

    Example:
        >>> choice = ChatCompletionChoice(
        ...     index=0,
        ...     message=ChatCompletionResponseMessage(
        ...         role="assistant",
        ...         content="Hello!"
        ...     ),
        ...     finish_reason="stop"
        ... )
    """

    index: int
    message: ChatCompletionResponseMessage
    finish_reason: Literal["stop", "length"]


class UsageInfo(BaseModel):
    """
    Token usage information.

    Example:
        >>> usage = UsageInfo(
        ...     prompt_tokens=10,
        ...     completion_tokens=20,
        ...     total_tokens=30
        ... )
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """
    POST /v1/chat/completions response schema (non-streaming).

    Example:
        >>> response = ChatCompletionResponse(
        ...     id="chatcmpl-123",
        ...     object="chat.completion",
        ...     created=1234567890,
        ...     model="gaia-code",
        ...     choices=[...],
        ...     usage=UsageInfo(...)
        ... )
    """

    id: str
    object: Literal["chat.completion"]
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: UsageInfo


class ModelInfo(BaseModel):
    """
    Model metadata for /v1/models endpoint.

    Example:
        >>> model = ModelInfo(
        ...     id="gaia-code",
        ...     object="model",
        ...     created=1234567890,
        ...     owned_by="amd-gaia",
        ...     max_input_tokens=32768,
        ...     max_output_tokens=8192,
        ...     description="Code agent description"
        ... )
    """

    model_config = ConfigDict(
        extra="allow"
    )  # Allow additional fields for extensibility

    id: str
    object: Literal["model"]
    created: int
    owned_by: str
    description: Optional[str] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None


class ModelListResponse(BaseModel):
    """
    GET /v1/models response schema.

    Example:
        >>> response = ModelListResponse(
        ...     object="list",
        ...     data=[ModelInfo(...), ModelInfo(...)]
        ... )
    """

    object: Literal["list"]
    data: List[ModelInfo]
