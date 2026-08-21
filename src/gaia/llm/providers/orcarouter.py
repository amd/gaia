# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""OrcaRouter provider - OpenAI-compatible gateway for routing LLM calls.

OrcaRouter is a gateway endpoint (``https://api.orcarouter.ai/v1``) that
accepts OpenAI-compatible chat completions and routes each request to a
model. Like the LiteLLM provider, it is a named gateway-style backend
registered in the LLM client factory.
"""

import os
from typing import Optional

from ..exceptions import NotSupportedError
from .openai_provider import OpenAIProvider

#: OrcaRouter gateway base URL (mirrors the official OpenAI-compatible path).
ORCAROUTER_DEFAULT_BASE_URL = "https://api.orcarouter.ai/v1"

#: Default model id routed through the gateway.
ORCAROUTER_DEFAULT_MODEL = "orcarouter/auto"


class OrcaRouterProvider(OpenAIProvider):
    """OrcaRouter gateway provider (OpenAI-compatible chat completions).

    Configurable via ``ORCAROUTER_API_KEY`` / ``ORCAROUTER_BASE_URL`` /
    ``ORCAROUTER_MODEL`` environment variables or explicit constructor
    arguments. Chat/generate/stream behavior is inherited from
    ``OpenAIProvider``; embeddings are not supported by the gateway.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        **_kwargs,
    ):  # pylint: disable=super-init-not-called
        # Deliberately replaces OpenAIProvider.__init__: the base class has no
        # base_url parameter, which OrcaRouter requires for the gateway endpoint.
        import openai

        self._client = openai.OpenAI(
            api_key=api_key or os.getenv("ORCAROUTER_API_KEY"),
            base_url=base_url
            or os.getenv("ORCAROUTER_BASE_URL", ORCAROUTER_DEFAULT_BASE_URL),
        )
        self._model = model or os.getenv("ORCAROUTER_MODEL", ORCAROUTER_DEFAULT_MODEL)
        self._system_prompt = system_prompt

    @property
    def provider_name(self) -> str:
        return "OrcaRouter"

    def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        # OrcaRouter is a chat-completions gateway; it does not expose an
        # embeddings endpoint.
        raise NotSupportedError(self.provider_name, "embed")
