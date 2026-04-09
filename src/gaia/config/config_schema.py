# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Configuration schema definition and validation for GAIA.

Provides type-safe configuration with validation rules,
supporting required fields, type checking, range validation,
and environment variable overrides.

Example:
    from gaia.config import ConfigSchema

    schema = ConfigSchema("agent_config")
    schema.add_field("model_id", str, required=True)
    schema.add_field("max_tokens", int, default=4096, min=1, max=32768)
    schema.add_field("temperature", float, default=0.7, min=0.0, max=2.0)
    schema.add_field("api_key", str, secret=True, env_var="GAIA_API_KEY")

    config = {"model_id": "Qwen3.5-35B", "temperature": 0.5}
    result = schema.validate(config)
    if not result.valid:
        print(f"Validation errors: {result.errors}")
"""

import logging
import re
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Type, Union

from gaia.config.validators import (
    validate_type,
    validate_range,
    validate_pattern,
    validate_required,
    validate_one_of,
)

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    """
    Severity levels for validation results.

    Attributes:
        ERROR: Blocks configuration usage
        WARNING: Logged but allowed
    """

    ERROR = "error"
    WARNING = "warning"


@dataclass
class FieldSchema:
    """
    Schema definition for a single configuration field.

    Attributes:
        name: Field name
        field_type: Expected Python type(s)
        required: Whether field is mandatory
        default: Default value if not provided
        validators: List of validator callables
        description: Human-readable description
        env_var: Environment variable name for override
        secret: Whether field contains sensitive data
        min_value: Minimum numeric value (if numeric type)
        max_value: Maximum numeric value (if numeric type)
        pattern: Regex pattern for string fields
        choices: Allowed values (enumeration)
    """

    name: str
    field_type: Union[Type, List[Type]]
    required: bool = False
    default: Any = None
    validators: List[Callable[[Any], bool]] = field(default_factory=list)
    description: str = ""
    env_var: Optional[str] = None
    secret: bool = False
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    pattern: Optional[str] = None
    choices: Optional[List[Any]] = None

    def validate(self, value: Any) -> List[str]:
        """
        Validate a value against this field's schema.

        Args:
            value: Value to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Check required
        if value is None:
            if self.required:
                errors.append(f"Field '{self.name}' is required")
            return errors

        # Check type
        if not validate_type(value, self.field_type):
            expected = (
                self.field_type.__name__
                if isinstance(self.field_type, type)
                else " | ".join(t.__name__ for t in self.field_type)
            )
            errors.append(
                f"Field '{self.name}' expected type {expected}, got {type(value).__name__}"
            )
            return errors  # Skip further validation if type is wrong

        # Check range (for numeric types)
        if self.min_value is not None or self.max_value is not None:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not validate_range(
                    value,
                    min_value=self.min_value,
                    max_value=self.max_value,
                ):
                    range_str = []
                    if self.min_value is not None:
                        range_str.append(f">= {self.min_value}")
                    if self.max_value is not None:
                        range_str.append(f"<= {self.max_value}")
                    errors.append(
                        f"Field '{self.name}' value {value} must be {' and '.join(range_str)}"
                    )

        # Check pattern (for strings)
        if self.pattern is not None and isinstance(value, str):
            if not validate_pattern(value, self.pattern):
                errors.append(
                    f"Field '{self.name}' value '{value}' does not match pattern '{self.pattern}'"
                )

        # Check choices
        if self.choices is not None:
            if not validate_one_of(value, self.choices):
                errors.append(
                    f"Field '{self.name}' value '{value}' must be one of: {self.choices}"
                )

        # Check custom validators
        for validator in self.validators:
            try:
                if not validator(value):
                    errors.append(f"Field '{self.name}' failed custom validation")
            except Exception as e:
                errors.append(f"Field '{self.name}' validator error: {e}")

        return errors


@dataclass
class ValidationResult:
    """
    Result of configuration validation.

    Attributes:
        valid: Overall validation status
        errors: List of error messages
        warnings: List of warning messages
        fields_validated: Count of validated fields

    Example:
        >>> result = schema.validate(config)
        >>> if result.valid:
        ...     print("Configuration is valid")
        >>> else:
        ...     for error in result.errors:
        ...         print(f"Error: {error}")
    """

    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    fields_validated: int = 0

    def add_error(self, message: str) -> None:
        """Add an error message."""
        self.errors.append(message)
        self.valid = False

    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)

    def merge(self, other: "ValidationResult") -> None:
        """
        Merge another validation result into this one.

        Args:
            other: ValidationResult to merge
        """
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.fields_validated += other.fields_validated
        if not other.valid:
            self.valid = False

    def __bool__(self) -> bool:
        """Return True if validation passed."""
        return self.valid

    def __str__(self) -> str:
        """Return human-readable summary."""
        if self.valid:
            return f"ValidationResult(valid=True, fields={self.fields_validated})"
        return f"ValidationResult(valid=False, errors={len(self.errors)}, warnings={len(self.warnings)})"


