# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for ConfigSchema.

Tests the configuration schema definition and validation.
"""

import pytest
from dataclasses import dataclass, field
from typing import Optional

from gaia.config.config_schema import ConfigSchema, FieldSchema, ValidationResult, ValidationSeverity


class TestConfigSchemaInit:
    """Test ConfigSchema initialization."""

    def test_init_basic(self):
        """Test basic initialization."""
        schema = ConfigSchema("test_config")
        assert schema.name == "test_config"
        assert schema.description == ""
        assert len(schema._fields) == 0

    def test_init_with_description(self):
        """Test initialization with description."""
        schema = ConfigSchema("app_config", "Application configuration")
        assert schema.name == "app_config"
        assert schema.description == "Application configuration"


class TestConfigSchemaAddField:
    """Test ConfigSchema add_field method."""

    def test_add_field_basic(self):
        """Test adding basic field."""
        schema = ConfigSchema("test")
        schema.add_field("name", str)

        assert "name" in schema._fields
        assert schema._fields["name"].field_type == str

    def test_add_field_required(self):
        """Test adding required field."""
        schema = ConfigSchema("test")
        schema.add_field("api_key", str, required=True)

        assert schema._fields["api_key"].required is True

    def test_add_field_with_default(self):
        """Test adding field with default."""
        schema = ConfigSchema("test")
        schema.add_field("debug", bool, default=False)

        assert schema._fields["debug"].default is False

    def test_add_field_with_range(self):
        """Test adding field with range."""
        schema = ConfigSchema("test")
        schema.add_field("port", int, min_value=1, max_value=65535)

        assert schema._fields["port"].min_value == 1
        assert schema._fields["port"].max_value == 65535

    def test_add_field_with_pattern(self):
        """Test adding field with pattern."""
        schema = ConfigSchema("test")
        schema.add_field("email", str, pattern=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

        assert schema._fields["email"].pattern is not None

    def test_add_field_secret(self):
        """Test adding secret field."""
        schema = ConfigSchema("test")
        schema.add_field("password", str, secret=True)

        assert schema._fields["password"].secret is True

    def test_add_field_env_var(self):
        """Test adding field with env var override."""
        schema = ConfigSchema("test")
        schema.add_field("api_key", str, env_var="MY_API_KEY")

        assert schema._fields["api_key"].env_var == "MY_API_KEY"

    def test_add_field_method_chaining(self):
        """Test method chaining."""
        schema = ConfigSchema("test")
        result = schema.add_field("field1", str).add_field("field2", int)

        assert result is schema
        assert len(schema._fields) == 2


class TestConfigSchemaValidation:
    """Test ConfigSchema validation."""

    def test_validate_empty_config(self):
        """Test validating empty config."""
        schema = ConfigSchema("test")
        schema.add_field("name", str)

        result = schema.validate({})
        assert result.valid is True  # Optional field

    def test_validate_required_field_present(self):
        """Test validating required field is present."""
        schema = ConfigSchema("test")
        schema.add_field("name", str, required=True)

        result = schema.validate({"name": "test"})
        assert result.valid is True

    def test_validate_required_field_missing(self):
        """Test validating required field is missing."""
        schema = ConfigSchema("test")
        schema.add_field("name", str, required=True)

        result = schema.validate({})
        assert result.valid is False
        assert "required" in result.errors[0].lower()

    def test_validate_type_correct(self):
        """Test validating correct type."""
        schema = ConfigSchema("test")
        schema.add_field("count", int)

        result = schema.validate({"count": 42})
        assert result.valid is True

    def test_validate_type_wrong(self):
        """Test validating wrong type."""
        schema = ConfigSchema("test")
        schema.add_field("count", int)

        result = schema.validate({"count": "not_an_int"})
        assert result.valid is False
        assert "type" in result.errors[0].lower()

    def test_validate_range_valid(self):
        """Test validating value in range."""
        schema = ConfigSchema("test")
        schema.add_field("port", int, min_value=1, max_value=65535)

        result = schema.validate({"port": 8080})
        assert result.valid is True

    def test_validate_range_too_low(self):
        """Test validating value below range."""
        schema = ConfigSchema("test")
        schema.add_field("port", int, min_value=1, max_value=65535)

        result = schema.validate({"port": 0})
        assert result.valid is False
        assert "must be >=" in result.errors[0].lower() or "minimum" in result.errors[0].lower()

    def test_validate_range_too_high(self):
        """Test validating value above range."""
        schema = ConfigSchema("test")
        schema.add_field("port", int, min_value=1, max_value=65535)

        result = schema.validate({"port": 70000})
        assert result.valid is False

    def test_validate_pattern_valid(self):
        """Test validating string matching pattern."""
        schema = ConfigSchema("test")
        schema.add_field("email", str, pattern=r"^[a-z]+@[a-z]+\.[a-z]+$")

        result = schema.validate({"email": "test@example.com"})
        assert result.valid is True

    def test_validate_pattern_invalid(self):
        """Test validating string not matching pattern."""
        schema = ConfigSchema("test")
        schema.add_field("email", str, pattern=r"^[a-z]+@[a-z]+\.[a-z]+$")

        result = schema.validate({"email": "invalid-email"})
        assert result.valid is False

    def test_validate_choices_valid(self):
        """Test validating value in choices."""
        schema = ConfigSchema("test")
        schema.add_field("log_level", str, choices=["DEBUG", "INFO", "WARNING", "ERROR"])

        result = schema.validate({"log_level": "INFO"})
        assert result.valid is True

    def test_validate_choices_invalid(self):
        """Test validating value not in choices."""
        schema = ConfigSchema("test")
        schema.add_field("log_level", str, choices=["DEBUG", "INFO", "WARNING", "ERROR"])

        result = schema.validate({"log_level": "VERBOSE"})
        assert result.valid is False


class TestConfigSchemaNormalize:
    """Test ConfigSchema normalize method."""

    def test_normalize_adds_defaults(self):
        """Test that normalize adds default values."""
        schema = ConfigSchema("test")
        schema.add_field("debug", bool, default=False)
        schema.add_field("log_level", str, default="INFO")

        result = schema.normalize({})
        assert result["debug"] is False
        assert result["log_level"] == "INFO"

    def test_normalize_preserves_existing(self):
        """Test that normalize preserves existing values."""
        schema = ConfigSchema("test")
        schema.add_field("debug", bool, default=False)
        schema.add_field("log_level", str, default="INFO")

        result = schema.normalize({"debug": True})
        assert result["debug"] is True
        assert result["log_level"] == "INFO"


class TestConfigSchemaGetters:
    """Test ConfigSchema getter methods."""

    def test_get_field(self):
        """Test getting field by name."""
        schema = ConfigSchema("test")
        schema.add_field("name", str)

        field_schema = schema.get_field("name")
        assert field_schema is not None
        assert field_schema.field_type == str

    def test_get_field_missing(self):
        """Test getting non-existent field."""
        schema = ConfigSchema("test")
        schema.add_field("name", str)

        field_schema = schema.get_field("nonexistent")
        assert field_schema is None

    def test_get_field_names(self):
        """Test getting all field names."""
        schema = ConfigSchema("test")
        schema.add_field("name", str)
        schema.add_field("count", int)

        names = schema.get_field_names()
        assert "name" in names
        assert "count" in names

    def test_get_defaults(self):
        """Test getting all defaults."""
        schema = ConfigSchema("test")
        schema.add_field("debug", bool, default=False)
        schema.add_field("log_level", str, default="INFO")
        schema.add_field("name", str)  # No default

        defaults = schema.get_defaults()
        assert defaults["debug"] is False
        assert defaults["log_level"] == "INFO"
        assert "name" not in defaults

    def test_get_required_fields(self):
        """Test getting required fields."""
        schema = ConfigSchema("test")
        schema.add_field("name", str, required=True)
        schema.add_field("count", int)
        schema.add_field("api_key", str, required=True)

        required = schema.get_required_fields()
        assert "name" in required
        assert "api_key" in required
        assert "count" not in required

    def test_get_secret_fields(self):
        """Test getting secret fields."""
        schema = ConfigSchema("test")
        schema.add_field("username", str)
        schema.add_field("password", str, secret=True)
        schema.add_field("api_key", str, secret=True)

        secrets = schema.get_secret_fields()
        assert "password" in secrets
        assert "api_key" in secrets
        assert "username" not in secrets


class TestConfigSchemaStrictMode:
    """Test ConfigSchema strict mode."""

    def test_strict_mode_unknown_field(self):
        """Test strict mode with unknown field."""
        schema = ConfigSchema("test")
        schema.add_field("name", str)
        schema.set_strict_mode(True)

        result = schema.validate({"name": "test", "unknown_field": "value"})
        assert result.valid is True  # Unknown fields are warnings, not errors
        assert len(result.warnings) > 0

    def test_non_strict_mode_unknown_field(self):
        """Test non-strict mode with unknown field."""
        schema = ConfigSchema("test")
        schema.add_field("name", str)
        schema.set_strict_mode(False)

        result = schema.validate({"name": "test", "unknown_field": "value"})
        assert len(result.warnings) == 0


class TestConfigSchemaFromDataclass:
    """Test ConfigSchema.from_dataclass method."""

    def test_from_dataclass_basic(self):
        """Test creating schema from dataclass."""
        @dataclass
        class TestConfig:
            name: str
            count: int = 10

        schema = ConfigSchema.from_dataclass(TestConfig)

        assert "name" in schema._fields
        assert "count" in schema._fields
        assert schema._fields["count"].default == 10

    def test_from_dataclass_with_metadata(self):
        """Test creating schema from dataclass with metadata."""
        @dataclass
        class TestConfig:
            port: int = field(
                default=8080,
                metadata={"min": 1, "max": 65535}
            )
            debug: bool = field(
                default=False,
                metadata={"env_var": "DEBUG_MODE"}
            )

        schema = ConfigSchema.from_dataclass(TestConfig)

        assert schema._fields["port"].min_value == 1
        assert schema._fields["port"].max_value == 65535
        assert schema._fields["debug"].env_var == "DEBUG_MODE"

    def test_from_dataclass_required(self):
        """Test that fields without defaults are required."""
        @dataclass
        class TestConfig:
            name: str
            count: int = 10

        schema = ConfigSchema.from_dataclass(TestConfig)

        assert schema._fields["name"].required is True
        assert schema._fields["count"].required is False


class TestConfigSchemaValidationResult:
    """Test ValidationResult class."""

    def test_result_valid(self):
        """Test valid result."""
        result = ValidationResult()
        assert result.valid is True
        assert len(result.errors) == 0

    def test_add_error(self):
        """Test adding error."""
        result = ValidationResult()
        result.add_error("Test error")

        assert result.valid is False
        assert "Test error" in result.errors

    def test_add_warning(self):
        """Test adding warning."""
        result = ValidationResult()
        result.add_warning("Test warning")

        assert "Test warning" in result.warnings

    def test_merge(self):
        """Test merging results."""
        result1 = ValidationResult()
        result1.add_error("Error 1")

        result2 = ValidationResult()
        result2.add_error("Error 2")
        result2.add_warning("Warning 1")

        result1.merge(result2)

        assert len(result1.errors) == 2
        assert len(result1.warnings) == 1
        assert result1.valid is False

    def test_bool_conversion(self):
        """Test boolean conversion."""
        valid_result = ValidationResult()
        assert bool(valid_result) is True

        invalid_result = ValidationResult()
        invalid_result.add_error("Error")
        assert bool(invalid_result) is False

    def test_str_representation(self):
        """Test string representation."""
        result = ValidationResult()
        result.fields_validated = 5

        str_repr = str(result)
        assert "valid=True" in str_repr
        assert "fields=5" in str_repr


class TestConfigSchemaToDict:
    """Test ConfigSchema to_dict method."""

    def test_to_dict(self):
        """Test converting schema to dictionary."""
        schema = ConfigSchema("test", "Test description")
        schema.add_field("name", str, required=True, description="Name field")
        schema.add_field("count", int, default=10)

        result = schema.to_dict()

        assert result["name"] == "test"
        assert result["description"] == "Test description"
        assert "fields" in result
        assert "name" in result["fields"]
        assert result["fields"]["name"]["required"] is True
