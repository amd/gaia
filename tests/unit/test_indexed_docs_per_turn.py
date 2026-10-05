"""Indexing a document must not change the system prompt (it would re-read it all)."""

import threading
from unittest.mock import MagicMock

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia.agents.tools import rag_tools  # noqa: E402
from tests.unit.test_profilespec_characterization import (  # noqa: E402
    chat_agent_build_context,
)


@pytest.mark.parametrize("profile", ["full", "doc"])
def test_the_prompt_is_the_same_before_and_after_indexing(profile):
    prompts = []
    for indexed in ([], ["/docs/a.pdf"], ["/docs/a.pdf", "/docs/b.txt"]):
        with chat_agent_build_context(profile, rag_indexed_files=indexed) as agent:
            prompts.append(agent._get_system_prompt())
    assert prompts[0] == prompts[1] == prompts[2]
    assert "[Indexed documents: ...]" in prompts[0]


def test_the_turn_names_what_is_indexed():
    with chat_agent_build_context("full", rag_indexed_files=[]) as agent:
        agent._memory_store = None
        assert agent.get_memory_dynamic_context() == ""
        agent.rag = MagicMock(indexed_files={"/docs/b.txt", "/x/a.pdf"})
        assert agent.get_memory_dynamic_context() == (
            "[Indexed documents: a.pdf, b.txt]"
        )


def test_a_document_still_indexing_is_not_listed_as_indexed():
    release = threading.Event()
    rag = MagicMock(indexed_files={"/docs/b.txt"})

    def index_document(path, progress_callback=None):
        assert release.wait(30)
        rag.indexed_files.add(path)
        return {"success": True}

    rag.index_document.side_effect = index_document
    with chat_agent_build_context("full", rag_indexed_files=[]) as agent:
        agent._memory_store = None
        agent.rag = rag
        try:
            result, job = rag_tools._index_within_budget(rag, "/docs/big.pdf", 0.05)
            assert result is None
            assert agent.get_memory_dynamic_context() == (
                "[Indexed documents: b.txt]\n"
                "[Still indexing in the background, not searchable yet: big.pdf]"
            )
        finally:
            release.set()
        assert job.done.wait(30)
        assert agent.get_memory_dynamic_context() == (
            "[Indexed documents: b.txt, big.pdf]"
        )


def test_profiles_without_document_tools_never_name_documents():
    with chat_agent_build_context("chat", rag_indexed_files=["/docs/a.pdf"]) as agent:
        agent._memory_store = None
        assert agent.get_memory_dynamic_context() == ""
