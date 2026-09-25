# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
OpenAI API-compatible Pydantic schemas

These schemas define the request and response structures for the
OpenAI-compatible API endpoints.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class ContentPart(BaseModel):
    """
    One element of an OpenAI content-part array.

    Only ``text`` parts are served; other types are accepted by the schema so
    the endpoint can reject them with a message naming the type.
    """

    model_config = ConfigDict(extra="allow")

    type: str
    text: Optional[str] = None


class ChatMessage(BaseModel):
    """
    Chat message in OpenAI format.

    Supports standard chat roles, 'developer' (treated as 'system'), and
    'tool' for tool call results. ``content`` is a string or an array of
    content parts.

    Example:
        >>> msg = ChatMessage(role="user", content="Hello")
        >>> msg.model_dump()
        {'role': 'user', 'content': 'Hello'}

        >>> tool_msg = ChatMessage(role="tool", tool_call_id="call_123", content="Result")
        >>> tool_msg.model_dump()
        {'role': 'tool', 'content': 'Result', 'tool_call_id': 'call_123'}
    """

    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: Union[str, List[ContentPart], None] = None
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
        ...     model="gaia",
        ...     messages=[{"role": "user", "content": "Hello"}]
        ... )
    """

    model: str = Field(..., description="Model ID (e.g., gaia)")
    messages: List[ChatMessage] = Field(..., description="Array of chat messages")
    stream: bool = Field(default=False, description="Enable SSE streaming")
    # Unset means the agent's own default, not the OpenAI one.
    temperature: Optional[float] = Field(
        default=None, ge=0, le=2, description="Sampling temperature"
    )
    max_tokens: Optional[int] = Field(
        default=None, gt=0, description="Maximum tokens per model call"
    )
    top_p: Optional[float] = Field(
        default=None, ge=0, le=1, description="Nucleus sampling parameter"
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
        ...     model="gaia",
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
        ...     id="gaia",
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
