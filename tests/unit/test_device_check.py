# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the Strix Halo / Radeon device-check guard in cli.py."""

import sys
from unittest.mock import patch

import pytest

from gaia.cli import _check_device_supported, _get_processor_name


# ── _get_processor_name ────────────────────────────────────────────────────


class TestGetProcessorName:
    def test_windows_registry_success(self):
        """On Windows, reads processor name from registry."""
        if sys.platform != "win32":
            pytest.skip("Windows-only test")
        name = _get_processor_name()
        assert isinstance(name, str)
        assert len(name) > 0

    def test_windows_registry_fallback_on_error(self):
        """Falls back gracefully when registry read fails."""
        if sys.platform != "win32":
            pytest.skip("Windows-only test")
        with patch("winreg.OpenKey", side_effect=OSError("access denied")):
            with patch("platform.processor", return_value="fallback"):
                name = _get_processor_name()
        # Either fallback value or empty string is acceptable
        assert isinstance(name, str)

    def test_non_windows_uses_platform(self):
        """On non-Windows, uses platform.processor()."""
        with patch.object(sys, "platform", "linux"):
            with patch("platform.processor", return_value="Intel Core i9"):
                name = _get_processor_name()
        assert name == "Intel Core i9"

    def test_returns_empty_string_on_all_failures(self):
        """Returns empty string when all detection methods fail."""
        with patch.object(sys, "platform", "linux"):
            with patch("platform.processor", side_effect=Exception("fail")):
                name = _get_processor_name()
        assert name == ""


# ── _check_device_supported ────────────────────────────────────────────────

# No matching GPU — used when we want the CPU check to be the only factor
_NO_GPUS = []


class TestCheckDeviceSupported:
    # ── Strix Halo CPU path ────────────────────────────────────────────

    @pytest.mark.parametrize(
        "processor_name",
        [
            "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S",
            "AMD Ryzen AI Max 390 w/ Radeon 890M",
            "AMD RYZEN AI MAX 395",
            "amd ryzen ai max+ 395",  # lowercase
        ],
    )
    def test_strix_halo_processors_are_supported(self, processor_name):
        """Ryzen AI Max processors are recognised as supported (no GPU check needed)."""
        with patch("gaia.cli._get_processor_name", return_value=processor_name):
            with patch("gaia.cli._get_gpu_info", return_value=_NO_GPUS):
                supported, name = _check_device_supported()
        assert supported is True
        assert name == processor_name

    # ── AMD Radeon GPU path ────────────────────────────────────────────

    @pytest.mark.parametrize(
        "gpu_name,vram_gb",
        [
            ("AMD Radeon RX 7900 XTX", 24.0),   # exactly 24 GB — pass
            ("AMD Radeon RX 7900 XTX", 24.1),   # slightly above — pass
            ("AMD Radeon Pro W7900", 48.0),       # workstation 48 GB — pass
            ("AMD Radeon Pro W7800", 32.0),       # workstation 32 GB — pass
            ("AMD Radeon Instinct MI300X", 192.0),  # compute 192 GB — pass
        ],
    )
    def test_radeon_gpu_with_sufficient_vram_is_supported(self, gpu_name, vram_gb):
        """AMD Radeon GPU with >= 24 GB VRAM passes the check."""
        with patch("gaia.cli._get_processor_name", return_value="Intel Core i9-13900K"):
            with patch("gaia.cli._get_gpu_info", return_value=[(gpu_name, vram_gb)]):
                supported, name = _check_device_supported()
        assert supported is True
        assert gpu_name in name
        assert "GB VRAM" in name

    @pytest.mark.parametrize(
        "gpu_name,vram_gb",
        [
            ("AMD Radeon RX 7900 XT", 20.0),     # 20 GB — below threshold
            ("AMD Radeon RX 7900 GRE", 16.0),    # 16 GB — below threshold
            ("AMD Radeon RX 6900 XT", 16.0),     # 16 GB — below threshold
            ("AMD Radeon RX 9070 XT", 16.0),     # 16 GB — below threshold
            ("AMD Radeon RX 7900 XTX", 23.9),    # just under 24 GB — fail
        ],
    )
    def test_radeon_gpu_with_insufficient_vram_is_rejected(self, gpu_name, vram_gb):
        """AMD Radeon GPU below 24 GB VRAM is rejected."""
        with patch("gaia.cli._get_processor_name", return_value="Intel Core i9-13900K"):
            with patch("gaia.cli._get_gpu_info", return_value=[(gpu_name, vram_gb)]):
                supported, _ = _check_device_supported()
        assert supported is False

    def test_non_amd_gpu_is_rejected(self):
        """NVIDIA or Intel GPUs are not allowed regardless of VRAM."""
        with patch("gaia.cli._get_processor_name", return_value="Intel Core i9-13900K"):
            with patch(
                "gaia.cli._get_gpu_info",
                return_value=[("NVIDIA GeForce RTX 4090", 24.0)],
            ):
                supported, _ = _check_device_supported()
        assert supported is False

    def test_multiple_gpus_passes_if_any_qualifies(self):
        """If one GPU qualifies, the device is supported."""
        gpus = [
            ("AMD Radeon RX 7900 XT", 20.0),   # not enough
            ("AMD Radeon RX 7900 XTX", 24.0),  # qualifies
        ]
        with patch("gaia.cli._get_processor_name", return_value="AMD Ryzen 9 7950X"):
            with patch("gaia.cli._get_gpu_info", return_value=gpus):
                supported, _ = _check_device_supported()
        assert supported is True

    # ── Fallback / edge cases ──────────────────────────────────────────

    @pytest.mark.parametrize(
        "processor_name",
        [
            "AMD Ryzen 9 7950X",
            "Intel Core i9-13900K",
            "Apple M3 Max",
            "AMD Ryzen AI 9 HX 375",  # Phoenix — not Strix Halo
            "AMD RYZEN AI 300",        # not AI MAX
        ],
    )
    def test_unsupported_cpu_no_gpu_is_rejected(self, processor_name):
        """Non-Strix-Halo CPU with no qualifying GPU is rejected."""
        with patch("gaia.cli._get_processor_name", return_value=processor_name):
            with patch("gaia.cli._get_gpu_info", return_value=_NO_GPUS):
                supported, name = _check_device_supported()
        assert supported is False
        assert name == processor_name

    def test_unknown_processor_is_allowed(self):
        """When processor name cannot be detected, we allow with a warning."""
        with patch("gaia.cli._get_processor_name", return_value=""):
            with patch("gaia.cli._get_gpu_info", return_value=_NO_GPUS):
                supported, name = _check_device_supported()
        assert supported is True
        assert name == "unknown"

    def test_returns_processor_name_for_strix_halo(self):
        """Returned name matches the CPU name for Strix Halo."""
        cpu = "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S"
        with patch("gaia.cli._get_processor_name", return_value=cpu):
            with patch("gaia.cli._get_gpu_info", return_value=_NO_GPUS):
                supported, name = _check_device_supported()
        assert name == cpu
        assert supported is True
