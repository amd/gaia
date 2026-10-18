# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gpt-oss as a supported local model: registry, fit, window and reasoning effort."""

import json
from unittest.mock import MagicMock, patch

import pytest
import responses

from gaia.llm import lemonade_client as lc
from gaia.llm import model_fit
from gaia.llm.model_fit import capacity_from_system_info

from .test_model_fit import CPU_ONLY, DGPU, MAC_M4, STRIX_HALO_64, STRIX_HALO_128

GGUF_BUILDS = {
    # id: (full-attention layers, size_gb)
    "user.ggml-gpt-oss-20b-MXFP4-GGUF": (12, 12.1),
    "gpt-oss-20b-GGUF": (12, 11.6),
    "gpt-oss-120b-mxfp-GGUF": (18, 63.4),
    "gpt-oss-120b-GGUF": (18, 62.8),
}
TWENTY = lc.find_model_requirement("user.ggml-gpt-oss-20b-MXFP4-GGUF")
ONE_TWENTY = lc.find_model_requirement("gpt-oss-120b-mxfp-GGUF")


def _cap(info):
    return capacity_from_system_info(info)


class TestRegistry:
    @pytest.mark.parametrize("model_id", sorted(GGUF_BUILDS))
    def test_every_gguf_build_is_registered_for_tools_and_sizing(self, model_id):
        mr = lc.find_model_requirement(model_id)
        assert mr is not None and mr.model_id == model_id
        assert mr.tool_calling and lc.is_tool_calling_model(model_id)
        assert mr.size_gb == GGUF_BUILDS[model_id][1]
        assert mr.max_ctx_size == 131072
        assert mr.min_ctx_size == lc.GPU_CTX_SIZE
        assert mr.scales_with_memory
        if model_id.startswith("user."):
            # Registered by file: Lemonade's built-in 20B launches its EAGLE3
            # draft head as the model.
            assert mr.pull_kwargs()["checkpoint"].endswith(":gpt-oss-20b-MXFP4.gguf")
            assert mr.pull_kwargs()["recipe"] == "llamacpp"
        else:
            # Built-ins pull by name: a recipe would make Lemonade 400 (#1655).
            assert mr.pull_kwargs() == {}

    @pytest.mark.parametrize("model_id", sorted(GGUF_BUILDS))
    def test_kv_cache_is_the_full_attention_layers_only(self, model_id):
        """Sliding-window layers hold 128 tokens and do not grow with the window."""
        layers = GGUF_BUILDS[model_id][0]
        heads, dims, k_and_v, f16 = 8, 64, 2, 2
        mr = lc.find_model_requirement(model_id)
        assert mr.kv_bytes_per_token == layers * heads * dims * k_and_v * f16

    def test_never_a_default(self):
        for model_id in GGUF_BUILDS:
            assert model_id not in lc.DEFAULT_MODEL_LADDER
        assert lc.OPTIONAL_CHAT_MODELS == (
            "user.ggml-gpt-oss-20b-MXFP4-GGUF",
            "gpt-oss-120b-mxfp-GGUF",
        )

    def test_sampling_is_the_published_recommendation(self):
        for model_id in GGUF_BUILDS:
            assert lc.local_sampling_defaults(model_id) == {
                "temperature": 1.0,
                "top_p": 1.0,
                "min_p": 0.0,
            }


class TestFit:
    @pytest.mark.parametrize(
        "info,expected",
        [
            (
                STRIX_HALO_128,
                ["user.ggml-gpt-oss-20b-MXFP4-GGUF", "gpt-oss-120b-mxfp-GGUF"],
            ),
            (STRIX_HALO_64, ["user.ggml-gpt-oss-20b-MXFP4-GGUF"]),
            (DGPU, ["user.ggml-gpt-oss-20b-MXFP4-GGUF"]),
            (CPU_ONLY, ["user.ggml-gpt-oss-20b-MXFP4-GGUF"]),
            (MAC_M4, []),
        ],
    )
    def test_optional_models_offered_only_where_they_fit(self, info, expected):
        assert lc.optional_chat_models_that_fit(_cap(info)) == expected

    def test_twenty_needs_about_15gb_at_its_floor(self):
        kv = model_fit.kv_cache_gb(TWENTY.kv_bytes_per_token, TWENTY.min_ctx_size)
        assert model_fit.required_memory_gb(TWENTY.size_gb, kv) == pytest.approx(
            15.4, abs=0.1
        )

    def test_one_twenty_needs_about_70gb_at_its_floor(self):
        kv = model_fit.kv_cache_gb(
            ONE_TWENTY.kv_bytes_per_token, ONE_TWENTY.min_ctx_size
        )
        assert model_fit.required_memory_gb(ONE_TWENTY.size_gb, kv) == pytest.approx(
            70.0, abs=0.1
        )

    def test_one_twenty_says_why_it_does_not_fit_a_64gb_strix_halo(self):
        kv = model_fit.kv_cache_gb(
            ONE_TWENTY.kv_bytes_per_token, ONE_TWENTY.min_ctx_size
        )
        verdict = model_fit.check_fit(ONE_TWENTY.size_gb, _cap(STRIX_HALO_64), kv)
        assert not verdict.fits and "needs ~70 GB" in verdict.reason

    @pytest.mark.parametrize(
        "mr,info,window",
        [
            (TWENTY, STRIX_HALO_128, 131072),
            (TWENTY, STRIX_HALO_64, 131072),
            (ONE_TWENTY, STRIX_HALO_128, 131072),
        ],
    )
    def test_window_grows_to_the_native_128k_where_memory_allows(
        self, mr, info, window
    ):
        assert lc.context_for_capacity(mr, _cap(info)) == window


