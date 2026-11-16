# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Linux NPU setup checks: what stops FastFlowLM before setup offers the NPU (#6632)."""

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.llm import npu_linux
from gaia.llm.npu_linux import NpuProblem

pytestmark = pytest.mark.npu_host_probe

MB = 1024 * 1024


def linux_system_info(flm_state="installable"):
    """``/system-info`` as Lemonade 2026.44 reports a Ryzen AI 7 350 on Fedora."""
    return {
        "OS Version": "Fedora Linux 46 (Workstation Edition Prerelease)",
        "Processor": "AMD Ryzen AI 7 350 w/ Radeon 860M",
        "Physical Memory": "31.06 GB",
        "devices": {
            "cpu": {"name": "AMD Ryzen AI 7 350", "available": True, "cores": 8},
            "amd_gpu": [
                {
                    "name": "AMD Radeon 860M",
                    "available": True,
                    "integrated": True,
                    "vram_gb": 0.5,
                    "virtual_mem_gb": 15.5,
                }
            ],
            "amd_npu": {"name": "RyzenAI-npu4", "available": True, "family": "XDNA2"},
        },
        "recipes": {
            "flm": {
                "default_backend": "npu",
                "backends": {"npu": {"state": flm_state, "version": "v1.0.7"}},
            },
            "llamacpp": {
                "default_backend": "vulkan",
                "backends": {"vulkan": {"state": "installed"}},
            },
        },
    }


@pytest.fixture(autouse=True)
def _no_fix_installed(monkeypatch):
    monkeypatch.setattr(npu_linux, "memlock_fix_installed", lambda root=None: False)


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(npu_linux, "_on_linux", lambda: True)


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    """A Linux root with a working XDNA2 NPU; tests take pieces away."""
    (tmp_path / "sys/module/amdxdna").mkdir(parents=True)
    device = tmp_path / "sys/class/accel/accel0/device"
    device.mkdir(parents=True)
    (device / "fw_version").write_text("1.1.2.64\n")
    (tmp_path / "dev/accel").mkdir(parents=True)
    (tmp_path / "dev/accel/accel0").write_text("")
    drivers = {device: "amdxdna"}
    monkeypatch.setattr(npu_linux, "_driver_of", lambda d: drivers.get(d))
    monkeypatch.setattr(npu_linux, "_can_open", lambda node: node.exists())
    return tmp_path


def codes(found):
    return [p.code for p in found]


class TestWhichLemonadeTheChecksSpeakFor:
    def test_a_linux_lemonade_reporting_an_npu(self, linux):
        assert npu_linux.applies_to(linux_system_info())

    def test_not_from_windows_or_mac(self, monkeypatch):
        monkeypatch.setattr(npu_linux, "_on_linux", lambda: False)
        assert not npu_linux.applies_to(linux_system_info())

    def test_not_for_a_windows_lemonade_reached_from_linux(self, linux):
        info = linux_system_info()
        info["OS Version"] = "Windows 11 Pro 6.3 (Build 26300)"
        assert not npu_linux.applies_to(info)

    def test_not_without_an_npu(self, linux):
        info = linux_system_info()
        info["devices"]["amd_npu"]["available"] = False
        assert not npu_linux.applies_to(info)
        assert npu_linux.check(info) == []


