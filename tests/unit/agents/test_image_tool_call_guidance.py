# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A wrong or empty call about an image says how to make the right one.

On a receipt photo Gemma called the spreadsheet tool (twice) and the image tool
with no arguments (four times), then told the user image reading was broken.
"""

from typing import Any, Dict
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.security import PathValidator


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _LookAgent(Agent):
    def __init__(self):
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(silent_mode=True, skip_lemonade=True)

    def _get_system_prompt(self) -> str:
        return "look"

    def _register_tools(self) -> None:
        @tool
        def answer_question_about_image(
            image_path: str, question: str
        ) -> Dict[str, Any]:
            """Answer a question about an image.

            Args:
                image_path: Local path to the image.
                question: What to answer.
            """
            return {"status": "success", "answer": "86.15"}


def test_a_call_with_no_arguments_names_every_argument_to_fill_in():
    result = _LookAgent()._execute_tool("answer_question_about_image", {})

    assert result["error_type"] == "invalid_arguments"
    assert result["error"].startswith(
        "Missing required arguments for answer_question_about_image: "
        "image_path, question."
    )
    assert "image_path (str), question (str)" in result["error"]


class _Files(FileSearchToolsMixin):
    pass


def test_the_spreadsheet_tool_on_an_image_points_to_the_image_tools(tmp_path):
    photo = tmp_path / "photo_receipt.png"
    photo.write_bytes(b"\x89PNG")
    files = _Files()
    files.path_validator = PathValidator(allowed_paths=[str(tmp_path)])
    files.register_file_search_tools()

    result = _TOOL_REGISTRY["analyze_data_file"]["function"](file_path=str(photo))

    assert result["status"] == "error"
    assert "is an image, not a spreadsheet" in result["error"]
    assert "query_documents" in result["error"]
    assert f"image_path set to {photo}" in result["error"]