class ConfigSchema:
    """
    Configuration schema definition and validation.

    Provides type-safe configuration with validation rules.

    Example:
        >>> schema = ConfigSchema("agent_config")
        >>> schema.add_field("model_id", str, required=True)
        >>> schema.add_field("max_tokens", int, default=4096, min=1, max=32768)
        >>> schema.add_field("temperature", float, default=0.7, min=0.0, max=2.0)
        >>> schema.add_field("api_key", str, secret=True, env_var="GAIA_API_KEY")
        >>>
        >>> config = {"model_id": "Qwen3.5-35B", "temperature": 0.5}
        >>> result = schema.validate(config)
        >>> if not result.valid:
        ...     print(f"Validation errors: {result.errors}")
    """

    def __init__(self, name: str, description: str = ""):
        """
        Initialize configuration schema.

        Args:
            name: Schema name for identification
            description: Human-readable description

        Example:
            >>> schema = ConfigSchema(
            ...     "llm_config",
            ...     "Configuration for LLM model settings"
            ... )
        """
        self.name = name
        self.description = description
        self._fields: Dict[str, FieldSchema] = {}
        self._strict_mode = False  # If True, unknown fields cause errors

    def add_field(
        self,
        name: str,
        field_type: Union[Type, List[Type]],
        required: bool = False,
        default: Any = None,
        min_value: Optional[Union[int, float]] = None,
        max_value: Optional[Union[int, float]] = None,
        pattern: Optional[str] = None,
        choices: Optional[List[Any]] = None,
        env_var: Optional[str] = None,
        secret: bool = False,
        description: str = "",
        validators: Optional[List[Callable[[Any], bool]]] = None,
    ) -> "ConfigSchema":
        """
        Add field to schema with validation rules.

        Args:
            name: Field name
            field_type: Expected Python type(s)
            required: Whether field is mandatory (default: False)
            default: Default value if not provided (default: None)
            min_value: Minimum numeric value (optional)
            max_value: Maximum numeric value (optional)
            pattern: Regex pattern for string fields (optional)
            choices: List of allowed values (optional)
            env_var: Environment variable name for override (optional)
            secret: Whether field contains sensitive data (default: False)
            description: Human-readable description (optional)
            validators: List of custom validator functions (optional)

        Returns:
            Self for method chaining

        Example:
            >>> schema.add_field(
            ...     "port",
            ...     int,
            ...     default=8080,
            ...     min_value=1,
            ...     max_value=65535,
            ...     description="Server port number"
            ... )
        """
        self._fields[name] = FieldSchema(
            name=name,
            field_type=field_type,
            required=required,
            default=default,
            validators=validators or [],
            description=description,
            env_var=env_var,
            secret=secret,
            min_value=min_value,
            max_value=max_value,
            pattern=pattern,
            choices=choices,
        )
        return self

    def get_field(self, name: str) -> Optional[FieldSchema]:
        """
        Get field schema by name.

        Args:
            name: Field name

        Returns:
            FieldSchema if found, None otherwise
        """
        return self._fields.get(name)

    def get_field_names(self) -> List[str]:
        """
        Get list of all field names.

        Returns:
            List of field names
        """
        return list(self._fields.keys())

    def set_strict_mode(self, strict: bool = True) -> "ConfigSchema":
        """
        Enable/disable strict mode for unknown fields.

        In strict mode, unknown fields cause validation errors.

        Args:
            strict: Enable strict mode (default: True)

        Returns:
            Self for method chaining
        """
        self._strict_mode = strict
        return self

    def validate(self, config: Dict[str, Any]) -> ValidationResult:
        """
        Validate configuration against schema.

        Performs:
        1. Required field check
        2. Type validation
        3. Range/constraint validation
        4. Custom validator execution
        5. Unknown field check (if strict mode)

        Args:
            config: Configuration dictionary to validate

        Returns:
            ValidationResult with errors/warnings

        Example:
            >>> config = {"model_id": "Qwen3.5-35B", "temperature": 0.5}
            >>> result = schema.validate(config)
            >>> if not result.valid:
            ...     for error in result.errors:
            ...         print(error)
        """
        result = ValidationResult()
        validated_fields = set()

        # Validate each defined field
        for name, field_schema in self._fields.items():
            value = config.get(name)

            # If value is None and default exists, use default for validation
            if value is None and field_schema.default is not None:
                value = field_schema.default

            # Validate the field
            field_errors = field_schema.validate(value)
            for error in field_errors:
                result.add_error(error)

            validated_fields.add(name)

        result.fields_validated = len(validated_fields)

        # Check for unknown fields in strict mode
        if self._strict_mode:
            config_keys = set(config.keys())
            schema_keys = set(self._fields.keys())
            unknown = config_keys - schema_keys

            for key in unknown:
                result.add_warning(f"Unknown configuration field: '{key}'")

        return result

    def normalize(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize configuration with defaults.

        Adds default values for missing optional fields.

        Args:
            config: Configuration dictionary

        Returns:
            Normalized configuration with defaults applied

        Example:
            >>> config = {"model_id": "Qwen3.5-35B"}
            >>> normalized = schema.normalize(config)
            >>> print(normalized)
            {'model_id': 'Qwen3.5-35B', 'max_tokens': 4096, 'temperature': 0.7}
        """
        normalized = dict(config)

        for name, field_schema in self._fields.items():
            if name not in normalized and field_schema.default is not None:
                normalized[name] = field_schema.default

        return normalized

    def get_defaults(self) -> Dict[str, Any]:
        """
        Get all default values from schema.

        Returns:
            Dictionary of field names to default values

        Example:
            >>> defaults = schema.get_defaults()
            >>> print(defaults)
            {'max_tokens': 4096, 'temperature': 0.7}
        """
        defaults = {}

        for name, field_schema in self._fields.items():
            if field_schema.default is not None:
                defaults[name] = field_schema.default

        return defaults

    def get_required_fields(self) -> List[str]:
        """
        Get list of required field names.

        Returns:
            List of required field names
        """
        return [name for name, fs in self._fields.items() if fs.required]

    def get_secret_fields(self) -> List[str]:
        """
        Get list of secret field names.

        Returns:
            List of field names marked as secret
        """
        return [name for name, fs in self._fields.items() if fs.secret]

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert schema to dictionary representation.

        Returns:
            Dictionary with schema metadata and fields

        Example:
            >>> schema_dict = schema.to_dict()
            >>> print(schema_dict["name"])
            'agent_config'
        """
        return {
            "name": self.name,
            "description": self.description,
            "strict_mode": self._strict_mode,
            "fields": {
                name: {
                    "type": (
                        fs.field_type.__name__
                        if isinstance(fs.field_type, type)
                        else [t.__name__ for t in fs.field_type]
                    ),
                    "required": fs.required,
                    "default": fs.default,
                    "description": fs.description,
                    "env_var": fs.env_var,
                    "secret": fs.secret,
                    "min_value": fs.min_value,
                    "max_value": fs.max_value,
                    "pattern": fs.pattern,
                    "choices": fs.choices,
                }
                for name, fs in self._fields.items()
            },
        }

    @classmethod
    def from_dataclass(cls, schema_class: Type) -> "ConfigSchema":
        """
        Create schema from dataclass definition.

        Uses dataclass field metadata for validation rules.

        Args:
            schema_class: Dataclass class to convert

        Returns:
            ConfigSchema instance

        Example:
            >>> @dataclass
            ... class AgentConfig:
            ...     model_id: str
            ...     max_tokens: int = field(
            ...         default=4096,
            ...         metadata={"min": 1, "max": 32768}
            ...     )
            ...     temperature: float = field(
            ...         default=0.7,
            ...         metadata={"min": 0.0, "max": 2.0}
            ...     )
            >>>
            >>> schema = ConfigSchema.from_dataclass(AgentConfig)
        """
        if not is_dataclass(schema_class):
            raise TypeError("from_dataclass requires a dataclass type")

        schema_name = schema_class.__name__
        schema = cls(name=schema_name)

        for f in fields(schema_class):
            # Check if there's a default
            has_default = f.default is not f.default_factory

            # Extract metadata
            metadata = f.metadata

            schema.add_field(
                name=f.name,
                field_type=f.type,
                required=not has_default,
                default=f.default if has_default else f.default_factory
                if has_default and f.default_factory is not None
                else None,
                min_value=metadata.get("min"),
                max_value=metadata.get("max"),
                pattern=metadata.get("pattern"),
                choices=metadata.get("choices"),
                env_var=metadata.get("env_var"),
                secret=metadata.get("secret", False),
                description=metadata.get("description", ""),
            )

        return schema

    def __repr__(self) -> str:
        """Return string representation."""
        return f"ConfigSchema(name={self.name!r}, fields={len(self._fields)})"

    def __len__(self) -> int:
        """Return number of fields."""
        return len(self._fields)
