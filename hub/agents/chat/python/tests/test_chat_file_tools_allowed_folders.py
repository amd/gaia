# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ChatAgent's own path tools check the same allowed-folders rule.

``list_files`` and ``text_to_speech`` are defined inline on ChatAgent, and its
RAG SDK used to keep a validator of its own — so a folder the user approved for
the agent was still refused when RAG read it.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia.security import PathValidator  # noqa: E402
from tests.unit.test_profilespec_characterization import (  # noqa: E402
    chat_agent_build_context,
)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    fake_temp = tmp_path / "systemp"
    fake_temp.mkdir()
    monkeypatch.setattr("gaia.security._system_temp_roots", lambda: {str(fake_temp)})
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (allowed / "notes.txt").write_text("in", encoding="utf-8")
    (outside / "secret.txt").write_text("out", encoding="utf-8")
    links = True
    try:
        (allowed / "linked_dir").symlink_to(outside, target_is_directory=True)
    except OSError:  # Windows without symlink privilege
        links = False
    return allowed.resolve(), outside.resolve(), links


@pytest.fixture
def chat(tree, tmp_path, monkeypatch):
    # PathValidator's cache dir resolves under gaia_home(), and the
    # chat_agent_build_context's Path.home() patch points at a fake,
    # never-written "/fake/home" — give it a real, writable GAIA_HOME
    # instead of letting it try to mkdir into "/fake".
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    allowed, _, _ = tree
    with chat_agent_build_context("full") as agent:
        agent.path_validator = PathValidator(allowed_paths=[str(allowed)])
        agent._register_tools()
        yield agent, agent._tools_registry


def _outside(tree, form):
    allowed, outside, links = tree
    if form == "symlink":
        if not links:
            pytest.skip("symlinks unavailable on this platform")
        return str(allowed / "linked_dir")
    if form == "dotdot":
        return str(allowed / ".." / "outside")
    return str(outside)


@pytest.mark.parametrize("form", ["direct", "dotdot", "symlink"])
def test_list_files_outside_is_refused(tree, chat, form):
    _, registry = chat

    result = registry["list_files"]["function"](path=_outside(tree, form))

    assert result["status"] == "error"
    assert "not in allowed paths" in result["error"]
    assert "files" not in result


def test_list_files_inside_lists(tree, chat):
    allowed, _, _ = tree
    _, registry = chat

    result = registry["list_files"]["function"](path=str(allowed))

    assert result["status"] == "success"
    assert result["files"] == ["notes.txt"]


def test_text_to_speech_refuses_an_output_outside(tree, chat):
    _, outside, _ = tree
    _, registry = chat
    if "text_to_speech" not in registry:
        pytest.skip("text_to_speech not registered on this install")
    target = outside / "speech.wav"

    result = registry["text_to_speech"]["function"](text="hi", output_path=str(target))

    assert result["status"] == "error"
    assert "not in allowed paths" in result["error"]
    assert not target.exists()


def test_rag_reads_through_the_agents_validator(tree):
    from gaia_agent_chat.agent import ChatAgent

    allowed, _, _ = tree
    agent = ChatAgent.__new__(ChatAgent)
    agent.observers = []
    agent.path_validator = PathValidator(allowed_paths=[str(allowed)])
    agent._rag_config = MagicMock()

    with patch("gaia_agent_chat.agent.RAGSDK") as rag_cls:
        rag = agent._build_rag()

    assert rag is rag_cls.return_value
    assert rag_cls.call_args.kwargs["path_validator"] is agent.path_validator


def test_is_path_allowed_refuses_a_secret_inside_the_scope(tree):
    from gaia_agent_chat.agent import ChatAgent

    allowed, _, _ = tree
    (allowed / ".env").write_text("TOKEN=x", encoding="utf-8")
    agent = ChatAgent.__new__(ChatAgent)
    agent.observers = []
    agent.path_validator = PathValidator(allowed_paths=[str(allowed)])

    assert agent._is_path_allowed(str(allowed / "notes.txt"))
    assert not agent._is_path_allowed(str(allowed / ".env"))
    assert not agent._is_path_allowed(str(Path(allowed).parent / "outside"))
