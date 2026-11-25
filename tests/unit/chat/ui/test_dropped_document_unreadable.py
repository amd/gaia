# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document taken off a relayed chat can no longer be read by the agent (#8374).

The sidecar took the document out of its index but kept the grant it made
when it attached it, so the next ``query_specific_file`` re-indexed it from
disk and answered from it. These run the real index and the real validator.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.security import PathValidator


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _index(tmp_path, cache="cache"):
    """A real index with fake vectors."""
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / cache),
                allowed_paths=[str(tmp_path)],
                use_llm_chunking=False,
            )
        )
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **_: np.ones((len(texts), 4), dtype="float32")
    sdk._encode_query = lambda _q: np.ones((1, 4), dtype="float32")
    sdk._get_hmac_key = lambda: b"test-cache-key" * 3
    return sdk


class _Handler:
    def _emit(self, _event):
        pass

    def print_tool_usage(self, _name):
        pass

    def pretty_print_json(self, *_args, **_kwargs):
        pass


class _Agent(RAGToolsMixin):
    """The flagship's document surface: real index, real validator."""

    def __init__(self, rag, validator):
        self.rag = rag
        self.path_validator = validator
        self.indexed_files = set()
        self.attached_documents = []
        self.current_session = None
        self.max_chunks = 5
        self._cancel_event = None
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query):
        return [query]


@pytest.fixture
def chat(tmp_path):
    """A relayed chat with two documents attached, then one taken off."""
    server = pytest.importorskip("gaiapy.server")
    kept = tmp_path / "docs" / "kept.txt"
    gone = tmp_path / "docs" / "gone.txt"
    kept.parent.mkdir()
    kept.write_text("The kept code is KEPT-1.", encoding="utf-8")
    gone.write_text("The gone code is GONE-2.", encoding="utf-8")
    kept, gone = str(kept.resolve()), str(gone.resolve())
    # The scope the first turn's allowed_paths built the session with.
    validator = PathValidator([kept, gone])
    validator.set_access_prompt(lambda _path: False)
    agent = _Agent(_index(tmp_path), validator)

    server._index_attached_documents(agent, [kept, gone], _Handler())
    assert str(Path(gone).absolute()) in agent.rag.indexed_files
    server._index_attached_documents(agent, [kept], _Handler())
    agent.register_rag_tools()
    return agent, kept, gone


def test_a_dropped_document_leaves_the_index_and_the_grant(chat):
    agent, kept, gone = chat

    assert str(Path(gone).absolute()) not in agent.rag.indexed_files
    assert gone not in agent.indexed_files
    assert not agent.path_validator.is_path_allowed(gone, prompt_user=False)
    assert agent.path_validator.is_path_allowed(kept, prompt_user=False)


def test_asking_for_a_dropped_document_does_not_read_it_back(chat):
    agent, _, gone = chat

    result = _TOOL_REGISTRY["query_specific_file"]["function"](
        file_path=gone, query="gone code"
    )

    assert result["status"] == "error", result
    assert "GONE-2" not in str(result)
    assert str(Path(gone).absolute()) not in agent.rag.indexed_files


def test_the_kept_document_is_still_searched(chat):
    result = _TOOL_REGISTRY["query_specific_file"]["function"](
        file_path=chat[1], query="kept code"
    )

    assert result["status"] == "success", result
    assert "KEPT-1" in " ".join(c["content"] for c in result["chunks"])


# ── GAIA's own copy, other chats, folders, re-adding ─────────────────────


@pytest.fixture
def managed(tmp_path, monkeypatch):
    """GAIA's documents folder, where a document dropped on a chat is copied."""
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    from gaia.ui.utils import managed_documents_dir

    folder = managed_documents_dir()
    folder.mkdir(parents=True)
    return folder.resolve()


def _relayed(tmp_path, folder, scope_extra=()):
    """A relayed chat on documents in *folder*, scoped the way the Agent UI scopes it."""
    server = pytest.importorskip("gaiapy.server")
    from gaia.ui.utils import managed_documents_dir

    folder.mkdir(parents=True, exist_ok=True)
    kept = folder / "kept.txt"
    gone = folder / "gone.txt"
    kept.write_text("The kept code is KEPT-1.", encoding="utf-8")
    gone.write_text("The gone code is GONE-2.", encoding="utf-8")
    kept, gone = str(kept.resolve()), str(gone.resolve())
    validator = PathValidator(
        [kept, gone, str(managed_documents_dir().resolve()), *scope_extra]
    )
    validator.set_access_prompt(lambda _path: False)
    agent = _Agent(_index(tmp_path), validator)
    agent.register_rag_tools()
    server._index_attached_documents(agent, [kept, gone], _Handler())
    return server, agent, kept, gone


