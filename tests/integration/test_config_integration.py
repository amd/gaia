# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Integration tests for configuration management.

Tests config integration with file loading, env overrides, and secrets.
"""

import json
import os
import pytest
import tempfile
from pathlib import Path

from gaia.config.config_manager import ConfigManager
from gaia.config.config_schema import ConfigSchema
from gaia.config.secrets_manager import SecretsManager


@pytest.fixture
def temp_dir():
    """Provide temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestConfigIntegrationFiles:
    """Config file integration tests."""

    def test_json_yaml_combo(self, temp_dir):
        """Test loading JSON and YAML files together."""
        from gaia.config.loaders import YAML_AVAILABLE

        if not YAML_AVAILABLE:
            pytest.skip("PyYAML not available")

        # Create JSON config
        json_path = temp_dir / "base.json"
        with open(json_path, "w") as f:
            json.dump({
                "debug": False,
                "log_level": "INFO",
                "app_name": "TestApp"
            }, f)

        # Create YAML config
        yaml_path = temp_dir / "override.yaml"
        with open(yaml_path, "w") as f:
            f.write("debug: true\nlog_level: DEBUG\n")

        manager = ConfigManager()
        manager.add_json_file(str(json_path))
        manager.add_yaml_file(str(yaml_path))
        manager.load(validate=False)

        # YAML should override JSON
        assert manager.get("debug") is True
        assert manager.get("log_level") == "DEBUG"
        # JSON-only key preserved
        assert manager.get("app_name") == "TestApp"

    def test_nested_config(self, temp_dir):
        """Test nested configuration."""
        config_path = temp_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump({
                "database": {
                    "host": "localhost",
                    "port": 5432,
                    "credentials": {
                        "username": "admin",
                        "password": "secret"
                    }
                }
            }, f)

        manager = ConfigManager()
        manager.add_json_file(str(config_path))
        manager.load(validate=False)

        assert manager.get("database.host") == "localhost"
        assert manager.get("database.port") == 5432
        assert manager.get("database.credentials.username") == "admin"


class TestConfigIntegrationEnvOverrides:
    """Environment override integration tests."""

    def test_env_overrides_file(self, temp_dir):
        """Test environment overrides file values."""
        os.environ["TEST_DEBUG"] = "false"
        os.environ["TEST_PORT"] = "9000"

        config_path = temp_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump({"debug": True, "port": 8080}, f)

        manager = ConfigManager(env_prefix="TEST_")
        manager.add_json_file(str(config_path))
        manager.load(validate=False)

        # Env should override file
        assert manager.get("debug") is False
        assert manager.get("port") == 9000

    def test_env_nested_key(self, temp_dir):
        """Test env override for nested key."""
        os.environ["TEST_DATABASE_HOST"] = "prod-db.example.com"

        config_path = temp_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump({
                "database": {"host": "localhost", "port": 5432}
            }, f)

        manager = ConfigManager(env_prefix="TEST_")
        manager.add_json_file(str(config_path))
        manager.load(validate=False)

        assert manager.get("database.host") == "prod-db.example.com"


class TestConfigIntegrationSchema:
    """Schema integration tests."""

    def test_schema_validation_with_files(self, temp_dir):
        """Test schema validation with file loading."""
        schema = ConfigSchema("app")
        schema.add_field("debug", bool, default=False)
        schema.add_field("port", int, min_value=1, max_value=65535)

        config_path = temp_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump({"debug": True, "port": 8080}, f)

        manager = ConfigManager(schema=schema)
        manager.add_json_file(str(config_path))
        result = manager.load()

        assert result.valid is True

    def test_schema_validation_catches_errors(self, temp_dir):
        """Test schema catches validation errors."""
        schema = ConfigSchema("app")
        schema.add_field("port", int, min_value=1, max_value=65535)

        config_path = temp_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump({"port": 99999}, f)  # Invalid

        manager = ConfigManager(schema=schema)
        manager.add_json_file(str(config_path))
        result = manager.load()

        assert result.valid is False
        assert len(result.errors) > 0


