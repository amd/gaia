"""Indexing a document must not change the system prompt (it would re-read it all)."""

import threading
from unittest.mock import MagicMock

import pytest

pytest.importorskip("gaia_agent")

from gaia.agents.tools import rag_tools  # noqa: E402
from tests.unit.test_profilespec_characterization import (  # noqa: E402
    chat_agent_build_context,
)


def _rag(indexed):
    rag = MagicMock(indexed_files=set(indexed))
    rag.document_scripts.return_value = ["Latin"]
    return rag


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
        agent.rag = _rag({"/docs/b.txt", "/x/a.pdf"})
        assert agent.get_memory_dynamic_context() == (
            "[Indexed documents: a.pdf (/x/a.pdf), b.txt (/docs/b.txt)]"
        )


def test_a_document_still_indexing_is_not_listed_as_indexed():
    release = threading.Event()
    rag = _rag({"/docs/b.txt"})

    def index_document(path, progress_callback=None, wait_callback=None):
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
                "[Indexed documents: b.txt (/docs/b.txt)]\n"
                "[Still indexing in the background, not searchable yet: big.pdf]"
            )
        finally:
            release.set()
        assert job.done.wait(30)
        assert agent.get_memory_dynamic_context() == (
            "[Indexed documents: b.txt (/docs/b.txt), big.pdf (/docs/big.pdf)]"
        )


def test_profiles_without_document_tools_never_name_documents():
    with chat_agent_build_context("chat", rag_indexed_files=["/docs/a.pdf"]) as agent:
        agent._memory_store = None
        assert agent.get_memory_dynamic_context() == ""


def test_a_non_latin_document_is_marked_and_brings_the_cross_language_note():
    scripts = {"/docs/report-ar.pdf": ["Arabic", "Latin"], "/docs/terms.pdf": ["Latin"]}
    rag = MagicMock(indexed_files=set(scripts))
    rag.document_scripts.side_effect = lambda fp: scripts[fp]
    with chat_agent_build_context("full", rag_indexed_files=[]) as agent:
        agent._memory_store = None
        agent.rag = rag
        context = agent.get_memory_dynamic_context()

    first, note = context.split("\n")
    assert first == (
        "[Indexed documents: report-ar.pdf (/docs/report-ar.pdf) [Arabic + Latin], "
        "terms.pdf (/docs/terms.pdf)]"
    )
    assert "translated into the document's language" in note
    assert "Answer in the language of the user's latest message" in note


def test_a_document_finishing_mid_turn_does_not_fail_the_turn():
    indexed = {"/docs/a.pdf", "/docs/b.pdf"}
    rag = MagicMock(indexed_files=indexed)
    rag.document_scripts.side_effect = lambda _fp: indexed.add("/docs/c.pdf") or []
    with chat_agent_build_context("full", rag_indexed_files=[]) as agent:
        agent._memory_store = None
        agent.rag = rag
        assert "a.pdf" in agent.get_memory_dynamic_context()


def test_latin_only_documents_get_no_cross_language_note():
    rag = MagicMock(indexed_files={"/docs/terms.pdf"})
    rag.document_scripts.return_value = ["Latin"]
    with chat_agent_build_context("full", rag_indexed_files=[]) as agent:
        agent._memory_store = None
        agent.rag = rag
        assert agent.get_memory_dynamic_context() == (
            "[Indexed documents: terms.pdf (/docs/terms.pdf)]"
        )
