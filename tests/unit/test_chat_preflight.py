# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for _maybe_load_expected_model pre-flight check in _chat_helpers.py.

LemonadeManager, LemonadeClient, and httpx are imported lazily (inside the
try-block) so patches must target their source modules, not _chat_helpers.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from gaia.ui._chat_helpers import _maybe_load_expected_model

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------
_HTTPX_GET = "httpx.get"
_LEMONADE_MANAGER = "gaia.llm.lemonade_manager.LemonadeManager"
_LEMONADE_CLIENT = "gaia.llm.lemonade_client.LemonadeClient"
_BASE_URL = "http://localhost:13305/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _health_ok(all_models):
    """Mock httpx.Response for a 200 /health reply."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {"all_models_loaded": all_models, "model_loaded": None}
    return resp


def _model(type_, name=None, ctx_size=32768):
    """Build an ``all_models_loaded`` entry.

    ``name`` defaults to a non-matching ``test-<type>`` so callers must opt
    in to the "right model loaded" fast path by passing the expected
    model_id explicitly. ``ctx_size`` defaults to the 32K floor the
    pre-flight requires; pass a smaller value to exercise the small-ctx
    reload branch.
    """
    return {
        "type": type_,
        "model_name": name if name is not None else f"test-{type_}",
        "recipe_options": {"ctx_size": ctx_size},
    }


# Constant used by fast-path tests below: the model_name *must* match the
# expected model passed to ``_maybe_load_expected_model`` for the new
# right-model + right-ctx pre-flight to short-circuit.
_EXPECTED_LLM = "Qwen3.5-35B-A3B-GGUF"


# ---------------------------------------------------------------------------
# 1. No model loaded → load_model called
# ---------------------------------------------------------------------------


def test_no_model_loaded_triggers_load():
    """Empty all_models_loaded → load_model must be called once."""
    health = _health_ok([])

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=health),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        _maybe_load_expected_model("Qwen3.5-35B-A3B-GGUF")

        mock_instance.load_model.assert_called_once()
        assert mock_instance.load_model.call_args.args[0] == "Qwen3.5-35B-A3B-GGUF"


# ---------------------------------------------------------------------------
# 2. Embedding model only → load_model called
# ---------------------------------------------------------------------------


def test_embedding_only_triggers_load():
    """Embedding-only Lemonade state → load_model must be called once."""
    health = _health_ok([_model("embedding")])

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=health),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        _maybe_load_expected_model("Qwen3.5-35B-A3B-GGUF")

        mock_instance.load_model.assert_called_once()


# ---------------------------------------------------------------------------
# 2b. Right model loaded but ctx_size < 32K → load_model IS called (reload)
# ---------------------------------------------------------------------------


def test_right_model_wrong_ctx_triggers_reload():
    """Right model name but ctx_size below the 32K floor must still
    trigger a reload — Lemonade may have auto-loaded the model at its
    default 4096 ctx, which silently truncates ChatAgent's >7K-token
    system prompt and produces empty streams.

    Negative coverage for the fast-path tests above: they confirm the
    skip; this confirms the reload happens whenever ctx is too small,
    even if the model name matches.
    """
    health = _health_ok([_model("llm", name=_EXPECTED_LLM, ctx_size=4096)])

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=health),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        _maybe_load_expected_model(_EXPECTED_LLM)

        # load_model must be called with the 32K ctx_size, not whatever
        # Lemonade was holding the model at.
        mock_instance.load_model.assert_called_once()
        kwargs = mock_instance.load_model.call_args.kwargs
        assert (
            kwargs.get("ctx_size") == 32768
        ), f"Expected reload at ctx_size=32768, got {kwargs.get('ctx_size')!r}"


# ---------------------------------------------------------------------------
# 3. LLM active (fast path) → load_model NOT called
# ---------------------------------------------------------------------------


def test_llm_active_skips_load():
    """Right LLM + ctx >= 32K → fast path; LemonadeClient must NOT be instantiated."""
    health = _health_ok([_model("llm", name=_EXPECTED_LLM)])

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=health),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL

        _maybe_load_expected_model(_EXPECTED_LLM)

        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 4. VLM active (fast path) → load_model NOT called
# ---------------------------------------------------------------------------


def test_vlm_active_skips_load():
    """Right VLM + ctx >= 32K → fast path; LemonadeClient must NOT be instantiated."""
    health = _health_ok([_model("vlm", name=_EXPECTED_LLM)])

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=health),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL

        _maybe_load_expected_model(_EXPECTED_LLM)

        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Lemonade returns non-200 → load_model NOT called, no crash
# ---------------------------------------------------------------------------


def test_non_200_health_skips_load():
    """Non-200 /health → skip silently; LemonadeClient must NOT be instantiated."""
    bad = MagicMock(spec=httpx.Response)
    bad.status_code = 503

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=bad),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL

        _maybe_load_expected_model("Qwen3.5-35B-A3B-GGUF")  # must not raise

        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Health check raises ConnectError → no crash, load_model NOT called
# ---------------------------------------------------------------------------


def test_connect_error_no_crash():
    """httpx.ConnectError during /health → swallowed; LemonadeClient NOT instantiated."""
    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, side_effect=httpx.ConnectError("refused")),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL

        _maybe_load_expected_model("Qwen3.5-35B-A3B-GGUF")  # must not raise

        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 7. model_id is empty/None → no HTTP calls made
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_id", ["", None])
def test_empty_model_id_no_http(model_id):
    """Empty or None model_id → return immediately; httpx.get must NOT be called."""
    with patch(_HTTPX_GET) as mock_get:
        _maybe_load_expected_model(model_id)
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# 8. load_model raises → warning SSE emitted, exception NOT re-raised
# ---------------------------------------------------------------------------


def test_load_model_exception_emits_warning_sse():
    """If load_model raises, a warning SSE is emitted and the exception is swallowed."""
    health = _health_ok([])
    sse = MagicMock()

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=health),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL
        mock_instance = MagicMock()
        mock_instance.load_model.side_effect = RuntimeError("load failed")
        mock_cls.return_value = mock_instance

        _maybe_load_expected_model("Qwen3.5-35B-A3B-GGUF", sse_handler=sse)

    warning_calls = [
        c for c in sse._emit.call_args_list if c.args[0].get("status") == "warning"
    ]
    assert warning_calls, "Expected a warning SSE event after load_model failure"


# ---------------------------------------------------------------------------
# 9. SSE handler receives "Loading LLM model..." when load is triggered
# ---------------------------------------------------------------------------


def test_sse_loading_message_emitted():
    """When a load is triggered, an info SSE with 'Loading LLM model...' is sent."""
    health = _health_ok([])
    sse = MagicMock()

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=health),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL
        mock_cls.return_value = MagicMock()

        _maybe_load_expected_model("Qwen3.5-35B-A3B-GGUF", sse_handler=sse)

    info_calls = [
        c
        for c in sse._emit.call_args_list
        if c.args[0].get("status") == "info"
        and "Loading LLM model" in c.args[0].get("message", "")
    ]
    assert info_calls, "Expected 'Loading LLM model...' info SSE when triggering load"


# ---------------------------------------------------------------------------
# 10. SSE handler NOT called on fast path
# ---------------------------------------------------------------------------


def test_sse_not_called_on_fast_path():
    """Right LLM + ctx >= 32K (fast path) → SSE handler must NOT be called."""
    health = _health_ok([_model("llm", name=_EXPECTED_LLM)])
    sse = MagicMock()

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=health),
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL

        _maybe_load_expected_model(_EXPECTED_LLM, sse_handler=sse)

    sse._emit.assert_not_called()


# ---------------------------------------------------------------------------
# 11. Concurrent calls: second thread skips load_model after re-check shows LLM
# ---------------------------------------------------------------------------


def test_concurrent_second_thread_skips_load():
    """Double-check inside the lock: if another thread already loaded the
    expected model with a sufficient context window, the current thread
    skips load_model entirely."""
    # First call (before lock): empty → triggers load path
    # Second call (inside lock re-check): right model + 32K ctx → skip
    empty_health = _health_ok([])
    loaded_health = _health_ok([_model("llm", name=_EXPECTED_LLM)])

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, side_effect=[empty_health, loaded_health]),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL

        _maybe_load_expected_model(_EXPECTED_LLM)

        # load_model must NOT be called because re-check found the right
        # model loaded with a sufficient context window.
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 11b. Wrong chat model resident → unload BEFORE load (issue #1382)
# ---------------------------------------------------------------------------


def test_wrong_model_resident_unloads_before_load():
    """Issue #1382: a different chat model resident (e.g. Gemma-4 preloaded at
    boot) must be unloaded before loading the expected model. Lemonade does not
    evict on /load, so without the unload both models stay resident — a
    double-load that wastes memory and can degrade output (the slash-spew)."""
    wrong = _health_ok([_model("llm", name="Gemma-4-E4B-it-GGUF")])

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=wrong),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        _maybe_load_expected_model(_EXPECTED_LLM)

        mock_instance.unload_model.assert_called_once()
        mock_instance.load_model.assert_called_once()
        order = [c[0] for c in mock_instance.method_calls]
        assert order.index("unload_model") < order.index(
            "load_model"
        ), f"unload must precede load; got call order {order}"


# ---------------------------------------------------------------------------
# 11c. Nothing resident → load WITHOUT a wasteful unload
# ---------------------------------------------------------------------------


def test_no_resident_model_skips_unload():
    """Fresh Lemonade (no chat model resident) → load_model is called but
    unload_model is NOT (nothing to evict)."""
    health = _health_ok([])

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, return_value=health),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        _maybe_load_expected_model(_EXPECTED_LLM)

        mock_instance.load_model.assert_called_once()
        mock_instance.unload_model.assert_not_called()


# ---------------------------------------------------------------------------
# 12. Re-check inside lock returns non-200 → load_model still called
# ---------------------------------------------------------------------------


def test_recheck_non200_proceeds_to_load():
    """If the re-check inside the lock returns non-200, load_model is still called
    (we cannot confirm another thread loaded the model, so we proceed)."""
    empty_health = _health_ok([])
    bad = MagicMock(spec=httpx.Response)
    bad.status_code = 503

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(_HTTPX_GET, side_effect=[empty_health, bad]),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        _maybe_load_expected_model("Qwen3.5-35B-A3B-GGUF")

        mock_instance.load_model.assert_called_once()


# ---------------------------------------------------------------------------
# 13. Re-check inside lock raises exception → outer handler catches, no crash
# ---------------------------------------------------------------------------


def test_recheck_exception_caught_by_outer_handler():
    """If the re-check inside the lock raises (e.g. ConnectError), the outer
    except block catches it — no crash, warning SSE emitted."""
    empty_health = _health_ok([])
    sse = MagicMock()

    with (
        patch(_LEMONADE_MANAGER) as mock_mgr,
        patch(
            _HTTPX_GET,
            side_effect=[empty_health, httpx.ConnectError("refused")],
        ),
        patch(_LEMONADE_CLIENT) as mock_cls,
    ):
        mock_mgr.get_base_url.return_value = _BASE_URL

        _maybe_load_expected_model(
            "Qwen3.5-35B-A3B-GGUF", sse_handler=sse
        )  # must not raise

        mock_cls.assert_not_called()

    warning_calls = [
        c for c in sse._emit.call_args_list if c.args[0].get("status") == "warning"
    ]
    assert warning_calls, "Expected a warning SSE after exception in re-check"
