# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError


def _client():
    return LemonadeClient(base_url="http://localhost:13305/api/v1", verbose=False)


def test_generate_3d_sends_contract_and_returns_glb():
    client = _client()
    client.list_models = MagicMock(return_value={"data": [{"id": "TRELLIS-3D", "downloaded": True, "labels": ["3d"]}]})
    response = MagicMock(status_code=200, content=b"glTF" + b"\x00" * 20)
    with patch("gaia.llm.lemonade_client.requests.post", return_value=response) as post:
        result = client.generate_3d_model("aW1hZ2U=", resolution=1024, seed=7, bg_removal="none")
    assert result.startswith(b"glTF")
    assert post.call_args.args[0].endswith("/3d/generations")
    assert post.call_args.kwargs["json"] == {
        "model": "TRELLIS-3D", "image": "aW1hZ2U=", "resolution": 1024, "seed": 7, "bg_removal": "none"
    }


def test_generate_3d_refuses_missing_download_without_post():
    client = _client()
    client.list_models = MagicMock(return_value={"data": [{"id": "TRELLIS-3D", "downloaded": False, "labels": ["3d"]}]})
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
