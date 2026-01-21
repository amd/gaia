# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Test to verify context_size is returned in health endpoint."""

import requests
import pytest
import os


def test_health_endpoint_returns_context_size():
    """Verify that the health endpoint returns context_size field."""
    port = os.environ.get("LEMONADE_PORT", "8000")
    url = f"http://localhost:{port}/api/v1/health"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        health_data = response.json()

        print(f"\nHealth endpoint response: {health_data}")

        # Check that context_size is present and non-zero
        assert "context_size" in health_data, (
            f"context_size not in health response. "
            f"Got keys: {list(health_data.keys())}"
        )

        context_size = health_data["context_size"]
        print(f"Context size: {context_size}")

        assert context_size > 0, (
            f"context_size should be > 0, got {context_size}"
        )

        # If we started with 32768, verify it
        expected_ctx = int(os.environ.get("EXPECTED_CTX_SIZE", "32768"))
        assert context_size >= expected_ctx, (
            f"context_size {context_size} is less than expected {expected_ctx}"
        )

    except requests.exceptions.ConnectionError:
        pytest.skip("Lemonade server not running")


if __name__ == "__main__":
    test_health_endpoint_returns_context_size()
