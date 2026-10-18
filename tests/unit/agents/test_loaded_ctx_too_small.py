# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`Agent._is_loaded_ctx_too_small` judges only the agent's own model (#5063).
On a context overflow the agent probes /health to choose between in-loop
trimming (model was loaded fine) and a reload (model was loaded undersized).
The probe used to flag *any* resident llm/vlm below its target and ignored the
model's trained 'max_context_window', so an unrelated small model, or an
unreachable target, made a real overflow look like a bad load.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from gaia.agents.base.agent import Agent
from gaia.llm.lemonade_client import LemonadeClientError

OWN_MODEL = "Qwen3-4B-GGUF"
EXPECTED = 65536


def _entry(name, ctx, ceiling=None, type_="llm"):
    entry = {"model_name": name, "type": type_, "recipe_options": {"ctx_size": ctx}}
    if ceiling is not None:
        entry["max_context_window"] = ceiling

    return entry


def _probe(entries, model_id=OWN_MODEL):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"all_models_loaded": entries}
    agent = SimpleNamespace(model_id=model_id, device="gpu")
    with (
        patch("httpx.get", return_value=resp),
        patch("gaia.llm.lemonade_client.resolve_ctx_size", return_value=EXPECTED),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value="http://localhost:13305/api/v1",
        ),
    ):
        return Agent._is_loaded_ctx_too_small(agent)


def test_correctly_loaded_agent_model_ignores_small_resident_vlm():
    assert (
        _probe([_entry(OWN_MODEL, EXPECTED), _entry("Qwen3-VL-4B", 4096, type_="vlm")])
        is False
    )


def test_resident_model_listed_first__mask_own_model():
    assert (
        _probe([_entry("Qwen3-VL-4B", 4096, type_="vlm"), _entry(OWN_MODEL, 4096)])
        is True
    )


def test_model_loaded_at_its_trained_max_is_not_undersized():
    assert _probe([_entry(OWN_MODEL, 32768, ceiling=32768)]) is False


def test_undersized_below_ceiling_is_still_flagged():
    assert _probe([_entry(OWN_MODEL, 4096, ceiling=32768)]) is True


def test_agent_model_undersized():
    assert _probe([_entry(OWN_MODEL, 4096)]) is True


def test_unknown_ceiling_does_not_clamp():
    # None / 0 means "unknown", that is:
    # there is no ceiling to respect nor a clamp to 0
    assert _probe([_entry(OWN_MODEL, 4096, ceiling=0)]) is True


def test_user_prefixed_entry_matches_own_model():
    assert _probe([_entry("user." + OWN_MODEL, 4096)]) is True


def test_own_model_not_loaded_is_false():
    assert _probe([_entry("Qwen3-VL-4B", 4096, type_="vlm")]) is False


def _probe_raw(
    payload=None, *, status=200, get_error=None, json_error=None, model_id=OWN_MODEL
):
    """Like the '_probe' but lets a test control the raw /health response"""
    resp = MagicMock(status_code=status)
    if json_error is not None:
        resp.json.side_effect = json_error
    else:
        resp.json.return_value = payload
    agent = SimpleNamespace(model_id=model_id, device="gpu")
    with (
        patch("httpx.get", return_value=resp, side_effect=get_error),
        patch("gaia.llm.lemonade_client.resolve_ctx_size", return_value=EXPECTED),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value="http://localhost:13305/api/v1",
        ),
    ):
        return Agent._is_loaded_ctx_too_small(agent)


@pytest.mark.parametrize(
    "ctx, expected",
    [(EXPECTED, False), (EXPECTED - 1, True)],
    ids=["exactly_at_target", "one_below_target"],
)
def test_ctx_boundary_is_strictly_below_target(ctx, expected):
    assert _probe([_entry(OWN_MODEL, ctx)]) is expected


@pytest.mark.parametrize(
    "entry",
    [
        {"model_name": OWN_MODEL, "type": "llm"},  # no recipe_options
        {"model_name": OWN_MODEL, "type": "llm", "recipe_options": {}},  # no ctx_size
        {"model_name": OWN_MODEL, "type": "llm", "recipe_options": {"ctx_size": None}},
        {"model_name": OWN_MODEL, "type": "llm", "recipe_options": {"ctx_size": 0}},
    ],
    ids=["no_recipe_options", "no_ctx_size", "ctx_size_none", "ctx_size_zero"],
)
def test_unknown_ctx_size_is_not_undersized(entry):
    assert _probe([entry]) is False


def test_non_200_health_response_returns_false():
    assert (
        _probe_raw({"all_models_loaded": [_entry(OWN_MODEL, 4096)]}, status=500)
        is False
    )


# 4. Probe failures fall back to trimming instead of raising.
@pytest.mark.parametrize(
    "kwargs",
    [
        {"get_error": httpx.ConnectError("lemonade down")},
        {"get_error": httpx.ReadTimeout("slow")},
        {"json_error": ValueError("not json")},
        {"get_error": LemonadeClientError("boom")},
    ],
    ids=["connect_error", "timeout", "bad_json", "lemonade_client_error"],
)
def test_probe_failure_returns_false(kwargs):
    assert (
        _probe_raw({"all_models_loaded": [_entry(OWN_MODEL, 4096)]}, **kwargs) is False
    )


@pytest.mark.parametrize(
    "payload",
    [{"all_models_loaded": []}, {}],
    ids=["empty_list", "missing_key"],
)
def test_no_loaded_models_returns_false(payload):
    assert _probe_raw(payload) is False


def test_matches_agent_model_by_id_field():
    entry = {
        "model_name": "something-else",
        "id": OWN_MODEL,
        "type": "llm",
        "recipe_options": {"ctx_size": 4096},
    }
    assert _probe([entry]) is True


def test_model_match_ignores_case():
    assert _probe([_entry(OWN_MODEL.lower(), 4096)]) is True


def test_non_chat_entry_with_same_name_is_ignored():
    assert _probe([_entry(OWN_MODEL, 4096, type_="embedding")]) is False


@pytest.mark.parametrize("model_id", [None, ""], ids=["none", "empty"])
def test_missing_model_id_returns_false(model_id):
    assert _probe([_entry(OWN_MODEL, 4096)], model_id=model_id) is False


def test_high_trained_ceiling_still_flags_undersized_load():
    assert _probe([_entry(OWN_MODEL, 4096, ceiling=262144)]) is True


def test_cloud_model_returns_false():
    cloud_id = "fireworks.some-model"
    assert _probe([_entry(cloud_id, 4096)], model_id=cloud_id) is False
