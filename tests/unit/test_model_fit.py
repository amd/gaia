# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which chat model a machine gets, and which models it may download.

The rule is shared with the TUI's model picker (Go), which carries its own copy
of the fit constants and the recommended-model registrations in
``tui/internal/lemonade/recommended_models.json``. The drift tests below are
the only thing that sees both sides.
"""

import json
from pathlib import Path

import pytest

from gaia.config import GaiaConfig
from gaia.llm import lemonade_client as lc
from gaia.llm import model_fit
from gaia.llm.model_fit import (
    MachineCapacity,
    ModelFitError,
    capacity_from_system_info,
    check_fit,
    check_server_supports,
    pick_default_model,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RECOMMENDED_JSON = (
    REPO_ROOT / "tui" / "internal" / "lemonade" / "recommended_models.json"
)

# Lemonade /system-info bodies, trimmed to what the fit check reads — the same
# shapes tui/internal/lemonade/catalog_test.go uses.
STRIX_HALO_128 = {
    "Physical Memory": "128 GB",
    "devices": {
        "amd_gpu": [
            {
                "available": True,
                "integrated": True,
                "vram_gb": 96.0,
                "virtual_mem_gb": 15.8,
            }
        ]
    },
    "model_storage": {"free_bytes": 900e9},
}
STRIX_HALO_64 = {
    "Physical Memory": "64 GB",
    "devices": {
        "amd_gpu": [
            {
                "available": True,
                "integrated": True,
                "vram_gb": 48.0,
                "virtual_mem_gb": 7.9,
            }
        ]
    },
    "model_storage": {"free_bytes": 900e9},
}
MAC_M4 = {
    "Physical Memory": "16 GB",
    "devices": {"amd_gpu": [], "metal": {"available": True, "vram_gb": 11.84}},
    "model_storage": {"free_bytes": 19.6e9},
}
CPU_ONLY = {"Physical Memory": "32 GB", "devices": {"amd_gpu": []}}
DGPU = {
    "Physical Memory": "64 GB",
    "devices": {"amd_gpu": [{"available": True, "integrated": False, "vram_gb": 24.0}]},
}

QWEN = lc.find_model_requirement(lc.LARGE_DEFAULT_MODEL_NAME)
FLASH = lc.find_model_requirement(lc.FLASH_OPTION_MODEL_NAME)
# Qwen3.6's KV cache at its 64K floor, the least it ever loads with.
QWEN_KV = model_fit.kv_cache_gb(QWEN.kv_bytes_per_token, QWEN.min_ctx_size)


class TestCapacity:
    def test_strix_halo_pool_is_vram_plus_shared_memory(self):
        cap = capacity_from_system_info(STRIX_HALO_128)
        assert cap.memory_source == "AMD iGPU"
        assert cap.memory_gb == pytest.approx(111.8)
        assert cap.disk_free_gb == pytest.approx(900)

    @pytest.mark.parametrize(
        "info,source,gb",
        [
            (MAC_M4, "Apple GPU", 11.84),
            (CPU_ONLY, "System RAM", 32),
            (DGPU, "AMD GPU", 24),
        ],
    )
    def test_other_machines(self, info, source, gb):
        cap = capacity_from_system_info(info)
        assert (cap.memory_source, cap.memory_gb) == (source, pytest.approx(gb))

    def test_no_memory_reported_fails_loudly(self):
        with pytest.raises(ModelFitError, match="Update Lemonade"):
            capacity_from_system_info({"devices": {}})


class TestFit:
    def test_qwen_fits_a_128gb_strix_halo(self):
        assert check_fit(
            QWEN.size_gb, capacity_from_system_info(STRIX_HALO_128), QWEN_KV
        ).fits

    def test_qwen_also_fits_a_64gb_strix_halo(self):
        # ~27 GB against a ~55.9 GB pool; Flash's 82.86 GB needs the 128 GB class.
        assert check_fit(
            QWEN.size_gb, capacity_from_system_info(STRIX_HALO_64), QWEN_KV
        ).fits

    def test_qwen_does_not_fit_the_smallest_machines(self):
        verdict = check_fit(QWEN.size_gb, capacity_from_system_info(MAC_M4), QWEN_KV)
        assert not verdict.fits and "memory" in verdict.reason

    def test_qwen_fits_a_32gb_cpu_box_by_memory_alone(self):
        # It fits; the GPU-only product rule (TestDefaultFollowsTheGpu) is
        # what keeps a CPU-only PC on Gemma.
        cap = capacity_from_system_info(CPU_ONLY)
        assert check_fit(QWEN.size_gb, cap, QWEN_KV).fits

    def test_qwen_kv_cache_is_its_full_attention_layers_at_64k(self):
        # Only 10 of 40 layers are full attention; the 30 Gated DeltaNet layers
        # keep a fixed-size state the shared margin covers.
        per_token = 10 * 2 * 256 * 2 * 2  # layers x KV heads x dims x K+V x f16
        assert QWEN_KV == pytest.approx(per_token * lc.GPU_CTX_SIZE / 1e9, abs=0.05)

    def test_qwen_needs_about_27gb(self):
        need = model_fit.required_memory_gb(QWEN.size_gb, QWEN_KV)
        assert need == pytest.approx(26.8, abs=0.05)

    def test_qwen_charges_its_kv_cache(self):
        # A 26 GB pool holds the weights and margin (~25.5 GB) but not the cache.
        assert QWEN_KV > 0
        cap = MachineCapacity(
            memory_gb=26.0, memory_source="AMD GPU", disk_free_gb=None
        )
        assert check_fit(QWEN.size_gb, cap).fits  # weights alone would pass
        verdict = check_fit(QWEN.size_gb, cap, QWEN_KV)
        assert not verdict.fits and "memory" in verdict.reason

    def test_flash_does_not_fit_a_64gb_strix_halo(self):
        # Flash's 82.86 GB (with the vision projector) still needs the full
        # 128 GB Strix Halo class — this is why it stays a manual opt-in
        # rather than something smaller machines get offered.
        verdict = check_fit(FLASH.size_gb, capacity_from_system_info(STRIX_HALO_64))
        assert not verdict.fits and "memory" in verdict.reason

    def test_disk_is_part_of_fit(self):
        cap = MachineCapacity(memory_gb=112, memory_source="AMD iGPU", disk_free_gb=10)
        verdict = check_fit(QWEN.size_gb, cap)
        assert not verdict.fits and "disk" in verdict.reason

    def test_gemma_fits_the_mac(self):
        assert check_fit(5.97, capacity_from_system_info(MAC_M4)).fits


class TestDefaultPick:
    def test_largest_that_fits_wins(self):
        cap = capacity_from_system_info(STRIX_HALO_128)
        assert pick_default_model([("big", 81.96), ("floor", 0)], cap) == ("big", [])

    def test_floor_is_returned_with_why_the_big_one_was_skipped(self):
        cap = capacity_from_system_info(MAC_M4)
        model_id, skipped = pick_default_model([("big", 81.96), ("floor", 0)], cap)
        assert model_id == "floor"
        assert skipped[0][0] == "big" and "memory" in skipped[0][1]

    @pytest.mark.parametrize(
        "info,expected",
        [
            (STRIX_HALO_128, lc.LARGE_DEFAULT_MODEL_NAME),
            (STRIX_HALO_64, lc.LARGE_DEFAULT_MODEL_NAME),
            (MAC_M4, lc.DEFAULT_MODEL_NAME),
        ],
    )
    def test_recommend_reads_lemonade(self, info, expected):
        class FakeClient:
            def get_system_info(self, timeout=None):
                return info

            def health_check(self):
                return {"version": "2026.39.1"}

        model_id, _, _ = lc.recommend_default_chat_model(FakeClient())
        assert model_id == expected


class TestResolveDefault:
    def test_unset_config_is_gemma(self):
        assert lc.resolve_default_chat_model() == lc.DEFAULT_MODEL_NAME

    def test_config_default_model_wins(self):
        cfg = GaiaConfig()
        cfg.default_model = lc.LARGE_DEFAULT_MODEL_NAME
        cfg.save()
        assert lc.resolve_default_chat_model() == lc.LARGE_DEFAULT_MODEL_NAME


DGPU_32 = {
    "Physical Memory": "64 GB",
    "devices": {"amd_gpu": [{"available": True, "integrated": False, "vram_gb": 32.0}]},
    "model_storage": {"free_bytes": 900e9},
}


def _recommend(system_info):
    """What `gaia init` would pick as the chat model on this machine."""
    from unittest.mock import MagicMock

    client = MagicMock()
    client.get_system_info.return_value = system_info
    client.health_check.return_value = {"status": "ok", "version": "2026.40.0"}
    model_id, skipped, _ = lc.recommend_default_chat_model(client)
    return model_id, dict(skipped)


class TestDefaultFollowsTheGpu:
    """Qwen3.6 35B is the default on any PC whose GPU holds it; Gemma elsewhere."""

    @pytest.mark.parametrize("info", [STRIX_HALO_128, STRIX_HALO_64, DGPU_32])
    def test_a_gpu_that_holds_it_gets_qwen(self, info):
        assert _recommend(info)[0] == lc.LARGE_DEFAULT_MODEL_NAME

    def test_a_24gb_gpu_is_too_small(self):
        model_id, skipped = _recommend(DGPU)
        assert model_id == lc.DEFAULT_MODEL_NAME
        assert "memory" in skipped[lc.LARGE_DEFAULT_MODEL_NAME]

    def test_a_cpu_only_pc_defaults_to_gemma_even_with_the_ram(self):
        model_id, skipped = _recommend(CPU_ONLY)
        assert model_id == lc.DEFAULT_MODEL_NAME
        reason = skipped[lc.LARGE_DEFAULT_MODEL_NAME]
        assert "GPU" in reason and "gaia config set default_model" in reason


class TestRegistry:
    def test_user_prefix_is_tolerated_in_lookups(self):
        listed = lc.FLASH_OPTION_MODEL_NAME[len("user.") :]
        assert lc.find_model_requirement(listed) is FLASH
        # The listed id must still load at GAIA's 64K window, not the 32K floor.
        assert FLASH.min_ctx_size == lc.GPU_CTX_SIZE

    def test_builtin_pulls_by_name_only(self):
        # Passing recipe for a built-in 400s (#1655).
        gemma = lc.find_model_requirement(lc.DEFAULT_MODEL_NAME)
        assert gemma.pull_kwargs() == {}
        # The default is also a Lemonade built-in — same rule applies.
        assert QWEN.pull_kwargs() == {}

    def test_custom_model_carries_its_registration(self):
        kwargs = FLASH.pull_kwargs()
        assert kwargs["checkpoint"].startswith("unsloth/Qwen3.8-Flash-Next-GGUF:")
        assert kwargs["recipe"] == "llamacpp"
        assert kwargs["mmproj"] == "mmproj-F16.gguf"
        assert kwargs["vision"] is True and kwargs["reasoning"] is True


@pytest.fixture(scope="module")
def doc():
    return json.loads(RECOMMENDED_JSON.read_text(encoding="utf-8"))


class TestTuiDrift:
    """recommended_models.json is the TUI's copy of this rule."""

    def test_fit_constants_match(self, doc):
        assert doc["fit"]["memory_overhead_factor"] == model_fit.MEMORY_OVERHEAD_FACTOR
        assert doc["fit"]["memory_overhead_gb"] == model_fit.MEMORY_OVERHEAD_GB

    def test_custom_registrations_match_the_python_registry(self, doc):
        custom = [m for m in doc["models"] if m.get("register_as")]
        assert custom, "the TUI must be able to register Qwen3.8 Flash"
        for entry in custom:
            mr = lc.find_model_requirement(entry["register_as"])
            assert mr is not None, f"{entry['register_as']} missing from MODELS"
            assert mr.model_id == entry["register_as"]
            assert entry["id"] == mr.model_id[len("user.") :]
            assert entry["checkpoint"] == mr.checkpoint
            assert entry["recipe"] == mr.recipe
            assert entry.get("mmproj") == mr.mmproj
            assert entry.get("vision", False) == mr.vision
            assert entry.get("reasoning", False) == mr.reasoning
            assert entry["size_gb"] == mr.size_gb
            assert entry.get("min_lemonade_version") == mr.min_lemonade_version

    def test_both_defaults_are_recommended(self, doc):
        local = {m["id"] for m in doc["models"] if m["provider"] == "local"}
        assert lc.DEFAULT_MODEL_NAME in local
        assert lc.LARGE_DEFAULT_MODEL_NAME in local

    def test_fit_inputs_match_the_python_registry(self, doc):
        """The TUI's fit and version checks must judge a model as Python does."""
        for entry in doc["models"]:
            mr = lc.find_model_requirement(entry.get("register_as") or entry["id"])
            if mr is not None:
                assert (
                    entry.get("kv_bytes_per_token", 0) == mr.kv_bytes_per_token
                ), entry["id"]
                if mr.kv_bytes_per_token:
                    assert entry["min_ctx_size"] == mr.min_ctx_size, entry["id"]
                assert entry.get("size_gb") == mr.size_gb, entry["id"]
                assert (
                    entry.get("min_lemonade_version") == mr.min_lemonade_version
                ), entry["id"]

    def test_the_default_leads_the_local_list(self, doc):
        local = [m["id"] for m in doc["models"] if m["provider"] == "local"]
        assert local[0] == lc.LARGE_DEFAULT_MODEL_NAME

    def test_the_multimodal_alternative_is_recommended_and_known(self, doc):
        """Switchable to by name, and sized so the fit check can judge it."""
        local = {m["id"] for m in doc["models"] if m["provider"] == "local"}
        assert lc.FLASH_OPTION_MODEL_NAME[len("user.") :] in local
        assert FLASH is not None and FLASH.size_gb and FLASH.tool_calling


