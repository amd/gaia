# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for AgentProfile and AgentCapabilities.

This test suite validates:
- AgentProfile creation, validation, and serialization
- AgentCapabilities creation, validation, and operations
- YAML serialization/deserialization
- Thread safety

Quality Gate 4 Criteria Covered:
- MOD-001: AgentProfile validation (100% accuracy)
- MOD-003: Backward compatibility (profiles work with existing patterns)
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from gaia.core.capabilities import AgentCapabilities
from gaia.core.profile import AgentProfile


# =============================================================================
# AgentCapabilities Tests
# =============================================================================

class TestAgentCapabilitiesCreation:
    """Tests for AgentCapabilities creation and initialization."""

    def test_create_default_capabilities(self):
        """Test creating capabilities with default values."""
        caps = AgentCapabilities()
        assert caps.supported_tools == []
        assert caps.supported_models == []
        assert caps.max_context_tokens is None
        assert caps.requires_workspace is False
        assert caps.requires_internet is False
        assert caps.requires_api_keys is False
        assert caps.supports_vision is False
        assert caps.supports_audio is False
        assert caps.supports_code_execution is False
        assert caps.metadata == {}

    def test_create_capabilities_with_all_fields(self):
        """Test creating capabilities with all fields specified."""
        caps = AgentCapabilities(
            supported_tools=["read_file", "write_file"],
            supported_models=["Qwen3.5-35B", "Qwen3-0.6B"],
            max_context_tokens=32768,
            requires_workspace=True,
            requires_internet=True,
            requires_api_keys=True,
            supports_vision=True,
            supports_audio=True,
            supports_code_execution=True,
            metadata={"custom": "value"},
        )
        assert len(caps.supported_tools) == 2
        assert len(caps.supported_models) == 2
        assert caps.max_context_tokens == 32768
        assert caps.requires_workspace is True
        assert caps.supports_vision is True
        assert caps.metadata == {"custom": "value"}

    def test_capabilities_post_init_converts_to_sets(self):
        """Test that __post_init__ creates internal sets."""
        caps = AgentCapabilities(supported_tools=["tool1", "tool2"])
        assert hasattr(caps, "_tool_set")
        assert "tool1" in caps._tool_set
        assert "tool2" in caps._tool_set


class TestAgentCapabilitiesValidation:
    """Tests for AgentCapabilities validation."""

    def test_validate_default_capabilities(self):
        """Test validating default capabilities."""
        caps = AgentCapabilities()
        assert caps.validate() is True

    def test_validate_with_all_fields(self):
        """Test validating capabilities with all fields."""
        caps = AgentCapabilities(
            supported_tools=["read_file"],
            max_context_tokens=16384,
            supports_vision=True,
        )
        assert caps.validate() is True

    def test_validate_negative_context_tokens_raises(self):
        """Test that negative max_context_tokens raises ValueError."""
        caps = AgentCapabilities(max_context_tokens=-100)
        with pytest.raises(ValueError, match="max_context_tokens must be positive"):
            caps.validate()

    def test_validate_excessive_context_tokens_raises(self):
        """Test that excessive max_context_tokens raises ValueError."""
        caps = AgentCapabilities(max_context_tokens=2000000)
        with pytest.raises(ValueError, match="exceeds maximum"):
            caps.validate()

    def test_validate_non_string_tool_raises(self):
        """Test that non-string tool name raises ValueError."""
        caps = AgentCapabilities(supported_tools=[123])
        with pytest.raises(ValueError, match="must be string"):
            caps.validate()

    def test_validate_empty_tool_name_raises(self):
        """Test that empty tool name raises ValueError."""
        caps = AgentCapabilities(supported_tools=[""])
        with pytest.raises(ValueError, match="cannot be empty"):
            caps.validate()

    def test_validate_whitespace_tool_name_raises(self):
        """Test that whitespace-only tool name raises ValueError."""
        caps = AgentCapabilities(supported_tools=["   "])
        with pytest.raises(ValueError, match="cannot be empty"):
            caps.validate()

    def test_validate_tool_with_whitespace_raises(self):
        """Test that tool name with leading/trailing whitespace raises ValueError."""
        caps = AgentCapabilities(supported_tools=[" read_file"])
        with pytest.raises(ValueError, match="whitespace"):
            caps.validate()

    def test_validate_non_string_model_raises(self):
        """Test that non-string model name raises ValueError."""
        caps = AgentCapabilities(supported_models=[456])
        with pytest.raises(ValueError, match="must be string"):
            caps.validate()

    def test_validate_invalid_metadata_type_raises(self):
        """Test that non-dict metadata raises ValueError."""
        caps = AgentCapabilities(metadata="not a dict")
        with pytest.raises(ValueError, match="must be a dictionary"):
            caps.validate()