class TestHostProblems:
    def test_a_set_up_pc_has_none(self, sysfs):
        assert npu_linux.host_problems(sysfs, memlock_hard=None) == []

    def test_driver_absent_stops_every_other_check(self, sysfs):
        (sysfs / "sys/module/amdxdna").rmdir()
        found = npu_linux.host_problems(sysfs, memlock_hard=8 * MB)
        assert codes(found) == ["driver"]
        assert "amdxdna" in found[0].title
        assert "6.14" in found[0].fix

    def test_driver_loaded_but_no_npu_bound(self, sysfs, monkeypatch):
        monkeypatch.setattr(npu_linux, "_driver_of", lambda d: None)
        assert codes(npu_linux.host_problems(sysfs, memlock_hard=None)) == ["device"]

    def test_old_firmware(self, sysfs):
        (sysfs / "sys/class/accel/accel0/device/fw_version").write_text("1.0.0.63\n")
        found = npu_linux.host_problems(sysfs, memlock_hard=None)
        assert codes(found) == ["firmware"]
        assert dict(found[0].params) == {"version": "1.0.0.63"}

    def test_device_node_missing(self, sysfs):
        (sysfs / "dev/accel/accel0").unlink()
        assert codes(npu_linux.host_problems(sysfs, memlock_hard=None)) == ["device"]

    def test_device_not_accessible_names_its_group(self, sysfs, monkeypatch):
        monkeypatch.setattr(npu_linux, "_can_open", lambda node: False)
        monkeypatch.setattr(npu_linux, "_group_name", lambda gid: "render")
        found = npu_linux.host_problems(sysfs, memlock_hard=None)
        assert codes(found) == ["access"]
        params = dict(found[0].params)
        assert params["group"] == "render"
        assert params["device"].endswith("dev/accel/accel0")
        assert "render group" in found[0].fix

    def test_device_owned_by_root_only_asks_for_access(self, sysfs, monkeypatch):
        monkeypatch.setattr(npu_linux, "_can_open", lambda node: False)
        monkeypatch.setattr(npu_linux, "_group_name", lambda gid: None)
        found = npu_linux.host_problems(sysfs, memlock_hard=None)
        assert "group" not in dict(found[0].params)
        assert "read and write access" in found[0].fix

    def test_memlock_too_low(self, sysfs):
        found = npu_linux.host_problems(sysfs, memlock_hard=8 * MB)
        assert codes(found) == ["memlock"]
        assert dict(found[0].params) == {"limit": "8 MB", "fix": "apply"}
        assert "administrator password" in found[0].fix

    def test_memlock_too_small_to_pin_the_npu_model(self, sysfs):
        found = npu_linux.host_problems(sysfs, memlock_hard=1024 * MB)
        assert codes(found) == ["memlock"]
        assert dict(found[0].params) == {"limit": "1.0 GB", "fix": "apply"}

    def test_memlock_large_enough_or_unlimited(self, sysfs):
        assert npu_linux.host_problems(sysfs, memlock_hard=64 * 1024 * MB) == []
        assert npu_linux.host_problems(sysfs, memlock_hard=None) == []

    def test_several_problems_are_all_reported(self, sysfs, monkeypatch):
        (sysfs / "sys/class/accel/accel0/device/fw_version").write_text("1.0.0.63")
        monkeypatch.setattr(npu_linux, "_can_open", lambda node: False)
        monkeypatch.setattr(npu_linux, "_group_name", lambda gid: "render")
        found = npu_linux.host_problems(sysfs, memlock_hard=8 * MB)
        assert codes(found) == ["firmware", "access", "memlock"]


def validate_json(**overrides):
    """``flm validate --json`` from FLM v1.0.7 on a set-up Strix Point."""
    report = {
        "platform": "linux",
        "npu_platform": "aie2p",
        "kernel": "7.4.0-0.rc1.fc46.x86_64",
        "devices": [
            {
                "path": "/dev/accel/accel0",
                "fw_major": 1,
                "fw_minor": 1,
                "fw_patch": 2,
                "fw_build": 64,
                "fw_ok": True,
                "cols": 8,
                "busy": False,
            }
        ],
        "drm_version": "0.7",
        "amd_device_found": True,
        "all_fw_ok": True,
        "enough_cols": True,
        "kernel_ok": True,
        "memlock_limit": -1,
        "memlock_ok": True,
        "ready": True,
    }
    report.update(overrides)
    return report


class TestFastFlowLMsOwnCheck:
    def run(self, monkeypatch, stdout="", stderr="", returncode=0):
        run = MagicMock(
            return_value=subprocess.CompletedProcess([], returncode, stdout, stderr)
        )
        monkeypatch.setattr(npu_linux.subprocess, "run", run)
        return run

    def test_ready(self, monkeypatch):
        run = self.run(monkeypatch, json.dumps(validate_json()))
        flm = Path("/opt/flm/flm")
        assert npu_linux.validate(flm) == []
        assert run.call_args.args[0] == [str(flm), "validate", "--json"]

    def test_flm_is_judged_at_the_hard_limit_not_our_soft_one(self, monkeypatch):
        order = []
        monkeypatch.setattr(
            npu_linux, "raise_memlock_soft_limit", lambda: order.append("raise")
        )
        monkeypatch.setattr(
            npu_linux.subprocess,
            "run",
            lambda *a, **k: order.append("flm")
            or subprocess.CompletedProcess([], 0, json.dumps(validate_json()), ""),
        )
        assert npu_linux.validate(Path("/opt/flm/flm")) == []
        assert order == ["raise", "flm"]

    def test_memlock_too_low_as_flm_1_0_7_reports_it(self, monkeypatch):
        # Captured from the Linux Strix Halo runner (#6632): bytes, as a string.
        out = "[Linux]  Kernel: 7.0.0-27-generic\n" + json.dumps(
            validate_json(ready=False, memlock_ok=False, memlock_limit="8388608")
        )
        self.run(monkeypatch, out, returncode=1)
        found = npu_linux.validate(Path("flm"))
        assert codes(found) == ["memlock"]
        assert dict(found[0].params) == {"limit": "8 MB", "fix": "apply"}

    def test_old_firmware_names_the_version(self, monkeypatch):
        device = dict(
            validate_json()["devices"][0],
            fw_minor=0,
            fw_patch=0,
            fw_build=63,
            fw_ok=False,
        )
        self.run(
            monkeypatch,
            json.dumps(validate_json(ready=False, all_fw_ok=False, devices=[device])),
        )
        found = npu_linux.validate(Path("flm"))
        assert codes(found) == ["firmware"]
        assert dict(found[0].params) == {"version": "1.0.0.63"}

    def test_first_generation_npu(self, monkeypatch):
        self.run(monkeypatch, json.dumps(validate_json(ready=False, enough_cols=False)))
        found = npu_linux.validate(Path("flm"))
        assert codes(found) == ["runtime"]
        assert "8 columns" in found[0].title

    def test_no_device_found(self, monkeypatch):
        self.run(
            monkeypatch, json.dumps(validate_json(ready=False, amd_device_found=False))
        )
        assert codes(npu_linux.validate(Path("flm"))) == ["device"]

    def test_binary_that_cannot_start(self, monkeypatch):
        self.run(
            monkeypatch,
            stderr="flm-real: error while loading shared libraries: libreadline.so.8: "
            "cannot open shared object file",
            returncode=127,
        )
        found = npu_linux.validate(Path("flm"))
        assert codes(found) == ["runtime"]
        assert "libreadline.so.8" in dict(found[0].params)["detail"]

    def test_an_flm_without_json_reports_does_not_block(self, monkeypatch):
        self.run(monkeypatch, "[Linux]  Memlock Limit: infinity\n", returncode=0)
        assert npu_linux.validate(Path("flm")) == []

    def test_a_brace_in_a_log_line_is_skipped(self, monkeypatch):
        self.run(monkeypatch, "[Linux] {not json}\n" + json.dumps(validate_json()))
        assert npu_linux.validate(Path("flm")) == []

    def test_binary_that_hangs(self, monkeypatch):
        monkeypatch.setattr(
            npu_linux.subprocess,
            "run",
            MagicMock(side_effect=subprocess.TimeoutExpired(["flm"], 30)),
        )
        assert codes(npu_linux.validate(Path("flm"))) == ["runtime"]


