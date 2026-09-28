# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The ``/health`` response schema, checked against a live server.

Roughly 45 unit tests hand-build Lemonade's health payload and none verify it:
8 in ``tests/unit/test_chat_preflight.py``, 9 in
``tests/unit/test_llamacpp_backend.py::TestEnsureModelLoadedCtxResolution``,
15 in ``tests/unit/test_lemonade_client_ctx_override.py``, and 13 in
``tests/unit/chat/ui/test_server.py::TestSystemStatus``. Those are fine as unit
tests -- they exercise decision logic, which is what mocks are for. What was
missing is anything proving the shape they invent is the shape Lemonade sends.

``ctx_size`` already moved once (Lemonade 9.1.4 relocated it from a top-level
``context_size`` into ``all_models_loaded[].recipe_options``). If it moves again,
all 45 stay green while the UI reports the wrong context size and the ctx-pinning
feature silently breaks.

The pre-existing live test
(``tests/test_lemonade_client.py::test_integration_health_check_914_format``)
cannot catch that: every assertion in it sits inside an ``if field in response``
guard, so a rename prints a warning and passes. This file asserts unconditionally,
and explicitly loads a model before inspecting the loaded-entry schema.
"""

import pytest

from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, LemonadeClient

pytestmark = pytest.mark.integration

# The path every ctx-aware caller walks: ui/routers/system.py, ui/server.py,
# ui/_chat_helpers.py, and LemonadeClient._ensure_model_loaded.
CTX_PATH = "all_models_loaded[].recipe_options.ctx_size"


@pytest.fixture
def health(require_lemonade):
    return LemonadeClient().health_check()


def test_health_reports_ok_status(health):
    assert health.get("status") == "ok", health


def test_all_models_loaded_is_present_and_a_list(health):
    """The 9.1.4+ container the mocked tests assume exists."""
    assert "all_models_loaded" in health, (
        f"'all_models_loaded' is gone -- {CTX_PATH} no longer resolves. "
        f"Every ctx-resolution unit test invents this key. Got: {sorted(health)}"
    )
    assert isinstance(health["all_models_loaded"], list), health["all_models_loaded"]


def test_loaded_model_entry_carries_the_fields_callers_read(require_lemonade):
    """A loaded entry must expose model identity and its context size.

    ``_ensure_model_loaded`` matches on ``id`` *or* ``model_name``, so either is
    acceptable -- but at least one must be there.
    """
    client = LemonadeClient()
    client.load_model(DEFAULT_MODEL_NAME)
    health = client.health_check()
    models = health["all_models_loaded"]
    assert models, "Lemonade reported no loaded model after a successful load"

    entry = models[0]
    assert "id" in entry or "model_name" in entry, (
        f"neither 'id' nor 'model_name' present; _ensure_model_loaded matches on "
        f"these and would reload on every call. Got: {sorted(entry)}"
    )
    assert "recipe_options" in entry, (
        f"'recipe_options' is gone -- {CTX_PATH} no longer resolves. "
        f"Got: {sorted(entry)}"
    )

    ctx = entry["recipe_options"].get("ctx_size")
    assert isinstance(ctx, int) and ctx > 0, (
        f"{CTX_PATH} is {ctx!r}, expected a positive int. The UI reports this as "
        f"the context size and the preflight gates on it."
    )