class TestAgentUiFollowsTheMachineDefault:
    def test_ui_default_is_the_configured_model(self):
        from gaia.ui.routers.system import _default_model_name

        assert _default_model_name() == lc.DEFAULT_MODEL_NAME
        cfg = GaiaConfig()
        cfg.default_model = lc.LARGE_DEFAULT_MODEL_NAME
        cfg.save()
        assert _default_model_name() == lc.LARGE_DEFAULT_MODEL_NAME

    def test_ui_matches_a_user_model_by_its_listed_id(self):
        from gaia.ui.routers.system import _norm_model_id

        assert _norm_model_id(lc.FLASH_OPTION_MODEL_NAME) == _norm_model_id(
            "Qwen3.8-Flash-Next-GGUF"
        )


HARDWARE = REPO_ROOT / "tests" / "fixtures" / "hardware"


def _fixture(name):
    return json.loads((HARDWARE / name).read_text(encoding="utf-8"))


class TestRealLemonadeReports:
    """Captured Lemonade 11 /system-info bodies, including ones without VRAM."""

    def test_linux_strix_halo_pool_is_carve_out_plus_gtt(self):
        cap = capacity_from_system_info(_fixture("lemonade11_amd_igpu_linux.json"))
        assert (cap.memory_source, cap.memory_gb) == ("AMD iGPU", pytest.approx(63.0))
        # The default Linux GTT limit (half of RAM) is too small for Flash's
        # 82.86 GB, but the ~27 GB default fits.
        assert not check_fit(FLASH.size_gb, cap).fits
        assert check_fit(QWEN.size_gb, cap, QWEN_KV).fits

    def test_macos_metal(self):
        cap = capacity_from_system_info(_fixture("lemonade11_metal_macos.json"))
        assert (cap.memory_source, cap.memory_gb) == ("Apple GPU", pytest.approx(51.84))

    def test_a_gpu_without_reported_memory_is_not_judged_on_system_ram(self):
        info = _fixture("lemonade11_amd_dgpu_windows.json")
        info["Physical Memory"] = "128 GB"  # would wrongly fit Qwen if used
        with pytest.raises(ModelFitError, match="not its memory"):
            capacity_from_system_info(info)

    _WINDOWS_STRIX_HALO = {
        "Physical Memory": "128.00 GB",
        "devices": {
            "amd_gpu": [
                {
                    "available": True,
                    "family": "gfx1151",
                    "integrated": True,
                    "name": "AMD Radeon(TM) 8060S Graphics",
                }
            ]
        },
    }

    def test_windows_igpu_is_sized_from_its_driver_carve_out(self, monkeypatch):
        """Lemonade 2026.40 on Windows reports this iGPU with no memory."""
        asked = []
        monkeypatch.setattr(
            model_fit,
            "_windows_adapter_memory_gb",
            lambda name: asked.append(name) or 64.0,
        )
        cap = capacity_from_system_info(self._WINDOWS_STRIX_HALO)

        assert asked == ["AMD Radeon(TM) 8060S Graphics"]
        assert (cap.memory_source, cap.memory_gb) == ("AMD iGPU", pytest.approx(64.0))
        assert check_fit(QWEN.size_gb, cap, QWEN_KV).fits

    def test_windows_igpu_with_no_recorded_memory_still_fails_loudly(self, monkeypatch):
        monkeypatch.setattr(model_fit, "_windows_adapter_memory_gb", lambda name: 0.0)
        with pytest.raises(ModelFitError, match="not its memory"):
            capacity_from_system_info(self._WINDOWS_STRIX_HALO)

    def test_a_discrete_gpu_never_reads_the_driver_carve_out(self, monkeypatch):
        monkeypatch.setattr(
            model_fit,
            "_windows_adapter_memory_gb",
            lambda name: pytest.fail("asked about a discrete GPU"),
        )
        with pytest.raises(ModelFitError, match="not its memory"):
            capacity_from_system_info(_fixture("lemonade11_amd_dgpu_windows.json"))

    def test_legacy_amd_igpu_key_is_read(self):
        info = {
            "devices": {
                "amd_igpu": {"available": True, "vram_gb": 96, "virtual_mem_gb": 16}
            }
        }
        assert capacity_from_system_info(info).memory_gb == pytest.approx(112)

    def test_unknown_capacity_keeps_the_floor_model_and_says_why(self):
        info = _fixture("lemonade11_amd_dgpu_windows.json")

        class FakeClient:
            def get_system_info(self, timeout=None):
                return info

            def health_check(self):
                return {"version": "2026.39.1"}

        model_id, skipped, capacity = lc.recommend_default_chat_model(FakeClient())
        assert model_id == lc.DEFAULT_MODEL_NAME and capacity is None
        assert "not its memory" in skipped[0][1]


