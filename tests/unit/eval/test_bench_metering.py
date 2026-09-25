# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Metering: the provider's own meter, a fixed window, and a named source for every dollar."""

import datetime as dt
import logging

import pytest

from gaia.eval.bench import config as bench_config
from gaia.eval.bench import metering

KEY = "fw_" + "SecretKeyValue0123456789"


class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._body


def _usage(prompt, cached, completion, model="glm-5p3-flash"):
    return {
        "serverlessCosts": [
            {
                "modelName": f"accounts/fireworks/models/{model}",
                "promptTokens": prompt,
                "cachedPromptTokens": cached,
                "uncachedPromptTokens": prompt - cached,
                "completionTokens": completion,
            }
        ]
    }


@pytest.fixture
def meter(monkeypatch):
    """A fake billing API; records every request it gets."""
    calls = []
    answers = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "headers": headers, "params": params})
        return answers.pop(0)

    monkeypatch.setenv("FIREWORKS_API_KEY", KEY)
    monkeypatch.setattr(metering.requests, "get", get)
    return calls, answers


def test_both_snapshots_count_from_one_fixed_start(meter):
    calls, answers = meter
    answers += [_Response(_usage(1000, 400, 100)), _Response(_usage(3000, 1400, 400))]
    before = metering.snapshot("acct")
    after = metering.snapshot("acct", metering.parse_window_start(before))
    assert calls[0]["params"]["startTime"] == calls[1]["params"]["startTime"]
    cost = metering.metered_cost(before, after, "fireworks.glm-5p3-flash")
    assert (cost["cached_tokens"], cost["input_tokens"], cost["output_tokens"]) == (
        1000,
        2000,
        300,
    )
    assert cost["source"] == metering.METERED
    assert cost["usd"] == pytest.approx((1000 * 0.15 + 1000 * 0.03 + 300 * 0.50) / 1e6)


def test_a_sliding_window_would_go_negative_and_is_refused(meter):
    _, answers = meter
    # A later window that no longer covers older usage reads lower than before.
    answers += [_Response(_usage(5000, 1000, 500)), _Response(_usage(900, 100, 90))]
    before = metering.snapshot("acct")
    after = metering.snapshot("acct", metering.parse_window_start(before))
    with pytest.raises(metering.MeterError, match="went backwards"):
        metering.metered_cost(before, after, "glm-5p3-flash")


def test_snapshots_from_different_windows_are_not_compared():
    before = {"window_start": "2026-09-01T00:00:00Z", "models": {}}
    after = {"window_start": "2026-09-02T00:00:00Z", "models": {}}
    with pytest.raises(metering.MeterError, match="different window starts"):
        metering.metered_cost(before, after, "glm-5p3-flash")


def test_the_window_starts_before_the_day_a_run_began():
    now = dt.datetime(2026, 9, 24, 23, 59, tzinfo=dt.timezone.utc)
    assert metering.window_start(now) == dt.datetime(
        2026, 9, 23, tzinfo=dt.timezone.utc
    )


def test_qwen_bills_under_its_deployment_name(meter):
    _, answers = meter
    billed = "qwen3p8-max-llm-thinking-nvfp4-0902"
    answers += [
        _Response(_usage(100, 0, 10, billed)),
        _Response(_usage(1100, 0, 110, billed)),
    ]
    before = metering.snapshot("acct")
    after = metering.snapshot("acct", metering.parse_window_start(before))
    for name in ("fireworks.qwen3p8-max", "qwen3p8-2p4t-a95b"):
        cost = metering.metered_cost(before, after, name)
        assert cost["billed_as"] == billed and cost["output_tokens"] == 100
        assert cost["usd"] == pytest.approx((1000 * 2.00 + 100 * 6.00) / 1e6)


def test_the_key_is_only_ever_a_header_and_never_logged(meter, caplog):
    calls, answers = meter
    caplog.set_level(logging.DEBUG)
    answers.append(_Response({"error": "nope"}, 403))
    with pytest.raises(metering.MeterError) as raised:
        metering.snapshot("acct")
    assert calls[0]["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert KEY not in calls[0]["url"] and KEY not in str(calls[0]["params"])
    assert KEY not in str(raised.value) and KEY not in caplog.text
    assert "FIREWORKS_ACCOUNT_ID" in str(raised.value)


def test_metering_without_an_account_fails_at_startup(monkeypatch):
    monkeypatch.delenv(bench_config.ENV_FIREWORKS_ACCOUNT, raising=False)
    with pytest.raises(bench_config.BenchConfigError, match="FIREWORKS_ACCOUNT_ID"):
        bench_config.resolve(meter="fireworks")
    monkeypatch.setenv(bench_config.ENV_FIREWORKS_ACCOUNT, "acct")
    assert bench_config.resolve(meter="fireworks").fireworks_account == "acct"


@pytest.mark.parametrize(
    "model, card",
    [
        ("fireworks.glm-5p3-flash", (0.15, 0.03, 0.50)),
        ("accounts/fireworks/models/deepseek-v4p1-flash", (0.30, 0.006, 1.20)),
        ("accounts/fireworks/routers/glm-5p3-fast", (1.40, 0.26, 4.40)),
        ("qwen3p8-2p4t-a95b", (2.00, 0.25, 6.00)),
        ("Gemma-4-E4B-it-GGUF", None),
    ],
)
def test_rate_cards(model, card):
    assert metering.rate_card(model) == card


def test_a_model_without_a_card_has_no_price_rather_than_a_guess():
    assert metering.price("Gemma-4-E4B-it-GGUF", 1000, 0, 100) is None
    assert metering.price("glm-5p3-flash", 1_000_000, 0, 0) == pytest.approx(0.15)
