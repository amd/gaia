# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Configuration Module - Enterprise-grade configuration management.

This module provides comprehensive configuration infrastructure with:
- Schema-based validation
- Hierarchical configuration management
- Hot-reload support
- Secure secrets handling
- Multiple file format support (JSON, YAML)
- Environment variable overrides

Example:
    from gaia.config import ConfigManager, ConfigSchema, SecretsManager

    # Define schema
    schema = ConfigSchema("app_config")
    schema.add_field("debug", bool, default=False)
    schema.add_field("log_level", str, default="INFO")
    schema.add_field("database_url", str, required=True, secret=True)

    # Create manager with hot-reload
    manager = ConfigManager(schema=schema)
    manager.add_json_file("./config/base.json")
    manager.add_json_file("./config/local.json")
    manager.enable_hot_reload()
    manager.load()

    # Access values
    debug = manager.get("debug")
    db_url = manager.get("database_url")

    # Manage secrets
    secrets = SecretsManager()
    secrets.register("api_key", env_var="GAIA_API_KEY", required=True)
    api_key = secrets.get("api_key")
"""

from gaia.config.config_schema import (
    ConfigSchema,
    FieldSchema,
    ValidationResult,
    ValidationSeverity,
)

from gaia.config.config_manager import ConfigManager

from gaia.config.secrets_manager import (
    SecretsManager,
    SecretEntry,
    get_secrets_manager,
    register_secret,
    get_secret,
)

from gaia.config.validators import (
    # Type validators
    validate_type,
    validate_type_strict,
    get_type_name,
    validate_type_with_coercion,
    validate_collection_types,
    # Range/constraint validators
    validate_range,
    validate_pattern,
    validate_length,
    validate_one_of,
    validate_not_empty,
    validate_predicate,
    validate_regex_full_match,
    validate_numeric_constraints,
    # Required field validators
    validate_required,
    validate_required_if,
    validate_required_with,
    validate_at_least_one,
    validate_exactly_one,
    validate_mutually_exclusive,
    validate_required_if_true,
    validate_not_blank,
)

from gaia.config.loaders import (
    # JSON loader
    JSONLoader,
    # YAML loader
    YAMLLoader,
    load_yaml,
    save_yaml,
    YAML_AVAILABLE,
    # Environment loader
    EnvLoader,
    load_env,
    get_env,
    # Hot-reload loader
    FileWatcherLoader,
    ConfigHotReload,
)

__all__ = [
    # Schema
    "ConfigSchema",
    "FieldSchema",
    "ValidationResult",
    "ValidationSeverity",
    # Manager
    "ConfigManager",
    # Secrets
    "SecretsManager",
    "SecretEntry",
    "get_secrets_manager",
    "register_secret",
    "get_secret",
    # Validators
    "validate_type",
    "validate_type_strict",
    "get_type_name",
    "validate_type_with_coercion",
    "validate_collection_types",
    "validate_range",
    "validate_pattern",
    "validate_length",
    "validate_one_of",
    "validate_not_empty",
    "validate_predicate",
    "validate_regex_full_match",
    "validate_numeric_constraints",
    "validate_required",
    "validate_required_if",
    "validate_required_with",
    "validate_at_least_one",
    "validate_exactly_one",
    "validate_mutually_exclusive",
    "validate_required_if_true",
    "validate_not_blank",
    # Loaders
    "JSONLoader",
    "YAMLLoader",
    "load_yaml",
    "save_yaml",
    "YAML_AVAILABLE",
    "EnvLoader",
    "load_env",
    "get_env",
    "FileWatcherLoader",
    "ConfigHotReload",
]

__version__ = "1.0.0"


def get_version() -> str:
    """Return module version."""
    return __version__
