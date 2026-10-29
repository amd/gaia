# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An image of a slide is the built slide, drawn: generate_image turns it away once."""

from gaia.sd.mixin import SDToolsMixin


class _Agent(SDToolsMixin):
    def __init__(self, request: str) -> None:
        self._current_query = request


def test_a_slide_image_request_is_redirected_to_the_slide_skill():
    agent = _Agent("Generate an image of a slide with our three company values")
    refusal = agent._slide_image_refusal()
    assert refusal is not None and refusal["status"] == "error"
    assert "slide-decks/export_slide_image" in refusal["error"]


def test_a_second_call_in_the_same_turn_goes_through():
    """The model judged it a real picture (a playground slide, say)."""
    agent = _Agent("Draw a kid going down a slide at the park")
    assert agent._slide_image_refusal() is not None
    assert agent._slide_image_refusal() is None


def test_a_new_message_is_checked_again():
    agent = _Agent("PNG of our Q3 chart")
    assert agent._slide_image_refusal() is not None
    agent._current_query = "And a PNG of the roadmap slide"
    assert agent._slide_image_refusal() is not None


def test_an_ordinary_picture_is_not_redirected():
    assert _Agent("Draw a lighthouse at sunset")._slide_image_refusal() is None
