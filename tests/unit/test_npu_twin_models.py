# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The NPU twins of the GPU defaults: which PCs are offered them, which one
``gaia init --profile npu`` picks, and that a profile switch switches models."""

import json
from pathlib import Path

import pytest
import responses

from gaia.config import GaiaConfig
from gaia.installer.init_command import (
    INIT_PROFILES,
    resolve_init_chat_model,
    with_chat_model,
)
from gaia.llm import lemonade_client as lc
from gaia.llm.model_fit import (
    NPU_RAM,
    ModelFitError,
    capacity_from_system_info,
    npu_capacity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NPU = {"available": True, "family": "XDNA2"}

# Strix Halo, 128 GB with a 64 GB GPU carve-out (Linux reports it as vram_gb).
STRIX_HALO_128 = {
    "Physical Memory": "128 GB",
    "devices": {
        "amd_gpu": [
            {
                "available": True,
                "integrated": True,
                "vram_gb": 64.0,
                "virtual_mem_gb": 32.0,
            }
        ],
        "amd_npu": NPU,
    },
    "model_storage": {"free_bytes": 900e9},
}
# A 16 GB Ryzen AI laptop (Krackan Point): a small carve-out, an NPU.
RYZEN_AI_16 = {
    "Physical Memory": "16 GB",
    "devices": {
        "amd_gpu": [
            {
                "available": True,
                "integrated": True,
                "vram_gb": 0.5,
                "virtual_mem_gb": 7.7,
            }
        ],
        "amd_npu": NPU,
    },
    "model_storage": {"free_bytes": 200e9},
}
NO_NPU = {
    "Physical Memory": "64 GB",
    "devices": {
        "amd_gpu": [{"available": True, "integrated": True, "vram_gb": 32.0}],
        "amd_npu": {"available": False},
    },
    "model_storage": {"free_bytes": 900e9},
}


class FakeClient:
    def __init__(self, info):
        self.info = info

    def get_system_info(self, timeout=None):
        return self.info

    def list_models(self, show_all=False):
        return {"data": []}

    def health_check(self):
        return {"version": "2026.43.3"}


class TestRegistry:
    def test_gemma_has_an_npu_twin(self):
        npu = lc.NPU_TWINS[lc.DEFAULT_MODEL_NAME]
        assert npu == lc.NPU_DEFAULT_MODEL_NAME
        mr = lc.find_model_requirement(npu)
        assert mr is not None and mr.size_gb and mr.vision
        assert lc.runs_on_npu(npu)
        # A built-in FLM model is pulled by name: a recipe would 400 (#1655).
        assert mr.recipe is None and mr.checkpoint is None

    def test_only_evaluated_models_are_offered(self):
        # Qwen3.6's FLM build was too slow per agent turn to ship.
        assert lc.NPU_MODEL_LADDER == (lc.NPU_DEFAULT_MODEL_NAME,)
        assert lc.LARGE_DEFAULT_MODEL_NAME not in lc.NPU_TWINS


_FLM_MODELS = sorted(
    mr.model_id for mr in lc.MODELS.values() if lc.runs_on_npu(mr.model_id)
)


class TestFreshPullRequestShape:
    """The /pull body a fresh NPU download sends. Lemonade lists ``*-FLM`` models
    from ``flm list`` as built-ins, so the body must be the name alone: a
    ``recipe`` or ``checkpoint`` turns it into a user-model registration that
    400s (#1655)."""

    def test_the_npu_twin_is_among_the_checked_models(self):
        assert lc.NPU_DEFAULT_MODEL_NAME in _FLM_MODELS

    @pytest.mark.parametrize("model_id", _FLM_MODELS)
    def test_flm_models_pull_by_name_only(self, model_id):
        client = lc.LemonadeClient(host="localhost", port=13305)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{client.base_url}/system-info",
                json={
                    "recipes": {"flm": {"backends": {"npu": {"state": "installed"}}}}
                },
            )
            rsps.add(
                responses.POST,
                f"{client.base_url}/pull",
                body=b"event: complete\ndata: {}\n\n",
                content_type="text/event-stream",
                stream=True,
            )
            list(client.pull_model_stream(model_id))
            body = json.loads(rsps.calls[-1].request.body)
        assert body == {"model_name": model_id, "stream": True}

    def test_an_flm_model_never_carries_registration(self):
        for model_id in _FLM_MODELS:
            mr = lc.find_model_requirement(model_id)
            assert not mr.model_id.startswith("user."), model_id
            assert mr.pull_kwargs() == {}, model_id


