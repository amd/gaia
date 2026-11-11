# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document named with ``gaia chat --index`` is readable from any working directory.

Startup documents are indexed through the agent's path validator, whose default
scope is the working directory. A file outside it was refused, the failure was
only logged, and the agent answered without the document.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("gaia_agent")

from gaia_agent.chat_agent import ChatAgent  # noqa: E402

from gaia.security import PathValidator  # noqa: E402


class _Rag:
    """Indexes only what the shared validator allows, like ``RAGSDK._safe_open``."""

    def __init__(self, validator):
        self.validator = validator
        self.indexed = []

    def index_document(self, path):
        if not self.validator.is_path_allowed(path, prompt_user=False):
            raise PermissionError(f"Access denied: {path} is not in allowed paths")
        self.indexed.append(path)
        return {"success": True, "num_chunks": 1}


def _agent(scope):
    validator = PathValidator([str(scope)])
    return SimpleNamespace(
        path_validator=validator,
        rag=_Rag(validator),
        indexed_files=set(),
        rebuild_system_prompt=lambda: None,
    )


def test_named_document_outside_cwd_is_indexed(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "report.md"
    doc.write_text("Q3 revenue was $14.2M.", encoding="utf-8")
    agent = _agent(cwd)

    ChatAgent._index_documents(agent, [str(doc)])

    assert agent.rag.indexed == [str(doc)]
    assert str(doc) in agent.indexed_files


def test_grant_covers_the_file_not_its_folder(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "report.md"
    doc.write_text("named", encoding="utf-8")
    sibling = docs / "secret.md"
    sibling.write_text("not named", encoding="utf-8")
    agent = _agent(cwd)

    ChatAgent._index_documents(agent, [str(doc)])

    assert agent.path_validator.is_path_allowed(str(doc), prompt_user=False)
    assert not agent.path_validator.is_path_allowed(str(sibling), prompt_user=False)


def test_missing_document_grants_nothing(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    missing = tmp_path / "nowhere" / "gone.md"
    agent = _agent(cwd)

    ChatAgent._index_documents(agent, [str(missing)])

    assert agent.rag.indexed == []
    assert not agent.path_validator.is_path_allowed(str(missing), prompt_user=False)


def test_named_folder_is_not_granted(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    sibling = docs / "secret.md"
    sibling.write_text("not named", encoding="utf-8")
    agent = _agent(cwd)

    ChatAgent._index_documents(agent, [str(docs)])

    assert agent.rag.indexed == []
    assert not agent.path_validator.is_path_allowed(str(docs), prompt_user=False)
    assert not agent.path_validator.is_path_allowed(str(sibling), prompt_user=False)


def test_folder_before_file_does_not_widen_the_file_grant(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "report.md"
    doc.write_text("named", encoding="utf-8")
    sibling = docs / "secret.md"
    sibling.write_text("not named", encoding="utf-8")
    agent = _agent(cwd)

    ChatAgent._index_documents(agent, [str(docs), str(doc)])

    assert agent.rag.indexed == [str(doc)]
    assert not agent.path_validator.is_path_allowed(str(sibling), prompt_user=False)
