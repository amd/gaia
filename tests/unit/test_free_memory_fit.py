# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A model load is judged against the memory free now, not the machine's total.

A 16 GB Linux laptop OOM-killed GAIA on first launch: Gemma-4-E4B always asked
for a 64K window, nothing checked free memory before a load, and first launch
ran setup twice and preloaded the model while setup was still loading it. These
tests pin each half of the fix on mocked machines, asserting the decision (the
window asked for, the load refused or sent), never a real load.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm import model_fit
from gaia.llm.model_fit import FreeMemory, GpuMemoryUse, OsMemory

pytestmark = pytest.mark.real_free_memory

REPO_ROOT = Path(__file__).resolve().parents[2]
URL = "http://127.0.0.1:13305/api/v1"
GEMMA = lc.DEFAULT_MODEL_NAME
QWEN = lc.LARGE_DEFAULT_MODEL_NAME
EMBEDDER = lc.DEFAULT_EMBEDDING_MODEL

CPU_16 = {"Physical Memory": "16 GB", "devices": {"amd_gpu": []}}
CPU_64 = {"Physical Memory": "64 GB", "devices": {"amd_gpu": []}}
LINUX_IGPU_16 = {
    "Physical Memory": "16 GB",
    "devices": {
        "amd_gpu": [
            {
                "available": True,
                "integrated": True,
                "vram_gb": 0.5,
                "virtual_mem_gb": 7.8,
            }
        ]
    },
}
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
NVIDIA_24 = {
    "Physical Memory": "64 GB",
    "devices": {"nvidia_gpu": [{"available": True, "vram_gb": 24.0}]},
}


def _ram(total, available, swap=0.0, gpus=()):
    return OsMemory(
        ram_total_gb=total,
        ram_available_gb=available,
        swap_free_gb=swap,
        gpus=tuple(gpus),
    )


@pytest.fixture
def machine(monkeypatch):
    """Mock what Lemonade and the OS report; returns a setter."""
    monkeypatch.delenv("GAIA_CTX_SIZE", raising=False)
    monkeypatch.setattr(lc, "_CAPACITY_CACHE", {})

    def set_machine(info, os_memory, loaded=None):
        monkeypatch.setattr(model_fit, "read_os_memory", lambda: os_memory)
        monkeypatch.setattr(
            lc.LemonadeClient,
            "get_system_info",
            lambda self, verbose=False, timeout=None: info,
        )
        models = [
            {"model_name": name, "recipe_options": {"ctx_size": ctx}}
            for name, ctx in (loaded or {}).items()
        ]
        monkeypatch.setattr(
            lc.LemonadeClient,
            "health_check",
            lambda self, timeout=None: {"all_models_loaded": models},
        )

    return set_machine


@pytest.fixture
def client(monkeypatch):
    """A client whose /load is recorded, never sent."""
    sent = []
    c = lc.LemonadeClient(base_url=URL, verbose=False)
    monkeypatch.setattr(c, "_model_recipe", lambda name: "llamacpp")
    monkeypatch.setattr(
        c,
        "_post_load_with_transient_retry",
        lambda url, data, *a, **k: sent.append(data) or {"status": "success"},
    )
    monkeypatch.setattr(
        c, "get_status", lambda **_: lc.LemonadeStatus(running=True, loaded_models=[])
    )
    monkeypatch.setattr(c, "get_model_max_context_window", lambda *a, **k: None)
    monkeypatch.setattr(
        c,
        "list_models",
        lambda show_all=False: {"data": [{"id": GEMMA, "downloaded": True}]},
    )
    c.sent = sent
    return c


# ── The window a load asks for ────────────────────────────────────────────


