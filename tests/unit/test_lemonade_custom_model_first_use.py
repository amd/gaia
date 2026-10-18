# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The first chat on a custom model that is not downloaded yet registers and pulls it.

A ``user.`` model exists on a Lemonade only once a pull registered it with its
checkpoint and recipe. Before that, ``/load`` answers 404 ``model_not_found``.
GAIA's auto-download must recognise that answer and pull the model WITH its
registration, or the user who picks it before running ``gaia init`` gets a
dead chat ("Failed to preload ... was not found").
"""

from unittest.mock import patch

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

#: The 404 Lemonade v2026.40.0 returns for a model it has never registered,
#: as LemonadeClient surfaces it (recorded on hardware).
NOT_REGISTERED = LemonadeClientError(
    "Request failed with status 404: "
    '{"error":{"code":"model_not_found","message":"Model '
    "'user.Qwen3.8-Flash-Next-GGUF' was not found. Available models include: "
    "'ACE-Step-Music', 'AMD-OLMo-1B-SFT-DPO-Hybrid', 'Bert-Phishing-ONNX', and "
    "207 more. Use 'lemonade list' to see all models.\","
    '"param":"model","type":"invalid_request_error"}}'
)


@pytest.fixture
def client():
    return LemonadeClient(host="127.0.0.1", port=18767, verbose=False)


def test_current_not_found_reply_is_recognised(client):
    assert client._is_model_error(NOT_REGISTERED)


@pytest.mark.parametrize(
    "error",
    [
        LemonadeClientError("Request failed with status 500: boom"),
        LemonadeClientError(
            'Request failed with status 400: {"error":{"code":"bad_request",'
            '"message":"ctx_size must be positive","type":"invalid_request_error"}}'
        ),
    ],
)
def test_other_failures_are_not_mistaken_for_a_missing_model(client, error):
    assert not client._is_model_error(error)


def _first_use(client, model):
    """Load *model* on a server that has never seen it; return what was pulled."""
    pulled = []

    def _pull(**kwargs):
        pulled.append(kwargs)
        yield {"event": "complete"}

    loads = [NOT_REGISTERED, {"status": "success"}]

    def _load(*_args, **_kwargs):
        step = loads.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    with (
        patch.object(client, "_post_load_with_transient_retry", side_effect=_load),
        patch.object(client, "pull_model_stream", side_effect=_pull),
        patch.object(
            client,
            "get_model_info",
            return_value={"id": model, "size_gb": 1.0, "downloaded": False},
        ),
        patch.object(
            client, "_model_storage_free_bytes", return_value=(10**13, "C:\\models")
        ),
        patch.object(client, "list_models", return_value={"data": []}),
        patch.object(lc, "fit_ctx_to_free_memory", side_effect=lambda _m, ctx, *_: ctx),
    ):
        client.load_model(model, ctx_size=65536, prompt=False, auto_download=True)
    assert not loads, "the load was not retried after the download"
    return pulled


def test_custom_model_is_pulled_with_its_registration(client):
    mr = lc.find_model_requirement(lc.FLASH_OPTION_MODEL_NAME)
    pulled = _first_use(client, lc.FLASH_OPTION_MODEL_NAME)
    assert pulled == [
        {
            "model_name": "user.Qwen3.8-Flash-Next-GGUF",
            "checkpoint": mr.checkpoint,
            "recipe": "llamacpp",
            "mmproj": mr.mmproj,
            "vision": True,
            "reasoning": True,
        }
    ]


def test_builtin_model_is_pulled_by_name_only(client):
    """Sending a recipe for a built-in makes Lemonade 400 (#1655)."""
    assert _first_use(client, lc.DEFAULT_MODEL_NAME) == [
        {"model_name": lc.DEFAULT_MODEL_NAME}
    ]
