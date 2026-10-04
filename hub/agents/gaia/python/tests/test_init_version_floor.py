# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GET /v1/gaia/init`` holds Lemonade to GAIA's one version floor."""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402

from gaia.version import LEMONADE_MIN_VERSION  # noqa: E402


def _init(monkeypatch, version):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(
        server_mod,
        "_probe_lemonade",
        lambda: {
            "base_url": "http://127.0.0.1:13305/api/v1",
            "reachable": True,
            "version": version,
            "present": True,
            "ctx_size": 65536,
            "model_id": "Gemma-4-E4B-it-GGUF",
        },
    )
    try:
        client = TestClient(server_mod.build_app(), base_url="http://127.0.0.1:8141")
        return client.get("/v1/gaia/init")
    finally:
        caller_auth.reset()


def test_too_old_lemonade_is_not_ready_and_names_the_fix(monkeypatch):
    response = _init(monkeypatch, "9.1.4")
    body = response.json()

    assert response.status_code == 503
    assert body["ready"] is False
    assert body["lemonade"]["compatible"] is False
    assert body["lemonade"]["min_version"] == LEMONADE_MIN_VERSION
    assert "9.1.4" in body["hint"]
    assert LEMONADE_MIN_VERSION in body["hint"]
    assert "gaia init --force-reinstall" in body["hint"]


def test_calver_dev_build_clears_the_floor(monkeypatch):
    body = _init(monkeypatch, "2026.39.0~12.abc1234").json()

    assert body["ready"] is True
    assert body["lemonade"]["compatible"] is True


def test_unparseable_version_is_reported_as_unknown(monkeypatch):
    body = _init(monkeypatch, "not-a-version").json()

    assert body["lemonade"]["compatible"] is None