class TestWindow:
    def test_16gb_box_with_6gb_free_never_asks_for_64k(self, machine):
        machine(CPU_16, _ram(16, 6))
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == model_fit.MIN_CONTEXT_TOKENS

    def test_16gb_box_with_some_room_gets_a_smaller_window(self, machine):
        # 10.5 GB free - 1 GB headroom - 1.35 GB embedder still to load
        # - 7.27 GB of weights leaves ~0.9 GB of KV: 48K, not 64K.
        machine(CPU_16, _ram(16, 10.5))
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == 49152

    def test_64gb_box_keeps_the_64k_eval_window(self, machine):
        machine(CPU_64, _ram(64, 40))
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == lc.GPU_CTX_SIZE

    def test_total_memory_alone_does_not_decide(self, machine):
        """Same 64 GB box; another app holds all but 9 GB of it."""
        machine(CPU_64, _ram(64, 9))
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) < lc.GPU_CTX_SIZE

    def test_another_lemonade_holding_the_igpu_is_counted(self, machine):
        """A user's own Lemonade has 6 GB of models in GTT: the OS sees it."""
        busy = GpuMemoryUse(
            vram_total_gb=0.5, vram_used_gb=0.4, gtt_total_gb=7.8, gtt_used_gb=6.0
        )
        machine(LINUX_IGPU_16, _ram(16, 9, gpus=[busy]))
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == model_fit.MIN_CONTEXT_TOKENS

    def test_a_resident_embedder_is_not_charged_twice(self, machine):
        machine(CPU_16, _ram(16, 10.5), loaded={EMBEDDER: 2048})
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == lc.GPU_CTX_SIZE

    def test_the_models_own_memory_counts_when_it_is_already_loaded(self, machine):
        """Loaded at 64K, little else free: re-resolving must not shrink it."""
        machine(CPU_16, _ram(16, 3.5), loaded={GEMMA: 65536, EMBEDDER: 2048})
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == lc.GPU_CTX_SIZE

    def test_the_window_holds_for_the_process(self, machine):
        """Free memory moving after the decision must not trigger a reload."""
        machine(CPU_16, _ram(16, 10.5))
        first = lc.resolve_ctx_size(GEMMA, base_url=URL)
        machine(CPU_16, _ram(16, 15))
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == first

    def test_gaia_ctx_size_still_pins_the_window(self, machine, monkeypatch):
        machine(CPU_16, _ram(16, 6))
        monkeypatch.setenv("GAIA_CTX_SIZE", "65536")
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == 65536

    def test_a_remote_lemonade_is_not_judged_on_this_pcs_memory(self, machine):
        machine(CPU_16, _ram(16, 2))
        remote = "http://gpu-box.lan:13305/api/v1"
        assert lc.resolve_ctx_size(GEMMA, base_url=remote) == lc.GPU_CTX_SIZE

    def test_unreadable_system_info_does_not_shrink_on_a_guess(
        self, machine, monkeypatch
    ):
        machine(CPU_16, _ram(16, 2))

        def down(self, verbose=False, timeout=None):
            raise lc.LemonadeClientError("connection refused")

        monkeypatch.setattr(lc.LemonadeClient, "get_system_info", down)
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == lc.GPU_CTX_SIZE

    @pytest.mark.parametrize("wired", [True, False])
    def test_ensure_ready_does_not_reload_a_memory_sized_model(
        self, machine, monkeypatch, wired
    ):
        """LemonadeManager must not demand 64K of a model that got 48K."""
        from unittest.mock import MagicMock, patch

        from gaia.llm import lemonade_manager
        from gaia.llm.lemonade_manager import LemonadeManager

        # Loaded at 48K, and only 48K fits even counting its own memory back.
        machine(CPU_16, _ram(16, 2.4))
        if not wired:  # what ensure_ready did before: the profile's 64K
            monkeypatch.setattr(
                lemonade_manager, "fit_ctx_to_free_memory", lambda m, ctx, url: ctx
            )
        resident = {
            "id": GEMMA,
            "model_name": GEMMA,
            "type": "llm",
            "recipe_options": {"ctx_size": 49152},
        }
        manager_client = MagicMock()
        manager_client.base_url = URL
        manager_client.get_status.return_value = lc.LemonadeStatus(
            running=True, context_size=49152, loaded_models=[resident]
        )
        manager_client.get_model_max_context_window.return_value = None
        LemonadeManager.reset()
        try:
            with patch(
                "gaia.llm.lemonade_manager.LemonadeClient",
                return_value=manager_client,
            ):
                LemonadeManager.ensure_ready(quiet=True, base_url=URL, model=GEMMA)
        finally:
            LemonadeManager.reset()
        assert manager_client.load_model.called is not wired


