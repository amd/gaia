# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Requested documents must index before a voice session can start (#3481)."""

import sys
from unittest.mock import AsyncMock, patch

import pytest

from gaia.talk.sdk import TalkConfig, TalkSDK

# asyncio uses a local socketpair for its Windows event-loop wakeup.
pytestmark = pytest.mark.allow_network


@pytest.fixture
def clients():
    """Replace model/audio clients, keeping the real SDK and CLI control flow."""
    with (
        patch("gaia.talk.sdk.AgentSDK") as agent_sdk,
        patch("gaia.talk.sdk.AudioClient") as audio_client,
    ):
        yield agent_sdk.return_value, audio_client.return_value


FAILURES = [
    pytest.param(None, id="document-not-indexed"),
    pytest.param(ImportError("No module named 'faiss'"), id="missing-rag-extra"),
    pytest.param(ConnectionError("embedder unavailable"), id="embedder-unavailable"),
]


@pytest.mark.parametrize("error", FAILURES)
def test_requested_documents_must_index_before_sdk_is_ready(clients, error):
    agent_sdk, _ = clients
    agent_sdk.enable_rag.return_value = False
    agent_sdk.enable_rag.side_effect = error

    with pytest.raises(RuntimeError, match="Could not index.*voice chat"):
        TalkSDK(TalkConfig(rag_documents=["manual.pdf"], enable_tts=False))

    agent_sdk.enable_rag.assert_called_once_with(documents=["manual.pdf"])


@pytest.mark.parametrize("error", FAILURES)
def test_cli_index_failure_exits_without_starting_voice_session(
    clients, error, monkeypatch, capsys
):
    agent_sdk, _ = clients
    agent_sdk.enable_rag.return_value = False
    agent_sdk.enable_rag.side_effect = error
    monkeypatch.setattr(
        sys,
        "argv",
        ["gaia", "talk", "--index", "manual.pdf", "--no-lemonade-check", "--no-tts"],
    )

    from gaia.cli import main

    with patch.object(TalkSDK, "start_voice_session", AsyncMock()) as start:
        with pytest.raises(SystemExit) as exc:
            main()

    assert exc.value.code == 1
    start.assert_not_awaited()
    output = capsys.readouterr()
    assert "Could not index" in output.err
    assert "Starting voice chat" not in output.out
    agent_sdk.enable_rag.assert_called_once_with(documents=["manual.pdf"])


@pytest.mark.parametrize("documents", [None, [], ["manual.pdf", "guide.pdf"]])
def test_successful_or_unrequested_indexing_allows_startup(clients, documents):
    agent_sdk, _ = clients
    agent_sdk.enable_rag.return_value = True

    talk = TalkSDK(TalkConfig(rag_documents=documents, enable_tts=False))

    assert talk.is_voice_session_active is False
    if documents:
        agent_sdk.enable_rag.assert_called_once_with(documents=documents)
    else:
        agent_sdk.enable_rag.assert_not_called()


def test_explicit_enable_rag_still_returns_failure(clients):
    """Only required startup documents change behavior; the bool API stays intact."""
    agent_sdk, _ = clients
    talk = TalkSDK(TalkConfig(enable_tts=False))
    agent_sdk.enable_rag.return_value = False

    assert talk.enable_rag(documents=["manual.pdf"]) is False


def test_missing_rag_dependencies_name_the_package_install(clients, caplog):
    agent_sdk, _ = clients
    agent_sdk.enable_rag.side_effect = ImportError("No module named 'faiss'")

    with pytest.raises(RuntimeError, match="Could not index"):
        TalkSDK(TalkConfig(rag_documents=["manual.pdf"], enable_tts=False))

    assert 'uv pip install "amd-gaia[talk,rag]"' in caplog.text


def test_cli_starts_voice_session_after_document_is_indexed(
    clients, monkeypatch, capsys
):
    agent_sdk, _ = clients
    agent_sdk.enable_rag.return_value = True
    monkeypatch.setattr(
        sys,
        "argv",
        ["gaia", "talk", "--index", "manual.pdf", "--no-lemonade-check", "--no-tts"],
    )

    from gaia.cli import main

    with patch.object(TalkSDK, "start_voice_session", AsyncMock()) as start:
        main()

    agent_sdk.enable_rag.assert_called_once_with(documents=["manual.pdf"])
    start.assert_awaited_once_with()
    assert "Starting voice chat" in capsys.readouterr().out
