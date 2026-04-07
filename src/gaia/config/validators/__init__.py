# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Configuration Validators Module.

Provides validation utilities for configuration schemas:
- Type validation with coercion support
- Range/constraint validation
- Required field validation

Example:
    from gaia.config.validators import (
        validate_type,
        validate_range,
        validate_required,
        validate_pattern,
    )

    # Type validation
    assert validate_type("hello", str) == True

    # Range validation
    assert validate_range(50, min_value=0, max_value=100) == True

    # Required field validation
    assert validate_required("value", "field") is None
"""

from gaia.config.validators.type_validator import (
    validate_type,
    validate_type_strict,
    get_type_name,
    validate_type_with_coercion,
    validate_collection_types,
)

from gaia.config.validators.range_validator import (
    validate_range,
    validate_pattern,
    validate_length,
    validate_one_of,
    validate_not_empty,
    validate_predicate,
    validate_regex_full_match,
    validate_numeric_constraints,
)

from gaia.config.validators.required_validator import (
    validate_required,
    validate_required_if,
    validate_required_with,
    validate_at_least_one,
    validate_exactly_one,
    validate_mutually_exclusive,
    validate_required_if_true,
    validate_not_blank,
)

__all__ = [
    # Type validators
    "validate_type",
    "validate_type_strict",
    "get_type_name",
    "validate_type_with_coercion",
    "validate_collection_types",
    # Range/constraint validators
    "validate_range",
    "validate_pattern",
    "validate_length",
    "validate_one_of",
    "validate_not_empty",
    "validate_predicate",
    "validate_regex_full_match",
    "validate_numeric_constraints",
    # Required field validators
    "validate_required",
    "validate_required_if",
    "validate_required_with",
    "validate_at_least_one",
    "validate_exactly_one",
    "validate_mutually_exclusive",
    "validate_required_if_true",
    "validate_not_blank",
]

__version__ = "1.0.0"