class TestStrixHaloUnchanged:
    """The baseline machines keep their windows and their default model."""

    def test_windows_igpu_with_no_recorded_carve_out_is_not_shrunk(
        self, machine, monkeypatch
    ):
        """Judging it on RAM alone would shrink a model its GPU holds."""
        monkeypatch.setattr(model_fit, "_windows_adapter_memory_gb", lambda name: 0.0)
        info = {
            "Physical Memory": "128.00 GB",
            "devices": {
                "amd_gpu": [
                    {
                        "available": True,
                        "integrated": True,
                        "name": "AMD Radeon(TM) 8060S Graphics",
                    }
                ]
            },
        }
        machine(info, _ram(32, 6))
        assert model_fit.free_memory(info, _ram(32, 6)) is None
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == lc.GPU_CTX_SIZE

    def test_windows_strix_halo_128(self, machine):
        machine(STRIX_HALO_128, _ram(32, 20))
        assert lc.resolve_ctx_size(QWEN, base_url=URL) == 262144
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == lc.GPU_CTX_SIZE

    def test_linux_strix_halo_128_with_qwen_resident(self, machine):
        card = GpuMemoryUse(
            vram_total_gb=96.0, vram_used_gb=30.0, gtt_total_gb=15.8, gtt_used_gb=0.5
        )
        machine(
            STRIX_HALO_128,
            _ram(32, 25, gpus=[card]),
            loaded={QWEN: 262144, EMBEDDER: 2048},
        )
        assert lc.resolve_ctx_size(QWEN, base_url=URL) == 262144

    def test_default_model_is_still_qwen(self, machine):
        machine(STRIX_HALO_128, _ram(32, 20))

        class FakeClient:
            base_url = URL

            def get_system_info(self, timeout=None):
                return STRIX_HALO_128

            def health_check(self):
                return {"version": "2026.40.0"}

        model_id, _, _ = lc.recommend_default_chat_model(FakeClient())
        assert model_id == QWEN


# ── The default model is chosen against the memory free now ────────────────

# A 48 GB Apple Silicon Mac: Metal can use ~36 GB, which holds Qwen3.6.
MAC_48 = {
    "Physical Memory": "48 GB",
    "devices": {"amd_gpu": [], "metal": {"available": True, "vram_gb": 36.0}},
    "model_storage": {"free_bytes": 400e9},
}


class _Lemonade:
    base_url = URL

    def __init__(self, info):
        self.info = info

    def get_system_info(self, timeout=None):
        return self.info

    def health_check(self):
        return {"version": "2026.40.0"}


class _Machine(_Lemonade):
    """A whole GAIA Lemonade: what setup, the check and the free-memory read ask."""

    def __init__(self, info):
        super().__init__(info)
        self.downloaded = set()

    def get_system_info(self, timeout=None, verbose=False):
        return self.info

    def health_check(self, timeout=None):
        return {"status": "ok", "version": "2026.40.0", "all_models_loaded": []}

    def check_model_available(self, model_id):
        return model_id in self.downloaded

    def list_models(self, show_all=False):
        return {"data": [{"id": m, "downloaded": True} for m in self.downloaded]}


