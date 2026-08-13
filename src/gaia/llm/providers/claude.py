# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Claude (Anthropic) provider — chat + native tool calling; no embeddings.

Tool-calling turns are re-encoded into the same sentinel envelope the Lemonade
provider emits (``NATIVE_TOOL_CALLS_PREFIX`` / ``{"__tool_calls__": [...]}``),
so the agent loop's response parser works unchanged against either backend.
"""

import json
import logging
import os
import time
from typing import Any, Dict, Iterator, List, Optional, Union

# The factory imports this module only when the claude provider is requested,
# so the guarded import never taxes the Lemonade-only path.
try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

from ..base_client import LLMClient
from .lemonade import _NATIVE_TC_KEY

logger = logging.getLogger(__name__)

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"

#: kwargs the Anthropic Messages API accepts from GAIA's chat layer. Everything
#: else (llama.cpp sampling knobs, ChatML ``stop`` token lists, temperature —
#: rejected with a 400 on current Claude models) is dropped with a debug log.
_PASSTHROUGH_KWARGS = frozenset({"max_tokens", "stop_sequences", "metadata", "timeout"})

#: ``max_tokens`` caps thinking + answer text together on current Claude
#: models, so small caps sized for Lemonade truncate mid-answer.
_MIN_MAX_TOKENS = 8192

_FINISH_REASON_MAP = {
    "tool_use": "tool_calls",
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
}


def _require_anthropic():
    if anthropic is None:
        raise ImportError(
            "The 'anthropic' package is required for --use-claude. "
            "Install it with: uv pip install anthropic "
            '(or the eval extras: uv pip install -e ".[eval]")'
        )
    return anthropic


class ClaudeProvider(LLMClient):
    """Claude (Anthropic) provider."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_CLAUDE_MODEL,
        system_prompt: Optional[str] = None,
        max_retries: int = 3,
        timeout: float = 300.0,
        **_kwargs,
    ):
        sdk = _require_anthropic()

        # The repo keeps ANTHROPIC_API_KEY in .env — same load as gaia.eval.claude.
        from dotenv import load_dotenv  # pylint: disable=import-outside-toplevel

        load_dotenv()

        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError(
                "ANTHROPIC_API_KEY not found in environment.\n\n"
                "The Claude backend (--use-claude) needs an Anthropic credential:\n"
                "  1. SUBSCRIPTION (recommended if you have Claude Code Max):\n"
                "     Run `claude setup-token`, follow the browser prompt, then\n"
                "     export the printed token as ANTHROPIC_API_KEY.\n"
                "  2. API KEY (billed to your Anthropic console):\n"
                "     export ANTHROPIC_API_KEY=sk-ant-...\n"
                "Either can also go in a `.env` file at the repo root."
            )

        client_kwargs: Dict[str, Any] = {"max_retries": max_retries, "timeout": timeout}
        if key.startswith("sk-ant-oat"):
            # OAuth tokens ride Authorization: Bearer, not x-api-key.
            client_kwargs["auth_token"] = key
            client_kwargs["default_headers"] = {"anthropic-beta": "oauth-2025-04-20"}
        else:
            client_kwargs["api_key"] = key

        self._anthropic = sdk
        self._client = sdk.Anthropic(**client_kwargs)
        self._model = (
            model if model and model.startswith("claude-") else DEFAULT_CLAUDE_MODEL
        )
        if model and not model.startswith("claude-"):
            logger.warning(
                "Ignoring non-Claude model id %r for the Claude provider; using %s",
                model,
                self._model,
            )
        self._system_prompt = system_prompt
        self._last_usage: Optional[dict] = None

    @property
    def provider_name(self) -> str:
        return "Claude"

    # ── request shaping ─────────────────────────────────────────────────

    def _resolve_model(self, model: Optional[str]) -> str:
        """First claude-* candidate wins — callers routinely pass their local
        Lemonade model id through the shared ``model`` kwarg."""
        for candidate in (model, self._model, DEFAULT_CLAUDE_MODEL):
            if candidate and candidate.startswith("claude-"):
                return candidate
        return DEFAULT_CLAUDE_MODEL

    @staticmethod
    def _to_anthropic_tools(tools: Optional[List[dict]]) -> Optional[List[dict]]:
        """OpenAI ``{"type":"function","function":{...}}`` → Anthropic shape."""
        if not tools:
            return None
        converted = []
        for tool in tools:
            fn = tool.get("function") if tool.get("type") == "function" else None
            if fn is None:
                # Already Anthropic-shaped (has name + input_schema) — pass through.
                converted.append(tool)
                continue
            converted.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        return converted

    def _split_system(self, messages: List[dict]) -> tuple:
        """Hoist role=system entries out of the array into the ``system`` param."""
        system_parts: List[str] = []
        cleaned: List[dict] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")
            if role == "system":
                if content:
                    system_parts.append(str(content))
                continue
            if content is None or content == "":
                # Anthropic rejects empty message content outright.
                logger.debug("Dropping empty %s message for Claude request", role)
                continue
            cleaned.append({"role": role, "content": content})
        system = "\n\n".join(system_parts) if system_parts else self._system_prompt
        return system, cleaned

    def _build_params(
        self,
        model: str,
        messages: List[dict],
        tools: Optional[List[dict]],
        kwargs: dict,
    ) -> dict:
        system, cleaned = self._split_system(messages)
        if not cleaned:
            raise ValueError(
                "Claude request has no user/assistant messages after hoisting the "
                "system prompt — nothing to send."
            )

        dropped = sorted(k for k in kwargs if k not in _PASSTHROUGH_KWARGS)
        if dropped:
            logger.debug(
                "Dropping non-Anthropic kwargs for Claude request: %s", dropped
            )

        params: Dict[str, Any] = {"model": model, "messages": cleaned}
        for k in _PASSTHROUGH_KWARGS:
            if k in kwargs and kwargs[k] is not None:
                params[k] = kwargs[k]
        params["max_tokens"] = max(int(params.get("max_tokens") or 0), _MIN_MAX_TOKENS)
        if system:
            params["system"] = system
        anthropic_tools = self._to_anthropic_tools(tools)
        if anthropic_tools:
            params["tools"] = anthropic_tools
        return params

    # ── error translation ───────────────────────────────────────────────

    def _raise_actionable(self, exc: Exception) -> None:
        anthropic = self._anthropic
        if isinstance(exc, anthropic.AuthenticationError):
            raise RuntimeError(
                "Anthropic rejected the credential (401). Check ANTHROPIC_API_KEY "
                "in your environment or repo .env — regenerate it at "
                "https://console.anthropic.com/ or via `claude setup-token`."
            ) from exc
        if isinstance(exc, anthropic.NotFoundError):
            raise RuntimeError(
                f"Anthropic model not found (404): {exc}. Pass a valid id via "
                "--claude-model (e.g. claude-sonnet-5)."
            ) from exc
        if isinstance(exc, anthropic.RateLimitError):
            raise RuntimeError(
                f"Anthropic rate limit hit (429): {exc}. Wait for the retry window "
                "or lower request volume; see console.anthropic.com for limits."
            ) from exc
        if isinstance(exc, anthropic.APIConnectionError):
            raise RuntimeError(
                f"Could not reach the Anthropic API: {exc}. Check network/proxy "
                "connectivity to api.anthropic.com, then retry."
            ) from exc
        if isinstance(exc, anthropic.APIStatusError):
            raise RuntimeError(
                f"Anthropic API error (HTTP {exc.status_code}): {exc.message}. "
                "See https://status.anthropic.com if this persists."
            ) from exc
        raise exc

    # ── chat ────────────────────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        return self.chat(
            [{"role": "user", "content": prompt}],
            model=model,
            stream=stream,
            **kwargs,
        )

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        stream: bool = False,
        tools: Optional[List[dict]] = None,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        self._last_usage = None
        params = self._build_params(self._resolve_model(model), messages, tools, kwargs)

        if stream:
            return self._stream_chat(params)

        start = time.monotonic()
        try:
            response = self._client.messages.create(**params)
        except Exception as exc:  # translated to actionable errors below
            self._raise_actionable(exc)
            raise  # unreachable — _raise_actionable always raises
        return self._parse_response(response, time.monotonic() - start)

    def _parse_response(self, response, elapsed: float) -> str:
        text_parts: List[str] = []
        tool_calls: List[dict] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input or {}),
                        },
                    }
                )
            # thinking / redacted_thinking blocks are never answer text.

        self._capture_usage(
            getattr(response.usage, "input_tokens", 0),
            getattr(response.usage, "output_tokens", 0),
            elapsed,
        )

        stop_reason = response.stop_reason or ""
        if stop_reason == "refusal":
            raise RuntimeError(
                "Claude declined this request (stop_reason=refusal). Rephrase the "
                "query or check stop_details in the Anthropic console logs."
            )
        finish_reason = _FINISH_REASON_MAP.get(stop_reason, stop_reason)
        if tool_calls:
            return json.dumps(
                {
                    _NATIVE_TC_KEY: tool_calls,
                    "finish_reason": finish_reason,
                    "content": "".join(text_parts) or None,
                }
            )
        return "".join(text_parts)

    def _stream_chat(self, params: dict) -> Iterator[str]:
        start = time.monotonic()
        try:
            events = self._client.messages.create(**params, stream=True)
        except Exception as exc:
            self._raise_actionable(exc)
            raise  # unreachable — _raise_actionable always raises

        text_parts: List[str] = []
        tool_slots: Dict[int, dict] = {}
        stop_reason = ""
        input_tokens = 0
        output_tokens = 0
        try:
            for event in events:
                etype = event.type
                if etype == "message_start":
                    input_tokens = getattr(event.message.usage, "input_tokens", 0)
                elif etype == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        tool_slots[event.index] = {
                            "id": block.id,
                            "type": "function",
                            "function": {"name": block.name, "arguments": ""},
                        }
                elif etype == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        text_parts.append(delta.text)
                        yield delta.text
                    elif delta.type == "input_json_delta":
                        slot = tool_slots.get(event.index)
                        if slot is not None:
                            slot["function"]["arguments"] += delta.partial_json
                    # thinking_delta / signature_delta are ignored.
                elif etype == "message_delta":
                    stop_reason = (
                        getattr(event.delta, "stop_reason", None) or stop_reason
                    )
                    usage = getattr(event, "usage", None)
                    if usage is not None:
                        output_tokens = (
                            getattr(usage, "output_tokens", 0) or output_tokens
                        )
        except Exception as exc:
            self._raise_actionable(exc)
            raise  # unreachable — _raise_actionable always raises

        self._capture_usage(input_tokens, output_tokens, time.monotonic() - start)

        if stop_reason == "refusal":
            raise RuntimeError(
                "Claude declined this request (stop_reason=refusal). Rephrase the "
                "query or check stop_details in the Anthropic console logs."
            )
        if tool_slots:
            for slot in tool_slots.values():
                if not slot["function"]["arguments"]:
                    slot["function"]["arguments"] = "{}"
            yield json.dumps(
                {
                    _NATIVE_TC_KEY: [tool_slots[i] for i in sorted(tool_slots)],
                    "finish_reason": _FINISH_REASON_MAP.get(stop_reason, stop_reason),
                    "content": "".join(text_parts) or None,
                }
            )

    def _capture_usage(
        self, input_tokens: int, output_tokens: int, elapsed: float
    ) -> None:
        self._last_usage = {
            "prompt_tokens": int(input_tokens or 0),
            "completion_tokens": int(output_tokens or 0),
            "total_tokens": int(input_tokens or 0) + int(output_tokens or 0),
            "tokens_per_second": (
                round(output_tokens / elapsed, 2)
                if elapsed > 0 and output_tokens
                else 0.0
            ),
        }

    # ── stats ───────────────────────────────────────────────────────────

    def get_performance_stats(self) -> dict:
        return dict(self._last_usage) if self._last_usage else {}

    def get_last_usage(self) -> Optional[dict]:
        """Token-usage dict from the most recent ``chat()`` call, or ``None``."""
        return self._last_usage

    # embed() inherited from ABC - raises NotSupportedError (Anthropic has no
    # embeddings API; Lemonade keeps serving embeddings under --use-claude).

    def vision(self, images: list[bytes], prompt: str, **kwargs) -> str:
        import base64  # pylint: disable=import-outside-toplevel

        image_b64 = base64.b64encode(images[0]).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        result = self.chat(messages, **kwargs)
        return result if isinstance(result, str) else "".join(result)

    # load_model() / unload_model() inherited from ABC - raise NotSupportedError
