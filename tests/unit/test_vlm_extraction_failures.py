# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A page that was never read must not be indexed as that page's text.

Vision extraction used to return `"[VLM extraction failed: …]"` when it failed.
No caller anywhere checked for that prefix, so the message was chunked, embedded
into the document index, retrieved, and quoted back as an answer — with nothing
telling the user the page had never been read (#3555).

These tests pin the new contract at the boundary that broke, and at each caller
that has to translate it: a tool reports an error envelope, an ingest helper
reports an error, and the page-level wrapper refuses to hand back partial text
as if it were complete.

No Lemonade server required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.vlm_client import VLMClient, VLMExtractionError


@pytest.fixture
def vlm():
    """A VLMClient whose transport is a mock; nothing reaches the network."""
    with patch("gaia.llm.lemonade_client.LemonadeClient"):
        client = VLMClient(base_url="http://localhost:13305/api/v1")
    client.client = MagicMock()
    return client


# ============================================================================
# 1. Every failure site raises
# ============================================================================


class TestTheThreeFailureSites:
    def test_an_unavailable_model_raises(self, vlm):
        vlm._ensure_vlm_loaded = lambda: False

        with pytest.raises(VLMExtractionError) as excinfo:
            vlm.extract_from_image(b"fake-png", page_num=4, image_num=2)

        assert excinfo.value.page_num == 4
        assert excinfo.value.image_num == 2

    def test_an_error_response_raises(self, vlm):
        vlm._ensure_vlm_loaded = lambda: True
        vlm.client.chat_completions.return_value = {"error": "model exploded"}

        with pytest.raises(VLMExtractionError):
            vlm.extract_from_image(b"fake-png")

    def test_an_unexpected_exception_raises_with_the_cause_attached(self, vlm):
        vlm._ensure_vlm_loaded = lambda: True
        vlm.client.chat_completions.side_effect = ConnectionError("refused")

        with pytest.raises(VLMExtractionError) as excinfo:
            vlm.extract_from_image(b"fake-png")

        assert isinstance(excinfo.value.__cause__, ConnectionError)

    def test_the_message_names_the_page_and_image(self, vlm):
        vlm._ensure_vlm_loaded = lambda: True
        vlm.client.chat_completions.side_effect = ConnectionError("refused")

        with pytest.raises(VLMExtractionError, match="page 9, image 3"):
            vlm.extract_from_image(b"fake-png", page_num=9, image_num=3)

    def test_a_successful_extraction_still_returns_its_text(self, vlm):
        vlm._ensure_vlm_loaded = lambda: True
        vlm.client.chat_completions.return_value = {
            "choices": [{"message": {"content": "# Real page text"}}]
        }

        assert vlm.extract_from_image(b"fake-png") == "# Real page text"


class TestNothingReturnsAnErrorString:
    """The specific shape that used to be indexed as content."""

    def test_a_failure_never_comes_back_as_a_return_value(self, vlm):
        vlm._ensure_vlm_loaded = lambda: False
        try:
            result = vlm.extract_from_image(b"fake-png")
        except VLMExtractionError:
            return
        pytest.fail(f"extraction returned {result!r} instead of raising")

    def test_the_page_wrapper_does_not_swallow_it_into_partial_text(self, vlm):
        """One bad image must not be reported as a page that extracted fine."""
        vlm._ensure_vlm_loaded = lambda: True
        vlm.client.chat_completions.side_effect = [
            {"choices": [{"message": {"content": "first image"}}]},
            ConnectionError("refused"),
        ]
        images = [
            {"image_bytes": b"a", "width": 10, "height": 10, "size_kb": 1.0},
            {"image_bytes": b"b", "width": 10, "height": 10, "size_kb": 1.0},
        ]

        with pytest.raises(VLMExtractionError):
            vlm.extract_from_page_images(images, page_num=1)


# ============================================================================
# 2. Callers translate it at their own boundary
# ============================================================================


class TestToolCallersReportAnError:
    """A tool must say the analysis failed, not return the error as the answer."""

    @staticmethod
    def _mixin(side_effect):
        from gaia.vlm.mixin import VLMToolsMixin

        class Host(VLMToolsMixin):
            def __init__(self):
                self.vlm_model = "test-vlm"
                self.vlm_client = MagicMock()
                self.vlm_client.extract_from_image.side_effect = side_effect

        return Host()

    def test_analyze_image_reports_an_error_envelope(self, tmp_path):
        image = tmp_path / "chart.png"
        image.write_bytes(b"not-really-a-png")
        host = self._mixin(VLMExtractionError("model unavailable", 1, 1))

        result = host._analyze_image(str(image), focus="all")

        assert result["status"] == "error"
        assert "description" not in result

    def test_answer_question_reports_an_error_envelope(self, tmp_path):
        image = tmp_path / "chart.png"
        image.write_bytes(b"not-really-a-png")
        host = self._mixin(VLMExtractionError("model unavailable", 1, 1))

        result = host._answer_question_about_image(str(image), "what is this?")

        assert result["status"] == "error"
        assert "answer" not in result


class TestIngestReportsAnError:
    def test_image_ingest_reports_the_failure_rather_than_the_text(self, tmp_path):
        from gaia.messaging import ingest

        image = tmp_path / "photo.png"
        image.write_bytes(b"not-really-a-png")

        with patch.object(ingest, "VLMClient") as factory:
            factory.return_value.extract_from_image.side_effect = VLMExtractionError(
                "model unavailable", 1, 1
            )
            factory.return_value.vlm_model = "test-vlm"
            result = ingest.ingest_image_to_vlm(str(image))

        assert result["status"] == "error"
        assert "text" not in result