class TestDefaultModelFollowsFreeMemory:
    """#5582: the pick was made on total memory, then the 23 GB download failed
    to load because only 20.4 GB was free."""

    def test_48gb_mac_with_20gb_free_gets_gemma_and_says_why(self, machine):
        machine(MAC_48, _ram(48, 20.4))
        model_id, skipped, _ = lc.recommend_default_chat_model(_Lemonade(MAC_48))
        assert model_id == GEMMA
        reason = dict(skipped)[QWEN]
        assert "20.4 GB" in reason and "free" in reason
        assert "Close" in reason

    def test_48gb_mac_with_room_free_gets_qwen(self, machine):
        machine(MAC_48, _ram(48, 40))
        model_id, _, _ = lc.recommend_default_chat_model(_Lemonade(MAC_48))
        assert model_id == QWEN

    def test_a_loaded_qwen_counts_its_own_memory_as_free(self, machine):
        machine(MAC_48, _ram(48, 4), loaded={QWEN: 32768})
        model_id, _, _ = lc.recommend_default_chat_model(_Lemonade(MAC_48))
        assert model_id == QWEN

    def test_a_finished_setup_stays_finished_when_more_memory_is_free_later(
        self, machine, monkeypatch
    ):
        """Set up at 20 GB free (Gemma); a later launch with 40 GB free must not
        expect Qwen3.6 and send a working user back to a 23 GB download."""
        from unittest.mock import MagicMock, patch

        from gaia.config import GaiaConfig
        from gaia.installer.init_command import InitCommand, check_setup_status

        lemonade = _Machine(MAC_48)
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        machine(MAC_48, _ram(48, 20.4))
        cmd = InitCommand(profile="gaia", yes=True)
        assert cmd._chat_model(lemonade) == GEMMA
        lemonade.downloaded |= {GEMMA, EMBEDDER}
        cmd._chat_model_ready = True
        GaiaConfig.update(cmd._record_chat_choice)

        machine(MAC_48, _ram(48, 40))
        embedded = MagicMock()
        embedded.status.return_value = MagicMock(
            installed=True,
            running=True,
            unresponsive_pid=None,
            version=embedded.version,
            base_url=URL,
        )
        with (
            patch("gaia.llm.lemonade_embedded.EmbeddedLemonade", return_value=embedded),
            patch(
                "gaia.llm.lemonade_manager.LemonadeManager.start_embedded_if_stopped"
            ),
            patch("gaia.llm.lemonade_client.LemonadeClient", return_value=lemonade),
        ):
            status = check_setup_status(profile="gaia")
        assert status.ready, status.reasons

    def test_setup_says_the_downgraded_pick_is_kept(self, machine, capsys):
        from gaia.installer.init_command import InitCommand

        machine(MAC_48, _ram(48, 20.4))
        InitCommand(profile="gaia", yes=True)._chat_model(_Machine(MAC_48))
        out = " ".join(capsys.readouterr().out.split())
        assert f"gaia config set default_model {QWEN}" in out

    def test_a_planned_model_is_the_one_set_up_even_with_more_memory_free(
        self, machine
    ):
        """The screen named Gemma at 20 GB free; the run must not switch to Qwen3.6."""
        from gaia.installer.init_command import InitCommand

        machine(MAC_48, _ram(48, 40))
        cmd = InitCommand(profile="gaia", yes=True, chat_model=GEMMA)
        assert cmd._chat_model(_Machine(MAC_48)) == GEMMA

    def test_a_planned_model_that_no_longer_fits_free_memory_is_refused(self, machine):
        from gaia.installer.init_command import InitCommand

        machine(MAC_48, _ram(48, 10))
        cmd = InitCommand(profile="gaia", yes=True, chat_model=QWEN)
        with pytest.raises(model_fit.ModelFitError, match="10.0 GB is free"):
            cmd._chat_model(_Machine(MAC_48))

    def test_the_plan_warns_when_even_the_smallest_model_cannot_load_now(self, machine):
        """Setup named Gemma, then its load failed: 7.8 GB needed, 6.2 GB free."""
        machine(CPU_16, _ram(16, 6.2))
        capacity = model_fit.capacity_from_system_info(CPU_16)
        warning = lc.free_memory_load_warning(
            URL, lc.find_model_requirement(GEMMA), capacity
        )
        assert warning.startswith(
            f"Not enough free memory to load {GEMMA} with a 32,768-token window"
        )
        assert "7.8 GB" in warning and "6.2 GB is free" in warning
        assert "Close apps" in warning

    def test_no_plan_warning_when_the_model_fits_free_memory(self, machine):
        machine(CPU_16, _ram(16, 12))
        capacity = model_fit.capacity_from_system_info(CPU_16)
        assert (
            lc.free_memory_load_warning(URL, lc.find_model_requirement(GEMMA), capacity)
            is None
        )

    def test_a_remote_server_is_not_judged_by_this_pcs_memory(self, machine):
        machine(MAC_48, _ram(48, 2))
        remote = _Lemonade(MAC_48)
        remote.base_url = "http://10.0.0.5:13305/api/v1"
        model_id, _, _ = lc.recommend_default_chat_model(remote)
        assert model_id == QWEN


