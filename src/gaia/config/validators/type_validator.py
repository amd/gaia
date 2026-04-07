# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Type validation utilities for GAIA configuration.

Provides type checking for configuration values with support
for Union types, None handling, and type coercion.

Example:
    from gaia.config.validators import validate_type

    # Basic type validation
    assert validate_type("hello", str) == True
    assert validate_type(42, int) == True

    # Union types
    assert validate_type("hello", [str, int]) == True
    assert validate_type(42, [str, int]) == True

    # None handling
    assert validate_type(None, str, allow_none=True) == True
"""

from typing import Any, List, Optional, Type, Union


def validate_type(
    value: Any,
    expected_type: Union[Type, List[Type]],
    allow_none: bool = False,
) -> bool:
    """
    Validate value matches expected type(s).

    Supports Union types via list of types and handles None/null gracefully.

    Args:
        value: Value to validate
        expected_type: Expected Python type or list of types for Union
        allow_none: Whether None is a valid value (default: False)

    Returns:
        True if value matches expected type, False otherwise

    Example:
        >>> validate_type("hello", str)
        True
        >>> validate_type(42, str)
        False
        >>> validate_type(None, str, allow_none=True)
        True
        >>> validate_type(42, [str, int])  # Union[str, int]
        True
    """
    # Handle None
    if value is None:
        return allow_none

    # Normalize expected_type to list
    if not isinstance(expected_type, list):
        expected_types = [expected_type]
    else:
        expected_types = expected_type

    # Check against each expected type
    for exp_type in expected_types:
        if _is_instance(value, exp_type):
            return True

    return False


def _is_instance(value: Any, expected_type: Type) -> bool:
    """
    Check if value is instance of expected type.

    Handles special cases like int/float compatibility and
    common Python types.

    Args:
        value: Value to check
        expected_type: Expected type

    Returns:
        True if value is instance of expected_type
    """
    # Handle bool before int (bool is subclass of int)
    if expected_type is int and isinstance(value, bool):
        return False

    # Basic isinstance check
    if isinstance(value, expected_type):
        return True

    # Handle numeric type compatibility
    if expected_type is int and isinstance(value, float) and value.is_integer():
        return True

    if expected_type is float and isinstance(value, int):
        return True

    # Handle string-like types
    if expected_type is str and isinstance(value, (str,)):
        return True

    # Handle list/tuple compatibility
    if expected_type is list and isinstance(value, (list, tuple)):
        return True

    if expected_type is dict and isinstance(value, dict):
        return True

    return False


def validate_type_strict(
    value: Any,
    expected_type: Type,
    allow_none: bool = False,
) -> bool:
    """
    Strict type validation without type coercion.

    Unlike validate_type, this does not allow int/float compatibility
    or other type coercion.

    Args:
        value: Value to validate
        expected_type: Expected Python type
        allow_none: Whether None is valid (default: False)

    Returns:
        True if value exactly matches expected type

    Example:
        >>> validate_type_strict(42, int)
        True
        >>> validate_type_strict(42.0, int)  # No coercion
        False
        >>> validate_type_strict(True, int)  # No bool->int
        False
    """
    if value is None:
        return allow_none

    # Strict bool check (bool is subclass of int)
    if expected_type is int and isinstance(value, bool):
        return False

    return isinstance(value, expected_type)


def get_type_name(value: Any) -> str:
    """
    Get human-readable type name for a value.

    Args:
        value: Value to get type name for

    Returns:
        Type name as string

    Example:
        >>> get_type_name("hello")
        'str'
        >>> get_type_name(42)
        'int'
        >>> get_type_name(None)
        'None'
    """
    if value is None:
        return "None"
    return type(value).__name__


def validate_type_with_coercion(
    value: Any,
    expected_type: Type,
    allow_none: bool = False,
) -> tuple:
    """
    Validate and attempt to coerce value to expected type.

    Returns tuple of (success, coerced_value, error_message).

    Args:
        value: Value to validate and coerce
        expected_type: Target type for coercion
        allow_none: Whether None is valid

    Returns:
        Tuple of (success: bool, coerced_value: Any, error_message: str)

    Example:
        >>> validate_type_with_coercion("42", int)
        (True, 42, "")
        >>> validate_type_with_coercion("hello", int)
        (False, "hello", "Cannot convert 'hello' to int")
    """
    if value is None:
        if allow_none:
            return (True, None, "")
        return (False, value, "None value not allowed")

    try:
        # Already correct type
        if isinstance(value, expected_type):
            # Special case: bool should not be considered int
            if expected_type is int and isinstance(value, bool):
                return (False, value, f"Expected int, got bool")
            return (True, value, "")

        # Attempt coercion
        if expected_type is int:
            if isinstance(value, float):
                if value.is_integer():
                    return (True, int(value), "")
                return (False, value, f"Cannot convert float {value} to int (has decimal)")
            if isinstance(value, str):
                return (True, int(value), "")

        if expected_type is float:
            if isinstance(value, (int, str)):
                return (True, float(value), "")

        if expected_type is bool:
            if isinstance(value, str):
                if value.lower() in ("true", "1", "yes", "on"):
                    return (True, True, "")
                if value.lower() in ("false", "0", "no", "off"):
                    return (True, False, "")
                return (False, value, f"Cannot convert '{value}' to bool")

        if expected_type is str:
            return (True, str(value), "")

        if expected_type is list:
            if isinstance(value, (tuple, set)):
                return (True, list(value), "")

        return (False, value, f"Cannot convert {get_type_name(value)} to {expected_type.__name__}")

    except (ValueError, TypeError) as e:
        return (False, value, str(e))


def validate_collection_types(
    value: Any,
    collection_type: Type,
    item_type: Optional[Type] = None,
    allow_none: bool = False,
) -> bool:
    """
    Validate collection type and optionally item types.

    Args:
        value: Value to validate
        collection_type: Expected collection type (list, dict, set, tuple)
        item_type: Expected type of items (optional)
        allow_none: Whether None is valid

    Returns:
        True if collection and items match expected types

    Example:
        >>> validate_collection_types([1, 2, 3], list, int)
        True
        >>> validate_collection_types([1, "two", 3], list, int)
        False
        >>> validate_collection_types({"a": 1, "b": 2}, dict, int)
        True
    """
    if value is None:
        return allow_none

    # Check collection type
    if not isinstance(value, collection_type):
        return False

    # Check item types if specified
    if item_type is not None:
        if collection_type is dict:
            # Check dict values
            for v in value.values():
                if not isinstance(v, item_type):
                    return False
        else:
            # Check list/tuple/set items
            for item in value:
                if not isinstance(item, item_type):
                    return False

    return True
