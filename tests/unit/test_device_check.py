# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for processor-name detection in gaia.device."""

import subprocess
import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

import gaia.device
from gaia.device import get_processor_name


@pytest.fixture(autouse=True)
def _fresh_cache():
    get_processor_name.cache_clear()
    yield
    get_processor_name.cache_clear()


class TestGetProcessorName:
    def test_windows_registry_success(self):
        if sys.platform != "win32":
            pytest.skip("Windows-only test")
        name = get_processor_name()
        assert isinstance(name, str)
        assert len(name) > 0

    def test_macos_reads_the_cpu_brand_string(self):
        with (
            patch.object(sys, "platform", "darwin"),
            patch(
                "gaia.device.subprocess.run",
                return_value=MagicMock(stdout="Apple M3 Max\n"),
            ) as run,
        ):
            assert get_processor_name() == "Apple M3 Max"
        assert run.call_args.args[0] == ["sysctl", "-n", "machdep.cpu.brand_string"]

    def test_macos_sysctl_failure_yields_no_name(self):
        with (
            patch.object(sys, "platform", "darwin"),
            patch(
                "gaia.device.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "sysctl"),
            ),
        ):
            assert get_processor_name() == ""

    def test_linux_reads_model_name_from_cpuinfo(self):
        cpuinfo = (
            "processor\t: 0\n"
            "vendor_id\t: AuthenticAMD\n"
            "model name\t: AMD Ryzen AI 9 HX 370 w/ Radeon 890M\n"
        )
        with (
            patch.object(sys, "platform", "linux"),
            patch("builtins.open", mock_open(read_data=cpuinfo)),
        ):
            assert get_processor_name() == "AMD Ryzen AI 9 HX 370 w/ Radeon 890M"

    def test_linux_without_model_name_yields_no_name(self):
        with (
            patch.object(sys, "platform", "linux"),
            patch("builtins.open", mock_open(read_data="processor\t: 0\n")),
        ):
            assert get_processor_name() == ""

    def test_result_is_cached(self):
        with (
            patch.object(sys, "platform", "darwin"),
            patch(
                "gaia.device.subprocess.run",
                return_value=MagicMock(stdout="Apple M3 Max\n"),
            ) as run,
        ):
            get_processor_name()
            get_processor_name()
        assert run.call_count == 1


def test_no_device_support_verdict_remains():
    """The Qwen3.5-35B-era gate is gone; Settings no longer flags hardware."""
    assert not hasattr(gaia.device, "check_device_supported")
