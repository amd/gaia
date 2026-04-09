# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Range and constraint validation utilities for GAIA configuration.

Provides validation for numeric ranges, string patterns, and
custom constraint predicates.

Example:
    from gaia.config.validators import validate_range, validate_pattern

    # Numeric range validation
    assert validate_range(50, min_value=0, max_value=100) == True
    assert validate_range(-1, min_value=0) == False

    # String pattern validation
    assert validate_pattern("user@example.com", r"^[\\w.-]+@[\\w.-]+\\.[\\w]+$") == True
"""

import re
from typing import Any, Callable, Optional, Union


def validate_range(
    value: Union[int, float],
    min_value: Optional[Union[int, float]] = None,
    max_value: Optional[Union[int, float]] = None,
    min_inclusive: bool = True,
    max_inclusive: bool = True,
) -> bool:
    """
    Validate numeric value within range.

    Supports optional min/max bounds with configurable inclusivity.

    Args:
        value: Numeric value to validate
        min_value: Minimum allowed value (None for no minimum)
        max_value: Maximum allowed value (None for no maximum)
        min_inclusive: Whether min_value is inclusive (default: True)
        max_inclusive: Whether max_value is inclusive (default: True)

    Returns:
        True if value is within range, False otherwise

    Raises:
        TypeError: If value is not numeric

    Example:
        >>> validate_range(50, min_value=0, max_value=100)
        True
        >>> validate_range(0, min_value=0, min_inclusive=True)
        True
        >>> validate_range(0, min_value=0, min_inclusive=False)
        False
        >>> validate_range(100, max_value=100)
        True
        >>> validate_range(-1, min_value=0)
        False
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"Expected numeric value, got {type(value).__name__}")

    # Check minimum
    if min_value is not None:
        if min_inclusive:
            if value < min_value:
                return False
        else:
            if value <= min_value:
                return False

    # Check maximum
    if max_value is not None:
        if max_inclusive:
            if value > max_value:
                return False
        else:
            if value >= max_value:
                return False

    return True


def validate_pattern(value: str, pattern: str) -> bool:
    """
    Validate string matches regex pattern.

    Args:
        value: String value to validate
        pattern: Regular expression pattern to match

    Returns:
        True if value matches pattern, False otherwise

    Raises:
        TypeError: If value is not a string
        re.error: If pattern is invalid

    Example:
        >>> validate_pattern("user@example.com", r"^[\\w.-]+@[\\w.-]+\\.[\\w]+$")
        True
        >>> validate_pattern("invalid", r"^\\d+$")  # Digits only
        False
        >>> validate_pattern("123", r"^\\d+$")
        True
    """
    if not isinstance(value, str):
        raise TypeError(f"Expected string value, got {type(value).__name__}")

    # Compile and match pattern
    regex = re.compile(pattern)
    return bool(regex.match(value))


def validate_length(
    value: Union[str, list, tuple, dict],
    min_length: Optional[int] = None,
    max_length: Optional[int] = None,
) -> bool:
    """
    Validate length of string or collection.

    Args:
        value: Value to check length of
        min_length: Minimum allowed length (None for no minimum)
        max_length: Maximum allowed length (None for no maximum)

    Returns:
        True if length is within bounds, False otherwise

    Raises:
        TypeError: If value doesn't have a length

    Example:
        >>> validate_length("hello", min_length=1, max_length=10)
        True
        >>> validate_length([1, 2, 3], min_length=5)
        False
        >>> validate_length("test", max_length=3)
        False
    """
    try:
        length = len(value)
    except TypeError:
        raise TypeError(
            f"Expected value with length, got {type(value).__name__}"
        )

    if min_length is not None and length < min_length:
        return False

    if max_length is not None and length > max_length:
        return False

    return True


