# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for ConfigManager.

Tests the hierarchical configuration management system.
"""

import json
import os
import pytest
import tempfile
from pathlib import Path
from typing import Any, Dict

from gaia.config.config_manager import ConfigManager
from gaia.config.config_schema import ConfigSchema


@pytest.fixture
def temp_dir():
    """Provide temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_schema():
    """Provide sample schema for tests."""
    schema = ConfigSchema("test_config")
    schema.add_field("debug", bool, default=False)
    schema.add_field("log_level", str, default="INFO")
    schema.add_field("port", int, default=8080, min_value=1, max_value=65535)
    return schema


@pytest.fixture
def sample_json_config(temp_dir):
    """Provide sample JSON config file."""
    config = {
        "debug": True,
        "log_level": "DEBUG",
        "database": {
            "host": "localhost",
            "port": 5432
        }
    }

    config_path = temp_dir / "config.json"
    with open(config_path, "w") as f:
        json.dump(config, f)

    return config_path


class TestConfigManagerInit:
    """Test ConfigManager initialization."""

    def test_init_default(self):
        """Test default initialization."""
        manager = ConfigManager()
        assert manager.schema is None
        assert manager.cache is None
        assert manager.enable_env_overrides is True

    def test_init_with_schema(self, sample_schema):
        """Test initialization with schema."""
        manager = ConfigManager(schema=sample_schema)
        assert manager.schema is sample_schema

    def test_init_disable_env_overrides(self):
        """Test initialization with env overrides disabled."""
        manager = ConfigManager(enable_env_overrides=False)
        assert manager.enable_env_overrides is False


class TestConfigManagerLoadFiles:
    """Test ConfigManager file loading."""

    def test_add_json_file(self, sample_json_config):
        """Test adding JSON file."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))

        assert len(manager._loaders) == 1
        assert str(sample_json_config) in manager._file_paths

    def test_add_json_file_required(self, temp_dir):
        """Test adding required JSON file that doesn't exist."""
        manager = ConfigManager()
        missing_path = temp_dir / "missing.json"

        with pytest.raises(FileNotFoundError):
            manager.add_json_file(str(missing_path), required=True)

    def test_add_json_file_optional(self, temp_dir):
        """Test adding optional JSON file that doesn't exist."""
        manager = ConfigManager()
        missing_path = temp_dir / "missing.json"

        # Should not raise
        manager.add_json_file(str(missing_path), required=False)
        assert len(manager._loaders) == 1

    def test_add_yaml_file(self, temp_dir):
        """Test adding YAML file."""
        from gaia.config.loaders import YAML_AVAILABLE

        if not YAML_AVAILABLE:
            pytest.skip("PyYAML not available")

        yaml_path = temp_dir / "config.yaml"
        with open(yaml_path, "w") as f:
            f.write("debug: true\nlog_level: DEBUG\n")

        manager = ConfigManager()
        manager.add_yaml_file(str(yaml_path))

        assert len(manager._loaders) == 1


class TestConfigManagerLoad:
    """Test ConfigManager load method."""

    def test_load_json(self, sample_json_config):
        """Test loading JSON config."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        result = manager.load(validate=False)

        assert manager._loaded is True
        assert manager.get("debug") is True

    def test_load_with_schema(self, sample_json_config, sample_schema):
        """Test loading with schema validation."""
        manager = ConfigManager(schema=sample_schema)
        manager.add_json_file(str(sample_json_config))
        result = manager.load()

        assert result.valid is True

    def test_load_invalid_schema(self, temp_dir):
        """Test loading with invalid config."""
        config_path = temp_dir / "invalid.json"
        with open(config_path, "w") as f:
            json.dump({"port": 99999}, f)  # Invalid: exceeds max

        schema = ConfigSchema("test")
        schema.add_field("port", int, min_value=1, max_value=65535)

        manager = ConfigManager(schema=schema)
        manager.add_json_file(str(config_path))
        result = manager.load()

        assert result.valid is False

    def test_load_multiple_files(self, temp_dir):
        """Test loading multiple config files."""
        base_path = temp_dir / "base.json"
        with open(base_path, "w") as f:
            json.dump({"debug": False, "log_level": "INFO"}, f)

        override_path = temp_dir / "override.json"
        with open(override_path, "w") as f:
            json.dump({"debug": True}, f)

        manager = ConfigManager()
        manager.add_json_file(str(base_path))
        manager.add_json_file(str(override_path))
        manager.load(validate=False)

        # Override file should take precedence
        assert manager.get("debug") is True
        assert manager.get("log_level") == "INFO"


class TestConfigManagerGet:
    """Test ConfigManager get methods."""

    def test_get_basic(self, sample_json_config):
        """Test basic get."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        assert manager.get("debug") is True

    def test_get_nested(self, sample_json_config):
        """Test nested get with dot notation."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        assert manager.get("database.host") == "localhost"

    def test_get_default(self, sample_json_config):
        """Test get with default value."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        result = manager.get("nonexistent", default="default_value")
        assert result == "default_value"

    def test_get_typed(self, sample_json_config):
        """Test get_typed method."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        port = manager.get_typed("database.port", int)
        assert port == 5432

    def test_get_typed_conversion(self, sample_json_config):
        """Test get_typed with type conversion."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        # String to int conversion
        os.environ["TEST_PORT"] = "9000"
        manager.enable_env_overrides = True
        manager.env_prefix = "TEST_"
        manager.load(validate=False)

        port = manager.get_typed("port", int, default=8080)
        assert port == 9000


