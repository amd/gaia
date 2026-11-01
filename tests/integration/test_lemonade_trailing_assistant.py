# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Opt-in live proof for the trailing-assistant request normalization."""

import os

import pytest

from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME, LemonadeClient

pytestmark = [pytest.mark.integration, pytest.mark.real_model]


def test_thinking_model_accepts_normalized_trailing_assistant_request(
    require_lemonade,
):
    """A real thinking model must accept the non-assistant request suffix."""
    if os.environ.get("GAIA_RUN_TRAILING_ASSISTANT_LIVE") != "1":
        pytest.skip("Opt in with GAIA_RUN_TRAILING_ASSISTANT_LIVE=1")

    client = LemonadeClient(verbose=False)
    catalog = client.list_models(show_all=True).get("data", [])
    model = next(
        (entry for entry in catalog if entry.get("id") == LARGE_DEFAULT_MODEL_NAME),
        None,
    )
    if not model or not model.get("downloaded"):
        pytest.skip(f"{LARGE_DEFAULT_MODEL_NAME} is not downloaded on this server.")

    response = client.chat_completions(
        model=LARGE_DEFAULT_MODEL_NAME,
        messages=[
            {"role": "user", "content": "Answer with one short sentence."},
            {"role": "assistant", "content": "The first partial answer"},
            {"role": "assistant", "content": "and its recovery continuation."},
        ],
        auto_download=False,
        max_completion_tokens=64,
    )

    assert response["choices"][0]["message"]["role"] == "assistant"
