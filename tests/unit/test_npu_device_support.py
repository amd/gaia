# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for multi-device (CPU/GPU/NPU) agent support (#1220)."""

import json
from unittest.mock import MagicMock, patch

import pytest

# ── DeviceConfig & AgentRegistration ─────────────────────────────────────


class TestDeviceConfig:
    """Test the DeviceConfig dataclass."""

    def test_defaults(self):
        from gaia.agents.registry import DeviceConfig

        dc = DeviceConfig(
            device="gpu",
            model="Gemma-4-E4B-it-GGUF",
            recipe="llamacpp",
            backend="llamacpp:vulkan",
        )
        assert dc.device == "gpu"
        assert dc.model == "Gemma-4-E4B-it-GGUF"
        assert dc.recipe == "llamacpp"
        assert dc.backend == "llamacpp:vulkan"
        assert dc.verified is False
        assert dc.ctx_size == 32768

    def test_npu_config(self):
        from gaia.agents.registry import DeviceConfig

        dc = DeviceConfig(
            device="npu",
            model="gemma4-it-e2b-FLM",
            recipe="flm",
            backend="flm:npu",
            verified=True,
            ctx_size=4096,
        )
        assert dc.device == "npu"
        assert dc.model == "gemma4-it-e2b-FLM"
        assert dc.recipe == "flm"
        assert dc.backend == "flm:npu"
        assert dc.verified is True
        assert dc.ctx_size == 4096


class TestDefaultDeviceConfigs:
    """Test the DEFAULT_DEVICE_CONFIGS constant."""

    def test_has_three_configs(self):
        from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS

        assert len(DEFAULT_DEVICE_CONFIGS) == 3

    def test_gpu_is_first_and_verified(self):
        from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS

        gpu = DEFAULT_DEVICE_CONFIGS[0]
        assert gpu.device == "gpu"
        assert gpu.verified is True
        assert gpu.recipe == "llamacpp"

    def test_cpu_config(self):
        from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS

        cpu = DEFAULT_DEVICE_CONFIGS[1]
        assert cpu.device == "cpu"
        assert cpu.backend == "llamacpp:cpu"

    def test_npu_config(self):
        from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS

        npu = DEFAULT_DEVICE_CONFIGS[2]
        assert npu.device == "npu"
        assert npu.model == "gemma4-it-e2b-FLM"
        assert npu.recipe == "flm"
        assert npu.backend == "flm:npu"
        assert npu.ctx_size == 4096


class TestAgentRegistrationDeviceConfigs:
    """Test that AgentRegistration includes device_configs."""

    def test_default_device_configs_populated(self):
        from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS, AgentRegistration

        reg = AgentRegistration(
            id="test",
            name="Test",
            description="Test agent",
            source="builtin",
            conversation_starters=[],
            factory=lambda: None,
            agent_dir=None,
            models=[],
        )
        assert len(reg.device_configs) == len(DEFAULT_DEVICE_CONFIGS)
        assert reg.device_configs[0].device == "gpu"


# ── GaiaConfig ───────────────────────────────────────────────────────────


class TestGaiaConfig:
    """Test persistent config read/write."""

    def test_defaults(self):
        from gaia.config import GaiaConfig

        cfg = GaiaConfig()
        assert cfg.profile == "chat"
        assert cfg.default_device == "gpu"

    def test_save_and_load(self, tmp_path):
        from gaia.config import GaiaConfig

        config_file = tmp_path / "config.json"
        with (
            patch("gaia.config.GAIA_CONFIG_FILE", config_file),
            patch("gaia.config.GAIA_CONFIG_DIR", tmp_path),
        ):
            cfg = GaiaConfig(profile="npu", default_device="npu")
            cfg.save()

            assert config_file.exists()
            data = json.loads(config_file.read_text())
            assert data["profile"] == "npu"
            assert data["default_device"] == "npu"

            loaded = GaiaConfig.load()
            assert loaded.profile == "npu"
            assert loaded.default_device == "npu"

    def test_load_missing_file(self, tmp_path):
        from gaia.config import GaiaConfig

        missing = tmp_path / "nonexistent.json"
        with patch("gaia.config.GAIA_CONFIG_FILE", missing):
            cfg = GaiaConfig.load()
            assert cfg.profile == "chat"
            assert cfg.default_device == "gpu"

    def test_load_corrupt_file(self, tmp_path):
        from gaia.config import GaiaConfig

        bad_file = tmp_path / "config.json"
        bad_file.write_text("not valid json{{{")
        with patch("gaia.config.GAIA_CONFIG_FILE", bad_file):
            cfg = GaiaConfig.load()
            assert cfg.profile == "chat"  # falls back to defaults


# ── LemonadeClient backend methods ───────────────────────────────────────