class TestLemonadeVersionGate:
    """The default (Qwen3.6 35B A3B) is a Lemonade built-in first listed in
    v11.7.0; an older server cannot pull it by name. Qwen3.8 Flash (the
    switchable alternative, not auto-selected) needs llama.cpp's qwen4exp,
    first bundled in v2026.39.1; verified via its ModelRequirement directly
    rather than through the ladder it is not part of.
    """

    def _client(self, info, version):
        class FakeClient:
            def get_system_info(self, timeout=None):
                return info

            def health_check(self):
                return {"version": version} if version else {}

        return FakeClient()

    @pytest.mark.parametrize(
        "version", ["2026.39.1", "2026.40.0~3.abc1234", "11.9.0", "11.7.0"]
    )
    def test_a_big_pc_gets_the_default_on_a_lemonade_that_lists_it(self, version):
        model_id, _, _ = lc.recommend_default_chat_model(
            self._client(STRIX_HALO_128, version)
        )
        assert model_id == lc.LARGE_DEFAULT_MODEL_NAME

    @pytest.mark.parametrize("version", ["11.6.0", None])
    def test_an_older_or_unknown_lemonade_keeps_gemma_and_says_upgrade(self, version):
        model_id, skipped, _ = lc.recommend_default_chat_model(
            self._client(STRIX_HALO_128, version)
        )
        assert model_id == lc.DEFAULT_MODEL_NAME
        assert "force-reinstall" in dict(skipped)[lc.LARGE_DEFAULT_MODEL_NAME]

    def test_a_pc_too_small_is_told_it_is_too_small_not_to_upgrade(self):
        _, skipped, _ = lc.recommend_default_chat_model(self._client(MAC_M4, "11.9.0"))
        assert "memory" in skipped[0][1] and "force-reinstall" not in skipped[0][1]

    def test_the_multimodal_alternative_still_needs_the_version_floor(self):
        verdict = check_server_supports(FLASH.min_lemonade_version, "11.9.0")
        assert not verdict.fits and "force-reinstall" in verdict.reason
        verdict = check_server_supports(FLASH.min_lemonade_version, "2026.39.1")
        assert verdict.fits