MAC_48_M4 = {
    "devices": {
        "metal": {"available": True, "name": "Apple M4 Pro", "vram_gb": 36.0},
    },
    "model_storage": {"free_bytes": 900e9},
}


class TestDefaultPickedByFreeMemory:
    """The default must load in the memory free now, or init downloads a model
    its own verify step then refuses (#5582)."""

    @pytest.fixture
    def recommend(self, machine, monkeypatch):
        def run(info, os_memory):
            machine(info, os_memory)
            monkeypatch.setattr(
                lc.LemonadeClient,
                "health_check",
                lambda self, timeout=None: {
                    "version": "2026.40.0",
                    "all_models_loaded": [],
                },
            )
            model_id, skipped, _ = lc.recommend_default_chat_model(
                lc.LemonadeClient(base_url=URL, verbose=False)
            )
            return model_id, dict(skipped)

        return run

    def test_a_mac_that_holds_qwen_but_has_little_free_gets_gemma(self, recommend):
        model_id, skipped = recommend(MAC_48_M4, _ram(48, 20.4))
        assert model_id == GEMMA
        assert "free memory" in skipped[QWEN] and "20.4 GB" in skipped[QWEN]
        assert "re-run `gaia init`" in skipped[QWEN]

    def test_the_same_mac_with_room_free_gets_qwen(self, recommend):
        assert recommend(MAC_48_M4, _ram(48, 40))[0] == QWEN

    def test_the_pick_agrees_with_the_load_guard(self, recommend, client):
        """Whatever is skipped for free memory, the load refuses too."""
        recommend(MAC_48_M4, _ram(48, 20.4))
        with pytest.raises(lc.InsufficientMemoryError):
            client.load_model(QWEN, ctx_size=model_fit.MIN_CONTEXT_TOKENS)

    def test_unreadable_free_memory_keeps_the_floor_and_says_why(
        self, recommend, monkeypatch
    ):
        def unreadable(url):
            raise model_fit.ModelFitError("no memory reported")

        monkeypatch.setattr(lc, "_free_memory_snapshot", unreadable)
        model_id, skipped = recommend(MAC_48_M4, _ram(48, 40))
        assert model_id == GEMMA
        assert "no memory reported" in skipped[QWEN]


# ── The check before every load ────────────────────────────────────────────


