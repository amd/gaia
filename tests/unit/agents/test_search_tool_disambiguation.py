# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The two file searches must tell the model which question each answers.

``search_file`` and ``search_file_content`` differ by one suffix and both read
as "search files". Across 688 benchmark tool calls the model selected
``search_file_content`` zero times — it reached for the name search, got a
successful empty result, and concluded the workspace held nothing.

Each docstring is the only thing the model sees, so each must name its sibling
and say what it cannot do. Tracked as #3967, which also covers the larger
problem: 11 tools whose names start with ``search_``.
"""

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_tools import FileSearchToolsMixin


class _Host(FileSearchToolsMixin):
    pass


@pytest.fixture
def docs():
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    _Host().register_file_search_tools()
    out = {
        name: (_TOOL_REGISTRY[name]["function"].__doc__ or "")
        for name in ("search_file", "search_file_content")
    }
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)
    return out


def test_the_name_search_says_it_does_not_read_contents(docs):
    doc = docs["search_file"]
    assert "NAME" in doc
    assert "search_file_content" in doc, "does not point at the content search"


def test_the_content_search_says_what_it_is_for(docs):
    doc = docs["search_file_content"]
    assert "grep" in doc.lower()
    # The phrasing that matters: this is the tool for "find every occurrence".
    assert "every occurrence" in doc.lower()


def test_the_content_search_documents_its_arguments(docs):
    # The sibling had a full Args block and this one had none, which is part
    # of why the model never chose it.
    doc = docs["search_file_content"]
    assert "Args:" in doc
    for arg in ("pattern", "directory", "file_pattern"):
        assert arg in doc, f"{arg} undocumented"


@pytest.mark.parametrize("name", ["search_file", "search_file_content"])
def test_neither_docstring_is_a_stub(docs, name):
    # A two-line description is what left the model guessing between them.
    assert len(docs[name].split()) > 40, f"{name} docstring is too thin to choose on"