class TestNpuCapacity:
    def test_npu_memory_is_ram_less_the_carve_out(self):
        cap = capacity_from_system_info(STRIX_HALO_128)
        assert cap.npu_memory_gb == pytest.approx(64.0)
        npu = npu_capacity(cap)
        assert (npu.memory_gb, npu.memory_source) == (pytest.approx(64.0), NPU_RAM)
        # The NPU view never counts as a GPU for the GPU-only product rule.
        assert not npu.on_gpu
        # And the GPU view is untouched.
        assert cap.memory_source == "AMD iGPU"

    def test_no_npu_means_no_npu_capacity(self):
        cap = capacity_from_system_info(NO_NPU)
        assert not cap.has_npu and npu_capacity(cap) is None

    def test_a_report_without_an_npu_key_has_none(self):
        info = {
            **STRIX_HALO_128,
            "devices": {"amd_gpu": STRIX_HALO_128["devices"]["amd_gpu"]},
        }
        assert not capacity_from_system_info(info).has_npu


class TestOffered:
    def test_big_pc_is_offered_gemma(self):
        cap = capacity_from_system_info(STRIX_HALO_128)
        assert lc.npu_chat_models_that_fit(cap) == list(lc.NPU_MODEL_LADDER)
        assert set(lc.NPU_MODEL_LADDER) <= set(lc.optional_chat_models_that_fit(cap))

    def test_16gb_laptop_is_offered_gemma_only(self):
        cap = capacity_from_system_info(RYZEN_AI_16)
        assert lc.npu_chat_models_that_fit(cap) == [lc.NPU_DEFAULT_MODEL_NAME]

    def test_pc_without_npu_is_offered_neither(self):
        cap = capacity_from_system_info(NO_NPU)
        assert lc.npu_chat_models_that_fit(cap) == []


class TestNpuProfilePick:
    def test_big_pc_still_gets_gemma(self):
        model, skipped, cap = lc.recommend_npu_chat_model(FakeClient(STRIX_HALO_128))
        assert model == lc.NPU_DEFAULT_MODEL_NAME and skipped == []
        assert cap.memory_source == NPU_RAM

    def test_16gb_laptop_gets_gemma(self):
        model, skipped, _ = lc.recommend_npu_chat_model(FakeClient(RYZEN_AI_16))
        assert (model, skipped) == (lc.NPU_DEFAULT_MODEL_NAME, [])

    def test_unreadable_report_gets_gemma(self):
        model, skipped, cap = lc.recommend_npu_chat_model(FakeClient("not an object"))
        assert (model, skipped, cap) == (lc.NPU_DEFAULT_MODEL_NAME, [], None)

    def test_pc_without_npu_is_refused_with_a_way_forward(self):
        with pytest.raises(ModelFitError, match="no available AMD NPU.*--profile npu"):
            lc.recommend_npu_chat_model(FakeClient(NO_NPU))

    def test_init_downloads_the_pick_and_the_flm_embedder(self):
        choice = resolve_init_chat_model(
            FakeClient(STRIX_HALO_128),
            reset_corrupt=False,
            enforce_fit=False,
            profile="npu",
        )
        ids = with_chat_model("npu", INIT_PROFILES["npu"]["models"], lambda: choice)
        assert ids == [lc.NPU_DEFAULT_MODEL_NAME, "embed-gemma-300m-FLM"]


class TestProfileSwitch:
    """A default the other profile recorded must not pin this one's device."""

    def test_npu_profile_replaces_a_gpu_default(self):
        cfg = GaiaConfig()
        cfg.default_model = lc.LARGE_DEFAULT_MODEL_NAME
        cfg.save()
        choice = resolve_init_chat_model(
            FakeClient(RYZEN_AI_16),
            reset_corrupt=False,
            enforce_fit=False,
            profile="npu",
        )
        assert (choice.model_id, choice.user_set) == (lc.NPU_DEFAULT_MODEL_NAME, False)

    def test_npu_profile_keeps_a_chosen_npu_model(self):
        cfg = GaiaConfig()
        cfg.default_model = lc.NPU_DEFAULT_MODEL_NAME
        cfg.save()
        choice = resolve_init_chat_model(
            FakeClient(STRIX_HALO_128),
            reset_corrupt=False,
            enforce_fit=False,
            profile="npu",
        )
        assert (choice.model_id, choice.user_set) == (lc.NPU_DEFAULT_MODEL_NAME, True)

    def test_plain_init_keeps_an_npu_model_the_user_picked(self):
        cfg = GaiaConfig()
        cfg.default_model = lc.NPU_DEFAULT_MODEL_NAME
        cfg.save()
        choice = resolve_init_chat_model(
            FakeClient(STRIX_HALO_128), reset_corrupt=False, enforce_fit=False
        )
        assert (choice.model_id, choice.user_set) == (lc.NPU_DEFAULT_MODEL_NAME, True)


