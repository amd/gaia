# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""summarize_document's schema names its valid summary_type values (#8621)."""

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import SUMMARY_TYPES, RAGToolsMixin

DOC = "/docs/handbook.pdf"


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _RagHost(RAGToolsMixin):
    def __init__(self):
        self.rag = MagicMock()
        self.indexed_files = set()
        self.current_session = None
        self.max_chunks = 5
        self.rebuild_system_prompt = MagicMock()


def _params():
    _RagHost().register_rag_tools()
    return _TOOL_REGISTRY["summarize_document"]["parameters"]


def test_summary_type_is_an_enum_of_the_accepted_values():
    summary_type = _params()["summary_type"]
    assert summary_type["type"] == "string"
    assert summary_type["enum"] == ["brief", "detailed", "bullets"]
    assert tuple(summary_type["enum"]) == SUMMARY_TYPES
    assert summary_type["required"] is False


def test_description_names_exactly_the_enum_values():
    summary_type = _params()["summary_type"]
    named = set(re.findall(r"'([a-z_]+)'", summary_type["description"]))
    assert named == set(summary_type["enum"])


def test_every_argument_has_a_description():
    params = _params()
    for name in ("file_path", "summary_type", "max_words_per_section"):
        assert params[name].get("description"), name


def test_args_block_is_not_repeated_in_the_tool_description():
    _RagHost().register_rag_tools()
    description = _TOOL_REGISTRY["summarize_document"]["description"]
    assert "Args:" not in description
    assert "max_words_per_section" not in description


class _DocHost(RAGToolsMixin):
    def __init__(self, chat):
        self.rag = SimpleNamespace(
            indexed_files=[DOC],
            file_metadata={DOC: {"full_text": "[Page 1]\n" + "word " * 50}},
            chat=chat,
        )


def _summarize(chat):
    _DocHost(chat).register_rag_tools()
    return _TOOL_REGISTRY["summarize_document"]["function"]


@pytest.mark.parametrize(
    "bad",
    ["short", "bullet_points", "Brief", " brief", "brief ", "", "brief|detailed"],
)
def test_a_value_outside_the_enum_fails_loudly_without_calling_the_llm(bad):
    chat = MagicMock()
    result = _summarize(chat)(file_path=DOC, summary_type=bad)

    assert result["status"] == "error"
    assert f"Invalid summary_type '{bad}'" in result["error"]
    assert "brief, detailed, bullets" in result["error"]
    chat.send.assert_not_called()


@pytest.mark.parametrize("value", SUMMARY_TYPES)
def test_every_enum_value_is_accepted(value):
    chat = MagicMock()
    chat.effective_model = "Gemma-4-E4B-it-GGUF"
    chat.send.return_value = SimpleNamespace(text="summary", finish_reason="stop")

    result = _summarize(chat)(file_path=DOC, summary_type=value)

    assert result["status"] == "success", result
