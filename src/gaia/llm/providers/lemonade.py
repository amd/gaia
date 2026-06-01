# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lemonade provider - supports ALL methods."""

import json
import logging
from typing import Iterator, List, Optional, Tuple, Union

from ..base_client import LLMClient
from ..lemonade_client import DEFAULT_MODEL_NAME, LemonadeClient, is_tool_calling_model

logger = logging.getLogger(__name__)

# Sentinel key used to encode native tool_calls inside a JSON string so that
# the response type stays `str` everywhere (no callers need updating).
_NATIVE_TC_KEY = "__tool_calls__"


# ── Typed errors ────────────────────────────────────────────────────────
#
# Lemonade returns structured errors as ``{"error": {"type": ..., "message": ...}}``.
# We translate the well-known transient failure modes into typed exceptions
# so the chat layer can decide whether to retry (after a model reload, etc.)
# vs. surface a friendly message immediately. Anything we don't recognise
# falls through to ``LemonadeError`` with the raw payload preserved.


class LemonadeError(ValueError):
    """Base class for Lemonade-side failures.

    Carries the raw response payload (when available) on ``.payload`` so
    higher layers can log it for diagnostics; ``.user_message`` is the
    short, plain-English text we'd show the end user.
    """

    retryable: bool = False
    user_message: str = "Something went wrong talking to the local LLM."

    def __init__(
        self,
        user_message: Optional[str] = None,
        payload: Optional[dict] = None,
    ):
        if user_message is not None:
            self.user_message = user_message
        self.payload = payload
        super().__init__(self.user_message)


class LemonadeModelNotLoadedError(LemonadeError):
    retryable = True
    user_message = "Reloading the model — give it a few seconds and try again."


class LemonadeContextOverflowError(LemonadeError):
    """Raised when the prompt + history exceeds the loaded model's ctx.

    ``retryable`` is dynamic — set in ``_classify_lemonade_response`` based
    on the reported ``n_ctx``. When n_ctx is smaller than GAIA's expected
    65536 (64K), the model was loaded with the wrong ctx_size; reloading
    via the pre-flight helper will fix it, so we mark retryable so the
    chat layer auto-recovers. When n_ctx is already at full size, this is
    a genuine "conversation too big" situation and retry won't help.
    """

    retryable = False  # default; set True dynamically when n_ctx < 65536
    user_message = (
        "This conversation got too long for the model's context window. "
        "Start a fresh task to keep going."
    )


class LemonadeNetworkError(LemonadeError):
    """Lemonade Server is unreachable (connection refused / DNS / TLS).

    Distinct from :class:`LemonadeUpstreamTimeoutError`, which is when
    Lemonade *is* reachable but its child llama-server didn't respond in
    time. Don't auto-retry indefinitely — surface the connectivity issue.
    """

    retryable = True
    user_message = (
        "Couldn't reach the local LLM. It may be loading or briefly busy "
        "— try sending the message again."
    )


class LemonadeUpstreamTimeoutError(LemonadeError):
    """Lemonade Server is reachable but its upstream model call timed out.

    This is the failure mode reported in #1030 — Lemonade's libcurl call
    to its child llama-server returns "CURL error: Timeout was reached"
    after the model takes too long to produce its first token. The
    server itself is fine; it's the model inference that hung. This
    typically happens when:

    * The prompt is too large for the loaded model on this hardware
      (heavy system prompt + huge tools schema).
    * The model was just loaded and is still initialising the KV cache.
    * Lemonade was just told to swap models and the swap is in flight.
    * The user is on Windows with iGPU and Gemma 4 E4B (~4.5B params)
      and the first inference cold-start exceeds Lemonade's internal
      upstream timeout.

    We mark this *non-retryable* because the chat layer's blind retry
    will hit the same hung backend; surface a useful remediation
    instead.
    """

    retryable = False
    user_message = (
        "The local model didn't respond in time. The Lemonade Server is "
        "running, but its model call timed out — usually because the "
        "model is still warming up (cold KV cache) or the prompt is too "
        "large for the current hardware on a fresh load. Try:\n"
        "  • Wait 30s and resend the same query — KV cache will be primed.\n"
        "  • Restart Lemonade cleanly:  gaia kill  &&  lemonade-server serve\n"
        "  • Reduce retrieved RAG chunks:  gaia chat --max-chunks 2 ...\n"
        "  • Close other GPU/NPU-heavy apps competing for the device."
    )


