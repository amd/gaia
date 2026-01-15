# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""LLM client exceptions."""


class NotSupportedError(Exception):
    """Raised when a provider doesn't support a method."""

    def __init__(self, provider: str, method: str):
        self.provider = provider
        self.method = method
        super().__init__(f"{provider} does not support {method}")
