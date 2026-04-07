# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Required field validation utilities for GAIA configuration.

Provides validation for required fields with support for
conditional requirements and default value handling.

Example:
    from gaia.config.validators import validate_required

    # Basic required field validation
    error = validate_required("value", "field_name")
    assert error is None  # Field present

    error = validate_required(None, "field_name")
    assert error == "Field 'field_name' is required"
"""

from typing import Any, Callable, Dict, List, Optional, Union


def validate_required(
    value: Any,
    field_name: str,
    allow_empty: bool = False,
) -> Optional[str]:
    """
    Validate required field is present.

    Returns error message if missing, None if valid.

    Args:
        value: Field value to validate
        field_name: Name of the field for error messages
        allow_empty: Whether empty values are allowed (default: False)

    Returns:
        Error message string if invalid, None if valid

    Example:
        >>> validate_required("hello", "name")
        None
        >>> validate_required(None, "name")
        "Field 'name' is required"
        >>> validate_required("", "name", allow_empty=False)
        "Field 'name' cannot be empty"
    """
    if value is None:
        return f"Field '{field_name}' is required"

    if not allow_empty:
        if isinstance(value, str) and len(value.strip()) == 0:
            return f"Field '{field_name}' cannot be empty"

        if isinstance(value, (list, tuple, dict)) and len(value) == 0:
            return f"Field '{field_name}' cannot be empty"

    return None


def validate_required_if(
    value: Any,
    field_name: str,
    condition_value: Any,
    other_field_value: Any,
    allow_empty: bool = False,
) -> Optional[str]:
    """
    Validate field is required based on another field's value.

    Args:
        value: Field value to validate
        field_name: Name of this field for error messages
        condition_value: The value that triggers the requirement
        other_field_value: The value of the other field
        allow_empty: Whether empty values are allowed

    Returns:
        Error message if invalid, None if valid

    Example:
        >>> # password_confirm is required if password is set
        >>> validate_required_if(
        ...     value=None,
        ...     field_name="password_confirm",
        ...     condition_value=None,  # Trigger when password is not None
        ...     other_field_value="secret123",
        ... )
        "Field 'password_confirm' is required when password is set"
    """
    # Check if condition is met
    if condition_value is None:
        # Required if other field is NOT None
        if other_field_value is None:
            return None
    else:
        # Required if other field EQUALS condition_value
        if other_field_value != condition_value:
            return None

    # Condition met - field is required
    if value is None:
        return f"Field '{field_name}' is required"

    if not allow_empty:
        if isinstance(value, str) and len(value.strip()) == 0:
            return f"Field '{field_name}' cannot be empty"

    return None


def validate_required_with(
    values: Dict[str, Any],
    required_fields: List[str],
    allow_empty: bool = False,
) -> List[str]:
    """
    Validate multiple required fields in a configuration dict.

    Args:
        values: Configuration dictionary
        required_fields: List of required field names
        allow_empty: Whether empty values are allowed

    Returns:
        List of error messages for missing required fields

    Example:
        >>> config = {"host": "localhost", "port": 5432}
        >>> errors = validate_required_with(
        ...     config,
        ...     ["host", "port", "database"]
        ... )
        >>> print(errors)
        ["Field 'database' is required"]
    """
    errors = []

    for field_name in required_fields:
        value = values.get(field_name)
        error = validate_required(value, field_name, allow_empty)
        if error:
            errors.append(error)

    return errors


def validate_at_least_one(
    values: Dict[str, Any],
    field_names: List[str],
    allow_empty: bool = False,
) -> Optional[str]:
    """
    Validate that at least one of the specified fields is present.

    Args:
        values: Configuration dictionary
        field_names: List of field names where at least one must be present
        allow_empty: Whether empty values count as present

    Returns:
        Error message if none present, None if at least one is valid

    Example:
        >>> config = {"email": None, "phone": None}
        >>> error = validate_at_least_one(config, ["email", "phone"])
        >>> print(error)
        "At least one of 'email, phone' is required"
    """
    for field_name in field_names:
        value = values.get(field_name)
        error = validate_required(value, field_name, allow_empty)
        if error is None:
            return None

    fields_str = ", ".join(f"'{f}'" for f in field_names)
    return f"At least one of {fields_str} is required"


def validate_exactly_one(
    values: Dict[str, Any],
    field_names: List[str],
    allow_empty: bool = False,
) -> Optional[str]:
    """
    Validate that exactly one of the specified fields is present.

    Args:
        values: Configuration dictionary
        field_names: List of field names where exactly one must be present
        allow_empty: Whether empty values count as present

    Returns:
        Error message if not exactly one, None if exactly one is valid

    Example:
        >>> config = {"email": "test@example.com", "phone": None}
        >>> error = validate_exactly_one(config, ["email", "phone"])
        >>> print(error)  # None - exactly one present
        >>> config2 = {"email": "test@example.com", "phone": "123"}
        >>> error2 = validate_exactly_one(config2, ["email", "phone"])
        >>> print(error2)  # "Exactly one of 'email, phone' must be set"
    """
    present_count = 0

    for field_name in field_names:
        value = values.get(field_name)
        error = validate_required(value, field_name, allow_empty)
        if error is None:
            present_count += 1

    if present_count == 0:
        fields_str = ", ".join(f"'{f}'" for f in field_names)
        return f"At least one of {fields_str} is required"

    if present_count > 1:
        fields_str = ", ".join(f"'{f}'" for f in field_names)
        return f"Exactly one of {fields_str} must be set (found {present_count})"

    return None


def validate_mutually_exclusive(
    values: Dict[str, Any],
    field_names: List[str],
) -> Optional[str]:
    """
    Validate that at most one of the specified fields is set.

    Unlike validate_exactly_one, this allows NONE of the fields to be set.

    Args:
        values: Configuration dictionary
        field_names: List of mutually exclusive field names

    Returns:
        Error message if more than one is set, None otherwise

    Example:
        >>> config = {"json_file": "config.json", "yaml_file": None}
        >>> error = validate_mutually_exclusive(config, ["json_file", "yaml_file"])
        >>> print(error)  # None
        >>> config2 = {"json_file": "config.json", "yaml_file": "config.yaml"}
        >>> error2 = validate_mutually_exclusive(config2, ["json_file", "yaml_file"])
        >>> print(error2)  # "Fields 'json_file, yaml_file' are mutually exclusive"
    """
    present_fields = []

    for field_name in field_names:
        value = values.get(field_name)
        if value is not None:
            present_fields.append(field_name)

    if len(present_fields) > 1:
        fields_str = ", ".join(f"'{f}'" for f in present_fields)
        return f"Fields {fields_str} are mutually exclusive"

    return None


def validate_required_if_true(
    value: Any,
    field_name: str,
    condition: bool,
    allow_empty: bool = False,
) -> Optional[str]:
    """
    Validate field is required if condition is True.

    Args:
        value: Field value to validate
        field_name: Name of field for error messages
        condition: Boolean condition - if True, field is required
        allow_empty: Whether empty values are allowed

    Returns:
        Error message if invalid, None if valid

    Example:
        >>> # API key required when use_api is True
        >>> validate_required_if_true(
        ...     value=None,
        ...     field_name="api_key",
        ...     condition=True,  # use_api is True
        ... )
        "Field 'api_key' is required"
    """
    if not condition:
        return None

    return validate_required(value, field_name, allow_empty)


def validate_not_blank(value: Any, field_name: str) -> Optional[str]:
    """
    Validate string field is not blank (None, empty, or whitespace).

    Args:
        value: String value to validate
        field_name: Name of field for error messages

    Returns:
        Error message if blank, None if valid

    Example:
        >>> validate_not_blank("hello", "name")
        None
        >>> validate_not_blank("", "name")
        "Field 'name' cannot be blank"
        >>> validate_not_blank("   ", "name")
        "Field 'name' cannot be blank"
    """
    if value is None:
        return f"Field '{field_name}' is required"

    if isinstance(value, str) and len(value.strip()) == 0:
        return f"Field '{field_name}' cannot be blank"

    return None