class TestProblems:
    @pytest.fixture(autouse=True)
    def _gaias_own(self, monkeypatch):
        monkeypatch.setattr(npu_linux, "_lemonade_in_use", lambda: (True, True))

    def host(self, monkeypatch, found):
        calls = []

        def host_problems(**kwargs):
            calls.append(kwargs)
            return list(found)

        monkeypatch.setattr(npu_linux, "host_problems", host_problems)
        return calls

    def test_a_memlock_flm_validate_passes_still_cannot_pin_the_model(
        self, monkeypatch
    ):
        # flm validate only wants 100 MB; the model's 9 GB of weights need more.
        self.host(monkeypatch, [npu_linux._memlock_problem("1.0 GB")])
        monkeypatch.setattr(npu_linux, "embedded_flm", lambda: Path("flm"))
        monkeypatch.setattr(npu_linux, "validate", lambda flm: [])
        assert codes(npu_linux.problems()) == ["memlock"]

    def test_flm_validate_replaces_a_host_finding_it_also_made(self, monkeypatch):
        self.host(monkeypatch, [npu_linux._memlock_problem("8 MB")])
        monkeypatch.setattr(npu_linux, "embedded_flm", lambda: Path("flm"))
        from_flm = npu_linux._memlock_problem("8 MB")
        monkeypatch.setattr(npu_linux, "validate", lambda flm: [from_flm])
        assert npu_linux.problems() == [from_flm]

    def test_before_the_backend_is_installed_the_host_checks_stand(self, monkeypatch):
        self.host(monkeypatch, [npu_linux._memlock_problem("8 MB")])
        monkeypatch.setattr(npu_linux, "embedded_flm", lambda: None)
        assert codes(npu_linux.problems()) == ["memlock"]

    def test_a_users_lemonade_on_this_pc_is_judged_by_its_hardware_only(
        self, monkeypatch
    ):
        """Lemonade's own service runs FLM with its own limits and user."""
        monkeypatch.setattr(npu_linux, "_lemonade_in_use", lambda: (False, True))
        calls = self.host(
            monkeypatch,
            [
                npu_linux._firmware_problem("1.0.0.63"),
                npu_linux._access_problem(Path("/dev/accel/accel0"), "render"),
            ],
        )
        validate = MagicMock()
        monkeypatch.setattr(npu_linux, "validate", validate)
        assert codes(npu_linux.problems()) == ["firmware"]
        assert calls == [{"memlock_hard": None}]
        validate.assert_not_called()

    def test_a_lemonade_on_another_pc_is_not_judged_by_this_one(self, monkeypatch):
        monkeypatch.setattr(npu_linux, "_lemonade_in_use", lambda: (False, False))
        calls = self.host(monkeypatch, [npu_linux._driver_problem()])
        assert npu_linux.problems() == []
        assert calls == []

    def test_no_driver_skips_flm_validate(self, monkeypatch):
        self.host(monkeypatch, [npu_linux._driver_problem()])
        validate = MagicMock()
        monkeypatch.setattr(npu_linux, "validate", validate)
        assert codes(npu_linux.problems()) == ["driver"]
        validate.assert_not_called()

    def test_both_kinds_are_merged_without_repeats(self, monkeypatch):
        self.host(
            monkeypatch,
            [npu_linux._access_problem(Path("/dev/accel/accel0"), "render")],
        )
        monkeypatch.setattr(npu_linux, "embedded_flm", lambda: Path("flm"))
        monkeypatch.setattr(
            npu_linux, "validate", lambda flm: [npu_linux._device_problem()]
        )
        assert codes(npu_linux.problems()) == ["access", "device"]

    def test_one_reading_stands_until_forgotten(self, monkeypatch):
        calls = self.host(monkeypatch, [])
        monkeypatch.setattr(npu_linux, "embedded_flm", lambda: None)
        npu_linux.problems()
        npu_linux.problems()
        assert len(calls) == 1
        npu_linux.forget()
        npu_linux.problems()
        assert len(calls) == 2


