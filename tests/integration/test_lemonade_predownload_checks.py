# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The pre-download checks against a live Lemonade Server (#4214).

``tests/unit/test_lemonade_predownload_checks.py`` mocks the ``/models``,
``/system-info`` and ``/health`` shapes; this proves the real server sends them.
"""

import pytest

from gaia.llm.lemonade_client import LemonadeClient

pytestmark = pytest.mark.integration


@pytest.fixture
def client(require_lemonade):
    return LemonadeClient()


def test_get_model_info_reports_catalog_size_for_undownloaded_model(client):
    catalog = client.list_models(show_all=True)["data"]
    undownloaded = [m for m in catalog if m.get("downloaded") is False]
    if not undownloaded:
        pytest.skip("every catalog model is already downloaded on this server")
    entry = undownloaded[0]
    assert entry["id"] not in [m["id"] for m in client.list_models()["data"]], (
        "undownloaded models now appear without show_all; the show_all "
        "requirement in get_model_info may be obsolete"
    )

    info = client.get_model_info(entry["id"])

    assert info["downloaded"] is False
    assert info["size_gb"] == pytest.approx(float(entry["size"]))


def test_model_storage_reports_free_bytes(client):
    free_bytes, path = client._model_storage_free_bytes()

    assert free_bytes > 0
    assert path != "path not reported"


def test_check_model_loaded_matches_health(client):
    loaded = [
        m["model_name"] for m in client.health_check().get("all_models_loaded", [])
    ]
    downloaded = [m["id"] for m in client.list_models()["data"]]

    for model_id in loaded:
        assert client.check_model_loaded(model_id) is True
    for model_id in set(downloaded) - set(loaded):
        assert client.check_model_loaded(model_id) is False
