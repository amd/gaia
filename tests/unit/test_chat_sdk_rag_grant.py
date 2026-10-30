# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document handed to ``AgentSDK.enable_rag`` / ``add_document`` is readable from any cwd.

The RAG SDK's path validator defaults to the working directory, so a file the
caller named outside it was refused and ``enable_rag`` quietly returned False.
"""

from __future__ import annotations

import logging

import pytest

from gaia.chat.sdk import AgentSDK
from gaia.security import PathValidator


class _FakeRag:
    """Indexes only what its validator allows, like ``RAGSDK._safe_open``."""

    def __init__(self, config=None, path_validator=None):
        self.path_validator = path_validator or PathValidator(
            getattr(config, "allowed_paths", None)
        )
        self.indexed = []

    def index_document(self, path):
        if not self.path_validator.is_path_allowed(path, prompt_user=False):
            return {"success": False, "error": f"Access denied: {path}"}
        self.indexed.append(path)
        return {"success": True}


@pytest.fixture
def layout(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "budget.md"
    doc.write_text("Total approved budget: $4.2M", encoding="utf-8")
    (docs / "other.md").write_text("not named", encoding="utf-8")
    monkeypatch.chdir(cwd)
    return docs, doc


def _sdk():
    sdk = AgentSDK.__new__(AgentSDK)
    sdk.log = logging.getLogger("test_chat_sdk_rag_grant")
    return sdk


def test_enable_rag_indexes_a_named_file_outside_cwd(layout, monkeypatch):
    _, doc = layout
    monkeypatch.setattr("gaia.rag.sdk.RAGSDK", _FakeRag)
    sdk = _sdk()
    sdk.config = type(
        "C", (), {"model": "m", "show_stats": False, "use_local_llm": True}
    )

    assert sdk.enable_rag(documents=[str(doc)]) is True
    assert sdk.rag.indexed == [str(doc)]


def test_add_document_grants_the_file_not_its_folder(layout):
    docs, doc = layout
    sdk = _sdk()
    sdk.rag = _FakeRag()
    sdk.rag_enabled = True

    assert sdk.add_document(str(doc)) is True
    assert not sdk.rag.path_validator.is_path_allowed(
        str(docs / "other.md"), prompt_user=False
    )


def test_missing_file_is_not_granted(layout):
    docs, _ = layout
    sdk = _sdk()
    sdk.rag = _FakeRag()
    sdk.rag_enabled = True

    assert sdk.add_document(str(docs / "missing.md")) is False