@pytest.mark.skipif(not npu_linux._on_linux(), reason="RLIMIT_MEMLOCK is POSIX")
def test_raise_memlock_soft_limit_lifts_soft_to_hard(monkeypatch):
    import resource

    calls = []
    monkeypatch.setattr(resource, "getrlimit", lambda which: (8 * MB, 64 * MB))
    monkeypatch.setattr(resource, "setrlimit", lambda which, lim: calls.append(lim))
    npu_linux.raise_memlock_soft_limit()
    assert calls == [(64 * MB, 64 * MB)]


# ── Where the answer reaches the user ─────────────────────────────────────

MEMLOCK = [npu_linux._memlock_problem("8 MB")]


@pytest.fixture
def not_set_up(linux, monkeypatch):
    """A Linux PC whose NPU Lemonade reports, with memlock left at 8 MB."""
    monkeypatch.setattr(npu_linux, "problems", lambda: list(MEMLOCK))


def test_capacity_reads_the_npu_setup_only_when_asked(linux, monkeypatch):
    from gaia.llm.model_fit import (
        capacity_from_system_info,
        npu_not_ready_reason,
        npu_problems,
    )

    probe = MagicMock(return_value=list(MEMLOCK))
    monkeypatch.setattr(npu_linux, "problems", probe)
    capacity = capacity_from_system_info(linux_system_info())
    # A GPU load reads capacity too; it must not pay for the NPU probe.
    probe.assert_not_called()
    assert capacity.has_npu and capacity.npu_setup_matters
    assert npu_problems(capacity) == MEMLOCK
    assert npu_not_ready_reason(capacity).startswith(
        "needs this PC's NPU set up first: The locked-memory limit is 8 MB"
    )


def test_setup_does_not_call_the_npu_model_a_fit(not_set_up):
    """#6632: setup showed the NPU model as "Fits this PC" and chat then failed."""
    from gaia.installer.model_choice import memory_fit
    from gaia.llm.lemonade_client import (
        NPU_DEFAULT_MODEL_NAME,
        npu_chat_models_that_fit,
    )
    from gaia.llm.model_fit import capacity_from_system_info

    capacity = capacity_from_system_info(linux_system_info())
    fit = memory_fit(NPU_DEFAULT_MODEL_NAME, 9.1, capacity)
    assert fit["fits"] is False
    assert "locked-memory limit" in fit["fit_reason"]
    # Picking it is refused like any NPU model that can't run, not offered as a memory risk.
    assert fit["fit_code"] == "npu"
    assert npu_chat_models_that_fit(capacity) == []


def test_a_set_up_linux_npu_is_offered(linux, monkeypatch):
    monkeypatch.setattr(npu_linux, "problems", lambda: [])
    from gaia.installer.model_choice import memory_fit
    from gaia.llm.lemonade_client import (
        NPU_DEFAULT_MODEL_NAME,
        npu_chat_models_that_fit,
    )
    from gaia.llm.model_fit import capacity_from_system_info

    capacity = capacity_from_system_info(linux_system_info())
    assert memory_fit(NPU_DEFAULT_MODEL_NAME, 9.1, capacity)["fits"] is True
    assert npu_chat_models_that_fit(capacity) == [NPU_DEFAULT_MODEL_NAME]


def test_recipe_reason_says_what_is_missing(not_set_up):
    from gaia.llm.model_device import recipe_unavailable_reason

    assert "8 MB" in recipe_unavailable_reason("flm", linux_system_info())
    # Lemonade's own "Requires XDNA2 NPU" for an unopenable device gives way too.
    info = linux_system_info(flm_state="unsupported")
    info["recipes"]["flm"]["backends"]["npu"]["message"] = "Requires XDNA2 NPU"
    assert "8 MB" in recipe_unavailable_reason("flm", info)
    assert recipe_unavailable_reason("llamacpp", linux_system_info()) is None


def test_reason_reads_as_a_sentence_where_it_is_wrapped(not_set_up):
    """Callers write "This model {reason}." and "{model} {reason}."."""
    from gaia.llm.model_device import recipe_unavailable_reason

    reason = recipe_unavailable_reason("flm", linux_system_info())
    sentence = f"This model {reason}."
    assert sentence.startswith("This model needs this PC's NPU set up first: The ")
    assert not sentence.endswith("..")


