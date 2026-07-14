# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""RED-first tests for the NPU auto-select resolver (#1439).

``resolve_default_email_model`` decides, per Lemonade server, whether the
email agent's default model should be the NPU-native ``gemma4-it-e2b-FLM``
or the existing GGUF default (``gaia.llm.lemonade_client.DEFAULT_MODEL_NAME``).
It probes Lemonade's raw ``/system-info`` (never
``LemonadeClient.get_system_info()`` -- that call carries a 900s default
timeout, the #1677 hang class) and, only when an NPU is reported available,
the ``/models`` catalog for FLM presence.

The two-literal guarantee is the security-relevant property under test: the
function must NEVER echo any field from the Lemonade response back as the
resolved model id -- it always returns one of exactly two hardcoded
constants.

``gaia_agent_email.model_select`` does not exist yet (a teammate implements
it after this file lands) -- every test here is expected to fail collection
with ``ModuleNotFoundError`` until then. That is the correct RED state.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

pytest.importorskip("gaia_agent_email")

import requests  # noqa: E402
from gaia_agent_email.model_select import (  # noqa: E402
    _LEMONADE_PROBE_CONNECT_TIMEOUT,
    _LEMONADE_PROBE_READ_TIMEOUT,
    NPU_EMAIL_MODEL_ID,
    _reset_model_select_cache,
    resolve_default_email_model,
)

from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME  # noqa: E402


def _system_info_body(*, npu_available, extra_npu_fields=None):
    amd_npu = {"available": npu_available}
    if extra_npu_fields:
        amd_npu.update(extra_npu_fields)
    return {"devices": {"amd_npu": amd_npu}}


def _models_body(*, present):
    return {"data": [{"id": NPU_EMAIL_MODEL_ID}] if present else []}


def _fake_get_factory(
    probe_base,
    *,
    system_info_body=None,
    system_info_raises=None,
    models_body=None,
    models_raises=None,
):
    """Strict ``requests.get`` stub serving ``/system-info`` and ``/models``
    rooted at ``probe_base``. Any other URL raises loudly instead of
    silently returning something plausible -- a resolver bug that queries
    the wrong endpoint (or the wrong server) must fail the test, not pass
    on a lucky mock. Returns ``(fake_get, calls)`` where ``calls`` is a
    mutable list of ``(url, kwargs)`` the caller can inspect after use.
    """
    calls: list = []

    def _fake_get(url, *args, **kwargs):
        calls.append((url, kwargs))
        if url == f"{probe_base}/system-info":
            if system_info_raises is not None:
                raise system_info_raises
            resp = MagicMock(status_code=200)
            resp.json.return_value = system_info_body
            return resp
        if url == f"{probe_base}/models":
            if models_raises is not None:
                raise models_raises
            resp = MagicMock(status_code=200)
            resp.json.return_value = models_body
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    return _fake_get, calls


# ---------------------------------------------------------------------------
# Behavior matrix
# ---------------------------------------------------------------------------


def test_npu_available_and_flm_servable_selects_npu_model(monkeypatch):
    probe_base = "http://127.0.0.1:9601/api/v1"
    fake, calls = _fake_get_factory(
        probe_base,
        system_info_body=_system_info_body(npu_available=True),
        models_body=_models_body(present=True),
    )
    monkeypatch.setattr(requests, "get", fake)

    result = resolve_default_email_model("http://127.0.0.1:9601")

    assert result == NPU_EMAIL_MODEL_ID
    assert any(url == f"{probe_base}/models" for url, _ in calls)


def test_npu_available_but_flm_not_servable_falls_back_and_logs(monkeypatch, caplog):
    probe_base = "http://127.0.0.1:9602/api/v1"
    fake, _calls = _fake_get_factory(
        probe_base,
        system_info_body=_system_info_body(npu_available=True),
        models_body=_models_body(present=False),
    )
    monkeypatch.setattr(requests, "get", fake)

    with caplog.at_level(logging.INFO):
        result = resolve_default_email_model("http://127.0.0.1:9602")

    assert result == DEFAULT_MODEL_NAME
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info_records) == 1
    assert "gaia init --profile npu" in info_records[0].getMessage()


@pytest.mark.parametrize(
    "system_info_body",
    [
        _system_info_body(npu_available=False),
        {},
        {"devices": {}},
    ],
    ids=["npu_unavailable", "devices_missing", "amd_npu_missing"],
)
def test_npu_unavailable_short_circuits_without_models_probe(
    monkeypatch, system_info_body
):
    probe_base = "http://127.0.0.1:9603/api/v1"
    fake, calls = _fake_get_factory(probe_base, system_info_body=system_info_body)
    monkeypatch.setattr(requests, "get", fake)

    result = resolve_default_email_model("http://127.0.0.1:9603")

    assert result == DEFAULT_MODEL_NAME
    assert not any(url == f"{probe_base}/models" for url, _ in calls)


