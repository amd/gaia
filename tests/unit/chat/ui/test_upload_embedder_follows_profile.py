# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An Agent UI upload indexes with the embedder chat searches with (#6008).

Vectors from another embedder sit under a different hash than chat's, so the
document would be embedded a second time on the first question about it.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import gaia.rag.sdk as rag_sdk
import gaia.ui._chat_helpers as helpers
from gaia import config as config_mod
from gaia.config import GaiaConfigError

FLM = "embed-gemma-300m-FLM"
GGUF = "user.embeddinggemma-300m-GGUF"


class _FakeRAG:
    configs: list = []

    def __init__(self, config=None):
        type(self).configs.append(config)
        self.config = config
        self.embedder = None
        self.llm_client = None
        self.use_lemonade_embeddings = False

    def index_document(self, path, **kwargs):
        self.llm_client = self.embedder = object()
        return {"success": True, "num_chunks": 1}


@pytest.fixture
def fake_rag(monkeypatch):
    _FakeRAG.configs = []
    monkeypatch.setattr(rag_sdk, "RAGSDK", _FakeRAG)
    return _FakeRAG


def _pc(monkeypatch, tmp_path, text):
    config_file = tmp_path / "config.json"
    if text is not None:
        config_file.write_text(text, encoding="utf-8")
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)


def _doc(tmp_path, name="notes.txt"):
    path = tmp_path / name
    path.write_text("The pump station logs 42 litres.", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "text, embedder",
    [
        # The NPU build's search isn't proven: an NPU PC embeds on the GPU.
        (json.dumps({"default_device": "npu"}), GGUF),
        # A saved choice from before it was withdrawn still loads.
        (json.dumps({"default_device": "npu", "embedding_device": "npu"}), FLM),
        (json.dumps({"default_device": "gpu"}), GGUF),
        (json.dumps({"default_device": "cpu"}), GGUF),
        (json.dumps({"profile": "gaia"}), GGUF),
        (None, GGUF),  # a fresh install with no config yet
    ],
)
def test_an_upload_uses_the_pcs_embedder(
    fake_rag, monkeypatch, tmp_path, text, embedder
):
    _pc(monkeypatch, tmp_path, text)

    assert asyncio.run(helpers._index_document(_doc(tmp_path))) == 1
    assert [c.embedding_model for c in fake_rag.configs] == [embedder]


def test_every_file_of_a_folder_uses_the_pcs_embedder(fake_rag, monkeypatch, tmp_path):
    _pc(monkeypatch, tmp_path, json.dumps({"embedding_device": "npu"}))
    paths = [_doc(tmp_path, f"f{i}.txt") for i in range(3)]

    async def _all():
        with helpers.one_embedder_load():
            return [await helpers._index_document(p) for p in paths]

    assert asyncio.run(_all()) == [1, 1, 1]
    assert {c.embedding_model for c in fake_rag.configs} == {FLM}


def test_an_unreadable_config_fails_the_upload_instead_of_guessing(
    fake_rag, monkeypatch, tmp_path
):
    """A guessed embedder would index under the wrong vectors."""
    _pc(monkeypatch, tmp_path, "{not json")

    with pytest.raises(RuntimeError, match="embedder this PC uses") as exc:
        asyncio.run(helpers._index_document(_doc(tmp_path)))
    assert isinstance(exc.value.__cause__, GaiaConfigError)
    assert fake_rag.configs == []
