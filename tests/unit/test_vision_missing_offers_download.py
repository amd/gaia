# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An image no vision model can read yet asks for the model instead of a workaround.

With the vision model not downloaded, the image tool said only "Question
answering failed: …", and the model went on to decode the PNG with a 58-line
Python script the user had to approve. The result now names the model it needs,
so the Agent UI can offer the download, and tells the model to stop there.

No Lemonade server required.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm.vlm_client import VisionModelMissingError, VLMClient, VLMExtractionError
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import SUMMARY_VIEW_KEYS
from gaia.vlm.mixin import VLMToolsMixin

GEMMA = lc.DEFAULT_MODEL_NAME


@pytest.fixture
def host(tmp_path):
    image = tmp_path / "red_square.png"
    image.write_bytes(b"png")
    with patch("gaia.llm.lemonade_client.LemonadeClient"):
        client = VLMClient(vlm_model=GEMMA, base_url="http://127.0.0.1:59993/api/v1")
    client.client = MagicMock()
    client.client.list_models.return_value = {
        "data": [{"id": GEMMA, "downloaded": False, "size": 5.97}]
    }
    client.client.health_check.return_value = {"all_models_loaded": []}

    class Host(VLMToolsMixin):
        def __init__(self):
            self.vlm_model = GEMMA
            self.vlm_client = client

    return Host(), str(image)


def test_the_missing_model_is_its_own_error():
    assert issubclass(VisionModelMissingError, VLMExtractionError)


@pytest.mark.parametrize(
    "call",
    [
        lambda h, image: h._answer_question_about_image(image, "What colour is it?"),
        lambda h, image: h._analyze_image(image, "all"),
    ],
)
def test_the_image_tools_name_the_model_they_need(host, call):
    tools, image = host
    result = call(tools, image)

    assert result["status"] == "error"
    assert result["needs_model"] == "vision"
    assert f"'{GEMMA}' is not downloaded" in result["error"]
    assert "Do not try to read the image another way" in result["hint"]


def _drain(handler: SSEOutputHandler) -> list:
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def test_the_ui_hears_which_model_is_needed():
    handler = SSEOutputHandler()
    handler.pretty_print_json(
        {"status": "error", "error": "not downloaded", "needs_model": "vision"},
        title="Result",
    )
    event = next(e for e in _drain(handler) if e["type"] == "tool_result")

    assert event["needs_model"] == "vision"
    assert "needs_model" in SUMMARY_VIEW_KEYS
