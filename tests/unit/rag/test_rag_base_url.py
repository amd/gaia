# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Indexing talks to the Lemonade the caller named, never the default server."""

import logging
from unittest.mock import patch

import pytest

from gaia.chat.sdk import AgentConfig, AgentSDK
from gaia.rag.sdk import RAGSDK, RAGConfig

URL = "http://127.0.0.1:59344/api/v1"


class _StopAfterClient(Exception):
    """Ends ``_start_embedder`` once the client is built."""


def test_enable_rag_forwards_the_chat_base_url(monkeypatch):
    seen = {}

    class _FakeRag:
        def __init__(self, config=None, **_):
            seen["base_url"] = config.base_url

    monkeypatch.setattr("gaia.rag.sdk.RAGSDK", _FakeRag)
    sdk = AgentSDK.__new__(AgentSDK)
    sdk.log = logging.getLogger("test_rag_base_url")
    sdk.config = AgentConfig(base_url=URL)

    assert sdk.enable_rag() is True
    assert seen["base_url"] == URL


def test_enable_rag_keeps_an_explicit_rag_base_url(monkeypatch):
    seen = {}

    class _FakeRag:
        def __init__(self, config=None, **_):
            seen["base_url"] = config.base_url

    monkeypatch.setattr("gaia.rag.sdk.RAGSDK", _FakeRag)
    sdk = AgentSDK.__new__(AgentSDK)
    sdk.log = logging.getLogger("test_rag_base_url")
    sdk.config = AgentConfig(base_url=URL)

    other = "http://127.0.0.1:1/api/v1"
    assert sdk.enable_rag(base_url=other) is True
    assert seen["base_url"] == other


def test_embedder_client_uses_the_configured_base_url(tmp_path):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        rag = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                base_url=URL,
            )
        )
    rag.llm_client = None

    built = {}

    def _client(*args, **kwargs):
        built.update(kwargs)
        raise _StopAfterClient

    with patch("gaia.llm.lemonade_client.LemonadeClient", _client):
        with pytest.raises(_StopAfterClient):
            rag._start_embedder()

    assert built.get("base_url") == URL