class TestAgentCapabilitiesOperations:
    """Tests for AgentCapabilities operations."""

    def test_has_tool_true(self):
        """Test has_tool returns True for existing tool."""
        caps = AgentCapabilities(supported_tools=["read_file", "write_file"])
        assert caps.has_tool("read_file") is True

    def test_has_tool_false(self):
        """Test has_tool returns False for non-existing tool."""
        caps = AgentCapabilities(supported_tools=["read_file"])
        assert caps.has_tool("nonexistent_tool") is False

    def test_has_model_true(self):
        """Test has_model returns True for existing model."""
        caps = AgentCapabilities(supported_models=["Qwen3.5-35B"])
        assert caps.has_model("Qwen3.5-35B") is True

    def test_has_model_false(self):
        """Test has_model returns False for non-existing model."""
        caps = AgentCapabilities(supported_models=["Qwen3.5-35B"])
        assert caps.has_model("Qwen3-0.6B") is False

    def test_add_tool(self):
        """Test adding a tool."""
        caps = AgentCapabilities()
        caps.add_tool("read_file")
        assert caps.has_tool("read_file") is True
        assert "read_file" in caps.supported_tools

    def test_add_duplicate_tool(self):
        """Test adding duplicate tool doesn't duplicate."""
        caps = AgentCapabilities(supported_tools=["read_file"])
        caps.add_tool("read_file")
        assert len(caps.supported_tools) == 1

    def test_add_model(self):
        """Test adding a model."""
        caps = AgentCapabilities()
        caps.add_model("Qwen3.5-35B")
        assert caps.has_model("Qwen3.5-35B") is True

    def test_remove_tool_successfully(self):
        """Test removing an existing tool."""
        caps = AgentCapabilities(supported_tools=["read_file", "write_file"])
        result = caps.remove_tool("read_file")
        assert result is True
        assert caps.has_tool("read_file") is False
        assert len(caps.supported_tools) == 1

    def test_remove_nonexistent_tool(self):
        """Test removing non-existent tool returns False."""
        caps = AgentCapabilities(supported_tools=["read_file"])
        result = caps.remove_tool("nonexistent")
        assert result is False

    def test_remove_model_successfully(self):
        """Test removing an existing model."""
        caps = AgentCapabilities(supported_models=["Qwen3.5-35B", "Qwen3-0.6B"])
        result = caps.remove_model("Qwen3.5-35B")
        assert result is True
        assert len(caps.supported_models) == 1

    def test_get_required_resources_empty(self):
        """Test get_required_resources with no resources."""
        caps = AgentCapabilities()
        resources = caps.get_required_resources()
        assert resources == []

    def test_get_required_resources_all(self):
        """Test get_required_resources with all resources."""
        caps = AgentCapabilities(
            requires_workspace=True,
            requires_internet=True,
            requires_api_keys=True,
        )
        resources = caps.get_required_resources()
        assert "workspace" in resources
        assert "internet" in resources
        assert "api_keys" in resources

    def test_get_special_capabilities_empty(self):
        """Test get_special_capabilities with no special capabilities."""
        caps = AgentCapabilities()
        special = caps.get_special_capabilities()
        assert special == []

    def test_get_special_capabilities_all(self):
        """Test get_special_capabilities with all capabilities."""
        caps = AgentCapabilities(
            supports_vision=True,
            supports_audio=True,
            supports_code_execution=True,
        )
        special = caps.get_special_capabilities()
        assert "vision" in special
        assert "audio" in special
        assert "code_execution" in special


