# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for configuration validators.

Tests type, range, and required field validators.
"""

import pytest

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


class TestValidateType:
    """Test validate_type function."""

    def test_validate_type_string(self):
        """Test string type validation."""
        assert validate_type("hello", str) is True
        assert validate_type(123, str) is False

    def test_validate_type_int(self):
        """Test int type validation."""
        assert validate_type(42, int) is True
        assert validate_type("42", int) is False

    def test_validate_type_float(self):
        """Test float type validation."""
        assert validate_type(3.14, float) is True
        assert validate_type("3.14", float) is False

    def test_validate_type_bool(self):
        """Test bool type validation."""
        assert validate_type(True, bool) is True
        assert validate_type(1, bool) is False  # Don't coerce

    def test_validate_type_list(self):
        """Test list type validation."""
        assert validate_type([1, 2, 3], list) is True
        assert validate_type((1, 2, 3), list) is True  # Tuple accepted
        assert validate_type("not a list", list) is False

    def test_validate_type_dict(self):
        """Test dict type validation."""
        assert validate_type({"key": "value"}, dict) is True
        assert validate_type([1, 2], dict) is False

    def test_validate_type_union(self):
        """Test union type validation."""
        assert validate_type("hello", [str, int]) is True
        assert validate_type(42, [str, int]) is True
        assert validate_type(3.14, [str, int]) is False

    def test_validate_type_none(self):
        """Test None handling."""
        assert validate_type(None, str, allow_none=True) is True
        assert validate_type(None, str, allow_none=False) is False

    def test_validate_type_int_float_compatibility(self):
        """Test int/float compatibility."""
        assert validate_type(42, float) is True  # int is valid float
        assert validate_type(42.0, int) is True  # whole float is valid int


class TestValidateTypeStrict:
    """Test validate_type_strict function."""

    def test_strict_type_match(self):
        """Test strict type matching."""
        assert validate_type_strict("hello", str) is True
        assert validate_type_strict(42, int) is True

    def test_strict_no_coercion(self):
        """Test no type coercion in strict mode."""
        assert validate_type_strict(42.0, int) is False
        assert validate_type_strict(True, int) is False

    def test_strict_none(self):
        """Test None handling in strict mode."""
        assert validate_type_strict(None, str, allow_none=True) is True
        assert validate_type_strict(None, str, allow_none=False) is False


class TestGetType:
    """Test get_type_name function."""

    def test_get_type_name_string(self):
        """Test getting type name for string."""
        assert get_type_name("hello") == "str"

    def test_get_type_name_int(self):
        """Test getting type name for int."""
        assert get_type_name(42) == "int"

    def test_get_type_name_none(self):
        """Test getting type name for None."""
        assert get_type_name(None) == "None"

    def test_get_type_name_list(self):
        """Test getting type name for list."""
        assert get_type_name([1, 2]) == "list"


class TestValidateTypeWithCoercion:
    """Test validate_type_with_coercion function."""

    def test_coerce_string_to_int(self):
        """Test coercing string to int."""
        success, value, error = validate_type_with_coercion("42", int)
        assert success is True
        assert value == 42

    def test_coerce_string_to_float(self):
        """Test coercing string to float."""
        success, value, error = validate_type_with_coercion("3.14", float)
        assert success is True
        assert value == 3.14

    def test_coerce_string_to_bool_true(self):
        """Test coercing string to bool (true)."""
        success, value, error = validate_type_with_coercion("true", bool)
        assert success is True
        assert value is True

    def test_coerce_string_to_bool_false(self):
        """Test coercing string to bool (false)."""
        success, value, error = validate_type_with_coercion("false", bool)
        assert success is True
        assert value is False

    def test_coerce_failure(self):
        """Test coercion failure."""
        success, value, error = validate_type_with_coercion("not_a_number", int)
        assert success is False
        assert error != ""

    def test_coerce_none(self):
        """Test None handling."""
        success, value, error = validate_type_with_coercion(None, str, allow_none=True)
        assert success is True
        assert value is None


class TestValidateCollectionTypes:
    """Test validate_collection_types function."""

    def test_list_with_item_type(self):
        """Test list with item type."""
        assert validate_collection_types([1, 2, 3], list, int) is True
        assert validate_collection_types([1, "two", 3], list, int) is False

    def test_dict_with_value_type(self):
        """Test dict with value type."""
        assert validate_collection_types({"a": 1, "b": 2}, dict, int) is True
        assert validate_collection_types({"a": 1, "b": "two"}, dict, int) is False

    def test_none_collection(self):
        """Test None collection."""
        assert validate_collection_types(None, list, allow_none=True) is True
        assert validate_collection_types(None, list, allow_none=False) is False


class TestValidateRange:
    """Test validate_range function."""

    def test_in_range(self):
        """Test value in range."""
        assert validate_range(50, min_value=0, max_value=100) is True

    def test_below_min(self):
        """Test value below minimum."""
        assert validate_range(-1, min_value=0) is False

    def test_above_max(self):
        """Test value above maximum."""
        assert validate_range(101, max_value=100) is False

    def test_at_min_inclusive(self):
        """Test value at minimum (inclusive)."""
        assert validate_range(0, min_value=0, min_inclusive=True) is True

    def test_at_min_exclusive(self):
        """Test value at minimum (exclusive)."""
        assert validate_range(0, min_value=0, min_inclusive=False) is False

    def test_at_max_inclusive(self):
        """Test value at maximum (inclusive)."""
        assert validate_range(100, max_value=100, max_inclusive=True) is True

    def test_at_max_exclusive(self):
        """Test value at maximum (exclusive)."""
        assert validate_range(100, max_value=100, max_inclusive=False) is False

    def test_non_numeric(self):
        """Test non-numeric value raises TypeError."""
        with pytest.raises(TypeError):
            validate_range("not a number", min_value=0)


class TestValidatePattern:
    """Test validate_pattern function."""

    def test_valid_email(self):
        """Test valid email pattern."""
        pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        assert validate_pattern("test@example.com", pattern) is True

    def test_invalid_email(self):
        """Test invalid email pattern."""
        pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        assert validate_pattern("invalid", pattern) is False

    def test_non_string(self):
        """Test non-string value raises TypeError."""
        with pytest.raises(TypeError):
            validate_pattern(123, r"\d+")


class TestValidateLength:
    """Test validate_length function."""

    def test_string_in_range(self):
        """Test string length in range."""
        assert validate_length("hello", min_length=1, max_length=10) is True

    def test_string_too_short(self):
        """Test string too short."""
        assert validate_length("hi", min_length=5) is False

    def test_string_too_long(self):
        """Test string too long."""
        assert validate_length("hello world", max_length=5) is False

    def test_list_length(self):
        """Test list length."""
        assert validate_length([1, 2, 3], min_length=1, max_length=5) is True

    def test_no_length(self):
        """Test value without length raises TypeError."""
        with pytest.raises(TypeError):
            validate_length(42, min_length=1)


class TestValidateOneOf:
    """Test validate_one_of function."""

    def test_in_choices(self):
        """Test value in choices."""
        assert validate_one_of("debug", ["debug", "info", "warning", "error"]) is True

    def test_not_in_choices(self):
        """Test value not in choices."""
        assert validate_one_of("verbose", ["debug", "info", "warning", "error"]) is False


class TestValidateNotEmpty:
    """Test validate_not_empty function."""

    def test_non_empty_string(self):
        """Test non-empty string."""
        assert validate_not_empty("hello") is True

    def test_empty_string(self):
        """Test empty string."""
        assert validate_not_empty("") is False

    def test_non_empty_list(self):
        """Test non-empty list."""
        assert validate_not_empty([1, 2]) is True

    def test_empty_list(self):
        """Test empty list."""
        assert validate_not_empty([]) is False

    def test_none(self):
        """Test None."""
        assert validate_not_empty(None) is False


class TestValidatePredicate:
    """Test validate_predicate function."""

    def test_even_number(self):
        """Test even number predicate."""
        assert validate_predicate(42, lambda x: x % 2 == 0) is True
        assert validate_predicate(43, lambda x: x % 2 == 0) is False

    def test_positive_number(self):
        """Test positive number predicate."""
        assert validate_predicate(10, lambda x: x > 0) is True
        assert validate_predicate(-5, lambda x: x > 0) is False


class TestValidateRegexFullMatch:
    """Test validate_regex_full_match function."""

    def test_full_match(self):
        """Test full regex match."""
        assert validate_regex_full_match("123", r"\d+") is True

    def test_partial_match_fails(self):
        """Test partial match fails full match."""
        assert validate_regex_full_match("123abc", r"\d+") is False

    def test_full_pattern_match(self):
        """Test full pattern match."""
        assert validate_regex_full_match("123abc", r"[\da-z]+") is True


class TestValidateNumericConstraints:
    """Test validate_numeric_constraints function."""

    def test_all_constraints_pass(self):
        """Test all constraints pass."""
        valid, error = validate_numeric_constraints(
            50, min_value=0, max_value=100, multiple_of=10
        )
        assert valid is True
        assert error == ""

    def test_below_min(self):
        """Test below minimum."""
        valid, error = validate_numeric_constraints(-1, min_value=0)
        assert valid is False
        assert "less than minimum" in error.lower()

    def test_above_max(self):
        """Test above maximum."""
        valid, error = validate_numeric_constraints(101, max_value=100)
        assert valid is False
        assert "greater than maximum" in error.lower()

    def test_not_multiple(self):
        """Test not a multiple."""
        valid, error = validate_numeric_constraints(7, multiple_of=2)
        assert valid is False
        assert "multiple" in error.lower()


class TestValidateRequired:
    """Test validate_required function."""

    def test_present(self):
        """Test required field present."""
        assert validate_required("value", "field_name") is None

    def test_none(self):
        """Test required field None."""
        error = validate_required(None, "field_name")
        assert error is not None
        assert "required" in error.lower()

    def test_empty_string_not_allowed(self):
        """Test empty string not allowed."""
        error = validate_required("", "field_name", allow_empty=False)
        assert error is not None

    def test_empty_string_allowed(self):
        """Test empty string allowed."""
        error = validate_required("", "field_name", allow_empty=True)
        assert error is None


class TestValidateRequiredIf:
    """Test validate_required_if function."""

    def test_condition_not_met(self):
        """Test when condition is not met."""
        error = validate_required_if(
            value=None,
            field_name="confirm",
            condition_value=None,
            other_field_value=None,
        )
        assert error is None

    def test_condition_met_field_present(self):
        """Test condition met and field present."""
        error = validate_required_if(
            value="confirmed",
            field_name="confirm",
            condition_value=None,
            other_field_value="value",
        )
        assert error is None

    def test_condition_met_field_missing(self):
        """Test condition met and field missing."""
        error = validate_required_if(
            value=None,
            field_name="confirm",
            condition_value=None,
            other_field_value="value",
        )
        assert error is not None


class TestValidateRequiredWith:
    """Test validate_required_with function."""

    def test_all_present(self):
        """Test all required fields present."""
        errors = validate_required_with(
            {"name": "test", "value": 42},
            ["name", "value"]
        )
        assert len(errors) == 0

    def test_one_missing(self):
        """Test one required field missing."""
        errors = validate_required_with(
            {"name": "test"},
            ["name", "value"]
        )
        assert len(errors) == 1


class TestValidateAtLeastOne:
    """Test validate_at_least_one function."""

    def test_one_present(self):
        """Test at least one present."""
        error = validate_at_least_one(
            {"email": "test@example.com", "phone": None},
            ["email", "phone"]
        )
        assert error is None

    def test_all_missing(self):
        """Test all missing."""
        error = validate_at_least_one(
            {"email": None, "phone": None},
            ["email", "phone"]
        )
        assert error is not None


class TestValidateExactlyOne:
    """Test validate_exactly_one function."""

    def test_exactly_one(self):
        """Test exactly one present."""
        error = validate_exactly_one(
            {"email": "test@example.com", "phone": None},
            ["email", "phone"]
        )
        assert error is None

    def test_none_present(self):
        """Test none present."""
        error = validate_exactly_one(
            {"email": None, "phone": None},
            ["email", "phone"]
        )
        assert error is not None

    def test_both_present(self):
        """Test both present."""
        error = validate_exactly_one(
            {"email": "test@example.com", "phone": "123"},
            ["email", "phone"]
        )
        assert error is not None


class TestValidateMutuallyExclusive:
    """Test validate_mutually_exclusive function."""

    def test_one_set(self):
        """Test one field set."""
        error = validate_mutually_exclusive(
            {"json_file": "config.json", "yaml_file": None},
            ["json_file", "yaml_file"]
        )
        assert error is None

    def test_both_set(self):
        """Test both fields set."""
        error = validate_mutually_exclusive(
            {"json_file": "config.json", "yaml_file": "config.yaml"},
            ["json_file", "yaml_file"]
        )
        assert error is not None

    def test_neither_set(self):
        """Test neither field set."""
        error = validate_mutually_exclusive(
            {"json_file": None, "yaml_file": None},
            ["json_file", "yaml_file"]
        )
        assert error is None


class TestValidateRequiredIfTrue:
    """Test validate_required_if_true function."""

    def test_condition_false(self):
        """Test when condition is false."""
        error = validate_required_if_true(
            value=None,
            field_name="api_key",
            condition=False,
        )
        assert error is None

    def test_condition_true_field_present(self):
        """Test condition true and field present."""
        error = validate_required_if_true(
            value="key123",
            field_name="api_key",
            condition=True,
        )
        assert error is None

    def test_condition_true_field_missing(self):
        """Test condition true and field missing."""
        error = validate_required_if_true(
            value=None,
            field_name="api_key",
            condition=True,
        )
        assert error is not None


class TestValidateNotBlank:
    """Test validate_not_blank function."""

    def test_non_blank(self):
        """Test non-blank value."""
        assert validate_not_blank("hello", "field") is None

    def test_none(self):
        """Test None value."""
        error = validate_not_blank(None, "field")
        assert error is not None

    def test_empty_string(self):
        """Test empty string."""
        error = validate_not_blank("", "field")
        assert error is not None

    def test_whitespace_only(self):
        """Test whitespace-only string."""
        error = validate_not_blank("   ", "field")
        assert error is not None
