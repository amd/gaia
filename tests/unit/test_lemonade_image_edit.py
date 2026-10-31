# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for Lemonade image editing requests."""

from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.llm import lemonade_client as lc


class _Response:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload or {"data": [{"b64_json": "encoded"}]}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


def test_edit_image_sends_multipart_request(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"png-bytes")
    response = _Response()
    captured = {}

    def post(*args, **kwargs):
        captured["url"] = args[0]
        captured["data"] = kwargs["data"]
        image = kwargs["files"]["image"]
        captured["filename"] = image[0]
        captured["content"] = image[1].read()
        captured["content_type"] = image[2]
        return response

    with (
        patch.object(lc.requests, "post", side_effect=post),
        patch("gaia.llm.lemonade_choice.setup_change_refusal", return_value=None),
    ):
        client = lc.LemonadeClient(
            base_url="http://127.0.0.1:13305/api/v1", verbose=False
        )
        result = client.edit_image(source, "add a red barn", n=2, size="512x512")

    assert result == response.json()
    assert captured["url"].endswith("/images/edits")
    assert captured["data"] == {
        "model": "Flux-2-Klein-4B",
        "prompt": "add a red barn",
        "n": 2,
        "response_format": "b64_json",
        "size": "512x512",
    }
    assert captured["filename"] == "source.png"
    assert captured["content"] == b"png-bytes"
    assert captured["content_type"] == "application/octet-stream"


def test_edit_image_rejects_missing_source(tmp_path):
    client = lc.LemonadeClient(base_url="http://127.0.0.1:13305/api/v1", verbose=False)

    with pytest.raises(lc.LemonadeClientError, match="does not exist"):
        client.edit_image(tmp_path / "missing.png", "change it")


def test_edit_image_rejects_invalid_arguments(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"png")
    client = lc.LemonadeClient(base_url="http://127.0.0.1:13305/api/v1", verbose=False)

    with pytest.raises(lc.LemonadeClientError, match="non-empty"):
        client.edit_image(source, "  ")
    with pytest.raises(lc.LemonadeClientError, match="between 1 and 10"):
        client.edit_image(source, "change it", n=0)
    with pytest.raises(lc.LemonadeClientError, match="between 1 and 10"):
        client.edit_image(source, "change it", n=11)
    with pytest.raises(lc.LemonadeClientError, match="WIDTHxHEIGHT"):
        client.edit_image(source, "change it", size="square")


def test_image_endpoints_reject_non_png_or_jpeg(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("not an image", encoding="utf-8")
    client = lc.LemonadeClient(base_url="http://127.0.0.1:13305/api/v1", verbose=False)

    with pytest.raises(lc.LemonadeClientError, match="PNG or JPEG"):
        client.edit_image(source, "change it")
    with pytest.raises(lc.LemonadeClientError, match="PNG or JPEG"):
        client.upscale_image(source)


def test_edit_image_surfaces_server_error(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"png")

    with (
        patch.object(
            lc.requests,
            "post",
            return_value=_Response(status_code=400, text="bad image"),
        ),
        patch("gaia.llm.lemonade_choice.setup_change_refusal", return_value=None),
    ):
        client = lc.LemonadeClient(
            base_url="http://127.0.0.1:13305/api/v1", verbose=False
        )
        with pytest.raises(lc.LemonadeClientError, match="400"):
            client.edit_image(source, "change it")


def test_upscale_image_sends_json_base64_request(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"png-bytes")
    captured = {}

    def post(*args, **kwargs):
        captured["url"] = args[0]
        captured["json"] = kwargs["json"]
        captured["headers"] = kwargs["headers"]
        captured["has_files"] = "files" in kwargs
        return _Response()

    with (
        patch.object(lc.requests, "post", side_effect=post),
        patch("gaia.llm.lemonade_choice.setup_change_refusal", return_value=None),
    ):
        client = lc.LemonadeClient(
            base_url="http://127.0.0.1:13305/api/v1", verbose=False
        )
        client.upscale_image(source)

    assert captured["url"].endswith("/images/upscale")
    assert captured["json"] == {
        "model": "RealESRGAN-x4plus",
        "image": "cG5nLWJ5dGVz",
    }
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["has_files"] is False


def test_image_edit_defaults_are_exposed():
    assert lc.LemonadeClient.IMAGE_EDIT_DEFAULT_MODEL == "Flux-2-Klein-4B"
    assert lc.LemonadeClient.IMAGE_UPSCALE_DEFAULT_MODEL == "RealESRGAN-x4plus"
    assert Path("src/gaia/sd/mixin.py").exists()
