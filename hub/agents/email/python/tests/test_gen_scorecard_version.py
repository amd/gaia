# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gen_scorecard's Lemonade version lookup: the right URL, with the right key."""

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

_PATH = Path(__file__).resolve().parents[1] / "packaging" / "gen_scorecard.py"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("gen_scorecard", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _response(body):
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


@pytest.mark.parametrize(
    "configured", ["http://127.0.0.1:13305", "http://127.0.0.1:13305/api/v1"]
)
def test_either_url_form_reaches_health_once(gen, monkeypatch, tmp_path, configured):
    """The resolver adds /api/v1; the lookup must not add it a second time."""
    from gaia.llm.lemonade_client import resolve_lemonade_base_url

    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    monkeypatch.setenv("LEMONADE_BASE_URL", configured)
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    with mock.patch(
        "urllib.request.urlopen", return_value=_response({"version": "11.8.1"})
    ) as urlopen:
        version = gen._query_lemonade_version(resolve_lemonade_base_url())

    assert version == "11.8.1"
    assert urlopen.call_args.args[0].full_url == "http://127.0.0.1:13305/api/v1/health"


def test_gaias_own_server_is_asked_with_its_key(gen, monkeypatch, tmp_path):
    import os

    (tmp_path / "lemonade").mkdir()
    (tmp_path / "lemonade" / "state.json").write_text(
        json.dumps({"pid": os.getpid(), "port": 51234, "api_key": "own-key"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    for name in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
        monkeypatch.delenv(name, raising=False)
    from gaia.llm.lemonade_client import resolve_lemonade_base_url

    with mock.patch(
        "urllib.request.urlopen", return_value=_response({"version": "11.8.1"})
    ) as urlopen:
        gen._query_lemonade_version(resolve_lemonade_base_url())

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://localhost:51234/api/v1/health"
    assert request.get_header("Authorization") == "Bearer own-key"
