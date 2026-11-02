# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Image variations: ``LemonadeClient.create_image_variation`` and its tool (#371)."""

import base64
import io
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
import responses
from PIL import Image

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.llm.lemonade_client import (
    LemonadeAuthError,
    LemonadeClient,
    LemonadeClientError,
)
from gaia.sd.mixin import SDToolsMixin

BASE = "http://localhost:13305/api/v1"
URL = f"{BASE}/images/variations"


def _png_bytes(colours=((200, 30, 30), (20, 20, 220))) -> bytes:
    img = Image.new("RGB", (8, 8), colours[0])
    if len(colours) > 1:
        img.paste(colours[1], (0, 0, 4, 8))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _multipart_fields(request):
    """Parse a recorded multipart body into {name: (filename, bytes)}."""
    raw = (
        f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
        + request.body
    )
    message = BytesParser(policy=email_policy).parsebytes(raw)
    return {
        part.get_param("name", header="content-disposition"): (
            part.get_filename(),
            part.get_payload(decode=True),
        )
        for part in message.iter_parts()
    }


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "cat.png"
    path.write_bytes(_png_bytes())
    return path


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    return LemonadeClient(base_url=BASE, verbose=False)


# --- the client ---------------------------------------------------------------


@responses.activate
def test_variation_sends_the_multipart_contract(client, source):
    reply = {"data": [{"b64_json": _b64(_png_bytes())}] * 3}
    responses.add(responses.POST, URL, json=reply)

    result = client.create_image_variation(source, size="512x512", n=3)

    assert result == reply
    request = responses.calls[0].request
    assert request.headers["Content-Type"].startswith("multipart/form-data")
    assert "Authorization" not in request.headers
    fields = _multipart_fields(request)
    assert fields["model"] == (None, b"Flux-2-Klein-4B")
    assert fields["n"] == (None, b"3")
    assert fields["size"] == (None, b"512x512")
    assert fields["response_format"] == (None, b"b64_json")
    assert fields["image"] == ("cat.png", source.read_bytes())
    assert "prompt" not in fields


@responses.activate
def test_variation_omits_size_when_not_given_and_sends_the_key(monkeypatch, source):
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    client = LemonadeClient(base_url=BASE, api_key="secret-key", verbose=False)
    responses.add(responses.POST, URL, json={"data": []})

    client.create_image_variation(source, model="SD-Turbo")

    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer secret-key"
    fields = _multipart_fields(request)
    assert "size" not in fields
    assert fields["model"] == (None, b"SD-Turbo")
    assert fields["n"] == (None, b"1")


@pytest.mark.parametrize("n", [0, 11, -1, True, "3", 2.0])
@responses.activate
def test_variation_rejects_an_out_of_range_count_without_a_request(client, source, n):
    with pytest.raises(LemonadeClientError, match="between 1 and 10"):
        client.create_image_variation(source, n=n)
    assert len(responses.calls) == 0


@pytest.mark.parametrize("size", ["512", "0x512", "512x0", "abc", "512x512 ", ""])
@responses.activate
def test_variation_rejects_a_malformed_size_without_a_request(client, source, size):
    with pytest.raises(LemonadeClientError, match="WIDTHxHEIGHT"):
        client.create_image_variation(source, size=size)
    assert len(responses.calls) == 0


@responses.activate
def test_variation_rejects_a_missing_or_non_png_file(client, tmp_path):
    with pytest.raises(LemonadeClientError, match="does not exist"):
        client.create_image_variation(tmp_path / "absent.png")
    jpeg = tmp_path / "photo.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff")
    with pytest.raises(LemonadeClientError, match="PNG only"):
        client.create_image_variation(jpeg)
    with pytest.raises(LemonadeClientError, match="does not exist"):
        client.create_image_variation(tmp_path)
    assert len(responses.calls) == 0


@responses.activate
def test_a_401_never_echoes_the_response_body(client, source):
    responses.add(responses.POST, URL, body="Authorization: Bearer leaked", status=401)

    with pytest.raises(LemonadeAuthError) as exc:
        client.create_image_variation(source)
    assert "LEMONADE_API_KEY" in str(exc.value)
    assert "leaked" not in str(exc.value)


@responses.activate
def test_a_server_error_names_the_status(client, source):
    responses.add(
        responses.POST, URL, json={"error": {"message": "n must be <= 10"}}, status=400
    )

    with pytest.raises(LemonadeClientError, match=r"\(400\).*n must be"):
        client.create_image_variation(source)


