# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Test to verify context_size is returned in health endpoint."""

import os

import pytest
import requests


def _get_context_size_from_health(health_data: dict) -> int:
    """Extract context size from health endpoint response.

    Supports both old format (context_size at root) and new format
    (ctx_size in all_models_loaded[].recipe_options).
    """
    # New format (9.1.4+): all_models_loaded[].recipe_options.ctx_size
    all_models = health_data.get("all_models_loaded", [])
    for model in all_models:
        if model.get("type") == "llm":
            recipe_options = model.get("recipe_options", {})
            ctx_size = recipe_options.get("ctx_size")
            if ctx_size is not None:
                return ctx_size

    # Old format: context_size at root level
    return health_data.get("context_size", 0)


def test_health_endpoint_returns_context_size():
    """Verify that the health endpoint returns context_size field."""
    port = os.environ.get("LEMONADE_PORT", "8000")
    url = f"http://localhost:{port}/api/v1/health"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        health_data = response.json()

        print(f"\nHealth endpoint response: {health_data}")

        # Extract context_size using the helper (handles both old and new formats)
        context_size = _get_context_size_from_health(health_data)
        print(f"Context size: {context_size}")

        assert context_size > 0, (
            f"context_size should be > 0, got {context_size}. "
            f"Health response: {health_data}"
        )

        # If we started with 32768, verify it
        expected_ctx = int(os.environ.get("EXPECTED_CTX_SIZE", "32768"))
        assert (
            context_size >= expected_ctx
        ), f"context_size {context_size} is less than expected {expected_ctx}"

    except requests.exceptions.ConnectionError:
        pytest.skip("Lemonade server not running")


if __name__ == "__main__":
    test_health_endpoint_returns_context_size()
