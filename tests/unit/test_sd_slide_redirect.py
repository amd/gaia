# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An image of a slide is the built slide, drawn: generate_image turns it away once."""

from types import SimpleNamespace

from gaia.sd.mixin import SDToolsMixin


class _Skills:
    def __init__(self, names) -> None:
        self._names = names

    def list_skills(self):
        return [SimpleNamespace(name=n) for n in self._names]


class _Agent(SDToolsMixin):
    def __init__(self, request: str, skills=("slide-decks",)) -> None:
        self._current_query = request
        self.skill_manager = _Skills(skills)


def test_a_slide_image_request_is_redirected_to_the_slide_skill():
    agent = _Agent("Generate an image of a slide with our three company values")
    refusal = agent._slide_image_refusal()
    assert refusal is not None and refusal["status"] == "error"
    assert "slide-decks/export_slide_image" in refusal["error"]


def test_the_refusal_names_the_slide_skill_only_when_it_is_installed():
    agent = _Agent("PNG of our Q3 chart", skills=("image-gen",))
    refusal = agent._slide_image_refusal()
    assert refusal is not None and "slide-decks" not in refusal["error"]
    assert ".pptx" in refusal["error"]


def test_the_users_own_words_decide_not_the_recalled_memory():
    """_current_query carries memory and skill hints that mention slides."""
    agent = _Agent(
        "Skills that may fit this request and are not loaded yet: image-gen, "
        "slide-decks.\nUpcoming: Q3 board presentation\n\nDraw a lighthouse at sunset"
    )
    agent._original_user_input = "Draw a lighthouse at sunset"
    assert agent._slide_image_refusal() is None
    agent._original_user_input = "Draw our roadmap slide"
    assert agent._slide_image_refusal() is not None


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