class TestAgentCapabilitiesSerialization:
    """Tests for AgentCapabilities serialization."""

    def test_to_dict(self):
        """Test converting to dictionary."""
        caps = AgentCapabilities(
            supported_tools=["read_file"],
            max_context_tokens=16384,
            supports_vision=True,
        )
        d = caps.to_dict()
        assert d["supported_tools"] == ["read_file"]
        assert d["max_context_tokens"] == 16384
        assert d["supports_vision"] is True

    def test_from_dict(self):
        """Test creating from dictionary."""
        d = {
            "supported_tools": ["read_file", "write_file"],
            "supported_models": ["Qwen3.5-35B"],
            "max_context_tokens": 32768,
            "requires_workspace": True,
            "supports_code_execution": True,
        }
        caps = AgentCapabilities.from_dict(d)
        assert len(caps.supported_tools) == 2
        assert caps.max_context_tokens == 32768
        assert caps.supports_code_execution is True

    def test_from_dict_with_defaults(self):
        """Test creating from dictionary uses defaults."""
        d = {}
        caps = AgentCapabilities.from_dict(d)
        assert caps.supported_tools == []
        assert caps.requires_workspace is False

    def test_to_dict_returns_copy(self):
        """Test that to_dict returns a copy, not reference."""
        caps = AgentCapabilities(supported_tools=["read_file"])
        d = caps.to_dict()
        d["supported_tools"].append("write_file")
        assert "write_file" not in caps.supported_tools

    def test_equality(self):
        """Test equality comparison."""
        caps1 = AgentCapabilities(supported_tools=["read_file"])
        caps2 = AgentCapabilities(supported_tools=["read_file"])
        caps3 = AgentCapabilities(supported_tools=["write_file"])
        assert caps1 == caps2
        assert caps1 != caps3

    def test_repr(self):
        """Test string representation."""
        caps = AgentCapabilities(
            supported_tools=["tool1", "tool2"],
            max_context_tokens=16384,
            supports_vision=True,
        )
        repr_str = repr(caps)
        assert "AgentCapabilities" in repr_str
        assert "tools=2" in repr_str
        assert "special" in repr_str


# =============================================================================
# AgentProfile Tests
# =============================================================================

class TestAgentProfileCreation:
    """Tests for AgentProfile creation and initialization."""

    def test_create_default_profile(self):
        """Test creating profile with default values."""
        profile = AgentProfile()
        assert profile.name == "Unnamed Agent"
        assert profile.description == ""
        assert profile.capabilities is not None
        assert profile.tools == []
        assert profile.model_config == {}
        assert profile.version == "1.0.0"
        assert profile.metadata == {}

    def test_create_profile_with_all_fields(self):
        """Test creating profile with all fields specified."""
        caps = AgentCapabilities(supports_vision=True)
        profile = AgentProfile(
            name="Test Agent",
            description="A test agent",
            capabilities=caps,
            tools=["read_file", "write_file"],
            model_config={"model_id": "Qwen3.5-35B", "temperature": 0.7},
            version="2.0.0",
            metadata={"custom": "value"},
        )
        assert profile.name == "Test Agent"
        assert profile.description == "A test agent"
        assert profile.capabilities.supports_vision is True
        assert len(profile.tools) == 2
        assert profile.model_config["temperature"] == 0.7
        assert profile.version == "2.0.0"

    def test_post_init_creates_capabilities_if_none(self):
        """Test that __post_init__ creates default capabilities."""
        profile = AgentProfile(capabilities=None)
        assert profile.capabilities is not None
        assert isinstance(profile.capabilities, AgentCapabilities)

    def test_post_init_copies_mutable_fields(self):
        """Test that __post_init__ creates copies of mutable fields."""
        tools = ["tool1"]
        model_config = {"key": "value1"}
        metadata = {"meta": "data1"}
        profile = AgentProfile(
            tools=tools,
            model_config=model_config,
            metadata=metadata,
        )
        # Modify originals
        tools.append("tool2")
        model_config["key2"] = "value2"
        metadata["meta2"] = "data2"
        # Profile should have original values
        assert profile.tools == ["tool1"]
        assert profile.model_config == {"key": "value1"}
        assert profile.metadata == {"meta": "data1"}


