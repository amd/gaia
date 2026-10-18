# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``create_lemonade_client(auto_load=True)`` fails loudly: a model that could
not be downloaded is not then loaded anyway, and a load is not cut off early.
"""

from unittest.mock import patch

import pytest

from gaia.llm import lemonade_client as lc

MODEL = "Gemma-4-E4B-it-GGUF"


def test_failed_pull_is_not_followed_by_a_load():
    with (
        patch.object(lc.LemonadeClient, "list_models", return_value={"data": []}),
        patch.object(
            lc.LemonadeClient,
            "pull_model",
            side_effect=lc.LemonadeClientError("HTTP 404: no such checkpoint"),
        ),
        patch.object(lc.LemonadeClient, "load_model") as load,
        patch.object(lc.LemonadeClient, "_is_cloud_model", return_value=False),
    ):
        with pytest.raises(lc.LemonadeClientError, match="could not download it"):
            lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    load.assert_not_called()


def test_auto_load_uses_the_full_load_timeout():
    """A load that queues behind another app's must not be cut off at 60 s."""
    with (
        patch.object(
            lc.LemonadeClient, "list_models", return_value={"data": [{"id": MODEL}]}
        ),
        patch.object(lc.LemonadeClient, "load_model") as load,
        patch.object(lc.LemonadeClient, "_is_cloud_model", return_value=False),
    ):
        lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    load.assert_called_once_with(MODEL)