def test_pull_refuses_before_downloading_9_gb(not_set_up):
    from gaia.llm.lemonade_client import (
        NPU_DEFAULT_MODEL_NAME,
        LemonadeClient,
        LemonadeClientError,
    )

    client = LemonadeClient.__new__(LemonadeClient)
    client.get_system_info = MagicMock(return_value=linux_system_info())
    client.install_backend = MagicMock()
    with pytest.raises(LemonadeClientError, match="locked-memory limit"):
        client.ensure_npu_backend(NPU_DEFAULT_MODEL_NAME)
    client.install_backend.assert_not_called()


def test_profile_npu_says_what_to_fix(not_set_up):
    from gaia.llm.lemonade_client import recommend_npu_chat_model
    from gaia.llm.model_fit import ModelFitError

    client = MagicMock()
    client.get_system_info.return_value = linux_system_info()
    with pytest.raises(
        ModelFitError, match="locked-memory.*without `--profile npu`"
    ) as e:
        recommend_npu_chat_model(client)
    assert_reads_as_sentences(str(e.value))


def test_npu_default_model_refusal_says_what_to_fix(not_set_up):
    from gaia.installer.init_command import _refuse_if_it_does_not_fit
    from gaia.llm.lemonade_client import NPU_DEFAULT_MODEL_NAME
    from gaia.llm.model_fit import ModelFitError

    client = MagicMock()
    client.list_models.return_value = {"data": []}
    client.get_system_info.return_value = linux_system_info()
    with pytest.raises(ModelFitError, match="locked-memory.*re-run `gaia init`") as e:
        _refuse_if_it_does_not_fit(client, NPU_DEFAULT_MODEL_NAME)
    assert_reads_as_sentences(str(e.value))


def assert_reads_as_sentences(message: str) -> None:
    # The reason is a predicate; spliced in bare it read "yet. needs this PC's…".
    assert "The NPU model needs this PC's NPU set up first: " in message
    assert not re.search(r"\. [a-z]", message), message
    assert not re.search(r"[a-z] (Run|Then) ", message), message


def test_setup_plan_lists_the_problems_for_the_screen(not_set_up):
    from gaia.llm.model_fit import capacity_from_system_info
    from gaia.ui.setup_plan import _npu_status

    status = _npu_status(capacity_from_system_info(linux_system_info()))
    assert status == {
        "present": True,
        "ready": False,
        "problems": [
            {
                "code": "memlock",
                "title": MEMLOCK[0].title,
                "fix": MEMLOCK[0].fix,
                "params": {"limit": "8 MB", "fix": "apply"},
            }
        ],
    }
    assert _npu_status(None) == {"present": False, "ready": False, "problems": []}


def test_npu_problem_is_a_plain_value():
    problem = NpuProblem("memlock", "t", "f", (("limit", "8 MB"),))
    assert problem.as_dict()["params"] == {"limit": "8 MB"}
    hash(problem)


@pytest.mark.skipif(not npu_linux._on_linux(), reason="reads a real sysfs-style link")
def test_bound_driver_is_read_from_the_sysfs_link(tmp_path):
    device = tmp_path / "sys/class/accel/accel0/device"
    device.mkdir(parents=True)
    (tmp_path / "bus/pci/drivers/amdxdna").mkdir(parents=True)
    (device / "driver").symlink_to(tmp_path / "bus/pci/drivers/amdxdna")
    assert npu_linux._driver_of(device) == "amdxdna"
    assert npu_linux.bound_devices(tmp_path) == [("accel0", device)]


def test_embedded_flm_is_found_once_the_backend_is_in(tmp_path, monkeypatch):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    monkeypatch.setattr(EmbeddedLemonade, "cache_dir", property(lambda self: tmp_path))
    assert npu_linux.embedded_flm() is None
    flm = tmp_path / "bin/flm/npu/flm"
    flm.parent.mkdir(parents=True)
    flm.write_text("#!/bin/bash\n")
    flm.chmod(0o755)
    assert npu_linux.embedded_flm() == flm


def test_which_lemonade_runs_flm(monkeypatch):
    from gaia.llm import lemonade_choice

    info = {"mode": lemonade_choice.MODE_GAIA, "base_url": None}
    monkeypatch.setattr(lemonade_choice, "describe", lambda: info)
    assert npu_linux._lemonade_in_use() == (True, True)
    info.update(
        mode=lemonade_choice.MODE_EXTERNAL, base_url="http://127.0.0.1:13305/api/v1"
    )
    assert npu_linux._lemonade_in_use() == (False, True)
    info.update(base_url="http://npu-box.lan:13305/api/v1")
    assert npu_linux._lemonade_in_use() == (False, False)


# ── The one-time locked-memory fix ────────────────────────────────────────


