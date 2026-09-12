# SPDX-License-Identifier: MIT

"""Regression tests for RAG startup/indexing status propagation."""

import argparse
from unittest.mock import Mock, patch

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_monitor_tools import FileToolsMixin
from gaia.chat.sdk import AgentConfig, AgentSDK
from gaia.rag import app as rag_app
from gaia.talk.sdk import TalkSDK


def _uninitialized_chat() -> AgentSDK:
    """Build an AgentSDK without starting an LLM provider."""
    chat = AgentSDK.__new__(AgentSDK)
    chat.config = AgentConfig()
    chat.log = Mock()
    chat.rag = None
    chat.rag_enabled = False
    return chat


def test_chat_enable_rag_requires_a_successful_index_result():
    """A non-empty failure dict must not produce a success log or result."""
    chat = _uninitialized_chat()

    with patch("gaia.rag.sdk.RAGSDK") as rag_class:
        rag_class.return_value.index_document.return_value = {
            "success": False,
            "error": "embedding model unavailable",
        }

        result = chat.enable_rag(documents=["manual.pdf"])

    assert result is False
    assert chat.rag_enabled is True
    assert any(
        "Failed to index document" in call.args[0]
        for call in chat.log.warning.call_args_list
    )
    assert not any(
        "Successfully indexed" in call.args[0] for call in chat.log.info.call_args_list
    )
    assert any(
        "indexed 0 of 1 documents" in call.args[0]
        for call in chat.log.warning.call_args_list
    )


def test_talk_enable_rag_propagates_chat_index_failure():
    """Talk must not report RAG success when Chat reports a failed index."""
    talk = TalkSDK.__new__(TalkSDK)
    talk.chat_sdk = Mock()
    talk.chat_sdk.enable_rag.return_value = False
    talk.log = Mock()

    result = talk.enable_rag(documents=["manual.pdf"])

    assert result is False
    talk.chat_sdk.enable_rag.assert_called_once_with(documents=["manual.pdf"])
    assert not any(
        "RAG enabled with" in call.args[0] for call in talk.log.info.call_args_list
    )
    talk.log.warning.assert_called_once_with(
        "RAG enabled but one or more documents failed to index"
    )


def test_talk_enable_rag_preserves_success_status():
    """Talk keeps its success log when Chat indexes every document."""
    talk = TalkSDK.__new__(TalkSDK)
    talk.chat_sdk = Mock()
    talk.chat_sdk.enable_rag.return_value = True
    talk.log = Mock()

    result = talk.enable_rag(documents=["manual.pdf"])

    assert result is True
    assert any(
        "RAG enabled with 1 documents" in call.args[0]
        for call in talk.log.info.call_args_list
    )
    talk.log.warning.assert_not_called()


def test_chat_add_document_returns_false_on_index_failure():
    """AgentSDK.add_document is annotated -> bool; a failed index must not
    forward the (always truthy) stats dict as if it were success."""
    chat = _uninitialized_chat()
    chat.rag_enabled = True
    chat.rag = Mock()
    chat.rag.index_document.return_value = {
        "success": False,
        "error": "file not found",
    }

    result = chat.add_document("missing.pdf")

    assert result is False


def test_chat_add_document_returns_true_on_index_success():
    """The success path must still report True, not the stats dict itself."""
    chat = _uninitialized_chat()
    chat.rag_enabled = True
    chat.rag = Mock()
    chat.rag.index_document.return_value = {"success": True, "num_chunks": 3}

    result = chat.add_document("manual.pdf")

    assert result is True


def test_talk_add_document_propagates_chat_index_failure():
    """Talk.add_document forwards whatever Chat.add_document returns; once
    Chat reports a real bool, Talk needs no change of its own to be correct."""
    talk = TalkSDK.__new__(TalkSDK)
    talk.chat_sdk = Mock()
    talk.chat_sdk.rag_enabled = True
    talk.chat_sdk.add_document.return_value = False
    talk.log = Mock()

    assert talk.add_document("missing.pdf") is False


def test_rag_app_index_command_counts_only_real_successes(tmp_path):
    """`gaia rag index` must not count a failed index_document call as
    indexed just because it returned a non-empty dict."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    args = argparse.Namespace(
        files=[str(pdf)],
        model=None,
        verbose=False,
        chunk_size=None,
        max_chunks=None,
    )

    with patch("gaia.rag.app.RAGSDK") as rag_class:
        rag_class.return_value.index_document.return_value = {
            "success": False,
            "error": "embedding model unavailable",
        }
        rag_class.return_value.get_status.return_value = {"total_chunks": 0}

        with patch("builtins.print") as mock_print:
            rag_app.index_command(args)

    assert any(
        "Indexed 0/1 documents" in call.args[0] for call in mock_print.call_args_list
    )


def test_rag_app_quick_command_reports_failure_and_stops(tmp_path):
    """`gaia rag quick` must not query after a failed index_document call."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    args = argparse.Namespace(
        file=str(pdf),
        question="What is this?",
        model=None,
        verbose=False,
        chunk_size=None,
        max_chunks=None,
    )

    with patch("gaia.rag.app.RAGSDK") as rag_class:
        rag_class.return_value.index_document.return_value = {
            "success": False,
            "error": "embedding model unavailable",
        }

        with patch("builtins.print") as mock_print:
            rag_app.quick_command(args)

        rag_class.return_value.query.assert_not_called()

    assert any(
        f"Failed to index: {pdf}" in call.args[0] for call in mock_print.call_args_list
    )


def _file_monitor_agent(rag_result):
    """Build a minimal FileToolsMixin host with just enough state to drive
    add_watch_directory's auto-indexing loop."""

    class _Host(FileToolsMixin):
        pass

    host = _Host()
    host.path_validator = Mock(is_path_allowed=Mock(return_value=True))
    host.watch_directories = []
    host.indexed_files = set()
    host.rag = Mock(index_document=Mock(return_value=rag_result))
    host._watch_directory = Mock()
    host._auto_save_session = Mock()
    host.register_file_tools()
    return host, _TOOL_REGISTRY["add_watch_directory"]["function"]


def test_add_watch_directory_does_not_count_failed_index_as_indexed(tmp_path):
    """A dict-returning failed index_document call must not be counted in
    files_indexed or added to indexed_files."""
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    host, add_watch_directory = _file_monitor_agent(
        {"success": False, "error": "embedding model unavailable"}
    )

    result = add_watch_directory(str(tmp_path))

    assert result["files_indexed"] == 0
    assert host.indexed_files == set()


def test_add_watch_directory_counts_real_success(tmp_path):
    """The success path must still index and count the file."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    host, add_watch_directory = _file_monitor_agent({"success": True, "num_chunks": 2})

    result = add_watch_directory(str(tmp_path))

    assert result["files_indexed"] == 1
    assert str(pdf) in host.indexed_files