def _classify_lemonade_response(response: dict) -> Tuple[Optional[LemonadeError], bool]:
    """Inspect a Lemonade response dict for a known error shape.

    Returns ``(error_instance_or_None, is_error)``. ``is_error=True`` with
    ``None`` means we saw an error envelope but couldn't classify it —
    caller should fall back to the generic ``LemonadeError``.
    """
    if not isinstance(response, dict):
        return None, False
    err = response.get("error")
    if not isinstance(err, dict):
        return None, False

    # Lemonade may nest the upstream llama-server error inside
    # ``details.response.error`` for ``backend_error`` envelopes.
    nested = (
        (err.get("details") or {}).get("response", {}).get("error")
        if isinstance(err.get("details"), dict)
        else None
    )
    candidate_types = []
    candidate_messages = []
    if isinstance(nested, dict):
        candidate_types.append((nested.get("type") or "").lower())
        candidate_messages.append(nested.get("message") or "")
    candidate_types.append((err.get("type") or "").lower())
    candidate_messages.append(err.get("message") or "")

    type_blob = " ".join(t for t in candidate_types if t)
    msg_blob = " ".join(m for m in candidate_messages if m).lower()

    if "model_not_loaded" in type_blob or "no model loaded" in msg_blob:
        return LemonadeModelNotLoadedError(payload=response), True
    if (
        "exceed_context_size" in type_blob
        or "exceeds the available context size" in msg_blob
    ):
        # Mark retryable when the model was loaded with an unexpectedly
        # small ctx (typical: 4096 from a pre-restart leftover, or 32K
        # from a Lemonade `lemonade load Gemma-4-E4B-it-GGUF` without
        # ``--ctx-size``). The chat layer's auto-reload at the expected
        # ctx will fix it, so let it try. GAIA's default expected ctx
        # is 65536 for chat / rag profiles — threshold is a deliberate
        # constant here rather than imported to avoid a circular dep
        # with lemonade_client.
        n_ctx_reported = 0
        if isinstance(nested, dict):
            n_ctx_reported = nested.get("n_ctx") or 0
        if not n_ctx_reported and isinstance(err, dict):
            n_ctx_reported = err.get("n_ctx") or 0
        err_instance = LemonadeContextOverflowError(payload=response)
        if 0 < n_ctx_reported < 65536:
            err_instance.retryable = True
        return err_instance, True
    # Distinguish "upstream model call timed out" (reachable Lemonade,
    # hung llama-server child) from "Lemonade unreachable" (true network
    # error). #1030 — both used to be lumped together so the user got
    # "couldn't reach the local LLM" even when the server was fine.
    is_timeout = (
        "timeout was reached" in msg_blob
        or "timed out" in msg_blob
        or "operation_timeout" in type_blob
    )
    is_unreachable = (
        "connection refused" in msg_blob
        or "could not resolve host" in msg_blob
        or "no route to host" in msg_blob
        or "couldn't connect" in msg_blob
    )

    if is_timeout and not is_unreachable:
        return LemonadeUpstreamTimeoutError(payload=response), True

    if "network_error" in type_blob or "curl error" in msg_blob or is_unreachable:
        return LemonadeNetworkError(payload=response), True

    # Recognised the error envelope but couldn't bucket it specifically.
    user_text = candidate_messages[0] if candidate_messages else ""
    return (
        LemonadeError(
            user_message=(
                "The local LLM hit an unexpected error. "
                f"{user_text[:200] if user_text else 'Try again in a moment.'}"
            ),
            payload=response,
        ),
        True,
    )


