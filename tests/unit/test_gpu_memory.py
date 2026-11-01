# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The GPU memory step (#6226): the split, what a bigger share unlocks, applying it.

Every test runs against a fake /sys, /proc and /etc/modprobe.d under tmp_path,
a temp GAIA home, and a stand-in for the elevated command. Nothing here can
reach pkexec, reg, bcdedit or the real system config.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.llm import gpu_memory as gm
from gaia.llm import lemonade_client as lc
from gaia.llm import model_fit

pytestmark = pytest.mark.allow_network  # TestClient only; nothing leaves the machine

LOCAL = "http://localhost:13305/api/v1"
QWEN = lc.LARGE_DEFAULT_MODEL_NAME
FLASH = lc.FLASH_OPTION_MODEL_NAME
REAL_READ_SYSTEM_INFO = gm._read_system_info
#: Lemonade reports GiB; the API answers in decimal GB.
GB_PER_GIB = 2**30 / 1e9
#: The kernel's default limit on a 128 GiB box: 62.5 GiB of 4 KiB pages.
LIVE_PAGES = 16_384_000


def linux_igpu(total_gb: float, gtt_gb: float, carve_gb: float = 0.5) -> dict:
    """Lemonade's /system-info for a Linux Strix Halo-class PC."""
    return {
        "Physical Memory": f"{total_gb:g} GB",
        "devices": {
            "amd_gpu": [
                {
                    "available": True,
                    "integrated": True,
                    "family": "gfx1151",
                    "vram_gb": carve_gb,
                    "virtual_mem_gb": gtt_gb,
                }
            ]
        },
    }


