# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The AMD LLM gateway's own prices: read from its /models, kept, never guessed."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from gaia.llm import gateway, gateway_prices
from gaia.llm.gateway import DEFAULT_AUTH_HEADER_NAME, GatewayError, GatewayManager
from gaia.llm.gateway_prices import GatewayPriceError, parse_prices

PAYLOAD = {
    "data": [
        {
            "id": "Claude-Opus-5-5",
            "pricing": {
                "source": "Enterprise",
                "input_per_million": 4.0,
                "output_per_million": 20.0,
                "cache_read_input_per_million": 0.2,
                "cache_creation_input_per_million": 5.0,
                "cache_creation_5m_per_million": 5.0,
            },
        },
        {
            "id": "DeepSeek-V4.1-Flash",
            "pricing": {
                "source": "Public",
                "input_per_million": 0.255,
                "output_per_million": 1.02,
            },
        },
    ]
}


def _response(status=200, body=PAYLOAD):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = body
    return response


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    monkeypatch.setattr(
        gateway, "resolve_gateway_token_with_source", lambda: ("KEY123", "env")
    )
    monkeypatch.setattr(
        gateway.GatewayState,
        "load",
        classmethod(lambda cls, path=None: cls(base_url="https://gw.example/v1")),
    )


def test_parse_keeps_every_rate_under_gaias_names():
    prices = parse_prices(PAYLOAD)
    assert prices["amd.Claude-Opus-5-5"] == {
        "input_per_million": 4.0,
        "output_per_million": 20.0,
        "cache_read_per_million": 0.2,
        "cache_write_per_million": 5.0,
        "source": "Enterprise",
    }
    flash = prices["amd.DeepSeek-V4.1-Flash"]
    assert flash["cache_read_per_million"] is None and flash["source"] == "Public"


@pytest.mark.parametrize(
    "pricing",
    [
        None,
        {},
        {"input_per_million": 1.0},
        {"input_per_million": "1.0", "output_per_million": 2.0},
        {"input_per_million": -1, "output_per_million": 2.0},
        {"input_per_million": float("nan"), "output_per_million": 2.0},
        {"input_per_million": True, "output_per_million": 2.0},
    ],
)
def test_a_model_without_a_usable_price_has_none(pricing):
    entry = {"id": "X"} if pricing is None else {"id": "X", "pricing": pricing}
    assert parse_prices({"data": [entry]}) == {}


@pytest.mark.parametrize("body", [[], {"models": []}, {"data": "x"}, None])
def test_a_payload_without_data_is_an_error(body):
    with pytest.raises(GatewayPriceError, match="'data'"):
        parse_prices(body)


def test_refresh_asks_the_gateway_with_its_key_header(online):
    with patch.object(gateway_prices.requests, "get", return_value=_response()) as get:
        doc = gateway_prices.refresh()
    (call,) = get.call_args_list
    assert call.args[0] == "https://gw.example/v1/models"
    assert call.kwargs["headers"] == {DEFAULT_AUTH_HEADER_NAME: "KEY123"}
    assert call.kwargs["allow_redirects"] is False
    assert set(doc["models"]) == {"amd.Claude-Opus-5-5", "amd.DeepSeek-V4.1-Flash"}
    assert (
        gateway_prices.gateway_price("amd.Claude-Opus-5-5")["output_per_million"]
        == 20.0
    )
    assert gateway_prices.gateway_price("amd.Unknown") is None


@pytest.mark.parametrize("status", [302, 401, 500])
def test_refresh_fails_loudly_on_a_bad_answer(online, status):
    with patch.object(gateway_prices.requests, "get", return_value=_response(status)):
        with pytest.raises(GatewayPriceError, match=f"HTTP {status}"):
            gateway_prices.refresh()
    assert gateway_prices.load() is None


def test_refresh_names_an_unreachable_gateway(online):
    with patch.object(
        gateway_prices.requests, "get", side_effect=requests.ConnectionError("down")
    ):
        with pytest.raises(GatewayPriceError, match="VPN"):
            gateway_prices.refresh()


def test_refresh_without_a_key_says_so(online, monkeypatch):
    def no_key():
        raise GatewayError("No AMD LLM gateway key found")

    monkeypatch.setattr(gateway, "resolve_gateway_token_with_source", no_key)
    with patch.object(gateway_prices.requests, "get") as get:
        with pytest.raises(GatewayPriceError, match="No AMD LLM gateway key"):
            gateway_prices.refresh()
    get.assert_not_called()


def test_refresh_sends_nothing_under_local_only(online, monkeypatch):
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: True)
    with patch.object(gateway_prices.requests, "get") as get:
        with pytest.raises(GatewayPriceError, match="Local only"):
            gateway_prices.refresh()
    get.assert_not_called()


def test_refresh_never_sends_the_key_over_plain_http(online, monkeypatch):
    monkeypatch.setattr(
        gateway.GatewayState,
        "load",
        classmethod(lambda cls, path=None: cls(base_url="http://gw.example/v1")),
    )
    with patch.object(gateway_prices.requests, "get") as get:
        with pytest.raises(GatewayPriceError, match="HTTPS"):
            gateway_prices.refresh()
    get.assert_not_called()


def test_ensure_fresh_reads_once_then_keeps(online):
    with patch.object(gateway_prices.requests, "get", return_value=_response()) as get:
        gateway_prices.ensure_fresh()
        gateway_prices.ensure_fresh()
    assert get.call_count == 1


def test_a_corrupt_price_file_is_named(online):
    gateway_prices.prices_file().write_text(json.dumps([1]), encoding="utf-8")
    with pytest.raises(GatewayPriceError, match="Delete the file"):
        gateway_prices.load()


def test_registration_probe_keeps_the_prices_it_reads(monkeypatch):
    monkeypatch.setenv(gateway.GATEWAY_ENV_VAR, "KEY123")
    manager = GatewayManager.__new__(GatewayManager)
    with patch("gaia.llm.gateway.requests.get", return_value=_response()):
        assert manager.check_reachable("https://gw.example/v1") == 2
    assert (
        gateway_prices.gateway_price("amd.DeepSeek-V4.1-Flash")["input_per_million"]
        == 0.255
    )


def test_registration_probe_without_a_key_keeps_nothing(monkeypatch):
    monkeypatch.delenv(gateway.GATEWAY_ENV_VAR, raising=False)
    monkeypatch.delenv("GAIA_GATEWAY_TOKEN", raising=False)
    manager = GatewayManager.__new__(GatewayManager)
    with patch("gaia.llm.gateway.requests.get", return_value=_response()):
        manager.check_reachable("https://gw.example/v1")
    assert gateway_prices.load() is None