class TestReasoningEffort:
    def test_default_is_the_template_default(self, monkeypatch):
        monkeypatch.delenv("GAIA_REASONING_EFFORT", raising=False)
        assert (
            lc.requested_reasoning_effort("user.ggml-gpt-oss-20b-MXFP4-GGUF")
            == "medium"
        )

    def test_env_overrides_the_registry(self, monkeypatch):
        monkeypatch.setenv("GAIA_REASONING_EFFORT", "HIGH")
        assert lc.requested_reasoning_effort("gpt-oss-120b-mxfp-GGUF") == "high"

    def test_caller_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("GAIA_REASONING_EFFORT", "high")
        assert (
            lc.requested_reasoning_effort(
                "gpt-oss-20b-GGUF", {"reasoning_effort": "low"}
            )
            == "low"
        )

    @pytest.mark.parametrize("bad", ["extreme", "0"])
    def test_invalid_effort_fails_loudly(self, monkeypatch, bad):
        monkeypatch.setenv("GAIA_REASONING_EFFORT", bad)
        with pytest.raises(lc.LemonadeClientError, match="GAIA_REASONING_EFFORT"):
            lc.requested_reasoning_effort("user.ggml-gpt-oss-20b-MXFP4-GGUF")

    def test_other_models_send_no_effort(self, monkeypatch):
        monkeypatch.setenv("GAIA_REASONING_EFFORT", "high")
        assert lc.requested_reasoning_effort(lc.DEFAULT_MODEL_NAME) is None
        assert lc.requested_reasoning_effort("Unknown-Model-GGUF") is None

    def test_side_calls_ask_for_the_least_reasoning(self):
        assert lc.no_thinking_kwargs("user.ggml-gpt-oss-20b-MXFP4-GGUF") == {
            "chat_template_kwargs": {"reasoning_effort": "low"}
        }

    def test_thinking_switch_is_never_sent(self):
        """gpt-oss's template ignores ``enable_thinking``; sending it is noise."""
        assert lc.requested_thinking("user.ggml-gpt-oss-20b-MXFP4-GGUF") is None


HOST, PORT = "127.0.0.1", 18765
API_BASE = f"http://{HOST}:{PORT}/api/v1"


@pytest.fixture
def no_env_effort(monkeypatch):
    monkeypatch.delenv("GAIA_REASONING_EFFORT", raising=False)


@responses.activate
def test_request_carries_reasoning_effort_and_no_thinking_switch(no_env_effort):
    responses.add(
        responses.POST,
        f"{API_BASE}/chat/completions",
        json={"id": "0", "choices": [{"message": {"content": "hi"}}]},
        status=200,
    )
    client = lc.LemonadeClient(host=HOST, port=PORT, verbose=False)
    with patch.object(lc.LemonadeClient, "_ensure_model_loaded"):
        client.chat_completions(
            model="user.ggml-gpt-oss-20b-MXFP4-GGUF",
            messages=[{"role": "user", "content": "hi"}],
        )
    body = json.loads(responses.calls[-1].request.body)
    assert body["chat_template_kwargs"] == {"reasoning_effort": "medium"}


@patch("gaia.llm.lemonade_client.LemonadeClient._ensure_model_loaded")
@patch("gaia.llm.lemonade_client.OpenAI")
def test_streamed_request_carries_reasoning_effort(mock_openai, _ensure, no_env_effort):
    mock_client = MagicMock()
    mock_openai.return_value = mock_client
    stream = MagicMock()
    stream.__iter__.return_value = iter([])
    mock_client.chat.completions.create.return_value = stream
    client = lc.LemonadeClient(host=HOST, port=PORT, verbose=False)
    list(
        client.chat_completions(
            model="gpt-oss-120b-mxfp-GGUF",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
            chat_template_kwargs={"reasoning_effort": "high"},
        )
    )
    sent = mock_client.chat.completions.create.call_args.kwargs
    assert sent["extra_body"]["chat_template_kwargs"] == {"reasoning_effort": "high"}
