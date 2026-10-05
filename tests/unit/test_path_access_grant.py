# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Granting a path mid-chat must make it usable by every tool that reads it.

The Agent UI repro: a document outside the chat's scope was refused by
``index_document`` with no prompt, ``read_file``'s prompt was abandoned by the
180 s tool timeout while still on screen, and an approved path stayed
unreadable to the RAG SDK, which kept its own copy of the scope.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.security import PathValidator


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """A chat scope plus an out-of-scope Documents folder holding the PDF.

    pytest's tmp_path sits in the system temp dir, which no prompt may grant,
    so the temp rule is pointed at a dedicated folder instead.
    """
    fake_temp = tmp_path / "systemp"
    fake_temp.mkdir()
    monkeypatch.setattr("gaia.security._system_temp_roots", lambda: {str(fake_temp)})
    scope = tmp_path / "scope"
    scope.mkdir()
    docs = tmp_path / "Documents"
    docs.mkdir()
    pdf = docs / "agent-memory-architecture.pdf"
    pdf.write_bytes(b"%PDF-1.7 placeholder")
    notes = docs / "notes.md"
    notes.write_text("# Notes\nbootstrap items are tagged discovered\n", "utf-8")
    return {"scope": scope, "pdf": pdf, "notes": notes}


class _Console:
    """Agent UI stand-in: answers each access prompt after *delay* seconds."""

    full_access = False

    def __init__(self, answer=True, delay=0.0):
        self.answer = answer
        self.delay = delay
        self.asked = []

    def auto_approve_confirmations_enabled(self):
        return False

    def confirm_tool_execution(self, tool_name, tool_args):
        self.asked.append((tool_name, tool_args["path"]))
        time.sleep(self.delay)
        return self.answer

    def __getattr__(self, name):  # print_* and friends
        return MagicMock()


class _ScopedAgent(Agent):
    def _register_tools(self):
        pass


def _agent(layout, console):
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _ScopedAgent(skip_lemonade=True, silent_mode=True)
    agent.path_validator = PathValidator(allowed_paths=[str(layout["scope"])])
    agent.console = console
    agent._install_path_access_prompt()
    return agent


# ── B: the tool timeout must not abandon a prompt the user is reading ─────


def test_a_prompt_inside_a_tool_does_not_count_against_its_timeout(layout, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "0.3")
    console = _Console(answer=True, delay=1.0)
    agent = _agent(layout, console)

    @tool
    def reads_document() -> dict:
        """Reads the document."""
        allowed, reason = agent.path_validator.validate_read(str(layout["notes"]))
        return {"status": "success" if allowed else "error", "reason": reason}

    result = agent._execute_tool("reads_document", {})

    assert result == {"status": "success", "reason": ""}
    assert len(console.asked) == 1


def test_a_tool_that_hangs_after_the_prompt_is_still_abandoned(layout, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "0.3")
    agent = _agent(layout, _Console(answer=True, delay=0.2))
    release = threading.Event()

    @tool
    def asks_then_hangs() -> dict:
        """Asks for access, then blocks."""
        agent.path_validator.validate_read(str(layout["notes"]))
        release.wait(timeout=30)
        return {"status": "success"}

    try:
        started = time.monotonic()
        result = agent._execute_tool("asks_then_hangs", {})
        assert time.monotonic() - started < 5.0
        assert result.get("timeout") is True
    finally:
        release.set()


# ── A/C: indexing asks, and one grant covers every reader ─────────────────


class _RagHost(RAGToolsMixin):
    """A RAG-tools host wired the way ChatAgent wires it."""

    def __init__(self, validator):
        self.path_validator = validator
        self.indexed_files = set()
        self.current_session = None
        self.rag = MagicMock()
        self.rag.indexed_files = set()
        self.rag.index_document.return_value = {
            "success": True,
            "file_name": "agent-memory-architecture.pdf",
            "num_chunks": 77,
        }

    def rebuild_system_prompt(self):
        pass

    def _is_path_allowed(self, path):
        return self.path_validator.is_path_allowed(path, prompt_user=False)


def _rag_host(layout, answer):
    validator = PathValidator(allowed_paths=[str(layout["scope"])])
    asked = []
    validator.set_access_prompt(lambda p: asked.append(p) or answer)
    host = _RagHost(validator)
    host.register_rag_tools()
    return host, asked


def test_index_document_asks_for_an_out_of_scope_file(layout):
    host, asked = _rag_host(layout, answer=True)

    result = _TOOL_REGISTRY["index_document"]["function"](str(layout["pdf"]))

    assert result["status"] == "success", result
    assert asked == [layout["pdf"].resolve()]
    host.rag.index_document.assert_called_once()


def test_query_specific_file_reuses_the_grant_without_asking_again(layout):
    host, asked = _rag_host(layout, answer=True)
    host.path_validator.validate_read(str(layout["pdf"]))  # approved once
    host.rag.query_specific_file = MagicMock(
        return_value=MagicMock(chunks=[], chunk_scores=[], chunk_metadata=[])
    )

    _TOOL_REGISTRY["query_specific_file"]["function"](str(layout["pdf"]), "source tag")

    assert asked == [layout["pdf"].resolve()]
    host.rag.index_document.assert_called_once()


def test_a_declined_index_tells_the_model_not_to_relocate_the_file(layout):
    host, _ = _rag_host(layout, answer=False)

    result = _TOOL_REGISTRY["index_document"]["function"](str(layout["pdf"]))

    assert result["status"] == "error"
    assert "Access denied" in result["error"]
    assert "Do not copy, move or convert it" in result["error"]
    host.rag.index_document.assert_not_called()


def test_the_rag_sdk_reads_a_file_the_user_granted(layout):
    from gaia.rag.sdk import RAGSDK, RAGConfig

    validator = PathValidator(allowed_paths=[str(layout["scope"])])
    validator.set_access_prompt(lambda p: True)
    assert validator.validate_read(str(layout["notes"])) == (True, "")

    with patch("gaia.rag.sdk.AgentSDK"):
        rag = RAGSDK(
            RAGConfig(
                allowed_paths=[str(layout["scope"])], cache_dir=str(layout["scope"])
            ),
            path_validator=validator,
        )

    with rag._safe_open(str(layout["notes"]), "rb") as handle:
        assert b"bootstrap" in handle.read()


def test_chat_agent_hands_its_validator_to_the_rag_sdk(layout):
    from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

    with (
        patch("gaia_agent_chat.agent.RAGSDK") as rag_sdk_cls,
        patch("gaia_agent_chat.agent.SessionManager"),
    ):
        agent = ChatAgent(
            ChatAgentConfig(
                prompt_profile="doc",
                silent_mode=True,
                allowed_paths=[str(layout["scope"])],
            )
        )
        assert agent.rag is rag_sdk_cls.return_value

    assert rag_sdk_cls.call_args.kwargs["path_validator"] is agent.path_validator


def test_a_document_attached_to_a_running_chat_joins_its_scope(layout):
    from types import SimpleNamespace

    from gaia.ui._chat_helpers import _compute_allowed_paths, _extend_cached_scope

    agent = SimpleNamespace(
        path_validator=PathValidator(allowed_paths=[str(layout["scope"])])
    )

    _extend_cached_scope(agent, _compute_allowed_paths([str(layout["pdf"])]))

    assert agent.path_validator.is_path_allowed(str(layout["pdf"]), prompt_user=False)
    assert not agent.path_validator.is_path_allowed(
        str(layout["notes"]), prompt_user=False
    )