def _query(path, query):
    return _TOOL_REGISTRY["query_specific_file"]["function"](
        file_path=path, query=query
    )


def test_gaias_copy_of_a_dropped_document_is_refused_despite_its_folder(
    tmp_path, managed
):
    server, agent, kept, gone = _relayed(tmp_path, managed)

    server._index_attached_documents(agent, [kept], _Handler())

    result = _query(gone, "gone code")
    assert result["status"] == "error", result
    assert "GONE-2" not in str(result)
    assert not agent.path_validator.is_path_allowed(gone, prompt_user=False)
    assert not agent.path_validator.validate_read(gone, prompt_user=False)[0]
    # The rest of the folder stays the chat's to read and write.
    assert agent.path_validator.is_path_allowed(kept, prompt_user=False)
    assert agent.path_validator.is_path_allowed(
        str(managed / "notes.md"), prompt_user=False
    )


def test_a_folder_the_user_granted_still_covers_a_dropped_document(tmp_path):
    folder = tmp_path / "project"
    server, agent, kept, gone = _relayed(tmp_path, folder, [str(folder)])

    server._index_attached_documents(agent, [kept], _Handler())

    assert str(Path(gone).absolute()) not in agent.rag.indexed_files
    assert agent.path_validator.is_path_allowed(gone, prompt_user=False)
    assert "GONE-2" in str(_query(gone, "gone code"))


def test_a_user_folder_inside_gaias_folder_still_covers_a_dropped_copy(
    tmp_path, managed
):
    folder = managed / "mine"
    server, agent, kept, gone = _relayed(tmp_path, folder, [str(folder)])

    server._index_attached_documents(agent, [kept], _Handler())

    assert agent.path_validator.is_path_allowed(gone, prompt_user=False)


def test_another_chat_with_the_same_document_still_reads_it(tmp_path, managed):
    server, first, kept, gone = _relayed(tmp_path, managed)
    second_validator = PathValidator([kept, gone, str(managed)])
    second_validator.set_access_prompt(lambda _path: False)
    second = _Agent(_index(tmp_path, "second"), second_validator)
    server._index_attached_documents(second, [kept, gone], _Handler())

    server._index_attached_documents(first, [kept], _Handler())

    assert not first.path_validator.is_path_allowed(gone, prompt_user=False)
    assert second.path_validator.is_path_allowed(gone, prompt_user=False)
    assert str(Path(gone).absolute()) in second.rag.indexed_files


@pytest.mark.parametrize("in_gaias_folder", [True, False])
def test_attaching_a_dropped_document_again_restores_it(
    tmp_path, managed, in_gaias_folder
):
    folder = managed if in_gaias_folder else tmp_path / "elsewhere"
    server, agent, kept, gone = _relayed(tmp_path, folder)

    server._index_attached_documents(agent, [kept], _Handler())
    server._index_attached_documents(agent, [kept, gone], _Handler())

    assert agent.path_validator.is_path_allowed(gone, prompt_user=False)
    assert "GONE-2" in str(_query(gone, "gone code"))

    # Dropped a second time, it is refused again.
    server._index_attached_documents(agent, [kept], _Handler())
    assert not agent.path_validator.is_path_allowed(gone, prompt_user=False)
    assert "GONE-2" not in str(_query(gone, "gone code"))


@pytest.mark.parametrize("in_gaias_folder", [True, False])
def test_removing_every_document_refuses_them_all(tmp_path, managed, in_gaias_folder):
    """Deleting a document from the library drops it from every turn after."""
    folder = managed if in_gaias_folder else tmp_path / "elsewhere"
    server, agent, kept, gone = _relayed(tmp_path, folder)

    server._index_attached_documents(agent, [], _Handler())

    for path in (kept, gone):
        assert not agent.path_validator.is_path_allowed(path, prompt_user=False)
        assert path not in agent.indexed_files
    assert not agent.rag.indexed_files


def test_an_unusable_gaia_home_does_not_fail_the_drop(tmp_path, monkeypatch):
    """GAIA_HOME at the home folder makes the Agent UI refuse to run, not the drop."""
    folder = tmp_path / "project"
    server, agent, kept, gone = _relayed(tmp_path, folder, [str(folder)])
    monkeypatch.setenv("GAIA_HOME", str(Path.home()))

    server._index_attached_documents(agent, [kept], _Handler())

    assert agent.path_validator.is_path_allowed(gone, prompt_user=False)
    assert str(Path(gone).absolute()) not in agent.rag.indexed_files
