# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The context window GAIA loads a model with: memory-sized, and never NPU-capped
for a model that does not run on the NPU.

No model on main opts in to memory sizing yet, so these tests register one: a
35B MoE with the KV cost of a hybrid-attention model (10 full-attention layers x
2 KV heads x 256 dims x K+V x f16 = 20 KiB/token) and a 262144-token native window.
"""

# pylint: disable=protected-access

import json
from unittest.mock import MagicMock, patch

import pytest
import responses

from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_client import (
    DEFAULT_MODEL_NAME,
    GPU_CTX_SIZE,
    MODELS,
    NPU_CTX_SIZE,
    LemonadeClient,
    LemonadeClientError,
    LemonadeStatus,
    ModelRequirement,
    ModelType,
    context_for_capacity,
    resolve_ctx_size,
)
from gaia.llm.model_fit import (
    SYSTEM_RAM,
    MachineCapacity,
    kv_cache_gb,
    largest_context,
    required_memory_gb,
)

SCALED_ID = "Test-35B-A3B-GGUF"
SCALED = ModelRequirement(
    model_type=ModelType.LLM,
    model_id=SCALED_ID,
    display_name="Scaled test model",
    min_ctx_size=GPU_CTX_SIZE,
    size_gb=23.3,
    max_ctx_size=262144,
    kv_bytes_per_token=20480,
)
BASE = "http://lemonade.test/api/v1"


def _igpu(vram_gb, gtt_gb):
    """A Strix Halo ``/system-info``: dedicated VRAM plus shared GTT."""
    return {
        "devices": {
            "amd_igpu": {
                "name": "AMD Radeon 8060S",
                "available": True,
                "vram_gb": vram_gb,
                "virtual_mem_gb": gtt_gb,
            }
        }
    }


def _dgpu(vram_gb):
    return {
        "devices": {
            "amd_dgpu": [{"name": "AMD Radeon", "available": True, "vram_gb": vram_gb}]
        }
    }


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    from gaia.config import GaiaConfig

    monkeypatch.delenv("GAIA_CTX_SIZE", raising=False)
    monkeypatch.setattr(GaiaConfig, "load", lambda: GaiaConfig(default_device="gpu"))
    monkeypatch.setitem(MODELS, "test-scaled", SCALED)
    lc._CAPACITY_CACHE.clear()
    yield
    lc._CAPACITY_CACHE.clear()


def _cap(memory_gb, source):
    return MachineCapacity(memory_gb=memory_gb, memory_source=source, disk_free_gb=None)


# ---------------------------------------------------------------------------
# The sizing rule, per machine class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "capacity,expected",
    [
        # 128 GB Strix Halo, 96 GB carve-out + 16 GB GTT: the native maximum.
        (_cap(112, "AMD iGPU"), 262144),
        # 64 GB Strix Halo, 32 GB carve-out + 16 GB GTT: still the maximum.
        (_cap(48, "AMD iGPU"), 262144),
        # 32 GB discrete GPU: 28.8 usable - 25.47 weights = 3.33 GB of KV.
        (_cap(32, "AMD GPU"), 155648),
        # 24 GB discrete GPU: the weights alone overflow it; the floor stands.
        (_cap(24, "AMD GPU"), GPU_CTX_SIZE),
    ],
)
def test_window_per_machine_class(capacity, expected):
    assert context_for_capacity(SCALED, capacity) == expected


def test_co_resident_embedder_is_reserved_on_unified_memory_only():
    """The embedder runs on the CPU backend, so it shares an iGPU's pool but
    never a discrete card's VRAM."""
    assert context_for_capacity(SCALED, _cap(34, "AMD GPU")) == 245760
    assert context_for_capacity(SCALED, _cap(34, "AMD iGPU")) == 180224
    assert context_for_capacity(SCALED, _cap(34, SYSTEM_RAM)) == 180224


def test_window_steps_are_whole_8k_multiples_within_bounds():
    for memory in range(20, 120):
        ctx = context_for_capacity(SCALED, _cap(memory, "AMD GPU"))
        assert ctx % 8192 == 0
        assert GPU_CTX_SIZE <= ctx <= 262144


