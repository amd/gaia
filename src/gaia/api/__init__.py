# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
GAIA OpenAI-Compatible API

This module provides an OpenAI-compatible REST API for GAIA agents.
Agents can be accessed via standard OpenAI client libraries.

Usage:
    # Start server
    gaia api start --port 8080

    # Use with OpenAI client
    from openai import OpenAI
    client = OpenAI(base_url="http://localhost:8080/v1", api_key="dummy")

    response = client.chat.completions.create(
        model="gaia-code-agent",
        messages=[{"role": "user", "content": "Write hello world"}]
    )
"""

__version__ = "1.0.0"
