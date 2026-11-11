# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lemonade image edit/upscale contracts and opt-in SDK tools (#370)."""

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.sd.mixin import SDToolsMixin
from gaia.security import PathValidator

BASE = "http://localhost:13305/api/v1"


def _png_bytes(colours=((200, 30, 30), (20, 20, 220))) -> bytes:
    image = Image.new("RGB", (8, 8), colours[0])
    image.paste(colours[1], (0, 0, 4, 8))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class _Response:
    def __init__(self, payload=None, status_code=200, text=""):
        self.payload = payload or {"data": [{"b64_json": _b64(_png_bytes())}]}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self.payload


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    return LemonadeClient(base_url=BASE, verbose=False)


def test_edit_sends_the_multipart_contract(client, tmp_path):
    source = tmp_path / "source.jpeg"
    source.write_bytes(b"jpeg bytes")
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        captured["uploaded"] = kwargs["files"]["image"][1].read()
        return _Response()

    with patch.object(lc.requests, "post", side_effect=post):
        result = client.edit_image(source, "add a red barn", size="512x512", n=2)

    assert result["data"]
    assert captured["url"].endswith("/images/edits")
    assert captured["data"] == {
        "model": "Flux-2-Klein-4B",
        "prompt": "add a red barn",
        "n": 2,
        "response_format": "b64_json",
        "size": "512x512",
    }
    assert captured["files"]["image"][0] == "source.jpeg"
    assert captured["files"]["image"][2] == "application/octet-stream"
    assert captured["uploaded"] == b"jpeg bytes"
    assert "json" not in captured


def test_upscale_sends_the_json_base64_contract(client, tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(_png_bytes())
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response()

    with patch.object(lc.requests, "post", side_effect=post):
        result = client.upscale_image(source)

    assert result["data"]
    assert captured["url"].endswith("/images/upscale")
    assert captured["json"] == {
        "image": _b64(source.read_bytes()),
        "model": "RealESRGAN-x4plus",
    }
    assert captured["headers"]["Content-Type"] == "application/json"
    assert "files" not in captured and "data" not in captured


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"prompt": " "}, "non-empty"),
        ({"prompt": "x", "n": 0}, "between 1 and 10"),
        ({"prompt": "x", "n": True}, "between 1 and 10"),
        ({"prompt": "x", "size": "square"}, "WIDTHxHEIGHT"),
    ],
)
def test_edit_rejects_bad_arguments_before_request(client, tmp_path, kwargs, message):
    source = tmp_path / "source.png"
    source.write_bytes(_png_bytes())
    with patch.object(lc.requests, "post") as post:
        with pytest.raises(LemonadeClientError, match=message):
            client.edit_image(source, **kwargs)
    post.assert_not_called()


