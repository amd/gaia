# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Indexing a folder loads the embedder once, not once per file.

In v0.26.0-rc18 each file of a folder index built its own ``RAGSDK``, and
each one unloaded and reloaded the embedder: 2.9 s of loading against
0.07 s of embedding per file, so 300 small files took 15 minutes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gaia.rag.sdk as rag_sdk
import gaia.ui._chat_helpers as helpers
from gaia.ui.server import create_app


class _FakeRAG:
    """Loads its embedder on first use, as ``RAGSDK._load_embedder`` does."""

    loads = 0
    vlm_pages = 0

    def __init__(self, config=None):
        self.config = config
        self.embedder = None
        self.llm_client = None
        self.use_lemonade_embeddings = False

    def index_document(self, path, **kwargs):
        if self.embedder is None:
            type(self).loads += 1
            self.llm_client = self.embedder = object()
            self.use_lemonade_embeddings = True
        return {"success": True, "num_chunks": 1, "vlm_pages": type(self).vlm_pages}


@pytest.fixture
def fake_rag(monkeypatch):
    _FakeRAG.loads = 0
    _FakeRAG.vlm_pages = 0
    monkeypatch.setattr(rag_sdk, "RAGSDK", _FakeRAG)
    return _FakeRAG


def _files(folder, count):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        path = folder / f"station_{i:03d}.txt"
        path.write_text(f"Station {i} logs {i * 7} litres.", encoding="utf-8")
        paths.append(path)
    return paths


async def _index_all(paths, batch: bool):
    if batch:
        with helpers.one_embedder_load():
            return [await helpers._index_document(p) for p in paths]
    return [await helpers._index_document(p) for p in paths]


def test_a_batch_loads_the_embedder_once(fake_rag, tmp_path):
    paths = _files(tmp_path / "small", 5)

    assert asyncio.run(_index_all(paths, batch=True)) == [1] * 5
    assert fake_rag.loads == 1


def test_a_single_document_still_loads_its_own(fake_rag, tmp_path):
    paths = _files(tmp_path / "small", 3)

    asyncio.run(_index_all(paths, batch=False))

    assert fake_rag.loads == 3


def test_a_file_read_with_the_vision_model_reloads_the_embedder_after_it(
    fake_rag, tmp_path
):
    """A vision model may have pushed the embedder out; the next file reloads it."""
    fake_rag.vlm_pages = 2
    paths = _files(tmp_path / "small", 3)

    asyncio.run(_index_all(paths, batch=True))

    assert fake_rag.loads == 3


def test_index_folder_indexes_on_one_embedder_load(fake_rag, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    folder = home / "lib"
    _files(folder, 6)
    client = TestClient(create_app(db_path=":memory:"))

    resp = client.post("/api/documents/index-folder", json={"folder_path": str(folder)})

    assert resp.status_code == 200, resp.text
    assert resp.json()["indexed"] == 6
    assert fake_rag.loads == 1