class TestTuiAgrees:
    def test_tui_recommends_the_same_npu_models(self):
        doc = json.loads(
            (REPO_ROOT / "tui/internal/lemonade/recommended_models.json").read_text(
                encoding="utf-8"
            )
        )
        npu = {m["id"]: m for m in doc["models"] if m.get("device") == "npu"}
        assert set(npu) == set(lc.NPU_MODEL_LADDER)
        for model_id, entry in npu.items():
            mr = lc.find_model_requirement(model_id)
            assert entry["size_gb"] == mr.size_gb
            assert entry.get("vision", False) == mr.vision
            # The TUI's server-start window must be the one Python loads with.
            assert entry["min_ctx_size"] == lc.npu_default_ctx(model_id)


class TestNpuWindow:
    def test_twins_get_a_64k_window_and_e2b_keeps_the_flm_ceiling(self):
        assert lc.npu_default_ctx(lc.NPU_DEFAULT_MODEL_NAME) == lc.GPU_CTX_SIZE
        assert lc.npu_default_ctx("gemma4-it-e2b-FLM") == lc.NPU_CTX_SIZE
        assert lc.npu_default_ctx("some-unknown-FLM") == lc.NPU_CTX_SIZE

    def test_an_override_wins_on_the_npu(self, monkeypatch):
        monkeypatch.setenv("GAIA_CTX_SIZE", "131072")
        assert lc.resolve_ctx_size(lc.NPU_DEFAULT_MODEL_NAME) == 131072
        assert lc.resolve_ctx_size("gemma4-it-e2b-FLM") == 131072


class TestGpuStaysTheDefault:
    """The GPU model is the default; the NPU model is an option that, once
    picked, is the model setup keeps."""

    def test_setup_runs_the_flagship_profile_even_after_an_npu_pick(self):
        from gaia.ui import setup_runner

        cfg = GaiaConfig()
        cfg.profile = "npu"
        cfg.default_model = lc.NPU_DEFAULT_MODEL_NAME
        cfg.save()
        assert setup_runner.check_args(False, False)[3] == "gaia"
        assert setup_runner.run_command(False) == "gaia init --profile gaia"

    def test_an_npu_pick_holds_under_the_flagship_profile(self):
        cfg = GaiaConfig()
        cfg.default_model = lc.NPU_DEFAULT_MODEL_NAME
        cfg.save()
        choice = resolve_init_chat_model(
            None, reset_corrupt=False, enforce_fit=False, profile="gaia"
        )
        assert (choice.model_id, choice.user_set) == (lc.NPU_DEFAULT_MODEL_NAME, True)

    def test_a_gpu_pick_after_the_npu_holds_too(self):
        cfg = GaiaConfig()
        cfg.profile = "npu"
        cfg.default_model = lc.DEFAULT_MODEL_NAME
        cfg.save()
        choice = resolve_init_chat_model(
            None, reset_corrupt=False, enforce_fit=False, profile="gaia"
        )
        assert (choice.model_id, choice.user_set) == (lc.DEFAULT_MODEL_NAME, True)

    def _record(self, profile, configured, picked=lc.NPU_DEFAULT_MODEL_NAME, last=None):
        from gaia.installer.init_command import ChatModelChoice, InitCommand

        cmd = InitCommand(profile=profile, yes=True)
        cmd._chat_choice = ChatModelChoice(
            model_id=picked, user_set=configured == picked, skipped=[]
        )
        cmd._chat_model_ready = True
        cmd._setup_verified = True
        cfg = GaiaConfig()
        cfg.default_model = configured
        cfg.last_model = last
        cmd._record_chat_choice(cfg)
        self.cfg = cfg
        return cfg.default_model

    def test_profile_npu_replaces_the_model_the_uis_start_on(self):
        self._record("npu", None, last=lc.DEFAULT_MODEL_NAME)
        assert self.cfg.last_model == lc.NPU_DEFAULT_MODEL_NAME
        assert self.cfg.last_provider == "local"

    def test_profile_npu_rerun_after_a_gpu_pick_returns_to_the_npu(self):
        npu = lc.NPU_DEFAULT_MODEL_NAME
        self._record("npu", npu, last=lc.DEFAULT_MODEL_NAME)
        assert (self.cfg.default_model, self.cfg.last_model) == (npu, npu)

    def test_profile_npu_leaves_a_cloud_last_model(self):
        self._record("npu", None, last="claude-sonnet-5")
        assert self.cfg.last_model == "claude-sonnet-5"

    def test_an_unverified_npu_setup_records_nothing(self):
        from gaia.installer.init_command import ChatModelChoice, InitCommand

        cmd = InitCommand(profile="npu", yes=True)
        cmd._chat_choice = ChatModelChoice(
            model_id=lc.NPU_DEFAULT_MODEL_NAME, user_set=False, skipped=[]
        )
        cmd._chat_model_ready = True
        cmd._setup_verified = False
        cfg = GaiaConfig()
        cfg.last_model = lc.DEFAULT_MODEL_NAME
        cmd._record_chat_choice(cfg)
        assert (cfg.default_model, cfg.last_model) == (None, lc.DEFAULT_MODEL_NAME)

    def test_profile_npu_replaces_a_gpu_default(self):
        assert self._record("npu", "Qwen3.6-35B-A3B-GGUF") == lc.NPU_DEFAULT_MODEL_NAME

    def test_profile_npu_leaves_a_cloud_default(self):
        assert self._record("npu", "claude-sonnet-5") == "claude-sonnet-5"

    def test_plain_init_never_replaces_a_users_model(self):
        assert (
            self._record(
                "gaia", lc.NPU_DEFAULT_MODEL_NAME, picked="Qwen3.6-35B-A3B-GGUF"
            )
            == lc.NPU_DEFAULT_MODEL_NAME
        )

    def test_plain_init_never_picks_the_npu(self):
        assert lc.NPU_DEFAULT_MODEL_NAME not in lc.DEFAULT_MODEL_LADDER


