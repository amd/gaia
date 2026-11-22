# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A transparent image reaches the vision model flattened onto white (#8821).

Vision models drop alpha, so black text on a transparent background read as a
"completely black" image. Every path that sends an image to a model must
composite it onto white first, and leave an opaque image byte for byte as is.

No Lemonade server required: each test reads the payload the model was sent.
"""

import base64
import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from gaia.llm.vlm_client import VLMClient, VLMExtractionError
from gaia.utils.image_alpha import UnreadableImageError, flatten_image_bytes

BASE_URL = "http://127.0.0.1:59993/api/v1"
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
_OK = {"choices": [{"message": {"content": "BLUE TRIANGLE 73"}}]}


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _transparent_text_png() -> bytes:
    """The issue's file: background (0,0,0,0), a black glyph (0,0,0,255)."""
    img = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((10, 5, 20, 15), fill=(0, 0, 0, 255))
    return _png(img)


def _opaque_png() -> bytes:
    img = Image.new("RGB", (40, 20), WHITE)
    ImageDraw.Draw(img).rectangle((10, 5, 20, 15), fill=BLACK)
    return _png(img)


def _opaque_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _assert_flattened(payload: bytes):
    """*payload* is the issue's image composited onto white, not black."""
    img = Image.open(io.BytesIO(payload))
    assert img.mode == "RGB"
    assert img.getpixel((0, 0)) == WHITE
    assert img.getpixel((15, 10)) == BLACK


def _sent_image(chat_completions) -> tuple[str, bytes]:
    """The MIME type and decoded bytes of the image in the last request."""
    content = chat_completions.call_args.kwargs["messages"][0]["content"]
    url = next(p["image_url"]["url"] for p in content if p["type"] == "image_url")
    header, data = url.split(",", 1)
    return header[len("data:") : -len(";base64")], base64.b64decode(data)


# --- the shared helper -------------------------------------------------------


def test_a_transparent_png_is_composited_onto_white():
    _assert_flattened(flatten_image_bytes(_transparent_text_png()))


@pytest.mark.parametrize("raw", [_opaque_png(), _opaque_jpeg()], ids=["png", "jpeg"])
def test_an_opaque_image_is_returned_byte_for_byte(raw):
    assert flatten_image_bytes(raw) is raw


def test_an_alpha_channel_with_nothing_transparent_is_left_alone():
    raw = _png(Image.new("RGBA", (8, 8), (10, 20, 30, 255)))
    assert flatten_image_bytes(raw) is raw


def test_a_palette_image_with_a_transparent_entry_is_flattened():
    img = Image.new("P", (40, 20), 0)
    img.putpalette([0, 0, 0] * 256)
    ImageDraw.Draw(img).rectangle((10, 5, 20, 15), fill=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=0)

    out = Image.open(io.BytesIO(flatten_image_bytes(buf.getvalue())))

    assert out.getpixel((0, 0)) == WHITE
    assert out.getpixel((15, 10)) == BLACK


def test_a_grey_and_alpha_image_is_flattened():
    img = Image.new("LA", (40, 20), (0, 0))
    ImageDraw.Draw(img).rectangle((10, 5, 20, 15), fill=(0, 255))
    _assert_flattened(flatten_image_bytes(_png(img)))


def test_bytes_that_are_not_an_image_are_refused():
    with pytest.raises(UnreadableImageError, match="Re-export it as PNG or JPEG"):
        flatten_image_bytes(b"not an image")


# --- VLMClient.extract_from_image -------------------------------------------


@pytest.fixture
def vlm():
    with patch("gaia.llm.lemonade_client.LemonadeClient"):
        client = VLMClient(base_url=BASE_URL)
    client.client = MagicMock()
    client.client.chat_completions.return_value = _OK
    client._vlm_catalog_checked = True
    return client