class TestMemlockFix:
    @pytest.fixture(autouse=True)
    def _real_files(self, monkeypatch):
        monkeypatch.setattr(
            npu_linux, "memlock_fix_installed", self.installed, raising=True
        )

    installed = staticmethod(npu_linux.memlock_fix_installed)

    def test_installed_needs_both_files_exactly(self, tmp_path):
        assert not npu_linux.memlock_fix_installed(tmp_path)
        for path, text in npu_linux.MEMLOCK_DROPINS.items():
            target = tmp_path / path.lstrip("/")
            target.parent.mkdir(parents=True)
            target.write_text(text)
        assert npu_linux.memlock_fix_installed(tmp_path)
        (tmp_path / "etc/security/limits.d/90-gaia-npu.conf").write_text("edited\n")
        assert not npu_linux.memlock_fix_installed(tmp_path)

    @pytest.mark.skipif(not npu_linux._on_linux(), reason="runs the script in sh")
    def test_the_script_writes_what_installed_checks_for(self, tmp_path):
        script = npu_linux.memlock_fix_script().replace("/etc/", f"{tmp_path}/etc/")
        script = script.replace("systemctl daemon-reload", "true")
        subprocess.run(["/bin/sh", "-c", script], check=True, timeout=10)
        assert npu_linux.memlock_fix_installed(tmp_path)

    def test_the_problem_says_restart_once_the_fix_is_in(self, monkeypatch):
        monkeypatch.setattr(npu_linux, "memlock_fix_installed", lambda root=None: True)
        monkeypatch.setattr(npu_linux, "restarted_since_fix", lambda root=None: False)
        problem = npu_linux._memlock_problem("8 MB")
        assert dict(problem.params) == {"limit": "8 MB", "fix": "restart"}
        assert "Restart the PC" in problem.fix
        assert "everyone on this PC" in problem.fix

    def test_still_low_after_a_restart_never_asks_for_another(self, monkeypatch):
        monkeypatch.setattr(npu_linux, "memlock_fix_installed", lambda root=None: True)
        monkeypatch.setattr(npu_linux, "restarted_since_fix", lambda root=None: True)
        problem = npu_linux._memlock_problem("8 MB")
        assert dict(problem.params) == {"limit": "8 MB", "fix": "stuck"}
        assert "DefaultLimitMEMLOCK" in problem.fix
        assert "Restart" not in problem.fix

    def test_restarted_since_is_boot_time_after_the_files(self, tmp_path):
        import os as _os

        for path, text in npu_linux.MEMLOCK_DROPINS.items():
            target = tmp_path / path.lstrip("/")
            target.parent.mkdir(parents=True)
            target.write_text(text)
            _os.utime(target, (1000, 1000))
        (tmp_path / "proc").mkdir()
        (tmp_path / "proc/stat").write_text("cpu 1 2 3\nbtime 900\n")
        assert not npu_linux.restarted_since_fix(tmp_path)
        (tmp_path / "proc/stat").write_text("cpu 1 2 3\nbtime 2000\n")
        assert npu_linux.restarted_since_fix(tmp_path)
        (tmp_path / "proc/stat").unlink()
        assert not npu_linux.restarted_since_fix(tmp_path)

    def apply(self, monkeypatch, returncode=0, pkexec="/usr/bin/pkexec", after=True):
        monkeypatch.setattr(npu_linux, "_on_linux", lambda: True)
        monkeypatch.setattr(npu_linux.shutil, "which", lambda name: pkexec)
        state = {"done": False}

        def run(argv, **kwargs):
            state["argv"] = argv
            state["done"] = after and returncode == 0
            return subprocess.CompletedProcess(argv, returncode, "", "")

        monkeypatch.setattr(npu_linux.subprocess, "run", run)
        monkeypatch.setattr(
            npu_linux, "memlock_fix_installed", lambda root=None: state["done"]
        )
        return state

    def test_one_administrator_prompt_writes_both_files(self, monkeypatch):
        state = self.apply(monkeypatch)
        npu_linux.apply_memlock_fix()
        argv = state["argv"]
        assert argv[:3] == ["/usr/bin/pkexec", "/bin/sh", "-c"]
        for path in npu_linux.MEMLOCK_DROPINS:
            assert path in argv[3]

    @pytest.mark.parametrize(
        "returncode,code", [(126, "cancelled"), (127, "not_authorized")]
    )
    def test_a_prompt_that_did_not_happen_changes_nothing_and_says_why(
        self, monkeypatch, returncode, code
    ):
        self.apply(monkeypatch, returncode=returncode)
        with pytest.raises(npu_linux.MemlockFixError) as e:
            npu_linux.apply_memlock_fix()
        assert e.value.code == code

    def test_no_pkexec_says_what_to_do_instead(self, monkeypatch):
        self.apply(monkeypatch, pkexec=None)
        with pytest.raises(npu_linux.MemlockFixError) as e:
            npu_linux.apply_memlock_fix()
        assert e.value.code == "no_pkexec"
        assert "npu#linux" in str(e.value)

    def test_a_step_that_wrote_nothing_fails_loudly(self, monkeypatch):
        self.apply(monkeypatch, after=False)
        with pytest.raises(npu_linux.MemlockFixError) as e:
            npu_linux.apply_memlock_fix()
        assert e.value.code == "failed"

    def test_not_on_windows_or_mac(self, monkeypatch):
        monkeypatch.setattr(npu_linux, "_on_linux", lambda: False)
        with pytest.raises(npu_linux.MemlockFixError) as e:
            npu_linux.apply_memlock_fix()
        assert e.value.code == "not_linux"