class TestAgentProfileValidation:
    """Tests for AgentProfile validation."""

    def test_validate_default_profile(self):
        """Test validating default profile."""
        profile = AgentProfile()
        assert profile.validate() is True

    def test_validate_with_all_fields(self):
        """Test validating profile with all fields."""
        profile = AgentProfile(
            name="Test Agent",
            version="1.0.0",
            tools=["read_file"],
        )
        assert profile.validate() is True

    def test_validate_empty_name_raises(self):
        """Test that empty name raises ValueError."""
        profile = AgentProfile(name="")
        with pytest.raises(ValueError, match="name cannot be empty"):
            profile.validate()

    def test_validate_whitespace_name_raises(self):
        """Test that whitespace-only name raises ValueError."""
        profile = AgentProfile(name="   ")
        with pytest.raises(ValueError, match="name cannot be empty"):
            profile.validate()

    def test_validate_name_with_leading_whitespace_raises(self):
        """Test that name with leading whitespace raises ValueError."""
        profile = AgentProfile(name=" Test Agent")
        with pytest.raises(ValueError, match="whitespace"):
            profile.validate()

    def test_validate_invalid_version_format_raises(self):
        """Test that invalid version format raises ValueError."""
        profile = AgentProfile(name="Test", version="invalid")
        with pytest.raises(ValueError, match="must be numeric"):
            profile.validate()

    def test_validate_valid_versions(self):
        """Test various valid version formats."""
        valid_versions = ["1", "1.0", "1.0.0", "1.0.0.0", "1.0.0-beta", "1.*"]
        for version in valid_versions:
            profile = AgentProfile(name="Test", version=version)
            assert profile.validate() is True, f"Version {version} should be valid"

    def test_validate_duplicate_tools_raises(self):
        """Test that duplicate tools raise ValueError."""
        profile = AgentProfile(tools=["read_file", "read_file"])
        with pytest.raises(ValueError, match="duplicates"):
            profile.validate()

    def test_validate_non_string_tool_raises(self):
        """Test that non-string tool raises ValueError."""
        profile = AgentProfile(tools=[123])
        with pytest.raises(ValueError, match="must be string"):
            profile.validate()

    def test_validate_capabilities_validation_propagates(self):
        """Test that capabilities validation is called."""
        caps = AgentCapabilities(max_context_tokens=-100)
        profile = AgentProfile(name="Test", capabilities=caps)
        with pytest.raises(ValueError, match="max_context_tokens must be positive"):
            profile.validate()


class TestAgentProfileOperations:
    """Tests for AgentProfile operations."""

    def test_get_tool_list(self):
        """Test getting tool list."""
        profile = AgentProfile(tools=["read_file", "write_file"])
        tools = profile.get_tool_list()
        assert tools == ["read_file", "write_file"]

    def test_get_tool_list_returns_copy(self):
        """Test that get_tool_list returns a copy."""
        profile = AgentProfile(tools=["read_file"])
        tools = profile.get_tool_list()
        tools.append("write_file")
        assert len(profile.tools) == 1

    def test_add_tool(self):
        """Test adding a tool."""
        profile = AgentProfile()
        profile.add_tool("read_file")
        assert "read_file" in profile.tools

    def test_add_tool_updates_capabilities(self):
        """Test that add_tool also updates capabilities."""
        profile = AgentProfile()
        profile.add_tool("read_file")
        assert profile.capabilities.has_tool("read_file") is True

    def test_remove_tool_successfully(self):
        """Test removing an existing tool."""
        profile = AgentProfile(tools=["read_file", "write_file"])
        result = profile.remove_tool("read_file")
        assert result is True
        assert "read_file" not in profile.tools

    def test_remove_tool_from_capabilities(self):
        """Test that remove_tool also updates capabilities."""
        profile = AgentProfile(tools=["read_file"])
        profile.remove_tool("read_file")
        assert profile.capabilities.has_tool("read_file") is False

    def test_get_model_config(self):
        """Test getting model config."""
        profile = AgentProfile(model_config={"model_id": "Qwen3.5-35B"})
        config = profile.get_model_config()
        assert config["model_id"] == "Qwen3.5-35B"

    def test_get_model_config_returns_copy(self):
        """Test that get_model_config returns a copy."""
        profile = AgentProfile(model_config={"model_id": "Qwen3.5-35B"})
        config = profile.get_model_config()
        config["temperature"] = 0.7
        assert "temperature" not in profile.model_config

    def test_set_model_config(self):
        """Test setting model config value."""
        profile = AgentProfile()
        profile.set_model_config("temperature", 0.7)
        assert profile.model_config["temperature"] == 0.7


