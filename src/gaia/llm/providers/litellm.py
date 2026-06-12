# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""LiteLLM provider - unified gateway for 100+ LLM providers."""

from typing import Iterator, Optional, Union

from ..base_client import LLMClient


class LiteLLMProvider(LLMClient):
    """LiteLLM AI gateway provider."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o",
        system_prompt: Optional[str] = None,
        **kwargs,
    ):
        try:
            import litellm  # noqa: F401  -- validate the optional dependency at construction
        except ImportError as e:
            raise ImportError(
                "litellm is not installed. Install it with: pip install gaia[litellm]"
            ) from e

        self._model = model
        self._system_prompt = system_prompt
        self._api_key = api_key
        self._extra_kwargs = kwargs

    @property
    def provider_name(self) -> str:
        return "LiteLLM"

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
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        import litellm

        if self._system_prompt:
            messages = [{"role": "system", "content": self._system_prompt}] + list(
                messages
            )

        call_kwargs = {**self._extra_kwargs, **kwargs}
        call_kwargs.setdefault("drop_params", True)
        if self._api_key:
            call_kwargs["api_key"] = self._api_key

        response = litellm.completion(
            model=model or self._model,
            messages=messages,
            stream=stream,
            **call_kwargs,
        )
        if stream:
            return self._handle_stream(response)
        return response.choices[0].message.content

    def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        import litellm

        model = kwargs.pop("model", self._model)
        call_kwargs = {**self._extra_kwargs, **kwargs}
        call_kwargs.setdefault("drop_params", True)
        if self._api_key:
            call_kwargs["api_key"] = self._api_key

        response = litellm.embedding(
            model=model,
            input=texts,
            **call_kwargs,
        )
        return [item["embedding"] for item in response.data]

    def _handle_stream(self, response) -> Iterator[str]:
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
