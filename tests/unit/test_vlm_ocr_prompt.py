# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Reading a scanned page keeps its language: an Arabic scan is stored in Arabic."""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.vlm_client import VLMClient


@pytest.fixture
def sent_prompt():
    with patch("gaia.llm.lemonade_client.LemonadeClient"):
        client = VLMClient(base_url="http://localhost:13305/api/v1")
    client.client = MagicMock()
    client.client.chat_completions.return_value = {
        "choices": [{"message": {"content": "نص الصفحة"}}]
    }

    client.extract_from_image(b"fake-png")

    content = client.client.chat_completions.call_args.kwargs["messages"][0]["content"]
    return next(part["text"] for part in content if part["type"] == "text")


def test_ocr_prompt_forbids_translating(sent_prompt):
    assert "translate" in sent_prompt.lower()
    assert "language" in sent_prompt.lower()


def test_ocr_prompt_keeps_the_original_script_and_digits(sent_prompt):
    assert "script" in sent_prompt.lower()
    assert "digits" in sent_prompt.lower()


def test_a_caller_prompt_is_sent_unchanged():
    with patch("gaia.llm.lemonade_client.LemonadeClient"):
        client = VLMClient(base_url="http://localhost:13305/api/v1")
    client.client = MagicMock()
    client.client.chat_completions.return_value = {
        "choices": [{"message": {"content": "a cat"}}]
    }

    client.extract_from_image(b"fake-png", prompt="Describe this image.")

    content = client.client.chat_completions.call_args.kwargs["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Describe this image."}