def test_edit_rejects_non_image_extension_before_request(client, tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("not an image", encoding="utf-8")
    with patch.object(lc.requests, "post") as post:
        with pytest.raises(LemonadeClientError, match="Image editing"):
            client.edit_image(source, "change it")
    post.assert_not_called()


def test_upscale_requires_a_png_before_request(client, tmp_path):
    jpeg = tmp_path / "source.jpg"
    jpeg.write_bytes(b"jpeg bytes")
    with patch.object(lc.requests, "post") as post:
        with pytest.raises(LemonadeClientError, match="Image upscaling"):
            client.upscale_image(jpeg)
    post.assert_not_called()


class _ClientFactory:
    IMAGE_EDIT_DEFAULT_MODEL = LemonadeClient.IMAGE_EDIT_DEFAULT_MODEL
    IMAGE_UPSCALE_DEFAULT_MODEL = LemonadeClient.IMAGE_UPSCALE_DEFAULT_MODEL
    SD_MODEL_DEFAULTS = LemonadeClient.SD_MODEL_DEFAULTS

    def __init__(self, instance):
        self.instance = instance

    def __call__(self, **_kwargs):
        return self.instance


@pytest.fixture
def edit_tools(monkeypatch, tmp_path):
    saved = dict(_TOOL_REGISTRY)
    backend = SimpleNamespace(
        base_url=BASE,
        edit_image=MagicMock(),
        upscale_image=MagicMock(),
        load_model=MagicMock(),
        list_sd_models=MagicMock(
            return_value=[{"id": "Flux-2-Klein-4B"}, {"id": "RealESRGAN-x4plus"}]
        ),
    )
    monkeypatch.setattr("gaia.sd.mixin.LemonadeClient", _ClientFactory(backend))
    host = SDToolsMixin()
    output = tmp_path / "outputs"
    try:
        host.init_sd(output_dir=str(output), enable_edit_tools=True)
        yield host, backend, output
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _scope(host, folder):
    validator = PathValidator(allowed_paths=[str(folder)])
    validator.set_access_prompt(lambda _path: False)
    host.path_validator = validator


def test_edit_and_upscale_are_not_registered_without_the_opt_in(monkeypatch, tmp_path):
    saved = dict(_TOOL_REGISTRY)
    backend = SimpleNamespace(base_url=BASE)
    monkeypatch.setattr("gaia.sd.mixin.LemonadeClient", _ClientFactory(backend))
    try:
        _TOOL_REGISTRY.pop("edit_image", None)
        _TOOL_REGISTRY.pop("upscale_image", None)
        SDToolsMixin().init_sd(output_dir=str(tmp_path), enable_edit_tools=False)
        assert "edit_image" not in _TOOL_REGISTRY
        assert "upscale_image" not in _TOOL_REGISTRY
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_edit_and_upscale_are_off_by_default(monkeypatch, tmp_path):
    saved = dict(_TOOL_REGISTRY)
    backend = SimpleNamespace(base_url=BASE)
    monkeypatch.setattr("gaia.sd.mixin.LemonadeClient", _ClientFactory(backend))
    try:
        _TOOL_REGISTRY.pop("edit_image", None)
        _TOOL_REGISTRY.pop("upscale_image", None)
        host = SDToolsMixin()
        host.init_sd(output_dir=str(tmp_path))
        assert host.sd_enable_edit_tools is False
        assert "edit_image" not in _TOOL_REGISTRY
        assert "upscale_image" not in _TOOL_REGISTRY
        assert "generate_image" in _TOOL_REGISTRY
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_registered_edit_and_upscale_save_in_scope_results(edit_tools, tmp_path):
    host, backend, output = edit_tools
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = allowed / "source.png"
    source.write_bytes(_png_bytes())
    _scope(host, allowed)
    backend.edit_image.return_value = {"data": [{"b64_json": _b64(_png_bytes())}]}
    backend.upscale_image.return_value = {
        "data": [{"b64_json": _b64(_png_bytes(((10, 200, 10), (20, 20, 220))))}]
    }

    edited = _TOOL_REGISTRY["edit_image"]["function"](str(source), "make it red")
    upscaled = _TOOL_REGISTRY["upscale_image"]["function"](str(source))

    assert edited["status"] == upscaled["status"] == "success"
    assert Path(edited["image_path"]).parent == output.resolve()
    assert Path(upscaled["image_path"]).parent == output.resolve()
    backend.edit_image.assert_called_once_with(
        source.resolve(),
        "make it red",
        model="Flux-2-Klein-4B",
        size=None,
        n=1,
        timeout=900,
    )
    backend.upscale_image.assert_called_once_with(
        source.resolve(), model="RealESRGAN-x4plus", timeout=900
    )


@pytest.mark.parametrize("form", ["direct", "dotdot", "symlink"])
def test_registered_image_tools_refuse_out_of_scope_sources(edit_tools, tmp_path, form):
    host, backend, _output = edit_tools
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    source = outside / "source.png"
    source.write_bytes(_png_bytes())
    if form == "direct":
        requested = source
    elif form == "dotdot":
        requested = allowed / ".." / "outside" / "source.png"
    else:
        requested = allowed / "linked.png"
        try:
            requested.symlink_to(source)
        except OSError:
            pytest.skip("symlinks unavailable on this platform")
    _scope(host, allowed)

    edited = _TOOL_REGISTRY["edit_image"]["function"](str(requested), "change it")
    upscaled = _TOOL_REGISTRY["upscale_image"]["function"](str(requested))

    assert edited["status"] == upscaled["status"] == "error"
    backend.edit_image.assert_not_called()
    backend.upscale_image.assert_not_called()


@pytest.mark.parametrize("method", ["edit_image", "upscale_image"])
def test_registered_image_tools_reject_unusable_reply_without_writing(
    edit_tools, tmp_path, method
):
    host, backend, output = edit_tools
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = allowed / "source.png"
    source.write_bytes(_png_bytes())
    _scope(host, allowed)
    getattr(backend, method).return_value = {"data": []}

    if method == "edit_image":
        result = _TOOL_REGISTRY[method]["function"](str(source), "change it")
    else:
        result = _TOOL_REGISTRY[method]["function"](str(source))

    assert result["status"] == "error"
    assert not list(output.glob("*.png"))


def test_save_image_keeps_a_model_path_inside_the_output_dir(edit_tools):
    host, _backend, output = edit_tools
    saved = host._save_image("edited source", b"image", "../../x")
    assert saved.parent == output.resolve()
    assert "/" not in saved.name and "\\" not in saved.name


@pytest.mark.parametrize("method", ["edit_image", "upscale_image"])
def test_a_model_that_is_not_downloaded_is_refused_with_a_remedy(
    edit_tools, tmp_path, method
):
    host, backend, output = edit_tools
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = allowed / "source.png"
    source.write_bytes(_png_bytes())
    _scope(host, allowed)
    backend.list_sd_models.return_value = []

    if method == "edit_image":
        result = _TOOL_REGISTRY[method]["function"](str(source), "change it")
    else:
        result = _TOOL_REGISTRY[method]["function"](str(source))

    assert result["status"] == "error"
    assert "not downloaded" in result["error"]
    assert "list_sd_models" in result["error"]
    getattr(backend, method).assert_not_called()
    backend.load_model.assert_not_called()
    assert not list(output.glob("*.png"))


@pytest.mark.parametrize("method", ["edit_image", "upscale_image"])
def test_a_source_inside_the_output_dir_is_never_overwritten(edit_tools, method):
    host, backend, output = edit_tools
    original = _png_bytes()
    source = output / "photo.png"
    source.write_bytes(original)
    _scope(host, output)
    reply = _png_bytes(((10, 200, 10), (20, 20, 220)))
    getattr(backend, method).return_value = {
        "data": [{"b64_json": _b64(reply)}, {"b64_json": _b64(reply)}]
    }

    if method == "edit_image":
        result = _TOOL_REGISTRY[method]["function"](str(source), "change it", n=2)
    else:
        result = _TOOL_REGISTRY[method]["function"](str(source))

    assert result["status"] == "success"
    assert source.read_bytes() == original
    assert str(source.resolve()) not in result["image_paths"]
    assert len(set(result["image_paths"])) == 2