class TestLoadTimeFreeMemory:
    """A load refuses or shrinks on the pool the model really uses."""

    def _machine(self, monkeypatch):
        from gaia.llm import model_fit

        gpu_free = model_fit.FreeMemory(90.0, 90.0)
        monkeypatch.setattr(lc, "_free_memory_snapshot", lambda url: (gpu_free, {}))
        monkeypatch.setattr(lc, "_system_info", lambda url: STRIX_HALO_128)
        monkeypatch.setattr(
            model_fit,
            "read_os_memory",
            lambda: model_fit.OsMemory(
                ram_total_gb=64, ram_available_gb=6, swap_free_gb=2
            ),
        )

    def test_npu_model_is_judged_on_free_system_ram(self, monkeypatch):
        self._machine(monkeypatch)
        free, _ = lc._free_memory_for(
            "http://localhost:13305/api/v1", lc.NPU_DEFAULT_MODEL_NAME
        )
        assert (free.free_gb, free.limit_gb) == (6, 8)
        verdict = lc.free_memory_verdict(
            "http://localhost:13305/api/v1",
            lc.find_model_requirement(lc.NPU_DEFAULT_MODEL_NAME),
            lc.GPU_CTX_SIZE,
        )
        assert not verdict.fits

    def test_gpu_model_keeps_the_gpu_pool(self, monkeypatch):
        self._machine(monkeypatch)
        free, _ = lc._free_memory_for(
            "http://localhost:13305/api/v1", lc.DEFAULT_MODEL_NAME
        )
        assert free.free_gb == 90.0


class TestPlainInitEnablesTheNpu:
    """A bare `gaia init` makes the NPU models available on a PC that has one."""

    def _cmd(self, monkeypatch, info, install_ok=True):
        from unittest.mock import MagicMock

        from gaia.installer.init_command import InitCommand

        client = MagicMock()
        client.get_system_info.return_value = info
        monkeypatch.setattr(
            "gaia.llm.lemonade_client.LemonadeClient", lambda **kw: client
        )
        cmd = InitCommand(profile="gaia", yes=True)
        installs = []
        monkeypatch.setattr(
            cmd,
            "_install_backend",
            lambda spec=None, recipe=None: installs.append(spec) or install_ok,
        )
        warnings = []
        monkeypatch.setattr(cmd, "_print_warning", warnings.append)
        return cmd, installs, warnings

    def test_npu_pc_gets_the_backend(self, monkeypatch):
        cmd, installs, warnings = self._cmd(monkeypatch, STRIX_HALO_128)
        cmd._enable_npu_if_present()
        assert installs == ["flm:npu"] and warnings == []

    def test_pc_without_npu_installs_nothing(self, monkeypatch):
        cmd, installs, warnings = self._cmd(monkeypatch, NO_NPU)
        cmd._enable_npu_if_present()
        assert installs == [] and warnings == []

    def test_a_failed_install_says_so_and_how_to_retry(self, monkeypatch):
        cmd, _, warnings = self._cmd(monkeypatch, STRIX_HALO_128, install_ok=False)
        cmd._enable_npu_if_present()
        assert len(warnings) == 1 and "Re-run `gaia init`" in warnings[0]


def test_single_turn_flm_builds_are_not_chat_options():
    from gaia.ui.routers.providers import _is_chat_model

    assert not _is_chat_model({"labels": ["vision", "single-turn", "chat"]})
    assert _is_chat_model({"labels": ["audio", "vision", "tool-calling", "chat"]})
