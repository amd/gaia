# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Thin tool schemas, and the docs that replace what they leave out.

llama.cpp builds its tool-call grammar from ``tools=``. A tool or argument
missing from that list is not refused: the model's call is quietly rewritten
(``search_web`` became ``search_past_conversations``) or the argument dropped
("lines 10 to 20" read the whole file). So thinning may cut prose only.
"""

from __future__ import annotations

import copy
import json

import pytest

from gaia.agents.base.tool_loader import (
    THIN_DESCRIPTION_CHARS,
    format_tool_docs,
    thin_tool_schema,
)


def _schema(description="Search the web. Use it for anything current.\n\nArgs: ..."):
    return {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for"},
                    "num_results": {"type": "integer", "description": "How many"},
                    "kind": {
                        "type": "string",
                        "enum": ["news", "pages"],
                        "description": "Which index",
                    },
                    "sites": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Limit to these",
                    },
                },
                "required": ["query"],
            },
        },
    }


class TestThinToolSchema:
    def test_every_parameter_stays_declared(self):
        full = _schema()
        thin = thin_tool_schema(full)["function"]["parameters"]
        original = full["function"]["parameters"]
        assert list(thin["properties"]) == list(original["properties"])
        assert thin["required"] == original["required"]

    def test_only_descriptions_leave_the_parameters(self):
        props = thin_tool_schema(_schema())["function"]["parameters"]["properties"]
        assert all("description" not in prop for prop in props.values())
        assert props["kind"]["enum"] == ["news", "pages"]
        # gpt-oss's chat template reads ``items``; Lemonade 500s without it.
        assert props["sites"]["items"] == {"type": "string"}

    def test_description_is_the_first_sentence(self):
        thin = thin_tool_schema(_schema())
        assert thin["function"]["description"] == "Search the web."

    def test_a_long_sentence_is_cut_at_a_word_boundary(self):
        text = "word " * 100
        cut = thin_tool_schema(_schema(text))["function"]["description"]
        assert len(cut) <= THIN_DESCRIPTION_CHARS
        assert cut.split() == ["word"] * len(cut.split())

    @pytest.mark.parametrize("description", ["", None])
    def test_a_tool_without_a_description_survives(self, description):
        assert thin_tool_schema(_schema(description))["function"]["description"] == ""

    def test_a_tool_without_parameters_survives(self):
        schema = {"type": "function", "function": {"name": "ping"}}
        thin = thin_tool_schema(schema)["function"]
        assert thin["parameters"] == {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def test_the_input_is_not_modified(self):
        full = _schema()
        before = copy.deepcopy(full)
        thin_tool_schema(full)
        assert full == before

    def test_output_is_stable_byte_for_byte(self):
        first = json.dumps(thin_tool_schema(_schema()))
        assert all(json.dumps(thin_tool_schema(_schema())) == first for _ in range(20))


class TestFormatToolDocs:
    def test_carries_what_the_thin_schema_dropped(self):
        docs = format_tool_docs(_schema())
        assert docs.startswith("[Tool docs: search_web]\n")
        assert "Use it for anything current." in docs
        assert "- query (string, required): What to look for" in docs
        assert "- num_results (integer, optional): How many" in docs
        assert "one of news, pages" in docs

    def test_a_tool_without_parameters_has_no_parameter_section(self):
        schema = {"type": "function", "function": {"name": "ping"}}
        assert format_tool_docs(schema) == "[Tool docs: ping]"
