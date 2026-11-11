# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AgentSDK sizes document context from the model's real window and never
answers without the documents it was asked about (#5067)."""

import sys
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from gaia.chat.sdk import AgentConfig, AgentSDK, RAGContextError
from gaia.llm.lemonade_client import GPU_CTX_SIZE, LemonadeClient
from gaia.llm.providers.claude import CLAUDE_CTX_SIZE

CHUNK = "relevant passage " * 50


def _response(chunks=(CHUNK,)):
    return SimpleNamespace(
        chunks=list(chunks),
        chunk_metadata=None,
        query_metadata=None,
        source_files=["a.pdf"],
    )


def _sdk(system_prompt="", model="Gemma-4-E4B-it-GGUF", **config):
    chat = AgentSDK.__new__(AgentSDK)
    chat.config = AgentConfig(
        model=model, system_prompt=system_prompt, max_tokens=512, **config
    )
    chat.chat_history = deque(maxlen=8)
    chat.log = Mock()
    chat.llm_client = Mock(spec=[])
    chat.rag_enabled = True
    chat.rag = Mock()
    chat.rag.query.return_value = _response()
    chat._generate_conversation = Mock(return_value="answer")
    return chat


def _window(tokens):
    return patch("gaia.chat.sdk.resolve_ctx_size", return_value=tokens)


@pytest.mark.parametrize("window", [32768, 65536, 131072])
def test_budget_follows_the_loaded_window(window):
    chat = _sdk()
    with _window(window) as resolve:
        _, meta = chat._enhance_with_rag("What is in the report?")

    resolve.assert_called_once_with(model="Gemma-4-E4B-it-GGUF", base_url=None)
    assert meta["context_window"] == window
    overhead = (
        150
        + 512
        + len("What is in the report?") // 4
        + len(str(chat.chat_history)) // 4
    )
    assert meta["available_tokens"] == window - overhead


def test_budget_charges_the_system_prompt():
    chat_plain, chat_prompted = _sdk(), _sdk(system_prompt="s" * 40_000)
    with _window(65536):
        _, plain = chat_plain._enhance_with_rag("q")
        _, prompted = chat_prompted._enhance_with_rag("q")

    assert plain["available_tokens"] - prompted["available_tokens"] == 10_000


def test_no_history_turn_does_not_charge_history():
    chat = _sdk()
    chat.chat_history.extend(["user: " + "h" * 8000, "gaia: ok"])
    with _window(65536):
        _, with_history = chat._enhance_with_rag("q")
        _, without = chat._enhance_with_rag("q", no_history=True)

    assert without["available_tokens"] > with_history["available_tokens"] + 1900


def test_send_without_history_passes_the_flag_through():
    chat = _sdk()
    chat.chat_history.extend(["user: " + "h" * 8000, "gaia: ok"])
    chat._enhance_with_rag = Mock(return_value=("enhanced", {}))
    chat.llm_client = Mock()

    chat.send("q", no_history=True)

    chat._enhance_with_rag.assert_called_once_with("q", no_history=True)


def test_claude_uses_its_own_window():
    chat = _sdk(use_claude=True)
    with _window(4096) as resolve:
        _, meta = chat._enhance_with_rag("q")

    resolve.assert_not_called()
    assert meta["context_window"] == CLAUDE_CTX_SIZE


def _pinned(chat, tokens):
    backend = LemonadeClient.__new__(LemonadeClient)
    backend.ctx_size_override = tokens
    backend.cloud_model_provider = Mock(
        side_effect=lambda model: (
            "fireworks" if model.startswith("fireworks.") else None
        )
    )
    chat.llm_client = SimpleNamespace(_backend=backend)
    return chat


def test_client_ctx_pin_wins():
    chat = _pinned(_sdk(), 16384)
    with _window(131072) as resolve:
        _, meta = chat._enhance_with_rag("q")

    resolve.assert_not_called()
    assert meta["context_window"] == 16384


def test_pin_does_not_shrink_a_cloud_model():
    chat = _pinned(_sdk(model="fireworks.some-model"), 16384)
    with _window(4096):
        _, meta = chat._enhance_with_rag("q")

    assert meta["context_window"] == GPU_CTX_SIZE


def test_cloud_model_uses_the_admission_window():
    chat = _sdk(model="fireworks.some-model")
    with _window(4096) as resolve:
        _, meta = chat._enhance_with_rag("q")

    resolve.assert_not_called()
    assert meta["context_window"] == GPU_CTX_SIZE
    # The reply lands in the provider's own window, not this input budget.
    assert (
        meta["available_tokens"]
        == GPU_CTX_SIZE - 150 - len(str(chat.chat_history)) // 4
    )


@pytest.mark.parametrize("stream", [False, True])
def test_no_room_raises_before_any_model_call(stream):
    # A 32K NPU window with a ~32K-token system prompt leaves no room.
    chat = _sdk(system_prompt="s" * 4 * 32_000)
    chat.chat_history.extend(["user: earlier", "gaia: reply"])
    with _window(32768), pytest.raises(RAGContextError) as exc:
        if stream:
            list(chat.send_stream("q"))
        else:
            chat.send("q")

    message = str(exc.value)
    assert "32768-token window" in message
    assert "Shorten the system prompt" in message
    assert "GAIA_CTX_SIZE" in message
    chat._generate_conversation.assert_not_called()
    assert list(chat.chat_history) == ["user: earlier", "gaia: reply"]


def test_pinned_client_is_told_to_raise_its_pin():
    chat = _pinned(_sdk(system_prompt="s" * 4 * 16_000), 16384)
    with pytest.raises(RAGContextError) as exc:
        chat._enhance_with_rag("q")

    assert "16384-token window" in str(exc.value)
    assert "ctx_size_override" in str(exc.value)
    assert "GAIA_CTX_SIZE" not in str(exc.value)


@pytest.mark.parametrize("stream", [False, True])
def test_retrieval_failure_is_reported_not_dropped(stream):
    chat = _sdk()
    chat.chat_history.extend(["user: earlier", "gaia: reply"])
    cause = RuntimeError("index file is corrupt")
    chat.rag.query.side_effect = cause
    with _window(65536), pytest.raises(RAGContextError) as exc:
        if stream:
            list(chat.send_stream("q"))
        else:
            chat.send("q")

    assert exc.value.__cause__ is cause
    assert "index file is corrupt" in str(exc.value)
    assert "disable_rag()" in str(exc.value)
    chat._generate_conversation.assert_not_called()
    assert list(chat.chat_history) == ["user: earlier", "gaia: reply"]


def test_no_matching_chunks_sends_the_question_unchanged():
    chat = _sdk()
    chat.rag.query.return_value = _response(chunks=())
    with _window(65536) as resolve:
        message, meta = chat._enhance_with_rag("q")

    assert message == "q"
    assert meta == {"rag_used": True, "chunks_retrieved": 0}
    resolve.assert_not_called()


def test_enable_rag_names_why_a_document_failed(monkeypatch):
    rag = Mock()
    rag.index_document.return_value = {"success": False, "error": "File not found"}
    fake = SimpleNamespace(RAGSDK=Mock(return_value=rag), RAGConfig=Mock())
    monkeypatch.setitem(sys.modules, "gaia.rag.sdk", fake)
    chat = _sdk()

    assert chat.enable_rag(documents=["missing.pdf"]) is False
    warned = " ".join(str(c.args[0]) for c in chat.log.warning.call_args_list)
    assert "missing.pdf" in warned and "File not found" in warned
