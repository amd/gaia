# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for AgentConsole image rendering methods."""

import sys
import types
from unittest.mock import patch

import pytest
from PIL import Image

from gaia.agents.base.console import AgentConsole


@pytest.fixture
def console():
    return AgentConsole()


@pytest.fixture
def tmp_image(tmp_path):
    """Create a small test image (10x10 red square)."""
    img = Image.new("RGB", (10, 10), color=(255, 0, 0))
    path = tmp_path / "test.png"
    img.save(path)
    return path


class TestRenderImageHalfblock:
    def test_returns_nonempty_for_valid_image(self, console, tmp_image):
        result = console._render_image_halfblock(str(tmp_image))
        assert result != ""

    def test_contains_ansi_codes(self, console, tmp_image):
        result = console._render_image_halfblock(str(tmp_image))
        assert "\x1b[" in result

    def test_contains_halfblock_char(self, console, tmp_image):
        result = console._render_image_halfblock(str(tmp_image))
        assert "\u2580" in result

    def test_returns_empty_for_nonexistent_file(self, console):
        result = console._render_image_halfblock("/nonexistent/path/image.png")
        assert result == ""

    def test_respects_max_width(self, console, tmp_image):
        # Create wider image to test resizing
        img = Image.new("RGB", (200, 100), color=(0, 255, 0))
        img_path = tmp_image.parent / "wide.png"
        img.save(img_path)

        result = console._render_image_halfblock(str(img_path), max_width=20)
        first_line = result.split("\n")[0]
        # Each character in rendered output is fg+bg+char (multiple bytes in ANSI)
        # Count half-block chars directly
        char_count = first_line.count("\u2580")
        assert char_count <= 20

    def test_returns_empty_on_pil_not_available(self, console, tmp_image):
        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
            result = console._render_image_halfblock(str(tmp_image))
            assert result == ""


def _make_failing_term_image_module():
    """Return a fake term_image.image module whose from_file raises Exception."""
    mod = types.ModuleType("term_image.image")

    def from_file(*args, **kwargs):
        raise Exception("terminal unsupported in test environment")

    mod.from_file = from_file
    return mod


@pytest.fixture
def fake_term_image_modules():
    """Patch sys.modules so term_image.image.from_file raises without hanging."""
    fake_pkg = types.ModuleType("term_image")
    fake_mod = _make_failing_term_image_module()
    fake_pkg.image = fake_mod
    with patch.dict(
        sys.modules, {"term_image": fake_pkg, "term_image.image": fake_mod}
    ):
        yield fake_mod


class TestPrintImage:
    def test_falls_through_to_halfblock_when_term_image_fails(
        self, console, tmp_image, fake_term_image_modules
    ):
        """When term-image raises an exception, half-block renderer is used."""
        halfblock_called = []
        original = console._render_image_halfblock

        def track_halfblock(path, **kwargs):
            halfblock_called.append(path)
            return original(path, **kwargs)

        with patch.object(
            console, "_render_image_halfblock", side_effect=track_halfblock
        ):
            console.print_image(str(tmp_image), caption="Test")

        assert len(halfblock_called) == 1

    def test_falls_through_to_metadata_when_halfblock_returns_empty(
        self, console, tmp_image, fake_term_image_modules
    ):
        """When half-block returns empty string, PIL metadata fallback is used."""
        printed_panels = []

        def capture_print(renderable, **kwargs):
            printed_panels.append(renderable)

        with patch.object(console, "_render_image_halfblock", return_value=""):
            with patch.object(console.console, "print", side_effect=capture_print):
                console.print_image(str(tmp_image), caption="Meta")

        # At least one panel should have been printed (the metadata panel)
        assert len(printed_panels) > 0

    def test_skips_nonexistent_path(self, console):
        """print_image returns early without error for missing file."""
        # Should not raise
        console.print_image("/nonexistent/path.png")

    def test_caption_printed_without_term_image(
        self, console, tmp_image, fake_term_image_modules
    ):
        """Caption is displayed when rendering with half-block renderer."""
        console_prints = []

        def capture(renderable=None, **kwargs):
            console_prints.append(str(renderable) if renderable is not None else "")

        with patch.object(console, "_render_image_halfblock", return_value="<blocks>"):
            with patch.object(console.console, "print", side_effect=capture):
                console.print_image(str(tmp_image), caption="My Caption")

        # Caption should appear in one of the console.print() calls
        assert any("My Caption" in s for s in console_prints)