@responses.activate
def test_a_non_json_reply_is_an_error(client, source):
    responses.add(responses.POST, URL, body="<html>proxy</html>", status=200)

    with pytest.raises(LemonadeClientError, match="non-JSON"):
        client.create_image_variation(source)


@responses.activate
def test_an_unreachable_server_is_an_error(client, source):
    responses.add(responses.POST, URL, body=requests.ConnectionError("refused"))

    with pytest.raises(LemonadeClientError, match="request failed"):
        client.create_image_variation(source)


# --- the tool -----------------------------------------------------------------


@pytest.fixture
def variation_tool(monkeypatch, tmp_path):
    """Register the opt-in tool on a real mixin host and return (host, tool, out)."""
    saved = dict(_TOOL_REGISTRY)
    sd_client = SimpleNamespace(
        base_url=BASE,
        load_model=MagicMock(return_value={"status": "success"}),
        list_sd_models=MagicMock(return_value=[{"id": "Flux-2-Klein-4B"}]),
        create_image_variation=MagicMock(),
    )
    monkeypatch.setattr("gaia.sd.mixin.LemonadeClient", _ClientFactory(sd_client))
    monkeypatch.setattr(
        "gaia.agents.tools.path_access.read_access_error", lambda *_a, **_k: None
    )
    out = tmp_path / "out"
    host = SDToolsMixin()
    try:
        host.init_sd(output_dir=str(out), enable_variation_tools=True)
        yield host, _TOOL_REGISTRY["create_image_variation"]["function"], out
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


class _ClientFactory:
    """Stands in for the LemonadeClient class: callable, with class constants."""

    IMAGE_VARIATION_DEFAULT_MODEL = LemonadeClient.IMAGE_VARIATION_DEFAULT_MODEL
    SD_MODEL_DEFAULTS = LemonadeClient.SD_MODEL_DEFAULTS

    def __init__(self, instance):
        self._instance = instance

    def __call__(self, **_kwargs):
        return self._instance


def test_the_tool_is_registered_only_when_enabled(monkeypatch, tmp_path):
    saved = dict(_TOOL_REGISTRY)
    monkeypatch.setattr(
        "gaia.sd.mixin.LemonadeClient", _ClientFactory(SimpleNamespace(base_url=BASE))
    )
    try:
        _TOOL_REGISTRY.pop("create_image_variation", None)
        SDToolsMixin.init_sd(SimpleNamespace(), output_dir=str(tmp_path))
        assert "create_image_variation" not in _TOOL_REGISTRY
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_each_variation_is_saved_as_its_own_png(variation_tool, source):
    host, create, out = variation_tool
    first = _png_bytes()
    second = _png_bytes(((10, 200, 10), (240, 240, 240)))
    host.sd_client.create_image_variation.return_value = {
        "data": [{"b64_json": _b64(first)}, {"b64_json": _b64(second)}]
    }

    result = create(str(source), n=2)

    assert result["status"] == "success", result
    assert result["count"] == 2
    paths = [Path(p) for p in result["image_paths"]]
    assert [p.read_bytes() for p in paths] == [first, second]
    assert all(p.parent == out.resolve() for p in paths)
    assert [p.name.startswith("variation_cat_") for p in paths] == [True, True]
    assert paths[0].name.endswith("_1.png") and paths[1].name.endswith("_2.png")
    host.sd_client.load_model.assert_called_once_with(
        "Flux-2-Klein-4B", auto_download=False, prompt=False, timeout=600
    )
    host.sd_client.create_image_variation.assert_called_once_with(
        source.resolve(), model="Flux-2-Klein-4B", size=None, n=2
    )
    assert host.sd_generations[-1]["image_paths"] == result["image_paths"]


def test_a_path_refusal_stops_before_any_request(variation_tool, monkeypatch, source):
    host, create, _ = variation_tool
    refusal = {"status": "error", "error": "Path is not allowed"}
    monkeypatch.setattr(
        "gaia.agents.tools.path_access.read_access_error", lambda *_a, **_k: refusal
    )

    assert create(str(source)) == refusal
    host.sd_client.load_model.assert_not_called()
    host.sd_client.create_image_variation.assert_not_called()


def test_a_missing_source_stops_before_any_request(variation_tool, tmp_path):
    host, create, _ = variation_tool

    result = create(str(tmp_path / "absent.png"))

    assert result["status"] == "error"
    assert "Cannot read image" in result["error"]
    host.sd_client.create_image_variation.assert_not_called()


