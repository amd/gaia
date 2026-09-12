# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The integration probe must use the same server and auth as real clients."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests


def _probe():
    path = Path(__file__).parents[1] / "conftest.py"
    spec = importlib.util.spec_from_file_location("availability_conftest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.lemonade_available.__wrapped__()


def test_probe_uses_configured_url_and_auth(monkeypatch):
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:19999/")
    monkeypatch.setenv("LEMONADE_API_KEY", "fixture-key")
    get = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr(requests, "get", get)

    assert _probe() is True
    get.assert_called_once_with(
        "http://127.0.0.1:19999/api/v1/health",
        timeout=5,
        headers={"Authorization": "Bearer fixture-key"},
    )


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_auth_is_not_misreported_as_offline(monkeypatch, status):
    monkeypatch.setattr(requests, "get", Mock(return_value=Mock(status_code=status)))
    with pytest.raises(pytest.fail.Exception, match="rejected authentication"):
        _probe()


def test_unreachable_server_is_unavailable(monkeypatch):
    monkeypatch.setattr(requests, "get", Mock(side_effect=requests.ConnectionError))
    assert _probe() is False