class TestConfigManagerSet:
    """Test ConfigManager set method."""

    def test_set_basic(self):
        """Test basic set."""
        manager = ConfigManager()
        manager.set("debug", True)
        manager.set("log_level", "DEBUG")

        assert manager.get("debug") is True
        assert manager.get("log_level") == "DEBUG"

    def test_set_nested(self):
        """Test nested set with dot notation."""
        manager = ConfigManager()
        manager.set("database.host", "localhost")
        manager.set("database.port", 5432)

        assert manager.get("database.host") == "localhost"
        assert manager.get("database.port") == 5432

    def test_set_overrides_file(self, sample_json_config):
        """Test that set values override file values."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        # File has debug=True
        assert manager.get("debug") is True

        # Set overrides
        manager.set("debug", False)
        assert manager.get("debug") is False


class TestConfigManagerEnvOverrides:
    """Test ConfigManager environment variable overrides."""

    def test_env_override(self, sample_json_config):
        """Test environment variable override."""
        os.environ["GAIA_DEBUG"] = "false"

        manager = ConfigManager(env_prefix="GAIA_")
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        # Env var should override file value (debug=true in file)
        assert manager.get("debug") is False

    def test_env_override_disabled(self, sample_json_config):
        """Test with env overrides disabled."""
        os.environ["GAIA_DEBUG"] = "false"

        manager = ConfigManager(env_prefix="GAIA_", enable_env_overrides=False)
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        # File value should be used
        assert manager.get("debug") is True


class TestConfigManagerGetAll:
    """Test ConfigManager get_all method."""

    def test_get_all(self, sample_json_config):
        """Test getting all config."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        config = manager.get_all()
        assert "debug" in config
        assert "log_level" in config


class TestConfigManagerHotReload:
    """Test ConfigManager hot-reload functionality."""

    def test_enable_hot_reload(self, sample_json_config):
        """Test enabling hot-reload."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))

        callback_called = []

        def callback(config):
            callback_called.append(config)

        manager.enable_hot_reload(callback=callback, debounce_seconds=0.1)

        assert manager._watcher is not None
        assert len(manager._callbacks) == 1

        manager._watcher.stop()  # Clean up


class TestConfigManagerReload:
    """Test ConfigManager reload method."""

    @pytest.mark.asyncio
    async def test_reload(self, sample_json_config):
        """Test manual reload."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        # Modify file
        with open(sample_json_config, "w") as f:
            json.dump({"debug": False, "log_level": "WARNING"}, f)

        result = await manager.reload()

        assert manager.get("debug") is False


class TestConfigManagerValidate:
    """Test ConfigManager validate method."""

    def test_validate(self, sample_json_config, sample_schema):
        """Test validation."""
        manager = ConfigManager(schema=sample_schema)
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        result = manager.validate()
        assert result.valid is True

    def test_validate_no_schema(self, sample_json_config):
        """Test validation without schema."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        result = manager.validate()
        assert result.valid is True  # No schema = always valid


class TestConfigManagerClear:
    """Test ConfigManager clear method."""

    def test_clear(self, sample_json_config):
        """Test clearing config."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        assert manager.is_loaded() is True

        manager.clear()

        assert manager.is_loaded() is False
        assert manager.get("debug") is None


class TestConfigManagerCallbacks:
    """Test ConfigManager callback functionality."""

    def test_on_reload(self, sample_json_config):
        """Test on_reload callback."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))

        called = []

        def callback(config):
            called.append(config)

        manager.on_reload(callback)
        manager.load(validate=False)

        # Callback not called on initial load
        assert len(called) == 0


class TestConfigManagerFileOperations:
    """Test ConfigManager file operations."""

    def test_get_file_paths(self, sample_json_config):
        """Test getting file paths."""
        manager = ConfigManager()
        manager.add_json_file(str(sample_json_config))

        paths = manager.get_file_paths()
        assert str(sample_json_config) in paths

    def test_is_loaded(self, sample_json_config):
        """Test is_loaded check."""
        manager = ConfigManager()
        assert manager.is_loaded() is False

        manager.add_json_file(str(sample_json_config))
        manager.load(validate=False)

        assert manager.is_loaded() is True


class TestConfigManagerAllFieldTypes:
    """Test ConfigManager with all field types."""

    def test_all_types_validation(self):
        """Test all field types are validated correctly."""
        schema = ConfigSchema("test")
        schema.add_field("str_field", str, required=True)
        schema.add_field("int_field", int, min_value=0, max_value=100)
        schema.add_field("float_field", float, min_value=0.0, max_value=1.0)
        schema.add_field("bool_field", bool, default=False)
        schema.add_field("list_field", list)

        config = {
            "str_field": "hello",
            "int_field": 50,
            "float_field": 0.5,
            "bool_field": True,
            "list_field": [1, 2, 3],
        }

        result = schema.validate(config)
        assert result.valid, f"Valid config rejected: {result.errors}"