@pytest.mark.parametrize(
    "reply",
    [
        {"data": []},
        {"data": None},
        {},
        [],
        {"data": [{"b64_json": ""}]},
        {"data": [{"b64_json": "not base64 !!"}]},
    ],
)
def test_an_unusable_reply_is_an_error_and_writes_nothing(
    variation_tool, source, reply
):
    host, create, out = variation_tool
    host.sd_client.create_image_variation.return_value = reply

    result = create(str(source))

    assert result["status"] == "error", result
    assert list(out.glob("variation_*")) == []


def test_one_flat_variation_fails_the_batch_and_writes_nothing(variation_tool, source):
    host, create, out = variation_tool
    host.sd_client.create_image_variation.return_value = {
        "data": [
            {"b64_json": _b64(_png_bytes())},
            {"b64_json": _b64(_png_bytes(((0, 0, 0),)))},
        ]
    }

    result = create(str(source), n=2)

    assert result["status"] == "error"
    assert "variation 2" in result["error"]
    assert list(out.glob("variation_*")) == []


def test_a_load_failure_is_reported_without_varying(variation_tool, source):
    host, create, _ = variation_tool
    host.sd_client.load_model.side_effect = LemonadeClientError(
        "Connection refused by server"
    )

    result = create(str(source))

    assert result["status"] == "error"
    assert "Cannot reach Lemonade Server" in result["error"]
    host.sd_client.create_image_variation.assert_not_called()


def test_a_model_that_is_not_downloaded_is_refused_without_a_download(
    variation_tool, source
):
    host, create, _ = variation_tool
    host.sd_client.list_sd_models.return_value = []

    result = create(str(source))

    assert result["status"] == "error"
    assert "not downloaded" in result["error"]
    host.sd_client.load_model.assert_not_called()
    host.sd_client.create_image_variation.assert_not_called()


def test_a_legacy_sd_model_still_downloads_on_demand(variation_tool, source):
    host, create, _ = variation_tool
    host.sd_client.create_image_variation.return_value = {
        "data": [{"b64_json": _b64(_png_bytes())}]
    }

    result = create(str(source), model="SD-Turbo")

    assert result["status"] == "success", result
    host.sd_client.list_sd_models.assert_not_called()
    host.sd_client.load_model.assert_called_once_with(
        "SD-Turbo", auto_download=True, prompt=False, timeout=600
    )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"n": 11}, "between 1 and 10"),
        ({"n": 0}, "between 1 and 10"),
        ({"size": "big"}, "WIDTHxHEIGHT"),
    ],
)
def test_bad_arguments_are_refused_before_any_model_load(
    variation_tool, source, kwargs, message
):
    host, create, _ = variation_tool

    result = create(str(source), **kwargs)

    assert result["status"] == "error"
    assert message in result["error"]
    host.sd_client.load_model.assert_not_called()


def test_a_non_png_is_refused_before_any_model_load(variation_tool, tmp_path):
    host, create, _ = variation_tool
    jpeg = tmp_path / "photo.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff")

    result = create(str(jpeg))

    assert result["status"] == "error"
    assert "PNG only" in result["error"]
    host.sd_client.load_model.assert_not_called()


def test_a_hostile_model_name_cannot_write_outside_the_output_dir(
    variation_tool, source
):
    host, create, out = variation_tool
    host.sd_client.create_image_variation.return_value = {
        "data": [{"b64_json": _b64(_png_bytes())}]
    }

    host.sd_client.list_sd_models.return_value = [{"id": "../../evil/x"}]

    result = create(str(source), model="../../evil/x")

    assert result["status"] == "success", result
    saved = Path(result["image_paths"][0])
    assert saved.parent == out.resolve()
    assert ".." not in saved.name and "/" not in saved.name


# --- the provider interface ---------------------------------------------------


@responses.activate
def test_the_provider_passes_variation_through_to_its_own_server(monkeypatch, source):
    from gaia.llm.providers.lemonade import LemonadeProvider

    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    reply = {"data": [{"b64_json": _b64(_png_bytes())}]}
    responses.add(responses.POST, URL, json=reply)
    provider = LemonadeProvider(base_url=BASE)

    result = provider.create_image_variation(source, model="SD-Turbo", n=1)

    assert result == reply
    fields = _multipart_fields(responses.calls[0].request)
    assert fields["model"] == (None, b"SD-Turbo")
    assert fields["image"] == ("cat.png", source.read_bytes())


@responses.activate
def test_the_provider_surfaces_variation_errors(monkeypatch, tmp_path):
    from gaia.llm.providers.lemonade import LemonadeProvider

    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    provider = LemonadeProvider(base_url=BASE)

    with pytest.raises(LemonadeClientError, match="does not exist"):
        provider.create_image_variation(tmp_path / "absent.png")
    assert len(responses.calls) == 0
