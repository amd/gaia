# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Lemonade's ``/tokenize`` as the Context & performance panel calls it.

Read-only against a running server: it tokenizes, never loads or generates,
and checks the loaded model is the same afterwards.
"""

import pytest

from gaia.ui import turn_insights


def test_tokenize_counts_with_the_loaded_model_and_loads_nothing(require_lemonade):
    base_url, headers = turn_insights._lemonade()
    before = turn_insights._loaded_chat_model(base_url, headers)
    if before is None:
        pytest.skip("No chat model is loaded; this test never loads one.")

    counter = turn_insights.ModelTokenCounter(base_url, headers)
    try:
        short = counter("hello world")
        longer = counter("hello world " * 200)
    finally:
        counter.close()

    assert 0 < short < longer
    assert turn_insights._loaded_chat_model(base_url, headers) == before
