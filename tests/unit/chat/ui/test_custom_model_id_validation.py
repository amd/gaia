# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A custom model ID is one short line; anything else is refused, not stored.

Fuzzing found PUT /api/settings saving a 200,000-character ID and IDs with a
newline or null byte, which were then sent to the model server and logged on
every turn.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.models import ModelStatus
from gaia.ui.server import create_app

UI = {"X-Gaia-UI": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    gaia_home = tmp_path / "gaia-home"
    monkeypatch.setenv("GAIA_HOME", str(gaia_home))
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", gaia_home / "config.json")
    return TestClient(
        create_app(db_path=":memory:"), base_url="http://127.0.0.1", headers=UI
    )


@pytest.mark.parametrize(
    "value",
    ["a\x00b", "qwen\nrm -rf", "tab\tname", "m" * 257],
    ids=["null-byte", "newline", "tab", "too-long"],
)
def test_an_impossible_model_id_is_refused_and_not_stored(client, value):
    r = client.put("/api/settings", json={"custom_model": value})
    assert r.status_code == 422
    assert client.get("/api/settings").json()["custom_model"] in (None, "")


@pytest.mark.parametrize(
    "value",
    ["huihui-ai/Huihui-Qwen3.5-35B-A3B-abliterated", "user.Phi-4-Mini-GGUF", ""],
    ids=["hugging-face", "user-namespace", "clear"],
)
def test_real_model_ids_still_save(client, value):
    # The real check asks the model server whether the model exists.
    status = ModelStatus(found=True, downloaded=True, loaded=False, checked=True)
    with patch(
        "gaia.ui.routers.system._check_model_status", AsyncMock(return_value=status)
    ):
        r = client.put("/api/settings", json={"custom_model": value})
    assert r.status_code == 200
    assert (r.json()["custom_model"] or "") == value
