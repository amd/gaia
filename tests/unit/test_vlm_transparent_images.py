# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A transparent image reaches the vision model on white, not on black (#8821)."""

import base64
import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from gaia.llm.vlm_client import VLMClient


def _sent_image(image_bytes: bytes) -> bytes:
    with patch("gaia.llm.lemonade_client.LemonadeClient"):
        client = VLMClient(base_url="http://localhost:13305/api/v1")
    client.client = MagicMock()
    client.client.chat_completions.return_value = {
        "choices": [{"message": {"content": "BLUE TRIANGLE 73"}}]
    }
    with patch.object(client, "_refuse_missing_vlm"):
        client.extract_from_image(image_bytes)
    content = client.client.chat_completions.call_args.kwargs["messages"][0]["content"]
    url = next(p for p in content if p["type"] == "image_url")["image_url"]["url"]
    header, b64 = url.split(",", 1)
    assert header.startswith("data:image/")
    return base64.b64decode(b64)


def _encode(img: Image.Image, fmt: str, **kwargs) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def _black_text_on_transparent() -> Image.Image:
    img = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for x in range(10, 30):
        img.putpixel((x, 10), (0, 0, 0, 255))
    return img


def test_rgba_png_background_is_sent_white():
    sent = Image.open(
        io.BytesIO(_sent_image(_encode(_black_text_on_transparent(), "PNG")))
    )

    assert sent.mode == "RGB"
    assert sent.getpixel((0, 0)) == (255, 255, 255)
    assert sent.getpixel((15, 10)) == (0, 0, 0)


def test_palette_png_with_transparency_key_is_sent_white():
    img = Image.new("P", (20, 20), 0)
    img.putpalette([0, 0, 0] * 256)
    img.putpixel((5, 5), 1)
    raw = _encode(img, "PNG", transparency=0)
    assert "transparency" in Image.open(io.BytesIO(raw)).info

    sent = Image.open(io.BytesIO(_sent_image(raw)))

    assert sent.getpixel((0, 0)) == (255, 255, 255)
    assert sent.getpixel((5, 5)) == (0, 0, 0)


@pytest.mark.parametrize("fmt", ["JPEG", "PNG"])
def test_opaque_image_is_sent_unchanged(fmt):
    raw = _encode(Image.new("RGB", (20, 20), (10, 20, 30)), fmt)

    assert _sent_image(raw) == raw
