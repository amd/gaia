"""Indexing a document must not change the system prompt (it would re-read it all)."""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("gaia_agent_chat")

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


def test_profiles_without_document_tools_never_name_documents():
    with chat_agent_build_context("chat", rag_indexed_files=["/docs/a.pdf"]) as agent:
        agent._memory_store = None
        assert agent.get_memory_dynamic_context() == ""
