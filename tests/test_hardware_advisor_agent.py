# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for Hardware Advisor Agent.

Purpose: Verify that the Hardware Advisor Agent correctly provides hardware
information and model recommendations.
"""

from unittest.mock import MagicMock, patch

import pytest

from examples.hardware_advisor_agent import HardwareAdvisorAgent


class TestHardwareAdvisorAgent:
    """Test suite for Hardware Advisor Agent."""

    @pytest.fixture
    def mock_lemonade_client(self):
        """Mock Lemonade client for testing without real server."""
        with patch(
            "examples.hardware_advisor_agent.LemonadeClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.get_system_info.return_value = {
                "OS Version": "Ubuntu 22.04",
                "Processor": "AMD Ryzen 9 7940HS",
                "Physical Memory": "32.0 GB",
                "devices": {
                    "npu": {"available": True, "name": "AMD Ryzen AI NPU"},
                },
            }
            mock_client.list_models.return_value = {
                "data": [
                    {
                        "id": "test-model-1",
                        "name": "Test Model 1",
                        "downloaded": True,
                        "labels": ["test"],
                    }
                ]
            }
            mock_client.get_model_info.return_value = {"size_gb": 5.0}
            mock_client_class.return_value = mock_client
            yield mock_client

    @pytest.fixture
    def agent(self, mock_lemonade_client):
        """Create Hardware Advisor Agent instance with mocked client."""
        agent = HardwareAdvisorAgent()
        return agent

    def test_agent_initialization(self, agent):
        """Test agent initializes correctly."""
        assert agent is not None
        assert agent.client is not None
        assert agent.max_steps == 50

    def test_get_hardware_info_tool_exists(self, agent):
        """Test that get_hardware_info tool is registered and callable."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        assert "get_hardware_info" in _TOOL_REGISTRY
        tool_func = _TOOL_REGISTRY["get_hardware_info"]["function"]
        assert callable(tool_func)

    def test_list_available_models_tool_exists(self, agent):
        """Test that list_available_models tool is registered and callable."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        assert "list_available_models" in _TOOL_REGISTRY
        tool_func = _TOOL_REGISTRY["list_available_models"]["function"]
        assert callable(tool_func)

    def test_recommend_models_tool_exists(self, agent):
        """Test that recommend_models tool is registered and callable."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        assert "recommend_models" in _TOOL_REGISTRY
        tool_func = _TOOL_REGISTRY["recommend_models"]["function"]
        assert callable(tool_func)

    def test_get_hardware_info_returns_success(self, agent, mock_lemonade_client):
        """Test get_hardware_info tool returns successful result."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        get_hardware_info = _TOOL_REGISTRY["get_hardware_info"]["function"]
        result = get_hardware_info()

        assert result["success"] is True
        assert "ram_gb" in result
        assert "gpu" in result
        assert "npu" in result
        assert result["ram_gb"] == 32.0

    def test_list_available_models_returns_success(self, agent, mock_lemonade_client):
        """Test list_available_models tool returns successful result."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        list_models = _TOOL_REGISTRY["list_available_models"]["function"]
        result = list_models()

        assert result["success"] is True
        assert "models" in result
        assert len(result["models"]) > 0
        assert result["models"][0]["id"] == "test-model-1"

    def test_recommend_models_returns_success(self, agent, mock_lemonade_client):
        """Test recommend_models tool returns successful result."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        recommend_models = _TOOL_REGISTRY["recommend_models"]["function"]
        result = recommend_models(ram_gb=32.0, gpu_memory_mb=8192)

        assert result["success"] is True
        assert "recommendations" in result
        assert "constraints" in result


class TestHardwareAdvisorAgentToolImplementation:
    """Test tool implementation details."""

    @pytest.fixture
    def mock_lemonade_client(self):
        """Mock Lemonade client."""
        with patch(
            "examples.hardware_advisor_agent.LemonadeClient"
        ) as mock_client_class:
            mock_client = MagicMock()
            mock_client.get_system_info.return_value = {
                "OS Version": "Linux",
                "Processor": "AMD",
                "Physical Memory": "64.0 GB",
                "devices": {"npu": {"available": True, "name": "Test NPU"}},
            }
            mock_client.list_models.return_value = {
                "data": [
                    {
                        "id": "model-1",
                        "name": "Model 1",
                        "downloaded": True,
                        "labels": [],
                    },
                    {
                        "id": "model-2",
                        "name": "Model 2",
                        "downloaded": False,
                        "labels": [],
                    },
                ]
            }
            mock_client.get_model_info.return_value = {"size_gb": 10.0}
            mock_client_class.return_value = mock_client
            yield mock_client

    @pytest.fixture
    def agent(self, mock_lemonade_client):
        """Create agent instance."""
        return HardwareAdvisorAgent()

    def test_get_hardware_info_includes_all_components(self, agent):
        """Test get_hardware_info returns all expected hardware components."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        get_hw = _TOOL_REGISTRY["get_hardware_info"]["function"]
        result = get_hw()

        assert "os" in result
        assert "processor" in result
        assert "ram_gb" in result
        assert "gpu" in result
        assert "npu" in result

    def test_recommend_models_respects_memory_constraints(self, agent):
        """Test recommend_models filters by available RAM."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        recommend = _TOOL_REGISTRY["recommend_models"]["function"]

        # Low RAM scenario - should only recommend small models
        result = recommend(ram_gb=8.0, gpu_memory_mb=0)

        # Check that constraints are applied
        assert result["constraints"]["available_ram_gb"] == 8.0
        # Max model size should be ~70% of RAM
        assert result["constraints"]["max_model_size_gb"] == pytest.approx(5.6, rel=0.1)