class TestLoadGuard:
    def test_16gb_box_with_6gb_free_refuses_instead_of_loading(self, machine, client):
        machine(CPU_16, _ram(16, 6))
        with pytest.raises(lc.InsufficientMemoryError) as err:
            client._ensure_model_loaded(GEMMA)
        message = str(err.value)
        assert "7.8 GB" in message and "6.0 GB" in message
        assert "Close apps" in message and lc.FREE_MEMORY_HELP_URL in message
        assert client.sent == []

    def test_a_smaller_window_loads_when_it_fits(self, machine, client):
        machine(CPU_16, _ram(16, 10.5))
        client._ensure_model_loaded(GEMMA)
        assert [d["ctx_size"] for d in client.sent] == [49152]

    def test_64gb_box_loads_at_64k(self, machine, client):
        machine(CPU_64, _ram(64, 40))
        client._ensure_model_loaded(GEMMA)
        assert [d["ctx_size"] for d in client.sent] == [lc.GPU_CTX_SIZE]

    def test_free_swap_avoids_a_refusal_but_not_a_smaller_window(self, machine, client):
        # 6 GB of RAM is too little for a window above the floor, but with 4 GB
        # of free swap the floor load cannot run the machine out of memory.
        machine(CPU_16, _ram(16, 6, swap=4))
        client._ensure_model_loaded(GEMMA)
        assert [d["ctx_size"] for d in client.sent] == [model_fit.MIN_CONTEXT_TOKENS]

    def test_an_explicit_64k_pin_is_refused_not_shrunk(self, machine, client):
        machine(CPU_16, _ram(16, 8))
        with pytest.raises(lc.InsufficientMemoryError, match="65,536-token"):
            client.load_model(GEMMA, ctx_size=65536, prompt=False)
        assert client.sent == []

    @pytest.mark.parametrize(
        "info",
        ["<html>502</html>", {}, {"devices": {}}],
        ids=["not-json-object", "empty", "no-memory"],
    )
    def test_malformed_system_info_fails_loudly(self, machine, client, info):
        machine(info, _ram(16, 12))
        with pytest.raises(lc.LemonadeClientError) as err:
            client.load_model(GEMMA, ctx_size=65536, prompt=False)
        assert not isinstance(err.value, lc.InsufficientMemoryError)
        assert "fits in memory" in str(err.value)
        assert client.sent == []

    def test_missing_system_info_fails_loudly(self, machine, client, monkeypatch):
        machine(CPU_16, _ram(16, 12))

        def missing(self, verbose=False, timeout=None):
            raise lc.LemonadeClientError("404 Not Found: /system-info")

        monkeypatch.setattr(lc.LemonadeClient, "get_system_info", missing)
        with pytest.raises(lc.LemonadeClientError, match="Cannot read free memory"):
            client.load_model(GEMMA, ctx_size=65536, prompt=False)
        assert client.sent == []

    def test_a_discrete_gpu_without_a_free_vram_report_is_left_to_lemonade(
        self, machine, client
    ):
        machine(NVIDIA_24, _ram(64, 1))
        client.load_model(GEMMA, ctx_size=65536, prompt=False)
        assert len(client.sent) == 1

    def test_a_refusal_forgets_the_window_so_a_retry_measures_again(
        self, machine, client
    ):
        machine(CPU_16, _ram(16, 10.5))
        assert lc.resolve_ctx_size(GEMMA, base_url=URL) == 49152
        machine(CPU_16, _ram(16, 8))  # a browser opened since
        with pytest.raises(lc.InsufficientMemoryError, match="49,152-token"):
            client._ensure_model_loaded(GEMMA)
        client._ensure_model_loaded(GEMMA)  # the user retries
        assert [d["ctx_size"] for d in client.sent] == [model_fit.MIN_CONTEXT_TOKENS]

    def test_an_unreadable_sysfs_fails_loudly(self, machine, client, monkeypatch):
        machine(CPU_16, _ram(16, 12))

        def unreadable():
            raise model_fit.ModelFitError(
                "Cannot read GPU memory use from /sys/class/drm/card1/device/"
                "mem_info_gtt_used: Permission denied"
            )

        monkeypatch.setattr(model_fit, "read_os_memory", unreadable)
        with pytest.raises(lc.LemonadeClientError, match="Permission denied"):
            client.load_model(GEMMA, ctx_size=65536, prompt=False)
        assert client.sent == []

    def test_an_unknown_model_is_not_judged(self, machine, client):
        machine(CPU_16, _ram(16, 1))
        client.load_model("Some-Unlisted-Model-GGUF", ctx_size=4096, prompt=False)
        assert len(client.sent) == 1


# ── Free memory per kind of machine ────────────────────────────────────────


