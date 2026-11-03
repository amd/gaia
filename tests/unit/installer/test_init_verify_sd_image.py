# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia init` only passes an SD model that returns a real picture."""

import base64
import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from gaia.installer.init_command import InitCommand


@pytest.fixture(autouse=True)
def _isolate_remote_env(monkeypatch):
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)


def _png_b64(*colours) -> str:
    img = Image.new("RGB", (8, 8), colours[0])
    if len(colours) > 1:
        img.paste(colours[1], (0, 0, 4, 8))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _verify(response):
    cmd = InitCommand(profile="sd", yes=True)
    cmd.console = MagicMock()
    client = MagicMock()
    client.generate_image.return_value = response
    return cmd._test_model_inference(client, "SDXL-Turbo")


def test_a_real_image_passes():
    image = _png_b64((200, 30, 30), (20, 20, 220))
    assert _verify({"data": [{"b64_json": image}]}) == (True, None)


@pytest.mark.parametrize(
    "response, reason",
    [
        ({"data": []}, "no image data"),
        ({"data": [{"b64_json": ""}]}, "empty image"),
        (
            {
                "data": [
                    {"b64_json": base64.b64encode(b"\x89PNG" + b"\0" * 50).decode()}
                ]
            },
            "not a readable image",
        ),
        ({"data": [{"b64_json": _png_b64((0, 0, 0))}]}, "single flat colour"),
    ],
    ids=["no-data", "empty", "corrupt", "black"],
)
def test_a_reply_without_a_real_image_fails_with_the_reason(response, reason):
    ok, error = _verify(response)
    assert ok is False
    assert error.startswith("No usable image: ")
    assert reason in error
