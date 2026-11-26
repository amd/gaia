# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the prompt tells the model about its own session.

Asked to "summarize vendor_contract.md in my documents folder", the flagship
spent ~10 tool calls trying ``~/Documents`` and the home folder before finding
GAIA's documents folder, which it could read all along. Asked to set up a
recurring task in the Agent UI, it said it had no scheduler.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("gaiapy")

from gaiapy.chat_agent import ChatAgent, ChatAgentConfig  # noqa: E402

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402


@pytest.fixture
def gaia_docs(tmp_path, monkeypatch):
    home = tmp_path / "gaia-home"
    docs = home / "documents"
    docs.mkdir(parents=True)
    monkeypatch.setenv("GAIA_HOME", str(home))
    return docs.resolve()


def _build(**overrides) -> ChatAgent:
    config = ChatAgentConfig(silent_mode=True, **overrides)
    with (
        patch("gaiapy.chat_agent.RAGSDK"),
        patch("gaiapy.chat_agent.RAGConfig"),
    ):
        return ChatAgent(config)


def _folders_line(prompt: str) -> str:
    [line] = [ln for ln in prompt.splitlines() if ln.startswith("Your folders")]
    return line


def test_the_prompt_names_exactly_the_sessions_existing_folders(gaia_docs, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    attached = work / "vendor_contract.md"
    attached.write_text("terms", encoding="utf-8")
    missing = tmp_path / "gone"

    agent = _build(
        allowed_paths=[
            str(work),
            str(gaia_docs),
            str(attached),
            str(missing),
            str(work),
        ]
    )
    try:
        line = _folders_line(agent.get_session_folders_system_prompt())
    finally:
        agent.__del__()

    listed = line.split(": ", 1)[1].split(". For", 1)[0]
    assert listed == f"{gaia_docs} (GAIA's documents folder), {work.resolve()}"
    assert '"my documents"' in line and "look here first" in line


def test_saving_to_my_documents_names_the_users_folder_not_gaias(
    gaia_docs, monkeypatch
):
    # "my Documents folder" sent new files into the hidden GAIA folder.
    documents = Path.home() / "OneDrive" / "Documents"
    monkeypatch.setattr("gaiapy.chat_agent.documents_dir", lambda: documents)
    agent = _build(allowed_paths=[str(gaia_docs)])
    try:
        line = _folders_line(agent.get_session_folders_system_prompt())
    finally:
        agent.__del__()
    assert f"save a new file to the user's Documents folder, use {documents}" in line


def test_no_documents_note_without_gaias_documents_folder(gaia_docs, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    agent = _build(allowed_paths=[str(work)])
    try:
        line = _folders_line(agent.get_session_folders_system_prompt())
    finally:
        agent.__del__()
    assert "Documents folder" not in line


def test_the_folders_line_does_not_change_mid_session(gaia_docs, tmp_path):
    later = tmp_path / "later"
    agent = _build(allowed_paths=[str(gaia_docs), str(later)])
    try:
        before = agent.get_session_folders_system_prompt()
        later.mkdir()
        assert agent.get_session_folders_system_prompt() == before
    finally:
        agent.__del__()


def test_no_folders_line_without_an_existing_folder(gaia_docs, tmp_path):
    agent = _build(allowed_paths=[str(tmp_path / "gone")])
    try:
        assert "Your folders" not in agent.get_session_folders_system_prompt()
    finally:
        agent.__del__()


def test_the_fast_chat_profile_has_no_folders_line(gaia_docs):
    # "chat" reads the shared registry; earlier agents' tools would leak in.
    with patch.dict(_TOOL_REGISTRY, clear=True):
        agent = _build(prompt_profile="chat", allowed_paths=[str(gaia_docs)])
        try:
            assert "Your folders" not in agent.get_session_folders_system_prompt()
        finally:
            agent.__del__()


def test_the_agent_ui_names_its_scheduled_tasks_page(gaia_docs):
    agent = _build(allowed_paths=[str(gaia_docs)], surface="agent_ui")
    try:
        context = agent.session_context
    finally:
        agent.__del__()

    assert "**Scheduled tasks** in the sidebar" in context
    assert "never say scheduling is unavailable" in context


@pytest.mark.parametrize("surface", [None, "tui", "cli"])
def test_other_front_ends_get_no_scheduled_tasks_line(gaia_docs, surface):
    agent = _build(allowed_paths=[str(gaia_docs)], surface=surface)
    try:
        assert "Scheduled tasks" not in agent.system_prompt + agent.session_context
    finally:
        agent.__del__()


def _unsupported_line(prompt: str) -> str:
    [line] = [ln for ln in prompt.splitlines() if ln.startswith("**UNSUPPORTED:**")]
    return line.split("—")[0].lower()


@pytest.mark.parametrize("surface", [None, "tui", "agent_ui"])
def test_only_the_agent_ui_is_pointed_at_scheduled_tasks(gaia_docs, surface):
    agent = _build(allowed_paths=[str(gaia_docs)], surface=surface)
    try:
        context = agent.session_context
    finally:
        agent.__del__()

    assert ("Scheduled tasks" in context) == (surface == "agent_ui")


# Gemma follows whichever of two contradicting lines it read last.
def test_the_agent_ui_line_overrides_unsupported_scheduling(gaia_docs):
    agent = _build(allowed_paths=[str(gaia_docs)], surface="agent_ui")
    try:
        unsupported = _unsupported_line(agent.system_prompt)
        context = agent.session_context
    finally:
        agent.__del__()

    assert "scheduling" in unsupported
    assert "replaces any **UNSUPPORTED:** entry for scheduling" in context


# A front end's turn can only reuse the prompt the start-up warm-up cached when
# both send the same system prompt and tools; Gemma re-reads all of it otherwise.
@pytest.mark.parametrize("surface", ["agent_ui", "tui", "cli"])
def test_every_front_end_sends_the_prefix_the_warm_up_caches(
    gaia_docs, tmp_path, surface
):
    work = tmp_path / "project"
    work.mkdir()
    warm = _build()
    turn = _build(
        surface=surface,
        ui_session_id="chat-1",
        working_directory=str(work),
        allowed_paths=[str(work), str(gaia_docs)],
    )
    try:
        assert turn.system_prompt == warm.system_prompt
        assert turn._openai_tools == warm._openai_tools
    finally:
        warm.__del__()
        turn.__del__()


def test_only_sending_email_is_unsupported_when_email_tools_are_registered(
    gaia_docs,
):
    """The email tools read mail; the prompt must not claim they send it."""
    agent = _build(allowed_paths=[str(gaia_docs)])
    try:
        without = _unsupported_line(agent._get_system_prompt())
        assert without.startswith("**unsupported:** email,")
        assert "sending email" not in without
        agent._instance_tools["list_inbox"] = {}
        with_tools = _unsupported_line(agent._get_system_prompt())
        assert with_tools.startswith("**unsupported:** sending email,")
    finally:
        agent.__del__()


def test_in_process_agent_ui_chats_say_they_are_the_agent_ui():
    from gaia.ui import _chat_helpers

    kwargs = _chat_helpers._session_agent_kwargs(
        rag_file_paths=[], library_paths=[], allowed=[], session_id="s"
    )
    assert kwargs["surface"] == "agent_ui"