# ---------------------------------------------------------------------------
# Transport failures -- always DEFAULT_MODEL_NAME, always logged, never cached
# ---------------------------------------------------------------------------


def test_system_info_connection_error_falls_back_and_logs(monkeypatch, caplog):
    probe_base = "http://127.0.0.1:9620/api/v1"
    fake, _calls = _fake_get_factory(
        probe_base, system_info_raises=requests.exceptions.ConnectionError("refused")
    )
    monkeypatch.setattr(requests, "get", fake)

    with caplog.at_level(logging.INFO):
        result = resolve_default_email_model("http://127.0.0.1:9620")

    assert result == DEFAULT_MODEL_NAME
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info_records) == 1


def test_system_info_timeout_falls_back_and_logs(monkeypatch, caplog):
    probe_base = "http://127.0.0.1:9621/api/v1"
    fake, _calls = _fake_get_factory(
        probe_base, system_info_raises=requests.exceptions.Timeout("timed out")
    )
    monkeypatch.setattr(requests, "get", fake)

    with caplog.at_level(logging.INFO):
        result = resolve_default_email_model("http://127.0.0.1:9621")

    assert result == DEFAULT_MODEL_NAME
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info_records) == 1


def test_system_info_transport_failure_is_not_cached(monkeypatch):
    probe_base = "http://127.0.0.1:9622/api/v1"
    fake, calls = _fake_get_factory(
        probe_base, system_info_raises=requests.exceptions.ConnectionError("refused")
    )
    monkeypatch.setattr(requests, "get", fake)

    resolve_default_email_model("http://127.0.0.1:9622")
    first_count = sum(1 for url, _ in calls if url == f"{probe_base}/system-info")

    resolve_default_email_model("http://127.0.0.1:9622")
    second_count = sum(1 for url, _ in calls if url == f"{probe_base}/system-info")

    assert second_count == first_count + 1


def test_models_probe_transport_failure_falls_back_and_logs(monkeypatch, caplog):
    probe_base = "http://127.0.0.1:9623/api/v1"
    fake, _calls = _fake_get_factory(
        probe_base,
        system_info_body=_system_info_body(npu_available=True),
        models_raises=requests.exceptions.ConnectionError("reset by peer"),
    )
    monkeypatch.setattr(requests, "get", fake)

    with caplog.at_level(logging.INFO):
        result = resolve_default_email_model("http://127.0.0.1:9623")

    assert result == DEFAULT_MODEL_NAME
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info_records) == 1


def test_models_probe_transport_failure_is_not_cached(monkeypatch):
    probe_base = "http://127.0.0.1:9624/api/v1"
    fake, calls = _fake_get_factory(
        probe_base,
        system_info_body=_system_info_body(npu_available=True),
        models_raises=requests.exceptions.ConnectionError("reset by peer"),
    )
    monkeypatch.setattr(requests, "get", fake)

    resolve_default_email_model("http://127.0.0.1:9624")
    first_count = sum(1 for url, _ in calls if url == f"{probe_base}/system-info")

    resolve_default_email_model("http://127.0.0.1:9624")
    second_count = sum(1 for url, _ in calls if url == f"{probe_base}/system-info")

    assert second_count == first_count + 1


# ---------------------------------------------------------------------------
# Success-only caching
# ---------------------------------------------------------------------------


def test_successful_flm_resolution_is_cached(monkeypatch):
    probe_base = "http://127.0.0.1:9610/api/v1"
    fake, calls = _fake_get_factory(
        probe_base,
        system_info_body=_system_info_body(npu_available=True),
        models_body=_models_body(present=True),
    )
    monkeypatch.setattr(requests, "get", fake)

    first = resolve_default_email_model("http://127.0.0.1:9610")
    count_after_first = len(calls)
    second = resolve_default_email_model("http://127.0.0.1:9610")
    count_after_second = len(calls)

    assert first == NPU_EMAIL_MODEL_ID
    assert second == NPU_EMAIL_MODEL_ID
    assert count_after_second == count_after_first


