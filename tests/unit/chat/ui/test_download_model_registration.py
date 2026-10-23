# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Downloading a model from Settings registers a custom model on its first pull.

Lemonade lists a ``user.`` model only once it is registered, and registers it
only from a pull that carries its checkpoint and recipe. A pull by name alone
404s for such a model; a pull that sends a recipe for a built-in 400s (#1655).
"""

import asyncio

import httpx
import pytest

from gaia.llm import lemonade_client as lc
from gaia.ui.routers import system


class _Resp:
    status_code = 500

    async def aread(self):
        return b"stop here"


class _Stream:
    async def __aenter__(self):
        return _Resp()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def sent(monkeypatch):
    calls = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, json=None, headers=None):
            calls.append(json)
            return _Stream()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return calls


def test_custom_model_pull_carries_its_registration(sent):
    asyncio.run(system._stream_lemonade_pull(lc.GPT_OSS_20B_MODEL_NAME, False))
    assert sent == [
        {
            "model_name": "user.ggml-gpt-oss-20b-MXFP4-GGUF",
            "stream": True,
            "checkpoint": "ggml-org/gpt-oss-20b-GGUF:gpt-oss-20b-MXFP4.gguf",
            "recipe": "llamacpp",
            "reasoning": True,
        }
    ]


def test_listed_name_without_the_namespace_still_registers_under_it(sent):
    """Lemonade lists a registered user model without ``user.``."""
    asyncio.run(system._stream_lemonade_pull("ggml-gpt-oss-20b-MXFP4-GGUF", False))
    assert sent[0]["model_name"] == "user.ggml-gpt-oss-20b-MXFP4-GGUF"
    assert sent[0]["checkpoint"]


@pytest.mark.parametrize(
    "model", ["gpt-oss-120b-mxfp-GGUF", lc.DEFAULT_MODEL_NAME, "Some-Unknown-GGUF"]
)
def test_builtin_and_unknown_models_pull_by_name_only(sent, model):
    asyncio.run(system._stream_lemonade_pull(model, True))
    assert sent == [{"model_name": model, "stream": True, "force": True}]


def test_progress_follows_the_model_once_lemonade_drops_the_user_prefix(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress(
        lc.GPT_OSS_20B_MODEL_NAME, {"state": "downloading", "percent": 40}
    )
    listed = lc.GPT_OSS_20B_MODEL_NAME[len("user.") :]
    assert system._get_download_progress(listed)["percent"] == 40
    assert system._get_download_progress(lc.GPT_OSS_20B_MODEL_NAME)["percent"] == 40
    assert system._get_download_progress("gpt-oss-20b-mxfp4-GGUF") is None