def validate_one_of(value: Any, allowed_values: list) -> bool:
    """
    Validate value is one of allowed values.

    Args:
        value: Value to validate
        allowed_values: List of allowed values

    Returns:
        True if value is in allowed_values, False otherwise

    Example:
        >>> validate_one_of("debug", ["debug", "info", "warning", "error"])
        True
        >>> validate_one_of("verbose", ["debug", "info", "warning", "error"])
        False
    """
    return value in allowed_values


def validate_not_empty(value: Any) -> bool:
    """
    Validate value is not empty.

    Works with strings, collections, and other truthy values.

    Args:
        value: Value to validate

    Returns:
        True if value is not empty/None/False

    Example:
        >>> validate_not_empty("hello")
        True
        >>> validate_not_empty("")
        False
        >>> validate_not_empty([])
        False
        >>> validate_not_empty([1, 2])
        True
    """
    if value is None:
        return False

    if isinstance(value, (str, list, tuple, dict, set)):
        return len(value) > 0

    # For other types, check truthiness
    return bool(value)


def validate_predicate(value: Any, predicate: Callable[[Any], bool]) -> bool:
    """
    Validate value against custom predicate function.

    Args:
        value: Value to validate
        predicate: Function that returns True if value is valid

    Returns:
        Result of predicate(value)

    Example:
        >>> validate_predicate(42, lambda x: x % 2 == 0)  # Even numbers
        True
        >>> validate_predicate(43, lambda x: x % 2 == 0)
        False
    """
    try:
        return predicate(value)
    except Exception:
        return False


def validate_regex_full_match(value: str, pattern: str) -> bool:
    """
    Validate string fully matches regex pattern (entire string).

    Unlike validate_pattern which uses match (start of string),
    this requires the entire string to match.

    Args:
        value: String value to validate
        pattern: Regular expression pattern

    Returns:
        True if entire string matches pattern, False otherwise

    Example:
        >>> validate_regex_full_match("123", r"\\d+")
        True
        >>> validate_regex_full_match("123abc", r"\\d+")  # Partial match
        False
        >>> validate_regex_full_match("123abc", r"[\\da-z]+")
        True
    """
    if not isinstance(value, str):
        raise TypeError(f"Expected string value, got {type(value).__name__}")

    regex = re.compile(pattern)
    return bool(regex.fullmatch(value))


def validate_numeric_constraints(
    value: Union[int, float],
    min_value: Optional[Union[int, float]] = None,
    max_value: Optional[Union[int, float]] = None,
    min_exclusive: bool = False,
    max_exclusive: bool = False,
    multiple_of: Optional[Union[int, float]] = None,
) -> tuple:
    """
    Validate numeric value with multiple constraints.

    Returns tuple of (valid, error_message) for detailed feedback.

    Args:
        value: Numeric value to validate
        min_value: Minimum allowed value
        max_value: Maximum allowed value
        min_exclusive: If True, min_value is exclusive
        max_exclusive: If True, max_value is exclusive
        multiple_of: Value must be a multiple of this number

    Returns:
        Tuple of (is_valid: bool, error_message: str)

    Example:
        >>> validate_numeric_constraints(50, min_value=0, max_value=100)
        (True, "")
        >>> validate_numeric_constraints(-1, min_value=0)
        (False, "Value -1 is less than minimum 0")
        >>> validate_numeric_constraints(7, multiple_of=2)
        (False, "Value 7 is not a multiple of 2")
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return (False, f"Expected numeric value, got {type(value).__name__}")

    # Check minimum
    if min_value is not None:
        if min_exclusive:
            if value <= min_value:
                return (False, f"Value {value} must be greater than {min_value}")
        else:
            if value < min_value:
                return (False, f"Value {value} is less than minimum {min_value}")

    # Check maximum
    if max_value is not None:
        if max_exclusive:
            if value >= max_value:
                return (False, f"Value {value} must be less than {max_value}")
        else:
            if value > max_value:
                return (False, f"Value {value} is greater than maximum {max_value}")

    # Check multiple
    if multiple_of is not None:
        if value % multiple_of != 0:
            return (False, f"Value {value} is not a multiple of {multiple_of}")

    return (True, "")