def test_flash_size_counts_the_vision_projector_lemonade_downloads():
    """Lemonade's own requirement for this checkpoint is 77.2 GiB (82.9 GB): the
    three shards plus mmproj. A smaller figure passes disks Lemonade then refuses."""
    cap = MachineCapacity(memory_gb=112, memory_source="AMD iGPU", disk_free_gb=82.5)
    verdict = check_fit(FLASH.size_gb, cap)
    assert not verdict.fits and "disk" in verdict.reason


def test_a_ladder_model_without_a_size_is_never_guessed_in(monkeypatch):
    """A size of 0 would fit every PC; a larger default must have a known size."""
    import dataclasses

    sizeless = dataclasses.replace(QWEN, size_gb=None)
    real = lc.find_model_requirement
    monkeypatch.setattr(
        lc,
        "find_model_requirement",
        lambda mid: sizeless if mid == lc.LARGE_DEFAULT_MODEL_NAME else real(mid),
    )

    class FakeClient:
        def get_system_info(self, timeout=None):
            return STRIX_HALO_128

        def health_check(self):
            return {"version": "2026.39.1"}

    model_id, skipped, _ = lc.recommend_default_chat_model(FakeClient())
    assert model_id == lc.DEFAULT_MODEL_NAME
    assert "size" in skipped[0][1]