class TestFreeMemory:
    def test_cpu_only_is_available_ram(self):
        assert model_fit.free_memory(CPU_16, _ram(16, 6, swap=2)) == FreeMemory(6, 8)

    def test_linux_igpu_is_free_carve_out_plus_free_gtt(self):
        card = GpuMemoryUse(
            vram_total_gb=0.5, vram_used_gb=0.1, gtt_total_gb=7.8, gtt_used_gb=2.8
        )
        free = model_fit.free_memory(LINUX_IGPU_16, _ram(16, 12, gpus=[card]))
        assert free.free_gb == pytest.approx(0.4 + 5.0)

    def test_linux_igpu_shared_memory_is_capped_by_free_ram(self):
        card = GpuMemoryUse(
            vram_total_gb=0.5, vram_used_gb=0.0, gtt_total_gb=7.8, gtt_used_gb=0.0
        )
        free = model_fit.free_memory(LINUX_IGPU_16, _ram(16, 3, gpus=[card]))
        assert free.free_gb == pytest.approx(3.5)

    def test_windows_igpu_is_carve_out_plus_available_ram(self):
        free = model_fit.free_memory(STRIX_HALO_128, _ram(32, 20))
        assert free.free_gb == pytest.approx(116)

    @staticmethod
    def _windows_registry_igpu(physical):
        info = {
            "devices": {
                "amd_gpu": [
                    {
                        "available": True,
                        "integrated": True,
                        "name": "AMD Radeon(TM) 8060S Graphics",
                    }
                ]
            }
        }
        if physical is not None:
            info["Physical Memory"] = physical
        return info

    def test_windows_registry_igpu_shared_part_is_capped_at_half_of_ram(
        self, monkeypatch
    ):
        monkeypatch.setattr(model_fit, "_windows_adapter_memory_gb", lambda name: 0.5)
        info = self._windows_registry_igpu("128 GB")
        # 100 GB free RAM, but Windows shares only half of its 127.5 GB.
        free = model_fit.free_memory(info, _ram(128, 100, swap=8))
        assert free == FreeMemory(0.5 + 63.75, 0.5 + 63.75)
        # Less free RAM than the shared cap: free RAM is the limit.
        free = model_fit.free_memory(info, _ram(128, 20, swap=8))
        assert free == FreeMemory(0.5 + 20.0, 0.5 + 28.0)

    def test_windows_registry_igpu_without_ram_figure_is_unknown(self, monkeypatch):
        monkeypatch.setattr(model_fit, "_windows_adapter_memory_gb", lambda name: 4.0)
        info = self._windows_registry_igpu(None)
        assert model_fit.free_memory(info, _ram(128, 100)) is None

    def test_amd_dgpu_with_sysfs_is_free_vram(self):
        info = {"devices": {"amd_dgpu": [{"available": True, "vram_gb": 24.0}]}}
        card = GpuMemoryUse(
            vram_total_gb=24.0, vram_used_gb=20.0, gtt_total_gb=32.0, gtt_used_gb=0
        )
        assert model_fit.free_memory(info, _ram(64, 40, gpus=[card])) == FreeMemory(
            4.0, 4.0
        )

    def test_linux_sysfs_is_read_per_amdgpu_card(self, tmp_path, monkeypatch):
        monkeypatch.setattr(model_fit.sys, "platform", "linux")
        amd = tmp_path / "card1" / "device"
        amd.mkdir(parents=True)
        for name, value in (
            ("vram_total", 512e6),
            ("vram_used", 100e6),
            ("gtt_total", 7.8e9),
            ("gtt_used", 2.8e9),
        ):
            (amd / f"mem_info_{name}").write_text(f"{int(value)}\n")
        (tmp_path / "card0" / "device").mkdir(parents=True)  # not amdgpu
        (tmp_path / "card1-eDP-1").mkdir()  # a connector, not a card

        cards = model_fit._amdgpu_cards(tmp_path)
        assert cards == (
            GpuMemoryUse(
                vram_total_gb=0.512, vram_used_gb=0.1, gtt_total_gb=7.8, gtt_used_gb=2.8
            ),
        )

    def test_discrete_gpu_without_a_report_is_unknown(self):
        assert model_fit.free_memory(NVIDIA_24, _ram(64, 40)) is None

    def test_no_memory_named_fails_loudly(self):
        with pytest.raises(model_fit.ModelFitError, match="Update Lemonade"):
            model_fit.free_memory({"devices": {}}, _ram(16, 8))

    def test_window_steps_down_and_stops_at_the_floor(self):
        def window(free_gb):
            return model_fit.context_for_free_memory(
                size_gb=5.97, kv_bytes_per_token=16384, ctx=65536, free_gb=free_gb
            )

        assert window(20) == 65536
        assert window(9.0) % model_fit.CONTEXT_STEP_TOKENS == 0
        assert model_fit.MIN_CONTEXT_TOKENS <= window(9.0) < 65536
        assert window(8.5) is None

    def test_small_machines_load_on_demand(self):
        assert not model_fit.preloads_chat_model(15.6)
        assert model_fit.preloads_chat_model(31.2)