class TestLemonadeClientBackendMethods:
    """Test install_backend, uninstall_backend, get_recipe_status."""

    def test_install_backend(self):
        from gaia.llm.lemonade_client import LemonadeClient

        client = LemonadeClient.__new__(LemonadeClient)
        client.base_url = "http://localhost:13305/api/v1"
        client.log = MagicMock()
        client._send_request = MagicMock(return_value={"status": "success"})

        result = client.install_backend("flm:npu")
        assert result == {"status": "success"}
        client._send_request.assert_called_once_with(
            "post",
            "http://localhost:13305/api/v1/install",
            {"spec": "flm:npu"},
            timeout=300,
        )

    def test_install_backend_with_force(self):
        from gaia.llm.lemonade_client import LemonadeClient

        client = LemonadeClient.__new__(LemonadeClient)
        client.base_url = "http://localhost:13305/api/v1"
        client.log = MagicMock()
        client._send_request = MagicMock(return_value={"status": "success"})

        client.install_backend("llamacpp:rocm", force=True)
        call_args = client._send_request.call_args
        assert call_args[0][2] == {"spec": "llamacpp:rocm", "force": True}

    def test_install_backend_error(self):
        from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

        client = LemonadeClient.__new__(LemonadeClient)
        client.base_url = "http://localhost:13305/api/v1"
        client.log = MagicMock()
        client._send_request = MagicMock(side_effect=Exception("connection refused"))

        with pytest.raises(LemonadeClientError, match="Failed to install backend"):
            client.install_backend("flm:npu")

    def test_uninstall_backend(self):
        from gaia.llm.lemonade_client import LemonadeClient

        client = LemonadeClient.__new__(LemonadeClient)
        client.base_url = "http://localhost:13305/api/v1"
        client.log = MagicMock()
        client._send_request = MagicMock(return_value={"status": "success"})

        result = client.uninstall_backend("flm:npu")
        assert result == {"status": "success"}
        client._send_request.assert_called_once_with(
            "post",
            "http://localhost:13305/api/v1/uninstall",
            {"spec": "flm:npu"},
            timeout=120,
        )

    def test_get_recipe_status_found(self):
        from gaia.llm.lemonade_client import LemonadeClient

        client = LemonadeClient.__new__(LemonadeClient)
        client.base_url = "http://localhost:13305/api/v1"
        client.log = MagicMock()

        mock_sysinfo = {
            "recipes": {
                "flm": {
                    "default_backend": "npu",
                    "backends": {"npu": {"state": "installed"}},
                }
            }
        }
        client.get_system_info = MagicMock(return_value=mock_sysinfo)

        status = client.get_recipe_status("flm")
        assert status is not None
        assert status["backends"]["npu"]["state"] == "installed"

    def test_get_recipe_status_not_found(self):
        from gaia.llm.lemonade_client import LemonadeClient

        client = LemonadeClient.__new__(LemonadeClient)
        client.base_url = "http://localhost:13305/api/v1"
        client.log = MagicMock()
        client.get_system_info = MagicMock(return_value={"recipes": {}})

        assert client.get_recipe_status("nonexistent") is None


# ── CLI argument parsing ─────────────────────────────────────────────────


class TestCLIArgs:
    """Test that CLI accepts --profile npu and --device flags."""

    def test_profile_npu_accepted(self):
        """Verify 'npu' is a valid profile choice."""
        from gaia.installer.init_command import INIT_PROFILES

        assert "npu" in INIT_PROFILES

    def test_npu_profile_config(self):
        """Verify NPU profile has correct structure."""
        from gaia.installer.init_command import INIT_PROFILES

        npu = INIT_PROFILES["npu"]
        assert npu["recipe"] == "flm"
        assert npu["backend"] == "flm:npu"
        assert npu["required_device"] == "amd_npu"
        assert "gemma4-it-e2b-FLM" in npu["models"]
        assert npu["min_context_size"] == 4096


# ── Init command NPU steps ───────────────────────────────────────────────


class TestInitCommandNPU:
    """Test NPU-specific init steps."""

    def _make_init(self, profile="npu"):
        from gaia.installer.init_command import InitCommand

        with patch.object(InitCommand, "__init__", lambda self, **kw: None):
            cmd = InitCommand.__new__(InitCommand)
            cmd.profile = profile
            cmd.verbose = False
            cmd.skip_models = False
            cmd.console = None
            return cmd

    def test_check_device_npu_available(self):
        cmd = self._make_init()
        cmd._print_success = MagicMock()
        cmd._print_error = MagicMock()

        mock_client = MagicMock()
        mock_client.get_system_info.return_value = {
            "devices": {
                "amd_npu": {"available": True, "name": "AMD Ryzen AI NPU"},
            }
        }

        # LemonadeClient is imported inside the method body, so patch it
        # at the source module level.
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=mock_client):
            result = cmd._check_device_available()

        assert result is True

    def test_check_device_npu_not_available(self):
        cmd = self._make_init()
        cmd._print_success = MagicMock()
        cmd._print_error = MagicMock()

        mock_client = MagicMock()
        mock_client.get_system_info.return_value = {
            "devices": {
                "amd_npu": {"available": False},
            }
        }

        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=mock_client):
            result = cmd._check_device_available()

        assert result is False
        assert cmd._print_error.called

    def test_check_device_not_required(self):
        """Non-NPU profiles skip device check."""
        cmd = self._make_init(profile="chat")
        result = cmd._check_device_available()
        assert result is True


# ── LemonadeManager device parameter ─────────────────────────────────────


class TestLemonadeManagerDevice:
    """Test that ensure_ready accepts device parameter."""

    def test_device_parameter_exists(self):
        """Verify ensure_ready accepts a device parameter."""
        import inspect

        from gaia.llm.lemonade_manager import LemonadeManager

        # Get the underlying function from the classmethod descriptor
        method = LemonadeManager.__dict__["ensure_ready"]
        func = method.__func__ if hasattr(method, "__func__") else method
        sig = inspect.signature(func)
        assert "device" in sig.parameters


# ── UI models ────────────────────────────────────────────────────────────


class TestUIModels:
    """Test UI model changes for device support."""

    def test_system_status_has_detected_devices(self):
        from gaia.ui.models import SystemStatus

        status = SystemStatus()
        assert hasattr(status, "detected_devices")
        assert status.detected_devices == []
        assert hasattr(status, "active_profile")
        assert status.active_profile == "chat"

    def test_agent_info_has_device_configs(self):
        from gaia.ui.models import AgentInfo

        info = AgentInfo(
            id="test",
            name="Test",
            description="Test",
            source="builtin",
        )
        assert hasattr(info, "device_configs")
        assert info.device_configs == []
