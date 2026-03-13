# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the file categorizer module."""

import pytest

from gaia.filesystem.categorizer import (
    _EXTENSION_TO_CATEGORY,
    _SUBCATEGORY_MAP,
    CATEGORY_MAP,
    auto_categorize,
)

# ---------------------------------------------------------------------------
# auto_categorize: known extensions
# ---------------------------------------------------------------------------


class TestAutoCategorizeKnownExtensions:
    """Verify auto_categorize returns correct (category, subcategory) for known extensions."""

    @pytest.mark.parametrize(
        "extension, expected",
        [
            ("py", ("code", "python")),
            ("pdf", ("document", "pdf")),
            ("xlsx", ("spreadsheet", "excel")),
            ("mp4", ("video", "mp4")),
            ("jpg", ("image", "jpeg")),
            ("json", ("data", "json")),
            ("zip", ("archive", "zip")),
            ("html", ("web", "html")),
            ("db", ("database", "generic")),
            ("ttf", ("font", "truetype")),
        ],
    )
    def test_known_extension(self, extension, expected):
        """auto_categorize returns the expected tuple for a known extension."""
        assert auto_categorize(extension) == expected


# ---------------------------------------------------------------------------
# auto_categorize: unknown and edge-case inputs
# ---------------------------------------------------------------------------


class TestAutoCategorizeEdgeCases:
    """Edge cases: unknown extensions, empty strings, leading dots, case insensitivity."""

    def test_unknown_extension_returns_other_unknown(self):
        """An unrecognised extension should return ('other', 'unknown')."""
        assert auto_categorize("xyz123") == ("other", "unknown")

    def test_empty_string_returns_other_unknown(self):
        """An empty string should return ('other', 'unknown')."""
        assert auto_categorize("") == ("other", "unknown")

    def test_leading_dot_stripped(self):
        """A leading dot should be stripped before lookup (.pdf -> pdf)."""
        assert auto_categorize(".pdf") == ("document", "pdf")

    def test_multiple_leading_dots_stripped(self):
        """Multiple leading dots should all be stripped (..pdf -> pdf)."""
        assert auto_categorize("..pdf") == ("document", "pdf")

    @pytest.mark.parametrize(
        "extension, expected",
        [
            ("PY", ("code", "python")),
            ("Pdf", ("document", "pdf")),
            ("JSON", ("data", "json")),
            ("Mp4", ("video", "mp4")),
            ("XLSX", ("spreadsheet", "excel")),
        ],
    )
    def test_case_insensitivity(self, extension, expected):
        """auto_categorize should be case-insensitive."""
        assert auto_categorize(extension) == expected

    def test_only_dots_returns_other_unknown(self):
        """A string of only dots should return ('other', 'unknown')."""
        assert auto_categorize("...") == ("other", "unknown")


# ---------------------------------------------------------------------------
# Data-structure consistency checks
# ---------------------------------------------------------------------------


class TestCategoryMapCompleteness:
    """Every extension present in CATEGORY_MAP must also exist in _EXTENSION_TO_CATEGORY."""

    def test_all_category_map_extensions_in_reverse_lookup(self):
        """Every extension across all categories should have an entry in _EXTENSION_TO_CATEGORY."""
        missing = []
        for category, extensions in CATEGORY_MAP.items():
            for ext in extensions:
                if ext not in _EXTENSION_TO_CATEGORY:
                    missing.append((ext, category))
        assert (
            missing == []
        ), f"Extensions in CATEGORY_MAP but not in _EXTENSION_TO_CATEGORY: {missing}"


class TestSubcategoryMapConsistency:
    """Every extension in _SUBCATEGORY_MAP must have its category matching CATEGORY_MAP."""

    def test_subcategory_categories_match_category_map(self):
        """For every (ext -> (cat, subcat)) in _SUBCATEGORY_MAP, ext must belong to cat in CATEGORY_MAP."""
        mismatches = []
        for ext, (cat, _subcat) in _SUBCATEGORY_MAP.items():
            if cat not in CATEGORY_MAP:
                mismatches.append((ext, cat, "category not found in CATEGORY_MAP"))
            elif ext not in CATEGORY_MAP[cat]:
                mismatches.append((ext, cat, f"extension not in CATEGORY_MAP['{cat}']"))
        assert (
            mismatches == []
        ), f"_SUBCATEGORY_MAP entries inconsistent with CATEGORY_MAP: {mismatches}"


class TestExtensionUniqueness:
    """No extension should appear in more than one category in CATEGORY_MAP."""

    def test_no_extension_in_multiple_categories(self):
        """Each extension must belong to exactly one category."""
        seen = {}
        duplicates = []
        for category, extensions in CATEGORY_MAP.items():
            for ext in extensions:
                if ext in seen:
                    duplicates.append((ext, seen[ext], category))
                else:
                    seen[ext] = category
        assert (
            duplicates == []
        ), f"Extensions appearing in multiple categories: {duplicates}"


# ---------------------------------------------------------------------------
# Reverse lookup correctness
# ---------------------------------------------------------------------------


class TestReverseLookupCorrectness:
    """_EXTENSION_TO_CATEGORY values should match the category the extension belongs to."""

    def test_reverse_lookup_values_match_category_map(self):
        """For each ext in _EXTENSION_TO_CATEGORY, the mapped category must contain that ext."""
        wrong = []
        for ext, cat in _EXTENSION_TO_CATEGORY.items():
            if cat not in CATEGORY_MAP or ext not in CATEGORY_MAP[cat]:
                wrong.append((ext, cat))
        assert (
            wrong == []
        ), f"_EXTENSION_TO_CATEGORY entries not matching CATEGORY_MAP: {wrong}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