def _full_plan(**over):
    """A complete plan, as ``build_plan`` returns it; *over* sets what a test checks."""
    plan = {
        "profile": "gaia",
        "lemonade": {"mode": "gaia", "url": None, "reachable": False},
        "chat_model": {
            "id": "Gemma-4-E4B-it-GGUF",
            "user_set": False,
            "size_gb": None,
            "downloaded": None,
        },
        "recommended_model": None,
        "decided_after_server": True,
        "memory_gb": None,
        "memory_source": None,
        "capacity_gb": None,
        "disk": {"path": None, "free_gb": None, "source": None},
        "download_gb": None,
        "disk_warning": None,
        "skipped_models": [],
        "options": [],
        "models": [],
        "catalog": [],
        "components": [],
        "backends": [],
        "npu": {"present": False, "ready": False, "problems": []},
        "system": {
            "gaia_build": {
                "version": "0.0.0",
                "kind": "source",
                "rc": None,
                "commit": None,
            },
            "gaia_version": "0.0.0",
            "gaia_home": "/home/gaia",
            "agent_version": None,
            "lemonade_version": None,
            "os": None,
            "processor": None,
            "physical_memory": None,
            "devices": [],
        },
        "vision_model": None,
    }
    plan.update(over)
    return plan


class TestSetupEndpoints:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from gaia.ui.routers import setup as setup_router

        monkeypatch.setattr(
            setup_router, "build_plan", lambda: _full_plan(recommended_model="npu-plan")
        )
        app = FastAPI()
        app.include_router(setup_router.router)
        self.app = app
        return TestClient(app, client=("127.0.0.1", 50000))

    UI = {"X-Gaia-UI": "1"}

    def test_the_fix_returns_the_new_plan(self, client, monkeypatch):
        applied = MagicMock()
        monkeypatch.setattr(npu_linux, "apply_memlock_fix", applied)
        r = client.post("/api/setup/npu/memlock-fix", headers=self.UI)
        assert r.status_code == 200 and r.json()["recommended_model"] == "npu-plan"
        applied.assert_called_once()

    def test_a_dismissed_prompt_is_a_409_with_its_code(self, client, monkeypatch):
        def refuse():
            raise npu_linux.MemlockFixError("cancelled", "closed")

        monkeypatch.setattr(npu_linux, "apply_memlock_fix", refuse)
        r = client.post("/api/setup/npu/memlock-fix", headers=self.UI)
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "cancelled"

    def test_the_fix_needs_the_ui_header(self, client):
        r = client.post("/api/setup/npu/memlock-fix", headers={"X-Gaia-UI": ""})
        assert r.status_code == 403

    def test_a_phone_or_another_pc_cannot_start_the_admin_step(
        self, client, monkeypatch
    ):
        from fastapi.testclient import TestClient

        applied = MagicMock()
        monkeypatch.setattr(npu_linux, "apply_memlock_fix", applied)
        remote = TestClient(self.app, client=("192.168.1.20", 50000))
        assert (
            remote.post("/api/setup/npu/memlock-fix", headers=self.UI).status_code
            == 403
        )
        tunnel = client.post(
            "/api/setup/npu/memlock-fix",
            headers={**self.UI, "X-Forwarded-For": "203.0.113.9"},
        )
        assert tunnel.status_code == 403
        applied.assert_not_called()

    @pytest.mark.parametrize(
        "code,status",
        [("not_authorized", 409), ("no_pkexec", 422), ("failed", 500)],
    )
    def test_each_refusal_has_its_status(self, client, monkeypatch, code, status):
        def refuse():
            raise npu_linux.MemlockFixError(code, "no")

        monkeypatch.setattr(npu_linux, "apply_memlock_fix", refuse)
        r = client.post("/api/setup/npu/memlock-fix", headers=self.UI)
        assert r.status_code == status
        assert r.json()["detail"]["code"] == code

    def test_recheck_reads_the_machine_afresh(self, client, monkeypatch):
        forget = MagicMock()
        monkeypatch.setattr(npu_linux, "forget", forget)
        r = client.post("/api/setup/npu/recheck", headers=self.UI)
        assert r.status_code == 200
        forget.assert_called_once()


