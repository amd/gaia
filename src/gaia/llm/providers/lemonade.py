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

        response = self._backend.chat_completions(
            model=effective_model, messages=messages, stream=stream, **kwargs
        )
        if stream:
            return self._handle_stream(response)
        return response["choices"][0]["message"]["content"]

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
        for chunk in response:
            if "choices" in chunk and chunk["choices"]:
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield content
                elif "text" in chunk["choices"][0]:
                    text = chunk["choices"][0]["text"]
                    if text:
                        yield text
