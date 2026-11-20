# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""summarize_document's schema names its valid summary_type values (#8621)."""

from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin


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
    for value in ("brief", "detailed", "bullets"):
        assert value in summary_type["description"]


def test_every_argument_has_a_description():
    params = _params()
    for name in ("file_path", "summary_type", "max_words_per_section"):
        assert params[name].get("description"), name


def test_args_block_is_not_repeated_in_the_tool_description():
    _RagHost().register_rag_tools()
    description = _TOOL_REGISTRY["summarize_document"]["description"]
    assert "Args:" not in description
    assert "max_words_per_section" not in description
