# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lemonade provider - supports ALL methods."""

from typing import Iterator, Optional, Union

from ..base_client import LLMClient
from ..lemonade_client import DEFAULT_MODEL_NAME, LemonadeClient


class LemonadeProvider(LLMClient):
    """Lemonade provider - local AMD-optimized inference."""

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        system_prompt: Optional[str] = None,
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
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        # Use provided model, instance model, or default CPU model
        effective_model = model or self._model or DEFAULT_MODEL_NAME

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

        response = self._backend.chat_completions(
            model=effective_model, messages=messages, stream=stream, **kwargs
        )
        if stream:
            return self._handle_stream(response)

        # Handle error responses gracefully
        if not isinstance(response, dict) or "choices" not in response:
            error_msg = f"Unexpected response format from Lemonade Server: {response}"
            raise ValueError(error_msg)

        if not response["choices"] or len(response["choices"]) == 0:
            raise ValueError("Empty choices in response from Lemonade Server")

        content = response["choices"][0]["message"]["content"]
        if not content:
            content = response["choices"][0]["message"].get("reasoning_content", "")
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
