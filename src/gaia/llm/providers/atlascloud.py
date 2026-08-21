# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Atlas Cloud provider using its OpenAI-compatible LLM API."""

import os
from typing import Optional

from ..exceptions import NotSupportedError
from .openai_provider import OpenAIProvider

DEFAULT_ATLASCLOUD_BASE_URL = "https://api.atlascloud.ai/v1"
DEFAULT_ATLASCLOUD_MODEL = "deepseek-ai/deepseek-v3.2"


class AtlasCloudProvider(OpenAIProvider):
    """Atlas Cloud chat-completions provider."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_ATLASCLOUD_MODEL,
        base_url: str = DEFAULT_ATLASCLOUD_BASE_URL,
        system_prompt: Optional[str] = None,
        **client_kwargs,
    ):  # pylint: disable=super-init-not-called
        resolved_api_key = api_key or os.getenv("ATLASCLOUD_API_KEY")
        if not resolved_api_key:
            raise ValueError(
                "Atlas Cloud API key is required. Set ATLASCLOUD_API_KEY or pass "
                "api_key."
            )

        import openai

        self._client = openai.OpenAI(
            api_key=resolved_api_key,
            base_url=base_url.rstrip("/"),
            **client_kwargs,
        )
        self._model = model
        self._system_prompt = system_prompt

    @property
    def provider_name(self) -> str:
        return "Atlas Cloud"

    def embed(
        self,
        texts: list[str],
        model: str = "text-embedding-3-small",
        **kwargs,
    ) -> list[list[float]]:
        raise NotSupportedError(self.provider_name, "embed")
