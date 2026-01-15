# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Base LLM client interface."""

from abc import ABC, abstractmethod
from typing import Iterator, Union

from .exceptions import NotSupportedError


class LLMClient(ABC):
    """
    Unified LLM client interface.

    Methods raise NotSupportedError if not available for this provider.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name for error messages."""
        ...

    @abstractmethod
    def generate(
        self,
        prompt: str,
        model: str | None = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        """Generate text completion."""
        ...

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        """Chat completion."""
        ...

    # Optional - default raises NotSupportedError
    def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        raise NotSupportedError(self.provider_name, "embed")

    def vision(self, images: list[bytes], prompt: str, **kwargs) -> str:
        raise NotSupportedError(self.provider_name, "vision")

    def get_performance_stats(self) -> dict:
        raise NotSupportedError(self.provider_name, "get_performance_stats")

    def load_model(self, model_name: str, **kwargs) -> None:
        raise NotSupportedError(self.provider_name, "load_model")

    def unload_model(self) -> None:
        raise NotSupportedError(self.provider_name, "unload_model")