def test_settings_lists_the_problems_for_each_flm_model(not_set_up):
    from gaia.ui.local_models import annotate_local_models

    models = [
        {"id": "gemma4-it-e4b-FLM", "recipe": "flm", "downloaded": True, "size": 9.1},
        {"id": "Gemma-4-E4B-it-GGUF", "recipe": "llamacpp", "downloaded": True},
    ]
    rows = annotate_local_models(
        models,
        health={},
        system_info=linux_system_info(),
        capacity=None,
        preferred_backend=None,
        speeds={},
    )
    flm, gguf = rows
    assert [p["code"] for p in flm["npu_problems"]] == ["memlock"]
    assert flm["available"] is False
    assert gguf["npu_problems"] == []


def test_settings_leaves_a_serving_npu_alone(linux, monkeypatch):
    """A loaded FLM model proves the NPU runs; flm validate mustn't poke it."""
    from gaia.ui.local_models import annotate_local_models

    probe = MagicMock(return_value=list(MEMLOCK))
    monkeypatch.setattr(npu_linux, "problems", probe)
    models = [{"id": "gemma4-it-e4b-FLM", "recipe": "flm", "downloaded": True}]
    health = {"all_models_loaded": [{"model_name": "gemma4-it-e4b-FLM"}]}
    rows = annotate_local_models(
        models,
        health=health,
        system_info=linux_system_info(),
        capacity=None,
        preferred_backend=None,
        speeds={},
    )
    assert rows[0]["npu_problems"] == []
    probe.assert_not_called()


class TestInitOffersTheFix:
    def init(self, monkeypatch, *, yes=False, tty=True, answer=True):
        from gaia.installer import init_command

        cmd = init_command.InitCommand.__new__(init_command.InitCommand)
        cmd.yes = yes
        cmd.printed = []
        monkeypatch.setattr(init_command, "stdin_is_tty", lambda: tty)
        monkeypatch.setattr(
            cmd, "_prompt_yes_no", lambda prompt, default=True: answer, raising=False
        )
        monkeypatch.setattr(cmd, "_print_warning", cmd.printed.append, raising=False)
        monkeypatch.setattr(cmd, "_print_success", cmd.printed.append, raising=False)
        return cmd

    def test_applies_it_when_someone_is_at_the_terminal(self, monkeypatch):
        applied = MagicMock()
        monkeypatch.setattr(npu_linux, "apply_memlock_fix", applied)
        cmd = self.init(monkeypatch)
        assert cmd._offer_memlock_fix(list(MEMLOCK))
        applied.assert_called_once()
        assert "Restart the PC" in cmd.printed[-1]

    @pytest.mark.parametrize(
        "kwargs", [{"yes": True}, {"tty": False}, {"answer": False}]
    )
    def test_never_prompts_unattended_or_unasked(self, monkeypatch, kwargs):
        applied = MagicMock()
        monkeypatch.setattr(npu_linux, "apply_memlock_fix", applied)
        assert not self.init(monkeypatch, **kwargs)._offer_memlock_fix(list(MEMLOCK))
        applied.assert_not_called()

    def test_profile_npu_offers_it_before_judging_the_model(self, monkeypatch):
        from gaia.llm import lemonade_client

        info = {"os_version": "Linux", "devices": {"amd_npu": {"available": True}}}
        client = MagicMock()
        client.get_system_info.return_value = info
        monkeypatch.setattr(lemonade_client, "LemonadeClient", lambda **kw: client)
        monkeypatch.setattr(npu_linux, "_on_linux", lambda: True)
        checked = MagicMock(return_value=list(MEMLOCK))
        monkeypatch.setattr(npu_linux, "check", checked)
        applied = MagicMock()
        monkeypatch.setattr(npu_linux, "apply_memlock_fix", applied)
        cmd = self.init(monkeypatch)
        cmd.verbose = False
        cmd._offer_memlock_fix_for_npu_profile()
        checked.assert_called_once_with(info)
        applied.assert_called_once()

    @pytest.mark.parametrize("kwargs", [{"yes": True}, {"tty": False}])
    def test_profile_npu_asks_nothing_unattended(self, monkeypatch, kwargs):
        from gaia.llm import lemonade_client

        client = MagicMock()
        monkeypatch.setattr(lemonade_client, "LemonadeClient", lambda **kw: client)
        monkeypatch.setattr(npu_linux, "_on_linux", lambda: True)
        cmd = self.init(monkeypatch, **kwargs)
        cmd.verbose = False
        cmd._offer_memlock_fix_for_npu_profile()
        client.get_system_info.assert_not_called()

    def test_a_refused_prompt_is_reported(self, monkeypatch):
        def refuse():
            raise npu_linux.MemlockFixError("cancelled", "closed, nothing changed")

        monkeypatch.setattr(npu_linux, "apply_memlock_fix", refuse)
        cmd = self.init(monkeypatch)
        assert not cmd._offer_memlock_fix(list(MEMLOCK))
        assert cmd.printed == ["closed, nothing changed"]