def test_fit_charges_the_same_kv_the_load_requests():
    """Above the floor, the chosen window plus its KV cache fits, with headroom."""
    for capacity in (_cap(32, "AMD GPU"), _cap(34, "AMD iGPU"), _cap(112, "AMD iGPU")):
        ctx = context_for_capacity(SCALED, capacity)
        need = required_memory_gb(SCALED.size_gb, kv_cache_gb(20480, ctx))
        assert need <= capacity.memory_gb * 0.9
        # One more step would not have fit, unless the native maximum stopped it.
        if ctx < SCALED.max_ctx_size:
            bigger = required_memory_gb(SCALED.size_gb, kv_cache_gb(20480, ctx + 8192))
            reserve = lc.co_resident_reserve_gb(capacity)
            assert bigger + reserve > capacity.memory_gb * 0.9


def test_largest_context_without_kv_cost_keeps_the_floor():
    assert (
        largest_context(
            size_gb=1.0,
            kv_bytes_per_token=0,
            min_ctx=4096,
            max_ctx=131072,
            capacity=_cap(128, "AMD iGPU"),
        )
        == 4096
    )


def test_gemma_stays_pinned_at_64k_without_asking_for_memory():
    """Gemma does not opt in; its window must not depend on the machine."""
    with patch.object(lc, "machine_capacity") as probe:
        assert resolve_ctx_size(DEFAULT_MODEL_NAME) == GPU_CTX_SIZE
        assert context_for_capacity(MODELS["gemma-4-e4b"], _cap(112, "AMD iGPU")) == (
            GPU_CTX_SIZE
        )
    probe.assert_not_called()


def test_unreadable_capacity_loads_at_the_floor_and_says_so(caplog):
    with patch.object(
        lc, "machine_capacity", side_effect=LemonadeClientError("refused")
    ):
        assert resolve_ctx_size(SCALED_ID) == GPU_CTX_SIZE
    assert "Cannot read this machine's memory" in caplog.text


def test_override_still_wins_over_memory_sizing(monkeypatch):
    monkeypatch.setenv("GAIA_CTX_SIZE", "98304")
    with patch.object(lc, "machine_capacity", return_value=_cap(112, "AMD iGPU")):
        assert resolve_ctx_size(SCALED_ID) == 98304


# ---------------------------------------------------------------------------
# Bug: an NPU device setting capped GPU models at the FLM ceiling
# ---------------------------------------------------------------------------


@pytest.fixture
def npu_profile(monkeypatch):
    """The state `gaia init --profile npu` leaves: default_device = npu."""
    from gaia.config import GaiaConfig

    monkeypatch.setattr(GaiaConfig, "load", lambda: GaiaConfig(default_device="npu"))


def test_npu_profile_still_caps_npu_models(npu_profile):
    assert resolve_ctx_size("gemma4-it-e2b-FLM") == NPU_CTX_SIZE
    assert resolve_ctx_size() == NPU_CTX_SIZE


def _serve_idle_lemonade(system_info):
    responses.get(BASE + "/health", json={"status": "ok", "all_models_loaded": []})
    responses.get(BASE + "/models", json={"data": []})
    responses.get(BASE + "/system-info", json=system_info)
    responses.post(BASE + "/load", json={"status": "success"})


def _load_bodies():
    return [
        json.loads(call.request.body)
        for call in responses.calls
        if call.request.method == "POST" and call.request.url.endswith("/load")
    ]


@pytest.mark.parametrize(
    "model,system_info,expected",
    [
        (DEFAULT_MODEL_NAME, _igpu(96, 16), GPU_CTX_SIZE),
        (SCALED_ID, _igpu(96, 16), 262144),
        (SCALED_ID, _dgpu(32), 155648),
        ("gemma4-it-e2b-FLM", _igpu(96, 16), NPU_CTX_SIZE),
    ],
)
@responses.activate
def test_load_request_on_an_npu_profile_machine(
    npu_profile, model, system_info, expected
):
    """The /load body is what Lemonade sizes the KV cache from."""
    _serve_idle_lemonade(system_info)
    LemonadeClient(base_url=BASE, keep_alive=True)._ensure_model_loaded(model)
    assert _load_bodies() == [{"model_name": model, "ctx_size": expected}]