def test_extract_from_image_sends_a_transparent_png_on_white(vlm):
    vlm.extract_from_image(_transparent_text_png())

    mime, payload = _sent_image(vlm.client.chat_completions)
    assert mime == "image/png"
    _assert_flattened(payload)


@pytest.mark.parametrize(
    "raw,mime",
    [(_opaque_png(), "image/png"), (_opaque_jpeg(), "image/jpeg")],
    ids=["png", "jpeg"],
)
def test_extract_from_image_sends_an_opaque_image_unchanged(vlm, raw, mime):
    vlm.extract_from_image(raw)

    assert _sent_image(vlm.client.chat_completions) == (mime, raw)


def test_extract_from_image_refuses_bytes_that_are_not_an_image(vlm):
    with pytest.raises(VLMExtractionError, match="page 3, image 2"):
        vlm.extract_from_image(b"not an image", page_num=3, image_num=2)

    vlm.client.chat_completions.assert_not_called()


# --- the image tools --------------------------------------------------------


@pytest.fixture
def host(vlm):
    from gaia.vlm.mixin import VLMToolsMixin

    class Host(VLMToolsMixin):
        def __init__(self):
            self.vlm_model = vlm.vlm_model
            self.vlm_client = vlm

    agent = Host()
    with patch.object(VLMToolsMixin, "_image_reader", return_value=vlm):
        yield agent


@pytest.mark.parametrize(
    "call",
    [
        lambda host, path: host._analyze_image(path, "all"),
        lambda host, path: host._answer_question_about_image(path, "What text?"),
    ],
    ids=["analyze_image", "answer_question_about_image"],
)
def test_the_image_tools_send_a_transparent_png_on_white(host, vlm, tmp_path, call):
    path = tmp_path / "transparent.png"
    path.write_bytes(_transparent_text_png())

    result = call(host, str(path))

    assert result["status"] == "success", result
    _assert_flattened(_sent_image(vlm.client.chat_completions)[1])


def test_structured_extraction_sends_a_transparent_png_on_white(vlm, tmp_path):
    from gaia.vlm.structured_extraction import StructuredVLMExtractor

    path = tmp_path / "transparent.png"
    path.write_bytes(_transparent_text_png())
    with patch("gaia.vlm.structured_extraction.VLMClient", return_value=vlm):
        extractor = StructuredVLMExtractor()

    result = extractor.extract(str(path))

    assert result["metadata"]["pages_failed"] == []
    _assert_flattened(_sent_image(vlm.client.chat_completions)[1])


def test_the_lemonade_provider_sends_a_transparent_png_on_white(vlm):
    from gaia.llm.providers.lemonade import LemonadeProvider

    provider = LemonadeProvider.__new__(LemonadeProvider)
    provider._backend = MagicMock(base_url=BASE_URL)
    with patch("gaia.llm.vlm_client.VLMClient", return_value=vlm):
        provider.vision([_transparent_text_png()], "What text?")

    _assert_flattened(_sent_image(vlm.client.chat_completions)[1])


def test_the_claude_provider_sends_a_transparent_png_on_white():
    from gaia.llm.providers.claude import ClaudeProvider

    provider = ClaudeProvider.__new__(ClaudeProvider)
    with patch.object(ClaudeProvider, "chat", return_value="ok") as chat:
        provider.vision([_transparent_text_png()], "What text?")

    source = chat.call_args.args[0][0]["content"][0]["source"]
    assert source["media_type"] == "image/png"
    _assert_flattened(base64.b64decode(source["data"]))


def test_the_claude_provider_sends_an_opaque_jpeg_unchanged():
    from gaia.llm.providers.claude import ClaudeProvider

    raw = _opaque_jpeg()
    provider = ClaudeProvider.__new__(ClaudeProvider)
    with patch.object(ClaudeProvider, "chat", return_value="ok") as chat:
        provider.vision([raw], "What text?")

    source = chat.call_args.args[0][0]["content"][0]["source"]
    assert source["media_type"] == "image/jpeg"
    assert base64.b64decode(source["data"]) == raw