WINDOWS_IGPU = {
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
DGPU = {
    "Physical Memory": "64 GB",
    "devices": {"amd_gpu": [{"available": True, "integrated": False, "vram_gb": 24.0}]},
}
CPU_ONLY = {"Physical Memory": "32 GB", "devices": {"amd_gpu": []}}
MAC = {
    "Physical Memory": "16 GB",
    "devices": {"metal": {"available": True, "vram_gb": 11.84}},
}


class Sandbox:
    """A fake Linux box: sysfs, procfs, modprobe.d, GAIA home, boot clock."""

    def __init__(self, root: Path, monkeypatch):
        self.root = root
        self.mp = monkeypatch
        self.sys_module = root / "sys" / "module"
        self.proc = root / "proc"
        self.modprobe_d = root / "etc" / "modprobe.d"
        self.ostree = root / "run" / "ostree-booted"
        for d in (self.proc, self.modprobe_d, self.ostree.parent):
            d.mkdir(parents=True, exist_ok=True)
        self.calls: list = []
        self.returncode = 0
        self.fail_after_write = False
        self.boot = 1000.0
        self.clock = 2000.0
        self.info = linux_igpu(128, 62.5)
        self.url = LOCAL
        self.os = "linux"
        self.set_module("ttm", pages=LIVE_PAGES)
        self.cmdline("BOOT_IMAGE=/vmlinuz root=UUID=abc ro quiet splash")
        self.meminfo(127.5)  # 128 GiB installed less the 0.5 GiB carve-out
        monkeypatch.setattr(
            gm,
            "PATHS",
            gm.SystemPaths(
                sys_module=self.sys_module,
                proc=self.proc,
                modprobe_d=self.modprobe_d,
                ostree_marker=self.ostree,
            ),
        )
        monkeypatch.setattr(gm, "_platform", lambda: self.os)
        monkeypatch.setattr(gm, "_boot_time", lambda: self.boot)
        monkeypatch.setattr(gm, "_now", self._now)
        monkeypatch.setattr(gm, "_is_root", lambda: False)
        self.tools = {"pkexec", "update-initramfs"}
        monkeypatch.setattr(
            gm,
            "_which",
            lambda name: f"/usr/bin/{name}" if name in self.tools else None,
        )
        monkeypatch.setattr(gm, "_run_elevated", self._run_elevated)
        monkeypatch.setattr(gm, "_read_system_info", lambda: (self.info, self.url))

    def set_module(self, name: str, pages=None, builtin: bool = False):
        params = self.sys_module / name / "parameters"
        params.mkdir(parents=True, exist_ok=True)
        if pages is not None:
            (params / "pages_limit").write_text(f"{pages}\n", encoding="ascii")
        initstate = self.sys_module / name / "initstate"
        if builtin:
            initstate.unlink(missing_ok=True)
        else:
            initstate.write_text("live\n", encoding="ascii")

    def meminfo(self, gib: float):
        kb = int(gib * 1024 * 1024)
        (self.proc / "meminfo").write_text(
            f"MemTotal:       {kb} kB\nMemFree:  1 kB\n", encoding="ascii"
        )

    def cmdline(self, text: str):
        (self.proc / "cmdline").write_text(text + "\n", encoding="utf-8")

    @property
    def conf(self) -> Path:
        return self.modprobe_d / "ttm.conf"

    def _run_elevated(self, argv):
        """Plays the elevated script against the fake /etc, after checking its argv."""
        self.calls.append(argv)
        assert argv[0] == "/usr/bin/pkexec"
        assert argv[1:3] == ["/bin/sh", "-c"] and argv[3] == gm._ELEVATED_SCRIPT
        conf, content, tool = argv[5:]
        assert Path(conf) == self.conf
        if self.returncode in (126, 127):
            return subprocess.CompletedProcess(argv, self.returncode, "", "dismissed")
        if content:
            Path(conf).write_text(content + "\n", encoding="utf-8")
        else:
            Path(conf).unlink(missing_ok=True)
        if self.fail_after_write:
            return subprocess.CompletedProcess(argv, 1, "", f"{tool}: failed")
        return subprocess.CompletedProcess(argv, self.returncode, "", "")

    def _now(self) -> float:
        self.clock += 1
        return self.clock

    def reboot(self, pages=None):
        """A restart: the boot clock moves and the kernel reads the new limit."""
        self.boot = self._now()
        if pages is not None:
            self.set_module("ttm", pages=pages)
            gtt = pages * 4096 / 2**30  # Lemonade reports GiB
            self.info["devices"]["amd_gpu"][0]["virtual_mem_gb"] = gtt


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("GAIA_HOME", str(home / ".gaia"))

    def no_real_subprocess(*a, **k):
        raise AssertionError(f"a real subprocess was started: {a!r}")

    monkeypatch.setattr(gm.subprocess, "run", no_real_subprocess)
    sandbox = Sandbox(tmp_path / "root", monkeypatch)
    assert str(gm.PATHS.modprobe_d).startswith(str(tmp_path))
    assert str(gm._state_path()).startswith(str(tmp_path))
    return sandbox


def model(result: dict, model_id: str) -> dict:
    return next(m for m in result["models"] if m["id"] == model_id)


# --- Reading the split -------------------------------------------------------------


class TestWhichPCsSeeTheStep:
    @pytest.mark.parametrize(
        "info,words",
        [
            (DGPU, "own memory"),
            (CPU_ONLY, "No GPU"),
            (MAC, "own memory"),
        ],
    )
    def test_hidden_without_an_integrated_amd_gpu(self, box, info, words):
        result = gm.describe(info, LOCAL)
        assert result["applies"] is False and result["show_in_setup"] is False
        assert words in result["hidden_reason"]
        assert result["method"] == gm.METHOD_UNSUPPORTED

    def test_hidden_for_a_remote_server(self, box):
        result = gm.describe(linux_igpu(128, 62.5), "http://10.0.0.5:13305/api/v1")
        assert result["applies"] is False
        assert "another machine" in result["hidden_reason"]

    def test_unreadable_system_info_is_loud(self, box):
        with pytest.raises(gm.Unavailable):
            gm.describe({"devices": {}}, LOCAL)

    def test_lemonade_not_answering_is_a_503(self, box, monkeypatch):
        def down(*a, **k):
            raise lc.LemonadeClientError("connection refused")

        monkeypatch.setattr(lc.LemonadeClient, "get_system_info", down)
        monkeypatch.setattr(gm, "_read_system_info", REAL_READ_SYSTEM_INFO)
        with pytest.raises(gm.Unavailable) as err:
            gm.status()
        assert err.value.status == 503
        assert "connection refused" in str(err.value)


class TestLinuxSplit:
    @pytest.mark.parametrize("total,gtt", [(16, 7.75), (32, 15.5), (64, 31.5)])
    def test_small_and_mid_pcs_see_no_setup_step(self, box, total, gtt):
        box.info = linux_igpu(total, gtt)
        result = gm.describe(box.info, LOCAL)
        assert result["applies"] is True
        assert result["show_in_setup"] is False
        assert result["recommended_setting_gb"] is None

    def test_a_16gb_pc_has_nothing_to_give(self, box):
        result = gm.describe(linux_igpu(16, 7.75), LOCAL)
        assert result["setting"]["max_gb"] == 0
        assert result["presets"] == []
        # No steps that would set the GPU to 0 GB.
        assert result["guided_steps"] == [] and result["guided_value"] is None

    def test_128gb_reports_the_split_and_keeps_16gb_for_the_cpu(self, box):
        result = gm.describe(box.info, LOCAL)
        assert result["os"] == "linux"
        assert result["method"] == gm.METHOD_APPLY
        # Lemonade's 128 GiB, 62.5 GiB and 0.5 GiB, in decimal GB.
        assert result["total_gb"] == 137.4
        assert result["gpu_gb"] == 67.6
        assert result["cpu_gb"] == 69.8
        assert result["setting"]["current_gb"] == 67.1
        assert result["setting"]["max_gb"] == 120  # 137.4 - 16 - 0.5 carve-out
        assert [p["setting_gb"] for p in result["presets"]] == [80, 96, 112, 120]

    def test_the_split_is_model_fits_pool(self, box):
        result = gm.describe(box.info, LOCAL)
        cap = model_fit.capacity_from_system_info(box.info)
        assert result["gpu_gb"] == round(cap.memory_gb * GB_PER_GIB, 1)

    def test_128gb_unlocks_flash_but_not_in_setup(self, box):
        result = gm.describe(box.info, LOCAL)
        assert model(result, QWEN)["fits_now"] is True
        flash = model(result, FLASH)
        assert flash["fits_now"] is False and flash["fits_at_setting_gb"] is not None
        assert flash["default"] is False
        assert FLASH in result["presets"][-1]["unlocks"]
        # Flash is opt-in, so it doesn't earn a first-run step.
        assert result["show_in_setup"] is False

    def test_flash_fit_point_agrees_with_check_fit(self, box):
        result = gm.describe(box.info, LOCAL)
        at = model(result, FLASH)["fits_at_setting_gb"]
        mr = lc.find_model_requirement(FLASH)

        def fits(gtt):
            gib = (0.5 * GB_PER_GIB + gtt) / GB_PER_GIB
            cap = model_fit.MachineCapacity(gib, "AMD iGPU", None)
            return model_fit.check_fit(mr.size_gb, cap, lc.kv_cache_for(mr, cap)).fits

        assert fits(at) and not fits(at - 1)

    def test_a_low_limit_shows_the_step_and_recommends_the_smallest_unlock(self, box):
        box.info = linux_igpu(64, 16)
        result = gm.describe(box.info, LOCAL)
        assert model(result, QWEN)["fits_now"] is False
        assert result["show_in_setup"] is True
        assert result["recommended_setting_gb"] == 32
        assert QWEN in next(
            p["unlocks"] for p in result["presets"] if p["setting_gb"] == 32
        )

    def test_guided_steps_render_the_asked_value(self, box):
        result = gm.describe(box.info, LOCAL, setting_gb=96)
        assert any(
            f"pages_limit={96 * 10**9 // 4096}" in s for s in result["guided_steps"]
        )

    def test_every_gb_is_decimal_and_pages_are_4kib(self, box):
        assert gm.pages_for(96) == 23_437_500  # 96e9 / 4096
        assert gm.gb_for(gm.pages_for(96)) == pytest.approx(96, abs=1e-3)
        steps = gm.describe(box.info, LOCAL, setting_gb=96)["guided_steps"]
        assert any("pages_limit=23437500" in s for s in steps)
        assert gm.describe(box.info, LOCAL, setting_gb=96)["guided_value"] == (
            "23437500"
        )

    def test_guided_steps_never_render_a_starving_value(self, box):
        with pytest.raises(gm.OutOfRange):
            gm.describe(box.info, LOCAL, setting_gb=124)

    def test_a_16gb_pc_refuses_any_value_with_a_clear_reason(self, box):
        box.info = linux_igpu(16, 7.75)
        with pytest.raises(gm.OutOfRange, match="can't give the GPU more"):
            gm.apply(8, confirm=True)

    def test_total_ram_from_meminfo_when_lemonade_omits_it(self, box):
        info = linux_igpu(128, 62.5)
        del info["Physical Memory"]
        (box.proc / "meminfo").write_text(
            "MemTotal:       131596288 kB\nMemFree: 1 kB\n", encoding="ascii"
        )
        result = gm.describe(info, LOCAL)
        # 131596288 KiB is 134.75 decimal GB, plus the 0.5 GB carve-out.
        assert result["total_gb"] == pytest.approx(135.3, abs=0.05)

    def test_no_total_ram_anywhere_is_loud(self, box):
        info = linux_igpu(128, 62.5)
        del info["Physical Memory"]
        (box.proc / "meminfo").unlink()
        with pytest.raises(gm.GpuMemoryError, match="meminfo"):
            gm.describe(info, LOCAL)


class TestLinuxGuidedWhenGaiaCantApply:
    def test_no_pkexec(self, box):
        box.tools.discard("pkexec")
        result = gm.describe(box.info, LOCAL)
        assert result["method"] == gm.METHOD_GUIDED
        assert "pkexec" in result["method_reason"]
        assert any(
            "sudo tee /etc/modprobe.d/ttm.conf" in s for s in result["guided_steps"]
        )

    def test_malformed_cmdline(self, box):
        box.cmdline('root=/dev/sda quiet "unterminated')
        result = gm.describe(box.info, LOCAL)
        assert result["method"] == gm.METHOD_GUIDED
        assert "kernel command line" in result["method_reason"]

    @pytest.mark.parametrize(
        "token", ["ttm.pages_limit=100", "amdttm.pages_limit=5", "amdgpu.gttsize=8192"]
    )
    def test_cmdline_override(self, box, token):
        box.cmdline(f"ro quiet {token}")
        result = gm.describe(box.info, LOCAL)
        assert result["method"] == gm.METHOD_GUIDED
        assert token in result["method_reason"]
        assert any("grubby" in s for s in result["guided_steps"])

    def test_cmdline_with_similar_but_harmless_keys_still_applies(self, box):
        box.cmdline(
            "ro amdgpu.ttm_pages_limit=1 xttm.pages_limit=2 ttm.page_pool_size=9"
        )
        assert gm.describe(box.info, LOCAL)["method"] == gm.METHOD_APPLY

    def test_a_modprobe_gttsize_is_a_conflict_too(self, box):
        (box.modprobe_d / "amdgpu.conf").write_text(
            "options amdgpu gttsize=8192\n", encoding="utf-8"
        )
        result = gm.describe(box.info, LOCAL)
        assert result["method"] == gm.METHOD_GUIDED
        assert "amdgpu.conf" in result["method_reason"]

    def test_another_modprobe_file_sets_it(self, box):
        (box.modprobe_d / "amdgpu-llm.conf").write_text(
            "options ttm pages_limit=1234\n", encoding="utf-8"
        )
        result = gm.describe(box.info, LOCAL)
        assert result["method"] == gm.METHOD_GUIDED
        assert "amdgpu-llm.conf" in result["method_reason"]

    def test_a_commented_out_line_is_not_a_conflict(self, box):
        (box.modprobe_d / "old.conf").write_text(
            "# options ttm pages_limit=1234\n", encoding="utf-8"
        )
        assert gm.describe(box.info, LOCAL)["method"] == gm.METHOD_APPLY

    def test_an_existing_conf_gaia_cant_restore_exactly(self, box):
        box.conf.write_text(
            "options ttm pages_limit=1000\ninstall ttm /bin/true\n", encoding="utf-8"
        )
        result = gm.describe(box.info, LOCAL)
        assert result["method"] == gm.METHOD_GUIDED
        assert "doesn't manage" in result["method_reason"]

    def test_builtin_ttm(self, box):
        box.set_module("ttm", pages=LIVE_PAGES, builtin=True)
        result = gm.describe(box.info, LOCAL)
        assert result["method"] == gm.METHOD_GUIDED
        assert "built into" in result["method_reason"]

    def test_ostree(self, box):
        box.ostree.write_text("", encoding="ascii")
        result = gm.describe(box.info, LOCAL)
        assert result["method"] == gm.METHOD_GUIDED
        assert any("rpm-ostree" in s for s in result["guided_steps"])

    def test_unreadable_limit(self, box):
        (box.sys_module / "ttm" / "parameters" / "pages_limit").write_text("garbage")
        assert gm.describe(box.info, LOCAL)["method"] == gm.METHOD_GUIDED

    def test_dkms_driver_uses_amdttm(self, box):
        box.set_module("amdttm", pages=LIVE_PAGES)
        gm.apply(96, confirm=True)
        assert "options amdttm pages_limit=" in box.conf.read_text(encoding="utf-8")


# --- Applying on Linux ----------------------------------------------------------------


class TestLinuxApply:
    def test_apply_writes_the_documented_setting_and_waits_for_a_restart(self, box):
        result = gm.apply(96, confirm=True)
        assert len(box.calls) == 1
        conf = box.conf.read_text(encoding="utf-8")
        assert f"options ttm pages_limit={96 * 10**9 // 4096}" in conf
        assert result["pending"]["state"] == gm.PENDING_RESTART
        assert result["pending"]["setting_gb"] == 96
        assert result["pending"]["previous_setting_gb"] == 67.1
        assert result["revert"] == {"available": True, "previous_setting_gb": 67.1}

    def test_values_travel_as_arguments_not_inside_the_script(self, box):
        gm.apply(96, confirm=True)
        argv = box.calls[0]
        assert argv[3] == gm._ELEVATED_SCRIPT
        assert argv[4] == "gaia-gpu-memory"
        assert argv[7] == "update-initramfs"

    def test_fedora_rebuilds_with_dracut(self, box):
        box.tools = {"pkexec", "dracut"}
        gm.apply(96, confirm=True)
        assert box.calls[0][7] == "dracut"

    def test_no_password_prompt_is_not_called_a_refusal(self, box):
        box.returncode = 127
        with pytest.raises(gm.PermissionNotGiven, match="no password prompt"):
            gm.apply(96, confirm=True)
        assert not box.conf.exists()

    def test_needs_explicit_confirmation(self, box):
        with pytest.raises(gm.NotConfirmed) as err:
            gm.apply(96, confirm=False)
        assert err.value.status == 400
        assert box.calls == [] and not box.conf.exists()

    @pytest.mark.parametrize("value", [121, 137, 1000, float("inf"), float("nan")])
    def test_refuses_a_value_that_would_starve_the_os(self, box, value):
        with pytest.raises(gm.OutOfRange) as err:
            gm.apply(value, confirm=True)
        assert err.value.status == 422
        assert box.calls == [] and not box.conf.exists()

    def test_refuses_a_value_below_the_floor(self, box):
        with pytest.raises(gm.OutOfRange):
            gm.apply(1, confirm=True)

    def test_permission_not_given_changes_nothing(self, box):
        box.returncode = 126
        with pytest.raises(gm.PermissionNotGiven) as err:
            gm.apply(96, confirm=True)
        assert err.value.status == 403
        assert not box.conf.exists()
        assert not gm._state_path().exists()

    def test_guided_machine_refuses_apply(self, box):
        box.tools.discard("pkexec")
        with pytest.raises(gm.NotPossible) as err:
            gm.apply(96, confirm=True)
        assert err.value.status == 409 and box.calls == []

    def test_already_set_and_live(self, box):
        with pytest.raises(gm.NotPossible, match="already"):
            gm.apply(67.1, confirm=True)

    def test_already_written_but_not_restarted(self, box):
        gm.apply(96, confirm=True)
        with pytest.raises(gm.NotPossible, match="Restart"):
            gm.apply(96, confirm=True)
        assert len(box.calls) == 1

    def test_a_failed_initramfs_rebuild_keeps_undo_possible(self, box):
        box.fail_after_write = True
        with pytest.raises(gm.GpuMemoryError) as err:
            gm.apply(96, confirm=True)
        assert err.value.status == 500
        assert "update-initramfs" in str(err.value)
        box.fail_after_write = False
        assert gm.describe(box.info, LOCAL)["revert"]["available"] is True

    def test_never_restarts_the_pc(self, box):
        gm.apply(96, confirm=True)
        script = gm._ELEVATED_SCRIPT
        for word in ("reboot", "shutdown", "systemctl", "init 6"):
            assert word not in script


class TestAfterRestart:
    def test_took_effect(self, box):
        gm.apply(96, confirm=True)
        box.reboot(pages=gm.pages_for(96))
        result = gm.describe(box.info, LOCAL)
        assert result["pending"]["state"] == gm.PENDING_TOOK_EFFECT
        assert result["setting"]["current_gb"] == 96

    def test_did_not_take_effect(self, box):
        gm.apply(96, confirm=True)
        box.reboot()  # the kernel still reads the old limit
        result = gm.describe(box.info, LOCAL)
        assert result["pending"]["state"] == gm.PENDING_NOT_APPLIED

    def test_keep_current_clears_the_notice_but_keeps_undo(self, box):
        gm.apply(96, confirm=True)
        box.reboot()
        result = gm.dismiss_pending()
        assert result["pending"] is None
        assert result["revert"]["available"] is True


class TestTheSplitAddsUp:
    """cpu_gb is memory the GPU can never use, so the two add up to the total."""

    @pytest.mark.parametrize("total,gtt", [(32, 15.5), (64, 31.5), (128, 62.5)])
    def test_linux(self, box, total, gtt):
        result = gm.describe(linux_igpu(total, gtt), LOCAL)
        self.check(result)

    @pytest.mark.parametrize(
        "physical,carve", [("32.00 GB", 10.0), ("64.00 GB", 16.0), ("128.00 GB", 0.5)]
    )
    def test_windows(self, box, windows, physical, carve):
        box.info = {**WINDOWS_IGPU, "Physical Memory": physical}
        windows["gb"] = carve
        self.check(gm.describe(box.info, LOCAL))

    @staticmethod
    def check(result):
        assert result["gpu_gb"] + result["cpu_gb"] == pytest.approx(
            result["total_gb"], abs=0.11
        )
        # Every offered target leaves the CPU at least the reserve.
        for p in result["presets"]:
            assert result["total_gb"] - p["gpu_gb"] >= result["reserve_gb"] - 0.05

    def test_shot_57_no_longer_offers_a_target_that_breaks_the_reserve(
        self, box, windows
    ):
        # 32 GiB Windows PC with a ~10 GiB carve-out: GPU 22 GB, CPU 12 GB.
        box.info = {**WINDOWS_IGPU, "Physical Memory": "32.00 GB"}
        windows["gb"] = 9.6
        result = gm.describe(box.info, LOCAL)
        assert result["cpu_gb"] < result["reserve_gb"]
        assert result["presets"] == [] and result["show_in_setup"] is False
        with pytest.raises(gm.OutOfRange):
            gm.record_pending(16)


class TestHidingTheStepForTheUsersSelection:
    """Front-ends hide the step unless a ticked model benefits; the API must say which."""

    def test_unlocks_and_fit_points_agree(self, box):
        box.info = linux_igpu(64, 16)
        result = gm.describe(box.info, LOCAL)
        for m in result["models"]:
            in_some_preset = any(m["id"] in p["unlocks"] for p in result["presets"])
            benefits = not m["fits_now"] and m["fits_at_setting_gb"] is not None
            assert in_some_preset == benefits, m

    def test_a_selection_without_a_beneficiary_hides_the_step(self, box):
        box.info = linux_igpu(64, 16)
        result = gm.describe(box.info, LOCAL)
        by_id = {m["id"]: m for m in result["models"]}
        ticked = [lc.DEFAULT_MODEL_NAME]  # already fits: nothing to gain
        assert not any(
            not by_id[i]["fits_now"] and by_id[i]["fits_at_setting_gb"] is not None
            for i in ticked
        )
        assert by_id[QWEN]["fits_at_setting_gb"] is not None


class TestEveryNumberIsTheSameUnit:
    @pytest.mark.parametrize(
        "total,gtt", [(16, 7.75), (32, 15.5), (48, 23.75), (64, 31.5), (128, 62.5)]
    )
    def test_fits_now_agrees_with_the_numbers_shown(self, box, total, gtt):
        result = gm.describe(linux_igpu(total, gtt), LOCAL)
        for m in result["models"]:
            if abs(m["need_gb"] - result["gpu_gb"]) < 0.1:
                continue  # one-decimal rounding can't decide a tie
            assert m["fits_now"] == (m["need_gb"] <= result["gpu_gb"]), m

    def test_need_is_decimal_gb(self, box):
        mr = lc.find_model_requirement(QWEN)
        need = model(gm.describe(box.info, LOCAL), QWEN)["need_gb"]
        floor = model_fit.required_memory_gb(mr.size_gb) * GB_PER_GIB
        assert need >= round(floor, 1)


class TestLinuxCapUsesVisibleRam:
    def test_memtotal_below_installed_ram_caps_the_limit(self, box):
        box.meminfo(100)  # the OS sees 107.4 GB of the 137.4 GB installed
        result = gm.describe(box.info, LOCAL)
        assert result["setting"]["max_gb"] == 91  # 107.4 - 16
        with pytest.raises(gm.OutOfRange, match="16 GB"):
            gm.apply(100, confirm=True)
        assert box.calls == []

    def test_installed_ram_still_caps_when_lower(self, box):
        box.meminfo(500)
        assert gm.describe(box.info, LOCAL)["setting"]["max_gb"] == 120

    def test_unreadable_meminfo_is_loud(self, box):
        (box.proc / "meminfo").write_text("garbage\n", encoding="ascii")
        with pytest.raises(gm.GpuMemoryError, match="MemTotal"):
            gm.describe(box.info, LOCAL)


class TestUnitsRoundTrip:
    """Lemonade reports GiB; the API's decimal GB must survive apply and restart."""

    def test_applying_the_fit_point_makes_the_model_fit(self, box):
        box.info = linux_igpu(48, 23.75)
        box.set_module("ttm", pages=int(23.75 * 2**30 / 4096))
        before = model(gm.describe(box.info, LOCAL), QWEN)
        assert before["fits_now"] is False and before["fits_at_setting_gb"]
        at = before["fits_at_setting_gb"]

        gm.apply(at, confirm=True)
        box.reboot(pages=gm.pages_for(at))

        after = gm.describe(box.info, LOCAL)
        assert model(after, QWEN)["fits_now"] is True
        assert after["pending"]["state"] == gm.PENDING_TOOK_EFFECT
        assert after["setting"]["current_gb"] == pytest.approx(at, abs=0.05)

    def test_a_guided_linux_change_is_recognised(self, box):
        box.ostree.write_text("", encoding="ascii")
        gm.record_pending(96)
        box.reboot(pages=gm.pages_for(96))
        result = gm.describe(box.info, LOCAL)
        assert result["setting"]["current_gb"] == 96
        assert result["pending"]["state"] == gm.PENDING_TOOK_EFFECT

    def test_results_carry_steps_for_the_value_just_recorded(self, box):
        steps = gm.apply(80, confirm=True)["guided_steps"]
        assert any(f"pages_limit={gm.pages_for(80)}" in s for s in steps)
        steps = gm.revert(confirm=True)["guided_steps"]
        assert any(f"pages_limit={gm.pages_for(67.1)}" in s for s in steps)

    def test_guided_record_carries_its_steps(self, box):
        box.ostree.write_text("", encoding="ascii")
        steps = gm.record_pending(96)["guided_steps"]
        assert any(f"pages_limit={gm.pages_for(96)}" in s for s in steps)


class TestRevert:
    def test_with_no_record_says_how_to_reset_by_hand(self, box):
        with pytest.raises(gm.NotPossible) as err:
            gm.revert(confirm=True)
        assert err.value.status == 409
        assert "amd-ttm --clear" in str(err.value)
        assert box.calls == []

    def test_needs_confirmation(self, box):
        gm.apply(96, confirm=True)
        with pytest.raises(gm.NotConfirmed):
            gm.revert(confirm=False)

    def test_removes_the_file_when_there_was_none(self, box):
        gm.apply(96, confirm=True)
        result = gm.revert(confirm=True)
        assert not box.conf.exists()
        assert result["pending"]["source"] == "revert"
        assert result["pending"]["setting_gb"] == 67.1
        assert result["revert"]["available"] is False

    def test_restores_a_file_gaia_replaced(self, box):
        mine = "# user's own\noptions ttm pages_limit=1000\n"
        box.conf.write_text(mine, encoding="utf-8")
        gm.apply(96, confirm=True)
        gm.revert(confirm=True)
        assert box.conf.read_text(encoding="utf-8") == mine

    @pytest.mark.parametrize(
        "tampered",
        [
            "install ttm /bin/sh -c 'id > /tmp/pwned'",
            "options ttm pages_limit=1\ninstall ttm /tmp/x",
            "# looks like a comment\x0binstall ttm /tmp/x",
            "# carriage\rinstall ttm /tmp/x",
            "options ttm pages_limit=1; rm -rf /",
            "softdep ttm pre: evil",
            "# null\x00byte",
        ],
    )
    def test_undo_refuses_a_tampered_record(self, box, tampered):
        """The record lives in the user's home; undo must not write it as root."""
        gm.apply(96, confirm=True)
        path = gm._state_path()
        state = json.loads(path.read_text(encoding="utf-8"))
        state["linux"]["previous_conf"] = tampered
        path.write_text(json.dumps(state), encoding="utf-8")
        calls = len(box.calls)
        with pytest.raises(gm.NotPossible, match="won't write"):
            gm.revert(confirm=True)
        assert len(box.calls) == calls

    def test_undo_refuses_a_record_that_would_starve_the_os(self, box):
        gm.apply(96, confirm=True)
        path = gm._state_path()
        state = json.loads(path.read_text(encoding="utf-8"))
        state["linux"]["previous_conf"] = "options ttm pages_limit=33554432"
        path.write_text(json.dumps(state), encoding="utf-8")
        calls = len(box.calls)
        with pytest.raises(gm.NotPossible, match="less than 16 GB"):
            gm.revert(confirm=True)
        assert len(box.calls) == calls

    def test_two_applies_still_undo_to_the_original(self, box):
        gm.apply(80, confirm=True)
        gm.apply(96, confirm=True)
        assert gm.describe(box.info, LOCAL)["revert"]["previous_setting_gb"] == 67.1
        gm.revert(confirm=True)
        assert not box.conf.exists()

    def test_revert_took_effect_after_restart(self, box):
        gm.apply(96, confirm=True)
        box.reboot(pages=gm.pages_for(96))
        gm.revert(confirm=True)
        box.reboot(pages=LIVE_PAGES)
        assert (
            gm.describe(box.info, LOCAL)["pending"]["state"] == gm.PENDING_TOOK_EFFECT
        )

    def test_a_corrupt_record_is_loud_not_ignored(self, box):
        path = gm._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(gm.GpuMemoryError, match="Delete it"):
            gm.revert(confirm=True)

    def test_a_record_of_the_wrong_shape_is_loud(self, box):
        path = gm._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(gm.GpuMemoryError, match="isn't a GPU memory record"):
            gm.describe(box.info, LOCAL)


# --- Windows ---------------------------------------------------------------------------


@pytest.fixture
def windows(box, monkeypatch):
    box.os = "windows"
    box.info = dict(WINDOWS_IGPU)
    carve = {"gb": 0.5}
    monkeypatch.setattr(
        model_fit, "_windows_adapter_memory_gb", lambda name: carve["gb"]
    )
    return carve


class TestWindows:
    def test_guided_with_amd_software_steps(self, box, windows):
        result = gm.describe(box.info, LOCAL)
        assert result["method"] == gm.METHOD_GUIDED
        assert result["setting"]["name"] == "Variable Graphics Memory"
        assert result["setting"]["current_gb"] == 0.5
        # WDDM shares half the RAM Windows sees: model_fit's pool.
        # 0.5 GiB carve-out plus half of the rest of 128 GiB, in decimal GB.
        assert result["gpu_gb"] == pytest.approx(69.0, abs=0.1)
        assert result["setting"]["max_gb"] == 103  # AMD's 75% cap of 137.4 GB
        steps = " ".join(result["guided_steps"])
        assert "Performance > Tuning" in steps and "BIOS" in steps

    def test_one_value_in_amd_softwares_own_units(self, box, windows):
        result = gm.describe(box.info, LOCAL, setting_gb=64)
        steps = result["guided_steps"]
        # 64 GB is 59.6 GiB, which AMD Software rounds up and shows as "60 GB".
        assert result["guided_value"] == "60"
        assert sum(" 60 GB" in s for s in steps) == 1
        assert not any("GiB" in s for s in steps)
        # The GPU total GAIA shows includes Windows' shared memory.
        assert any("shares with the GPU" in s for s in steps)

    def test_the_explanation_names_the_gpu_total_for_that_setting(self, box, windows):
        result = gm.describe(box.info, LOCAL, setting_gb=64)
        total = round(64 + (result["total_gb"] - 64) / 2)
        assert any(f"{total} GB GPU total" in s for s in result["guided_steps"])

    def test_no_gib_anywhere_in_the_response(self, box, windows):
        assert "GiB" not in json.dumps(gm.describe(box.info, LOCAL))

    def test_without_shared_memory_the_reason_is_the_units(self, box, windows):
        # Lemonade reporting the carve-out itself and no shared memory.
        gpu = {**WINDOWS_IGPU["devices"]["amd_gpu"][0], "vram_gb": 8.0}
        gpu["virtual_mem_gb"] = 0.0
        box.info = {**WINDOWS_IGPU, "devices": {"amd_gpu": [gpu]}}
        steps = gm.describe(box.info, LOCAL, setting_gb=64)["guided_steps"]
        assert not any("shares with the GPU" in s for s in steps)
        assert any("a little larger than GAIA's" in s for s in steps)

    def test_projection_matches_model_fit(self, box, windows):
        result = gm.describe(box.info, LOCAL)
        preset = result["presets"][-1]
        windows["gb"] = preset["setting_gb"] / GB_PER_GIB  # the driver's GiB
        cap = model_fit.capacity_from_system_info(box.info)
        assert preset["gpu_gb"] == pytest.approx(cap.memory_gb * GB_PER_GIB, abs=0.1)

    def test_flash_unlocks_at_a_bigger_carve_out(self, box, windows):
        flash = model(gm.describe(box.info, LOCAL), FLASH)
        assert flash["fits_now"] is False
        assert 0.5 < flash["fits_at_setting_gb"] <= 96

    def test_apply_is_never_offered(self, box, windows):
        with pytest.raises(gm.NotPossible):
            gm.apply(64, confirm=True)
        assert box.calls == []

    def test_a_hand_made_change_is_judged_by_a_fresh_reading(self, box, windows):
        result = gm.record_pending(64)
        assert result["pending"]["state"] == gm.PENDING_RESTART
        assert result["pending"]["source"] == "guided"
        box.reboot()
        assert (
            gm.describe(box.info, LOCAL)["pending"]["state"] == gm.PENDING_NOT_APPLIED
        )
        windows["gb"] = 60.0  # the GiB the steps told the user to pick
        assert (
            gm.describe(box.info, LOCAL)["pending"]["state"] == gm.PENDING_TOOK_EFFECT
        )

    @pytest.mark.parametrize("target", [29, 43, 58, 72, 87, 101])
    def test_following_the_steps_exactly_counts(self, box, windows, target):
        steps = gm.record_pending(target)["guided_steps"]
        gib = math.ceil(target * 1e9 / 2**30)
        assert any(f"set it to {gib} GB, as AMD Software shows it" in s for s in steps)
        box.reboot()
        windows["gb"] = float(gib)  # what the driver reads after the restart
        assert (
            gm.describe(box.info, LOCAL)["pending"]["state"] == gm.PENDING_TOOK_EFFECT
        )

    def test_a_raise_short_of_the_target_is_not_applied(self, box, windows):
        gm.record_pending(64)
        box.reboot()
        windows["gb"] = 48.0
        assert (
            gm.describe(box.info, LOCAL)["pending"]["state"] == gm.PENDING_NOT_APPLIED
        )

    def test_a_cut_counts_at_or_below_the_rounded_target(self, box, windows):
        windows["gb"] = 96.0
        gm.record_pending(43)
        box.reboot()
        windows["gb"] = 40.0  # 43 GB rounds up to 41 GiB; 40 GiB is below it
        assert (
            gm.describe(box.info, LOCAL)["pending"]["state"] == gm.PENDING_TOOK_EFFECT
        )
        windows["gb"] = 64.0
        assert (
            gm.describe(box.info, LOCAL)["pending"]["state"] == gm.PENDING_NOT_APPLIED
        )

    def test_guided_record_has_no_undo(self, box, windows):
        gm.record_pending(64)
        assert gm.describe(box.info, LOCAL)["revert"]["available"] is False
        with pytest.raises(gm.NotPossible):
            gm.revert(confirm=True)

    def test_keep_current_forgets_a_guided_record(self, box, windows):
        gm.record_pending(64)
        assert gm.dismiss_pending()["pending"] is None
        assert not gm._state_path().exists()

    def test_pending_refuses_a_starving_value(self, box, windows):
        with pytest.raises(gm.OutOfRange):
            gm.record_pending(120)

    def test_32gb_windows_is_capped_by_the_reserve(self, box, windows):
        box.info = {**WINDOWS_IGPU, "Physical Memory": "32.00 GB"}
        windows["gb"] = 4.0
        result = gm.describe(box.info, LOCAL)
        # Windows shares half of what the carve-out leaves, so keeping 16 GB the
        # GPU can never use caps the setting at 34.4 - 32 = 2 GB: nothing to offer.
        assert result["setting"]["max_gb"] == 2
        assert result["presets"] == []
        assert result["show_in_setup"] is False


def test_pending_on_an_apply_machine_is_refused(box):
    with pytest.raises(gm.NotPossible, match="apply"):
        gm.record_pending(96)


# --- The model_fit split it reuses ---------------------------------------------------


class TestIntegratedGpuMemory:
    def test_linux_split_is_lemonades(self):
        split = model_fit.integrated_gpu_memory(linux_igpu(128, 62.5))
        assert (split.carve_out_gb, split.shared_gb, split.reported_by) == (
            0.5,
            62.5,
            "lemonade",
        )

    def test_windows_split_comes_from_the_driver(self, monkeypatch):
        monkeypatch.setattr(model_fit, "_windows_adapter_memory_gb", lambda n: 96.0)
        split = model_fit.integrated_gpu_memory(WINDOWS_IGPU)
        assert (split.carve_out_gb, split.shared_gb) == (96.0, 16.0)

    @pytest.mark.parametrize("info", [DGPU, CPU_ONLY, MAC])
    def test_none_without_an_integrated_amd_gpu(self, info):
        assert model_fit.integrated_gpu_memory(info) is None


# --- Routes ------------------------------------------------------------------------------


UI = {"X-Gaia-UI": "1"}


@pytest.fixture
def ui(box):
    from gaia.ui.routers import gpu_memory as router

    app = FastAPI()
    app.include_router(router.router)
    return TestClient(app, client=("127.0.0.1", 50000))


class TestUiRoutes:
    def test_read_needs_the_ui_header(self, ui):
        assert ui.get("/api/gpu-memory", headers={"X-Gaia-UI": ""}).status_code == 403
        assert ui.get("/api/gpu-memory", headers=UI).json()["applies"] is True

    def test_apply_maps_errors_to_status_codes(self, ui, box):
        r = ui.post("/api/gpu-memory/apply", json={"setting_gb": 96}, headers=UI)
        assert r.status_code == 400
        r = ui.post(
            "/api/gpu-memory/apply",
            json={"setting_gb": 200, "confirm": True},
            headers=UI,
        )
        assert r.status_code == 422 and "16 GB" in r.json()["detail"]
        box.returncode = 126
        r = ui.post(
            "/api/gpu-memory/apply",
            json={"setting_gb": 96, "confirm": True},
            headers=UI,
        )
        assert r.status_code == 403

    def test_apply_then_revert(self, ui, box):
        r = ui.post(
            "/api/gpu-memory/apply",
            json={"setting_gb": 96, "confirm": True},
            headers=UI,
        )
        assert (
            r.status_code == 200 and r.json()["pending"]["state"] == "restart_required"
        )
        r = ui.post("/api/gpu-memory/revert", json={"confirm": True}, headers=UI)
        assert r.status_code == 200 and not box.conf.exists()

    def test_apply_is_refused_over_mobile_access(self, ui, box):
        r = ui.post(
            "/api/gpu-memory/apply",
            json={"setting_gb": 96, "confirm": True},
            headers={**UI, "X-Forwarded-For": "1.2.3.4"},
        )
        assert r.status_code == 403 and box.calls == []

    @pytest.mark.parametrize("route", ["apply", "revert"])
    def test_root_steps_are_refused_from_another_computer(self, box, route):
        from gaia.ui.routers import gpu_memory as router

        app = FastAPI()
        app.include_router(router.router)
        remote = TestClient(app, client=("192.168.1.20", 50000))
        r = remote.post(
            f"/api/gpu-memory/{route}",
            json={"setting_gb": 96, "confirm": True},
            headers=UI,
        )
        assert r.status_code == 403 and box.calls == []

    def test_pending_routes(self, ui, box):
        r = ui.post("/api/gpu-memory/pending", json={"setting_gb": 96}, headers=UI)
        assert r.status_code == 409
        assert ui.delete("/api/gpu-memory/pending", headers=UI).status_code == 200

    def test_lemonade_down_is_503(self, ui, box, monkeypatch):
        def down():
            raise gm.Unavailable("the model server didn't answer")

        monkeypatch.setattr(gm, "_read_system_info", down)
        assert ui.get("/api/gpu-memory", headers=UI).status_code == 503


class TestDaemonRoutes:
    TOKEN = "daemon-client-token"

    @pytest.fixture
    def daemon(self, box):
        from gaia.daemon.gpu_memory_routes import build_gpu_memory_router

        app = FastAPI()
        app.include_router(build_gpu_memory_router(self.TOKEN))
        return TestClient(app)

    def test_needs_the_daemon_token(self, daemon):
        assert daemon.get("/daemon/v1/gpu-memory").status_code == 401

    def test_same_answer_as_the_ui(self, daemon, ui, box):
        auth = {"Authorization": f"Bearer {self.TOKEN}"}
        assert (
            daemon.get("/daemon/v1/gpu-memory", headers=auth).json()
            == ui.get("/api/gpu-memory", headers=UI).json()
        )
        r = daemon.post(
            "/daemon/v1/gpu-memory/apply",
            json={"setting_gb": 96, "confirm": True},
            headers=auth,
        )
        assert r.status_code == 200 and box.conf.exists()