class TestAgentProfileSerialization:
    """Tests for AgentProfile serialization."""

    def test_to_dict(self):
        """Test converting to dictionary."""
        profile = AgentProfile(
            name="Test Agent",
            version="2.0.0",
            tools=["read_file"],
        )
        d = profile.to_dict()
        assert d["name"] == "Test Agent"
        assert d["version"] == "2.0.0"
        assert d["tools"] == ["read_file"]

    def test_from_dict(self):
        """Test creating from dictionary."""
        d = {
            "name": "Test Agent",
            "description": "A test agent",
            "tools": ["read_file", "write_file"],
            "model_config": {"model_id": "Qwen3.5-35B"},
            "version": "2.0.0",
        }
        profile = AgentProfile.from_dict(d)
        assert profile.name == "Test Agent"
        assert len(profile.tools) == 2
        assert profile.model_config["model_id"] == "Qwen3.5-35B"

    def test_from_dict_with_capabilities(self):
        """Test creating from dictionary with capabilities."""
        d = {
            "name": "Test Agent",
            "capabilities": {
                "supported_tools": ["read_file"],
                "max_context_tokens": 16384,
            },
        }
        profile = AgentProfile.from_dict(d)
        assert profile.capabilities is not None
        assert profile.capabilities.has_tool("read_file") is True

    def test_to_dict_returns_copy(self):
        """Test that to_dict returns a copy."""
        profile = AgentProfile(name="Test", tools=["read_file"])
        d = profile.to_dict()
        d["tools"].append("write_file")
        assert "write_file" not in profile.tools

    def test_from_dict_with_capabilities_instance(self):
        """Test creating from dict with Capabilities instance."""
        caps = AgentCapabilities(supported_tools=["read_file"])
        d = {"name": "Test", "capabilities": caps}
        profile = AgentProfile.from_dict(d)
        assert profile.capabilities.has_tool("read_file") is True

    def test_yaml_serialization(self):
        """Test YAML serialization."""
        profile = AgentProfile(
            name="YAML Test Agent",
            tools=["read_file", "write_file"],
            version="1.0.0",
        )
        yaml_str = profile.to_yaml()
        assert "YAML Test Agent" in yaml_str
        assert "read_file" in yaml_str

    def test_yaml_roundtrip(self):
        """Test YAML roundtrip serialization."""
        original = AgentProfile(
            name="Roundtrip Agent",
            description="Testing YAML roundtrip",
            tools=["read_file"],
            model_config={"model_id": "Qwen3.5-35B"},
            version="1.0.0",
        )
        yaml_str = original.to_yaml()
        restored = AgentProfile.from_yaml(yaml_str)
        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.tools == original.tools
        assert restored.version == original.version

    def test_from_yaml_invalid_yaml_raises(self):
        """Test that invalid YAML raises ValueError."""
        with pytest.raises(ValueError, match="must represent a dictionary"):
            AgentProfile.from_yaml("- item1\n- item2")

    def test_equality(self):
        """Test equality comparison."""
        profile1 = AgentProfile(name="Test", version="1.0.0")
        profile2 = AgentProfile(name="Test", version="1.0.0")
        profile3 = AgentProfile(name="Other", version="1.0.0")
        assert profile1 == profile2
        assert profile1 != profile3

    def test_repr(self):
        """Test string representation."""
        profile = AgentProfile(
            name="Test Agent",
            version="2.0.0",
            tools=["tool1", "tool2", "tool3"],
            model_config={"key": "value"},
        )
        repr_str = repr(profile)
        assert "AgentProfile" in repr_str
        assert "name='Test Agent'" in repr_str
        assert "version='2.0.0'" in repr_str
        assert "tools=3" in repr_str


