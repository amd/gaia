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
    def test_qwen_30b_fits_a_128gb_strix_halo(self):
        assert check_fit(
            QWEN.size_gb, capacity_from_system_info(STRIX_HALO_128), QWEN.kv_cache_gb
        ).fits

    def test_qwen_30b_also_fits_a_64gb_strix_halo(self):
        # At 17.4 GB, the new default has a much smaller footprint than Flash's
        # 82.86 GB — a 64 GB Strix Halo (~55.9 GB pool) now qualifies too.
        assert check_fit(
            QWEN.size_gb, capacity_from_system_info(STRIX_HALO_64), QWEN.kv_cache_gb
        ).fits

    def test_qwen_30b_does_not_fit_the_smallest_machines(self):
        verdict = check_fit(
            QWEN.size_gb, capacity_from_system_info(MAC_M4), QWEN.kv_cache_gb
        )
        assert not verdict.fits and "memory" in verdict.reason

    def test_qwen_30b_fits_a_32gb_cpu_box(self):
        cap = capacity_from_system_info(CPU_ONLY)
        assert check_fit(QWEN.size_gb, cap, QWEN.kv_cache_gb).fits

    def test_qwen_30b_charges_its_kv_cache(self):
        # 17.4 GB of weights plus ~6.4 GB of KV cache at 64K: a 24 GB card
        # holds the weights but not the cache, so it must not be offered.
        assert QWEN.kv_cache_gb > 0
        cap = capacity_from_system_info(DGPU)
        assert check_fit(QWEN.size_gb, cap).fits  # weights alone would pass
        verdict = check_fit(QWEN.size_gb, cap, QWEN.kv_cache_gb)
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
            # 17.4 GB fits a 64 GB Strix Halo too — a much wider footprint
            # than Flash's 128 GB-class-only 82.86 GB.
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

    def test_kv_cache_matches_the_python_registry(self, doc):
        """The TUI's fit check must charge the same KV cache as Python's."""
        for entry in doc["models"]:
            mr = lc.find_model_requirement(entry.get("register_as") or entry["id"])
            if mr is not None:
                assert entry.get("kv_cache_gb", 0.0) == mr.kv_cache_gb, entry["id"]

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
        # 82.86 GB, but the new, much smaller 17.4 GB default fits fine.
        assert not check_fit(FLASH.size_gb, cap).fits
        assert check_fit(QWEN.size_gb, cap, QWEN.kv_cache_gb).fits

    def test_macos_metal(self):
        cap = capacity_from_system_info(_fixture("lemonade11_metal_macos.json"))
        assert (cap.memory_source, cap.memory_gb) == ("Apple GPU", pytest.approx(51.84))

    def test_a_gpu_without_reported_memory_is_not_judged_on_system_ram(self):
        info = _fixture("lemonade11_amd_dgpu_windows.json")
        info["Physical Memory"] = "128 GB"  # would wrongly fit Qwen if used
        with pytest.raises(ModelFitError, match="not its memory"):
            capacity_from_system_info(info)

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
    """The default (Qwen3 30B A3B) is a Lemonade built-in with no version floor
    of its own — a big PC gets it on any supported Lemonade. Qwen3.8 Flash
    (the switchable alternative, no longer auto-selected) is the one that
    needs llama.cpp's qwen4exp, first bundled in v2026.39.1; verified via its
    ModelRequirement directly rather than through the ladder it is no longer
    part of.
    """

    def _client(self, info, version):
        class FakeClient:
            def get_system_info(self, timeout=None):
                return info

            def health_check(self):
                return {"version": version} if version else {}

        return FakeClient()

    @pytest.mark.parametrize(
        "version", ["2026.39.1", "2026.40.0~3.abc1234", "11.9.0", None]
    )
    def test_a_big_pc_gets_the_default_on_any_lemonade_version(self, version):
        model_id, _, _ = lc.recommend_default_chat_model(
            self._client(STRIX_HALO_128, version)
        )
        assert model_id == lc.LARGE_DEFAULT_MODEL_NAME

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