def test_gemma_size_and_kv_cache_match_its_architecture():
    gemma = lc.find_model_requirement(GEMMA)
    # Q4_K_M weights plus the F16 vision projector, as Lemonade lists them.
    assert gemma.size_gb == pytest.approx(4.98 + 0.99, abs=0.01)
    # 4 full-attention layers own KV: 2 KV heads x 512 dims x K+V x f16.
    assert gemma.kv_bytes_per_token == 4 * 2 * 512 * 2 * 2
    # It never grows past the 64K the eval baseline was captured at.
    assert not gemma.scales_with_memory and gemma.min_ctx_size == lc.GPU_CTX_SIZE


# ── First launch ──────────────────────────────────────────────────────────


def test_the_desktop_installer_leaves_model_setup_to_the_app():
    """#5629: the installer downloads no model. The app's setup screen is the one
    place first launch runs `gaia init`, after the user picks cloud or local."""
    source = (
        REPO_ROOT / "src/gaia/apps/webui/services/backend-installer.cjs"
    ).read_text(encoding="utf-8")
    assert not re.search(r'\[\s*"init"', source), (
        "backend-installer.cjs runs `gaia init` again; model setup belongs to "
        "the app's first-run setup screen"
    )


def test_warm_up_does_not_load_the_model_on_a_small_machine(monkeypatch):
    from gaia.ui import _chat_helpers, warmup

    monkeypatch.setattr(model_fit, "ram_total_gb", lambda: 15.6)

    def must_not_run(*args, **kwargs):
        raise AssertionError("warm-up built an agent or loaded a model")

    monkeypatch.setattr(_chat_helpers, "get_agent_registry", must_not_run)
    monkeypatch.setattr(_chat_helpers, "_maybe_load_expected_model", must_not_run)
    assert warmup._warm_new_chat_prefix(db=None) is None
    assert "16 GB" in warmup.preload_skip_reason()


def test_gaia_init_verifies_the_window_this_pc_can_hold(machine, monkeypatch):
    """`gaia init` on a tight 16 GB box must not demand a 64K load."""
    from unittest.mock import MagicMock, patch

    from gaia.installer.init_command import InitCommand

    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    machine(CPU_16, _ram(16, 10.5))
    cmd = InitCommand(profile="gaia", yes=True)
    cmd.console = MagicMock()
    init_client = MagicMock()
    init_client.base_url = URL
    init_client.health_check.return_value = {"status": "ok"}
    init_client.get_system_info.return_value = CPU_16
    with (
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=init_client),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready", return_value=True
        ) as ensure_ready,
        patch.object(cmd, "_chat_model", return_value=GEMMA),
        patch.object(cmd, "_prompt_yes_no", return_value=False),
    ):
        cmd._verify_setup()

    assert ensure_ready.call_args.kwargs["min_context_size"] == 49152
