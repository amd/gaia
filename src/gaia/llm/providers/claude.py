# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Claude provider - no embeddings support."""

from typing import Iterator, Optional, Union

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore

from ..base_client import LLMClient


class ClaudeProvider(LLMClient):
    """Claude (Anthropic) provider."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        system_prompt: Optional[str] = None,
        **_kwargs,
    ):
        if anthropic is None:
            raise ImportError(
                "anthropic package is required for ClaudeProvider. "
                "Install it with: pip install anthropic"
            )

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._system_prompt = system_prompt

    @property
    def provider_name(self) -> str:
        return "Claude"

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
        # Build parameters for Anthropic messages.create
        params = {
            "model": model or self._model,
            "messages": messages,
            "stream": stream,
            **kwargs,
        }
        # Claude API requires system prompt as separate parameter, not in messages
        if self._system_prompt:
            params["system"] = self._system_prompt

        response = self._client.messages.create(**params)
        if stream:
            return self._handle_stream(response)
        return response.content[0].text

    # embed() inherited from ABC - raises NotSupportedError

    def vision(self, images: list[bytes], prompt: str, **kwargs) -> str:
        import base64

        # Claude supports vision via messages
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
        return self.chat(messages, **kwargs)

    # get_performance_stats() inherited from ABC - raises NotSupportedError
    # load_model() inherited from ABC - raises NotSupportedError
    # unload_model() inherited from ABC - raises NotSupportedError

    def _handle_stream(self, response) -> Iterator[str]:
        for chunk in response:
            if hasattr(chunk, "delta") and hasattr(chunk.delta, "text"):
                yield chunk.delta.text