def test_successful_e4b_resolution_is_cached_npu_unavailable(monkeypatch):
    probe_base = "http://127.0.0.1:9611/api/v1"
    fake, calls = _fake_get_factory(
        probe_base, system_info_body=_system_info_body(npu_available=False)
    )
    monkeypatch.setattr(requests, "get", fake)

    first = resolve_default_email_model("http://127.0.0.1:9611")
    count_after_first = len(calls)
    second = resolve_default_email_model("http://127.0.0.1:9611")
    count_after_second = len(calls)

    assert first == DEFAULT_MODEL_NAME
    assert second == DEFAULT_MODEL_NAME
    assert count_after_second == count_after_first


def test_reset_model_select_cache_clears_cache(monkeypatch):
    probe_base = "http://127.0.0.1:9612/api/v1"
    fake, calls = _fake_get_factory(
        probe_base,
        system_info_body=_system_info_body(npu_available=True),
        models_body=_models_body(present=True),
    )
    monkeypatch.setattr(requests, "get", fake)

    resolve_default_email_model("http://127.0.0.1:9612")
    count_before_reset = len(calls)

    _reset_model_select_cache()

    resolve_default_email_model("http://127.0.0.1:9612")
    count_after_reset = len(calls)

    assert count_after_reset > count_before_reset


def test_different_base_urls_get_independent_cache_entries(monkeypatch):
    base_a = "http://127.0.0.1:9613"
    base_b = "http://127.0.0.1:9614"
    probe_base_a = f"{base_a}/api/v1"
    probe_base_b = f"{base_b}/api/v1"
    calls: list = []

    def _fake_get(url, *args, **kwargs):
        calls.append((url, kwargs))
        if url == f"{probe_base_a}/system-info":
            resp = MagicMock(status_code=200)
            resp.json.return_value = _system_info_body(npu_available=True)
            return resp
        if url == f"{probe_base_a}/models":
            resp = MagicMock(status_code=200)
            resp.json.return_value = _models_body(present=True)
            return resp
        if url == f"{probe_base_b}/system-info":
            resp = MagicMock(status_code=200)
            resp.json.return_value = _system_info_body(npu_available=False)
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    result_a = resolve_default_email_model(base_a)
    result_b = resolve_default_email_model(base_b)

    assert result_a == NPU_EMAIL_MODEL_ID
    assert result_b == DEFAULT_MODEL_NAME
    # base_b must have been genuinely probed, not short-circuited by
    # base_a's already-cached entry.
    assert any(url == f"{probe_base_b}/system-info" for url, _ in calls)


# ---------------------------------------------------------------------------
# Two-literal guarantee + timeout pin + default probe base
# ---------------------------------------------------------------------------


def test_resolved_value_is_always_exactly_one_of_two_literals(monkeypatch):
    probe_base = "http://127.0.0.1:9615/api/v1"
    injected_fields = {
        "name": "'; DROP TABLE models;--",
        "some_id_field": "not-a-real-model",
    }
    fake, _calls = _fake_get_factory(
        probe_base,
        system_info_body=_system_info_body(
            npu_available=True, extra_npu_fields=injected_fields
        ),
        models_body=_models_body(present=True),
    )
    monkeypatch.setattr(requests, "get", fake)

    result = resolve_default_email_model("http://127.0.0.1:9615")

    assert result in (NPU_EMAIL_MODEL_ID, DEFAULT_MODEL_NAME)
    assert result == NPU_EMAIL_MODEL_ID
    for injected_value in injected_fields.values():
        assert injected_value not in result


def test_system_info_probe_uses_pinned_timeout_tuple(monkeypatch):
    probe_base = "http://127.0.0.1:9616/api/v1"
    fake, calls = _fake_get_factory(
        probe_base, system_info_body=_system_info_body(npu_available=False)
    )
    monkeypatch.setattr(requests, "get", fake)

    resolve_default_email_model("http://127.0.0.1:9616")

    system_info_calls = [
        kwargs for url, kwargs in calls if url == f"{probe_base}/system-info"
    ]
    assert len(system_info_calls) == 1
    assert system_info_calls[0].get("timeout") == (
        _LEMONADE_PROBE_CONNECT_TIMEOUT,
        _LEMONADE_PROBE_READ_TIMEOUT,
    )


def test_resolve_default_email_model_with_no_args_uses_default_probe_base(
    monkeypatch,
):
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    probe_base = "http://localhost:13305/api/v1"
    fake, calls = _fake_get_factory(
        probe_base, system_info_body=_system_info_body(npu_available=False)
    )
    monkeypatch.setattr(requests, "get", fake)

    result = resolve_default_email_model()

    assert result == DEFAULT_MODEL_NAME
    assert any(url == f"{probe_base}/system-info" for url, _ in calls)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
