# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""OpenAI provider - no vision support."""

from typing import Iterator, Optional, Union

from ..base_client import LLMClient


class OpenAIProvider(LLMClient):
    """OpenAI (OpenAI API) provider."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o",
        system_prompt: Optional[str] = None,
        **_kwargs,
    ):
        import openai

        self._client = openai.OpenAI(api_key=api_key)
        self._model = model
        self._system_prompt = system_prompt

    @property
    def provider_name(self) -> str:
        return "OpenAI"

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        # OpenAI doesn't have a separate completions endpoint for chat models
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
        # Prepend system prompt if set
        if self._system_prompt:
            messages = [{"role": "system", "content": self._system_prompt}] + list(
                messages
            )

        response = self._client.chat.completions.create(
            model=model or self._model, messages=messages, stream=stream, **kwargs
        )
        if stream:
            return self._handle_stream(response)
        return response.choices[0].message.content

    def embed(
        self, texts: list[str], model: str = "text-embedding-3-small", **kwargs
    ) -> list[list[float]]:
        response = self._client.embeddings.create(model=model, input=texts, **kwargs)
        return [item.embedding for item in response.data]

    # vision() inherited from ABC - raises NotSupportedError
    # get_performance_stats() inherited from ABC - raises NotSupportedError
    # load_model() inherited from ABC - raises NotSupportedError
    # unload_model() inherited from ABC - raises NotSupportedError

    def _handle_stream(self, response) -> Iterator[str]:
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