class TestQwenWindowFollowsMemory:
    """Qwen3.6 loads with the largest window this PC's memory holds, to 256K."""

    @pytest.mark.parametrize(
        "info,window",
        [
            # 96 GB carve-out + 15.8 GB GTT: the native 262144.
            (STRIX_HALO_128, 262144),
            # 48 GB carve-out + 7.9 GB GTT: still the native maximum.
            (STRIX_HALO_64, 262144),
            # 32 GB card: 28.8 GB usable - 25.5 GB weights leaves ~152K of KV.
            (
                {"devices": {"amd_dgpu": [{"available": True, "vram_gb": 32.0}]}},
                155648,
            ),
            # 24 GB card: Qwen3.6 is not the default here; forced, it gets 64K.
            (DGPU, lc.GPU_CTX_SIZE),
        ],
    )
    def test_window_per_machine(self, info, window):
        cap = capacity_from_system_info(info)
        assert lc.context_for_capacity(QWEN, cap) == window

    def test_native_maximum_is_registered_next_to_the_floor(self):
        assert (QWEN.min_ctx_size, QWEN.max_ctx_size) == (lc.GPU_CTX_SIZE, 262144)
        assert QWEN.kv_bytes_per_token == 10 * 2 * 256 * 2 * 2

    def test_gemma_does_not_opt_in(self):
        gemma = lc.find_model_requirement(lc.DEFAULT_MODEL_NAME)
        assert not gemma.scales_with_memory
        for info in (STRIX_HALO_128, DGPU, CPU_ONLY):
            cap = capacity_from_system_info(info)
            assert lc.context_for_capacity(gemma, cap) == lc.GPU_CTX_SIZE

    def test_default_pick_charges_the_window_the_load_requests(self, monkeypatch):
        """A 32 GB card still qualifies at the ~152K window it will load."""
        charged = []
        real = model_fit.check_fit

        def spy(size_gb, capacity, kv=0.0):
            charged.append(kv)
            return real(size_gb, capacity, kv)

        monkeypatch.setattr(model_fit, "check_fit", spy)
        info = {
            "devices": {"amd_dgpu": [{"available": True, "vram_gb": 32.0}]},
            "model_storage": {"free_bytes": 900e9},
        }

        class FakeClient:
            def get_system_info(self, timeout=None):
                return info

            def health_check(self):
                return {"version": "2026.40.0"}

        model_id, _, cap = lc.recommend_default_chat_model(FakeClient())
        assert model_id == lc.LARGE_DEFAULT_MODEL_NAME
        window = lc.context_for_capacity(QWEN, cap)
        assert window == 155648
        assert charged[0] == pytest.approx(
            model_fit.kv_cache_gb(QWEN.kv_bytes_per_token, window)
        )