class LemonadeProvider(LLMClient):
    """Lemonade provider - local AMD-optimized inference."""

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        system_prompt: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs,
    ):
        # Build kwargs for LemonadeClient, only including non-None values
        backend_kwargs = {}
        if model is not None:
            backend_kwargs["model"] = model
        if base_url is not None:
            backend_kwargs["base_url"] = base_url
        if host is not None:
            backend_kwargs["host"] = host
        if port is not None:
            backend_kwargs["port"] = port
        if api_key is not None:
            backend_kwargs["api_key"] = api_key
        backend_kwargs.update(kwargs)

        self._backend = LemonadeClient(**backend_kwargs)
        self._model = model
        self._system_prompt = system_prompt

    @property
    def provider_name(self) -> str:
        return "Lemonade"

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        # Use chat endpoint (completions endpoint not available in Lemonade v9.1+)
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
    ) -> Union[str, dict, Iterator[str]]:
        # Use provided model, instance model, or default CPU model
        effective_model = model or self._model or DEFAULT_MODEL_NAME
        tool_capable = is_tool_calling_model(effective_model)

        # Prepend system prompt if set
        if self._system_prompt:
            messages = [{"role": "system", "content": self._system_prompt}] + list(
                messages
            )

        # Default to low temperature for deterministic responses (matches old LLMClient behavior)
        kwargs.setdefault("temperature", 0.1)

        # Repetition prevention: penalise recently-generated tokens so the
        # model doesn't get stuck in a loop repeating tables, paragraphs, etc.
        #
        # We use TWO layers of protection:
        #   1. OpenAI-standard params (frequency_penalty, presence_penalty) –
        #      work in both streaming (OpenAI client) and non-streaming paths.
        #   2. llama.cpp-native params (repeat_penalty, repeat_last_n) –
        #      passed via extra_body for the streaming OpenAI client path,
        #      and directly in kwargs for the non-streaming requests.post path.
        #
        # frequency_penalty: additive penalty proportional to token frequency
        #                    in generated text so far (0.0 = off, 0.0–2.0 range)
        # presence_penalty:  flat penalty if token appeared at all in output
        #                    (0.0 = off, 0.0–2.0 range)
        # repeat_penalty:    llama.cpp multiplicative penalty on tokens in the
        #                    last repeat_last_n window (1.0 = off, 1.1–1.3 typical)
        # repeat_last_n:     how far back to look (default 64; 256 covers tables)
        kwargs.setdefault("frequency_penalty", 0.3)
        kwargs.setdefault("presence_penalty", 0.1)
        kwargs.setdefault("repeat_penalty", 1.1)
        kwargs.setdefault("repeat_last_n", 256)

        # For tool-calling models with tools, always use non-streaming so tool_calls
        # are returned as a complete structured dict (not fragmented SSE chunks).
        # Streaming of tool_call delta frames is deferred to a future release.
        effective_stream = stream and not (tool_capable and tools)
        effective_tools = tools if tool_capable else None

        response = self._backend.chat_completions(
            model=effective_model,
            messages=messages,
            stream=effective_stream,
            tools=effective_tools,
            **kwargs,
        )
        if effective_stream:
            return self._handle_stream(response)

        # Handle error responses — classify into typed exceptions so the
        # chat layer can decide whether to auto-retry vs. surface a
        # friendly message. Raw payload is preserved on the exception
        # for diagnostic logging.
        if not isinstance(response, dict) or "choices" not in response:
            classified, _is_err = _classify_lemonade_response(
                response if isinstance(response, dict) else {}
            )
            if classified is not None:
                logger.warning(
                    "Lemonade error: type=%s payload=%r",
                    type(classified).__name__,
                    response,
                )
                raise classified
            # Truly unrecognised shape (no error envelope, no choices) —
            # last-resort generic.
            logger.warning("Unexpected Lemonade response: %r", response)
            raise LemonadeError(
                user_message=(
                    "The local LLM returned an unexpected response. "
                    "Try the message again."
                ),
                payload=response if isinstance(response, dict) else {"raw": response},
            )

        if not response["choices"] or len(response["choices"]) == 0:
            raise ValueError("Empty choices in response from Lemonade Server")

        choice = response["choices"][0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")
        tool_calls = message.get("tool_calls")

        if tool_calls:
            logger.debug(
                "tool_call_path=native model_id=%s tool_calling_flag=%s "
                "finish_reason=%s n_tool_calls=%d",
                effective_model,
                tool_capable,
                finish_reason,
                len(tool_calls),
            )
            # Some tool-calling models (e.g. Gemma-4-E4B) emit assistant text
            # alongside tool_calls in the same response. Surface that text in
            # the envelope so the agent loop can attach it to the assistant
            # message and not silently drop it. Per OpenAI spec, ``content``
            # may be null when only tool_calls are emitted — pass it through
            # unchanged so callers can distinguish "no content" from "empty
            # string content".
            tc_content = message.get("content")
            if tc_content is None:
                # Some llama.cpp builds put text in ``reasoning_content``
                # instead of ``content`` when the model emits a thought
                # before a tool call. Treat that as content too.
                tc_content = message.get("reasoning_content")
            # Encode as JSON string so callers can keep treating responses as str.
            return json.dumps(
                {
                    _NATIVE_TC_KEY: tool_calls,
                    "finish_reason": finish_reason,
                    "content": tc_content,
                }
            )

        content = message.get("content") or message.get("reasoning_content") or ""
        logger.debug(
            "tool_call_path=%s model_id=%s tool_calling_flag=%s finish_reason=%s",
            "plain_text",
            effective_model,
            tool_capable,
            finish_reason,
        )
        return content

    def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        response = self._backend.embeddings(texts, **kwargs)
        return [item["embedding"] for item in response["data"]]

    def vision(self, images: list[bytes], prompt: str, **kwargs) -> str:
        # Delegate to VLMClient
        from ..vlm_client import VLMClient

        vlm = VLMClient(base_url=self._backend.base_url)
        return vlm.extract_from_image(images[0], prompt=prompt)

    def get_performance_stats(self) -> dict:
        return self._backend.get_stats() or {}

    def load_model(self, model_name: str, **kwargs) -> None:
        self._backend.load_model(model_name, **kwargs)
        self._model = model_name

    def unload_model(self) -> None:
        self._backend.unload_model()

    def _extract_text(self, response: dict) -> str:
        return response["choices"][0]["text"]

    def _handle_stream(self, response) -> Iterator[str]:
        in_thinking = False
        for chunk in response:
            if "choices" in chunk and chunk["choices"]:
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content")
                if content:
                    # Close thinking block before yielding actual content
                    if in_thinking:
                        yield "</think>"
                        in_thinking = False
                    yield content
                else:
                    # Thinking models (e.g. Qwen3.5) stream reasoning in a
                    # separate field. Wrap in <think> tags so the UI can
                    # display it in a collapsible section.
                    reasoning = delta.get("reasoning_content")
                    if reasoning:
                        if not in_thinking:
                            yield "<think>"
                            in_thinking = True
                        yield reasoning
                    elif "text" in chunk["choices"][0]:
                        text = chunk["choices"][0]["text"]
                        if text:
                            if in_thinking:
                                yield "</think>"
                                in_thinking = False
                            yield text
        # Close any unclosed thinking block at end of stream
        if in_thinking:
            yield "</think>"