# =============================================================================
# Thread Safety Tests
# =============================================================================

class TestCapabilitiesThreadSafety:
    """Thread safety tests for AgentCapabilities."""

    def test_concurrent_has_tool(self):
        """Test concurrent has_tool calls."""
        caps = AgentCapabilities(supported_tools=[f"tool_{i}" for i in range(100)])
        results = []
        lock = threading.Lock()

        def check_tools(agent_id):
            for i in range(50):
                result = caps.has_tool(f"tool_{i}")
                with lock:
                    results.append(result)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(check_tools, i) for i in range(20)]
            for future in as_completed(futures):
                future.result()

        assert len(results) == 1000
        assert all(results[:100])  # First 100 should be True

    def test_concurrent_add_tool(self):
        """Test concurrent add_tool calls."""
        caps = AgentCapabilities()
        errors = []
        lock = threading.Lock()

        def add_tool(tool_id):
            try:
                caps.add_tool(f"tool_{tool_id}")
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(add_tool, i) for i in range(50)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0
        assert len(caps.supported_tools) == 50


class TestProfileThreadSafety:
    """Thread safety tests for AgentProfile."""

    def test_concurrent_get_tool_list(self):
        """Test concurrent get_tool_list calls."""
        profile = AgentProfile(tools=[f"tool_{i}" for i in range(50)])
        results = []
        lock = threading.Lock()

        def get_tools(thread_id):
            for _ in range(20):
                tools = profile.get_tool_list()
                with lock:
                    results.append(len(tools))

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(get_tools, i) for i in range(10)]
            for future in as_completed(futures):
                future.result()

        assert len(results) == 200
        assert all(r == 50 for r in results)

    def test_concurrent_add_tool(self):
        """Test concurrent add_tool calls on profile."""
        profile = AgentProfile()
        errors = []
        lock = threading.Lock()

        def add_tool(tool_id):
            try:
                profile.add_tool(f"tool_{tool_id}")
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [executor.submit(add_tool, i) for i in range(30)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0
        assert len(profile.tools) == 30


# =============================================================================
# Integration Tests with Existing Agents (ISS-003)
# =============================================================================

class TestAgentProfileIntegrationWithExistingAgents:
    """Integration tests for AgentProfile with existing agent implementations."""

    def test_profile_creation_for_code_agent_pattern(self):
        """Test creating profile compatible with CodeAgent pattern."""
        profile = AgentProfile(
            id="code-agent",
            name="Code Generation Agent",
            role="Expert software developer",
            description="Intelligent autonomous code agent",
            capabilities=AgentCapabilities(
                supported_tools=[
                    "read_file",
                    "write_file",
                    "execute_python",
                    "run_tests",
                ],
                supports_code_execution=True,
                requires_workspace=True,
            ),
            tools=["read_file", "write_file", "execute_python", "run_tests"],
            model_config={"model_id": "Qwen3.5-35B-A3B-GGUF"},
            version="1.0.0",
        )
        assert profile.validate() is True
        assert profile.id == "code-agent"
        assert profile.role == "Expert software developer"
        assert profile.capabilities.supports_code_execution is True

    def test_profile_creation_for_chat_agent_pattern(self):
        """Test creating profile compatible with ChatAgent pattern."""
        profile = AgentProfile(
            id="chat-agent",
            name="Chat Agent with RAG",
            role="Document Q&A assistant",
            description="Interactive chat with RAG capabilities",
            capabilities=AgentCapabilities(
                supported_tools=[
                    "search_files",
                    "read_file",
                    "shell_command",
                ],
                max_context_tokens=32768,
            ),
            tools=["search_files", "read_file", "shell_command"],
            model_config={"model_id": "Qwen3.5-35B-A3B-GGUF"},
            version="1.0.0",
        )
        assert profile.validate() is True
        assert profile.id == "chat-agent"
        assert profile.capabilities.max_context_tokens == 32768

    def test_profile_serialization_compatibility(self):
        """Test profile serialization is compatible with existing patterns."""
        # Create profile with both old and new fields
        profile = AgentProfile(
            id="test-agent",
            name="Test Agent",
            role="Testing",
            description="Legacy description field",
            tools=["tool1"],
        )

        # Serialize to dict
        data = profile.to_dict()

        # Verify all fields present
        assert data["id"] == "test-agent"
        assert data["name"] == "Test Agent"
        assert data["role"] == "Testing"
        assert data["description"] == "Legacy description field"

        # Deserialize back
        restored = AgentProfile.from_dict(data)
        assert restored.id == profile.id
        assert restored.name == profile.name
        assert restored.role == profile.role
        assert restored.description == profile.description

    def test_yaml_roundtrip_with_existing_agents(self):
        """Test YAML roundtrip compatibility with existing agent configs."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        profile = AgentProfile(
            id="yaml-test-agent",
            name="YAML Test Agent",
            role="YAML testing",
            tools=["read_file", "write_file"],
            model_config={"model_id": "Qwen3.5-35B"},
            version="1.0.0",
        )

        # Serialize to YAML
        yaml_str = profile.to_yaml()

        # Verify key fields in YAML
        assert "id: yaml-test-agent" in yaml_str
        assert "name: YAML Test Agent" in yaml_str
        assert "role: YAML testing" in yaml_str

        # Deserialize from YAML
        restored = AgentProfile.from_yaml(yaml_str)
        assert restored.id == profile.id
        assert restored.name == profile.name
        assert restored.role == profile.role

    def test_backward_compatibility_name_only_pattern(self):
        """Test backward compatibility with name-only creation pattern."""
        # Old pattern that should still work
        profile = AgentProfile(
            name="Legacy Agent",
            description="Created with old pattern",
            tools=["tool1"],
        )
        # id should auto-generate
        assert profile.id == "unnamed-agent"  # Default value
        assert profile.name == "Legacy Agent"
        assert profile.validate() is True

    def test_spec_aligned_creation_pattern(self):
        """Test new spec-aligned creation pattern."""
        # New spec-aligned pattern
        profile = AgentProfile(
            id="spec-aligned-agent",
            name="Spec Aligned Agent",
            role="Following Phase 3 spec",
            tools=["tool1"],
        )
        assert profile.id == "spec-aligned-agent"
        assert profile.role == "Following Phase 3 spec"
        assert profile.validate() is True

    def test_profile_with_all_spec_fields(self):
        """Test profile with all spec-aligned fields."""
        profile = AgentProfile(
            id="full-spec-agent",
            name="Full Spec Agent",
            role="Complete implementation",
            description="Backward compatible description",
            capabilities=AgentCapabilities(
                supported_tools=["tool1", "tool2"],
                max_context_tokens=16384,
                supports_vision=True,
            ),
            tools=["tool1", "tool2"],
            model_config={"model_id": "Qwen3.5-35B", "temperature": 0.7},
            version="2.0.0",
            metadata={"custom": "value"},
        )

        assert profile.validate() is True
        data = profile.to_dict()

        # Verify all spec fields
        assert data["id"] == "full-spec-agent"
        assert data["name"] == "Full Spec Agent"
        assert data["role"] == "Complete implementation"
        assert data["description"] == "Backward compatible description"
        assert data["capabilities"]["max_context_tokens"] == 16384
        assert data["capabilities"]["supports_vision"] is True