@responses.activate
def test_load_and_resolver_agree_on_one_window():
    _serve_idle_lemonade(_igpu(32, 16))
    resolved = resolve_ctx_size(SCALED_ID, base_url=BASE)
    LemonadeClient(base_url=BASE, keep_alive=True)._ensure_model_loaded(SCALED_ID)
    assert _load_bodies() == [{"model_name": SCALED_ID, "ctx_size": resolved}]
    assert resolved == context_for_capacity(SCALED, _cap(48, "AMD iGPU"))
    # Read once per server, not once per call.
    reads = [c for c in responses.calls if c.request.url.endswith("/system-info")]
    assert len(reads) == 1


# ---------------------------------------------------------------------------
# Who asks for which window at startup
# ---------------------------------------------------------------------------


def test_agent_does_not_push_its_model_window_onto_the_manager():
    """The manager resizes whatever model is resident; handing it this agent's
    window would reload another model at it."""
    from gaia.agents.base.agent import Agent

    class Reached(Exception):
        pass

    class ProbeAgent(Agent):
        def _register_tools(self):
            pass

        def _get_system_prompt(self):
            return "test"

    with patch(
        "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready", side_effect=Reached
    ) as ready:
        with pytest.raises(Reached):
            ProbeAgent(model_id=SCALED_ID)
    assert ready.call_args.kwargs["min_context_size"] is None


@patch("gaia.llm.lemonade_manager.LemonadeClient")
def test_idle_server_is_seeded_at_the_default_models_own_window(mock_cls, npu_profile):
    """On an NPU profile the floor is 32K, but Gemma GGUF loads at 64K: seeding
    it at 32K would make the first chat reload it."""
    from gaia.llm.lemonade_manager import LemonadeManager

    LemonadeManager.reset()
    client = MagicMock()
    client.base_url = BASE
    client.get_model_max_context_window.return_value = None
    client.get_status.side_effect = [
        LemonadeStatus(running=True, context_size=0, loaded_models=[]),
        LemonadeStatus(
            running=True,
            context_size=GPU_CTX_SIZE,
            loaded_models=[{"id": DEFAULT_MODEL_NAME}],
        ),
    ]
    mock_cls.return_value = client
    try:
        with patch("gaia.llm.lemonade_manager.gaia_runs_lemonade", return_value=False):
            assert LemonadeManager.ensure_ready(quiet=True) is True
    finally:
        LemonadeManager.reset()
    assert client.load_model.call_args.args[0] == DEFAULT_MODEL_NAME
    assert client.load_model.call_args.kwargs["ctx_size"] == GPU_CTX_SIZE


@pytest.mark.parametrize(
    "model,n_ctx,retryable",
    [
        # The CI failure: a GGUF model loaded at the FLM ceiling is a wrong
        # load, and reloading at its own window fixes it.
        (DEFAULT_MODEL_NAME, NPU_CTX_SIZE, True),
        (DEFAULT_MODEL_NAME, GPU_CTX_SIZE, False),
        ("gemma4-it-e2b-FLM", NPU_CTX_SIZE, False),
    ],
)
def test_overflow_is_retryable_below_the_models_own_window(
    npu_profile, model, n_ctx, retryable
):
    from gaia.llm.providers.lemonade import (
        LemonadeContextOverflowError,
        _classify_lemonade_response,
    )

    payload = {
        "error": {
            "type": "exceed_context_size_error",
            "message": f"request exceeds the available context size ({n_ctx} tokens)",
            "n_ctx": n_ctx,
        }
    }
    err, recognised = _classify_lemonade_response(payload, model)
    assert recognised
    assert isinstance(err, LemonadeContextOverflowError)
    assert err.retryable is retryable
