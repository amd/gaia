# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.file_snapshots import SnapshotError
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.sd.mixin import SDToolsMixin


def _client():
    return LemonadeClient(base_url="http://localhost:13305/api/v1", verbose=False)


def test_generate_3d_sends_contract_and_returns_glb():
    client = _client()
    client.list_models = MagicMock(
        return_value={
            "data": [{"id": "TRELLIS-3D", "downloaded": True, "labels": ["3d"]}]
        }
    )
    response = MagicMock(status_code=200, content=b"glTF" + b"\x00" * 20)
    with patch("gaia.llm.lemonade_client.requests.post", return_value=response) as post:
        result = client.generate_3d_model(
            "aW1hZ2U=", resolution=1024, seed=7, bg_removal="none"
        )
    assert result.startswith(b"glTF")
    assert post.call_args.args[0].endswith("/3d/generations")
    assert post.call_args.kwargs["json"] == {
        "model": "TRELLIS-3D",
        "image": "aW1hZ2U=",
        "resolution": 1024,
        "seed": 7,
        "bg_removal": "none",
    }


def test_generate_3d_refuses_missing_download_without_post():
    client = _client()
    client.list_models = MagicMock(
        return_value={
            "data": [{"id": "TRELLIS-3D", "downloaded": False, "labels": ["3d"]}]
        }
    )
    with patch("gaia.llm.lemonade_client.requests.post") as post:
        with pytest.raises(LemonadeClientError, match="not downloaded"):
            client.generate_3d_model("aW1hZ2U=")
    post.assert_not_called()


@pytest.mark.parametrize("resolution", [0, 777, 2048])
def test_generate_3d_validates_resolution_before_network(resolution):
    client = _client()
    with patch("gaia.llm.lemonade_client.requests.post") as post:
        with pytest.raises(LemonadeClientError, match="resolution"):
            client.generate_3d_model("aW1hZ2U=", resolution=resolution)
    post.assert_not_called()


@pytest.fixture
def model3d_tool(monkeypatch, tmp_path):
    """Register the opt-in tool on a minimal host and return its wrapper."""
    saved = dict(_TOOL_REGISTRY)
    client = SimpleNamespace(
        base_url="http://localhost:13305/api/v1",
        generate_3d_model=MagicMock(return_value=b"glTFmesh"),
    )
    monkeypatch.setattr("gaia.sd.mixin.LemonadeClient", lambda **_kwargs: client)
    monkeypatch.setattr(
        "gaia.agents.tools.path_access.read_access_error", lambda *_args: None
    )
    host = SimpleNamespace(model_id=None)
    try:
        SDToolsMixin.init_sd(host, output_dir=str(tmp_path), enable_3d_tools=True)
        yield host, _TOOL_REGISTRY["generate_3d_model"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_3d_tool_is_registered_only_when_explicitly_enabled(monkeypatch, tmp_path):
    saved = dict(_TOOL_REGISTRY)
    monkeypatch.setattr(
        "gaia.sd.mixin.LemonadeClient",
        lambda **_kwargs: SimpleNamespace(base_url="http://localhost:13305/api/v1"),
    )
    try:
        host = SimpleNamespace()
        SDToolsMixin.init_sd(host, output_dir=str(tmp_path), enable_3d_tools=False)
        assert "generate_3d_model" not in _TOOL_REGISTRY
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_3d_tool_saves_glb_and_reloads_chat_model(model3d_tool, tmp_path):
    host, generate = model3d_tool
    host.model_id = "chat-model"
    host.sd_client._ensure_model_loaded = MagicMock()
    source = tmp_path / "source.png"
    source.write_bytes(b"image")

    result = generate(str(source))

    output = Path(result["model_path"])
    assert result["status"] == "success"
    assert output.suffix == ".glb"
    assert output.read_bytes() == b"glTFmesh"
    host.sd_client._ensure_model_loaded.assert_called_once_with("chat-model")


def test_3d_tool_keeps_saved_mesh_when_chat_reload_fails(model3d_tool, tmp_path):
    host, generate = model3d_tool
    host.model_id = "chat-model"
    host.sd_client._ensure_model_loaded = MagicMock(
        side_effect=LemonadeClientError("model slot is busy")
    )
    source = tmp_path / "source.png"
    source.write_bytes(b"image")

    result = generate(str(source))

    assert result["status"] == "success"
    assert Path(result["model_path"]).is_file()
    assert "warning" in result
    assert "reloading chat model" in result["warning"]


def test_3d_tool_returns_path_refusal_without_generating(
    model3d_tool, monkeypatch, tmp_path
):
    host, generate = model3d_tool
    refusal = {"status": "error", "error": "Path is not allowed"}
    monkeypatch.setattr(
        "gaia.agents.tools.path_access.read_access_error", lambda *_args: refusal
    )

    assert generate(str(tmp_path / "blocked.png")) == refusal
    host.sd_client.generate_3d_model.assert_not_called()


def test_3d_tool_removes_staging_file_when_snapshot_fails(
    model3d_tool, monkeypatch, tmp_path
):
    _host, generate = model3d_tool
    source = tmp_path / "source.png"
    source.write_bytes(b"image")
    monkeypatch.setattr(
        "gaia.sd.mixin.before_change",
        MagicMock(side_effect=SnapshotError("snapshot unavailable")),
    )

    result = generate(str(source))

    assert result["status"] == "error"
    assert "snapshot unavailable" in result["error"]
    assert not list(tmp_path.glob("*.part"))