class TestConfigIntegrationSecrets:
    """Secrets integration tests."""

    def test_secrets_with_config(self, temp_dir):
        """Test secrets manager with config manager."""
        os.environ["APP_API_KEY"] = "secret_key_123"

        # Setup secrets
        secrets = SecretsManager()
        secrets.register("api_key", env_var="APP_API_KEY")

        # Setup config
        config_path = temp_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump({"app_name": "TestApp"}, f)

        manager = ConfigManager(env_prefix="APP_")
        manager.add_json_file(str(config_path))
        manager.load(validate=False)

        # Get secret
        api_key = secrets.get("api_key")
        assert api_key == "secret_key_123"

        # Get config
        app_name = manager.get("app_name")
        assert app_name == "TestApp"

    def test_secret_as_config_field(self, temp_dir):
        """Test secret field in config schema."""
        os.environ["APP_DB_PASSWORD"] = "db_secret"

        schema = ConfigSchema("app")
        schema.add_field("db_password", str, secret=True, env_var="APP_DB_PASSWORD")

        manager = ConfigManager(schema=schema, env_prefix="APP_")
        manager.load()

        # Get secret value
        password = manager.get("db_password")
        assert password == "db_secret"


class TestConfigIntegrationDefaults:
    """Defaults integration tests."""

    def test_schema_defaults_applied(self, temp_dir):
        """Test schema defaults are applied."""
        schema = ConfigSchema("app")
        schema.add_field("debug", bool, default=False)
        schema.add_field("log_level", str, default="INFO")
        schema.add_field("port", int, default=8080)

        # Empty config file
        config_path = temp_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump({}, f)

        manager = ConfigManager(schema=schema)
        manager.add_json_file(str(config_path))
        manager.load()

        assert manager.get("debug") is False
        assert manager.get("log_level") == "INFO"
        assert manager.get("port") == 8080

    def test_file_overrides_defaults(self, temp_dir):
        """Test file values override defaults."""
        schema = ConfigSchema("app")
        schema.add_field("debug", bool, default=False)

        config_path = temp_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump({"debug": True}, f)

        manager = ConfigManager(schema=schema)
        manager.add_json_file(str(config_path))
        manager.load()

        # File value should be used
        assert manager.get("debug") is True


class TestConfigIntegrationProgrammatic:
    """Programmatic config integration tests."""

    def test_programmatic_overrides_all(self, temp_dir):
        """Test programmatic values override everything."""
        os.environ["TEST_DEBUG"] = "false"

        config_path = temp_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump({"debug": True}, f)

        manager = ConfigManager(env_prefix="TEST_")
        manager.add_json_file(str(config_path))
        manager.load(validate=False)

        # File value (overridden by env)
        assert manager.get("debug") is False

        # Programmatic override
        manager.set("debug", True)
        assert manager.get("debug") is True


class TestConfigIntegrationAllSources:
    """Test all config sources together."""

    def test_full_priority_chain(self, temp_dir):
        """Test full priority chain: defaults < files < env < programmatic."""
        os.environ["FULL_TEST_VALUE"] = "from_env"

        schema = ConfigSchema("test")
        schema.add_field("value", str, default="from_default")

        # Base file
        base_path = temp_dir / "base.json"
        with open(base_path, "w") as f:
            json.dump({"value": "from_base"}, f)

        # Override file
        override_path = temp_dir / "override.json"
        with open(override_path, "w") as f:
            json.dump({"value": "from_override"}, f)

        manager = ConfigManager(schema=schema, env_prefix="FULL_TEST_")
        manager.add_json_file(str(base_path))
        manager.add_json_file(str(override_path))
        manager.load()

        # Env overrides files
        assert manager.get("value") == "from_env"

        # Programmatic overrides env
        manager.set("value", "from_programmatic")
        assert manager.get("value") == "from_programmatic"
