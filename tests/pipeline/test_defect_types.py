"""
Tests for GAIA DefectType Taxonomy (defect_types.py).

Tests cover:
- DefectType enum values and count
- DEFECT_KEYWORDS mapping completeness
- DEFECT_SPECIALISTS mapping completeness
- defect_type_from_string() classification
- get_defect_keywords() utility
- get_defect_specialists() utility
- detect_defect_types() multi-type detection
- get_all_defect_types() completeness
- get_defect_type_info() structure
"""

import pytest

from gaia.pipeline.defect_types import (
    DEFECT_KEYWORDS,
    DEFECT_SPECIALISTS,
    DefectType,
    defect_type_from_string,
    detect_defect_types,
    get_all_defect_types,
    get_defect_keywords,
    get_defect_specialists,
    get_defect_type_info,
)

# ---------------------------------------------------------------------------
# DefectType enum
# ---------------------------------------------------------------------------


class TestDefectTypeEnum:
    """Tests for the DefectType enumeration."""

    EXPECTED_TYPES = {
        "SECURITY",
        "PERFORMANCE",
        "TESTING",
        "DOCUMENTATION",
        "CODE_QUALITY",
        "REQUIREMENTS",
        "ARCHITECTURE",
        "ACCESSIBILITY",
        "COMPATIBILITY",
        "DATA_INTEGRITY",
        "UNKNOWN",
    }

    def test_all_expected_members_present(self):
        """All expected DefectType members exist."""
        actual = {m.name for m in DefectType}
        assert self.EXPECTED_TYPES == actual

    def test_member_count(self):
        """DefectType contains exactly 11 members."""
        assert len(list(DefectType)) == 11

    def test_unknown_member_exists(self):
        """UNKNOWN member exists as fallback."""
        assert DefectType.UNKNOWN is not None

    def test_members_are_unique(self):
        """All DefectType values are unique."""
        values = [m.value for m in DefectType]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# DEFECT_KEYWORDS mapping
# ---------------------------------------------------------------------------


class TestDefectKeywordsMapping:
    """Tests for the DEFECT_KEYWORDS constant."""

    def test_all_defect_types_have_keywords(self):
        """Every non-UNKNOWN DefectType has at least one keyword."""
        for defect_type in DefectType:
            if defect_type == DefectType.UNKNOWN:
                continue
            assert (
                defect_type in DEFECT_KEYWORDS
            ), f"{defect_type.name} missing from DEFECT_KEYWORDS"
            assert (
                len(DEFECT_KEYWORDS[defect_type]) > 0
            ), f"{defect_type.name} has empty keyword list"

    def test_keywords_are_lowercase_strings(self):
        """All keywords are lowercase strings for case-insensitive matching."""
        for defect_type, keywords in DEFECT_KEYWORDS.items():
            for kw in keywords:
                assert isinstance(kw, str), f"Keyword {kw!r} is not a string"
                assert (
                    kw == kw.lower()
                ), f"Keyword {kw!r} for {defect_type.name} is not lowercase"

    def test_security_keywords_include_injection(self):
        """Security keywords include 'injection' (canonical SQL injection term)."""
        security_kws = DEFECT_KEYWORDS[DefectType.SECURITY]
        assert any("injection" in kw for kw in security_kws)

    def test_performance_keywords_include_latency_or_slow(self):
        """Performance keywords include latency or slow indicators."""
        perf_kws = DEFECT_KEYWORDS[DefectType.PERFORMANCE]
        assert any(kw in ("slow", "latency", "memory leak") for kw in perf_kws) or any(
            "slow" in kw or "latency" in kw or "memory" in kw for kw in perf_kws
        )

    def test_testing_keywords_include_coverage_or_test(self):
        """Testing keywords include coverage or test."""
        test_kws = DEFECT_KEYWORDS[DefectType.TESTING]
        assert any("test" in kw or "coverage" in kw for kw in test_kws)


# ---------------------------------------------------------------------------
# DEFECT_SPECIALISTS mapping
# ---------------------------------------------------------------------------


class TestDefectSpecialistsMapping:
    """Tests for the DEFECT_SPECIALISTS constant."""

    def test_all_defect_types_have_specialists(self):
        """Every DefectType has at least one specialist agent."""
        for defect_type in DefectType:
            assert (
                defect_type in DEFECT_SPECIALISTS
            ), f"{defect_type.name} missing from DEFECT_SPECIALISTS"
            assert (
                len(DEFECT_SPECIALISTS[defect_type]) > 0
            ), f"{defect_type.name} has empty specialist list"

    def test_unknown_fallback_to_senior_developer(self):
        """UNKNOWN defect type falls back to senior-developer."""
        assert "senior-developer" in DEFECT_SPECIALISTS[DefectType.UNKNOWN]

    def test_security_specialist_is_security_auditor(self):
        """Security defects have security-auditor as primary specialist."""
        assert "security-auditor" in DEFECT_SPECIALISTS[DefectType.SECURITY]

    def test_performance_specialist_is_performance_analyst(self):
        """Performance defects have performance-analyst as specialist."""
        assert "performance-analyst" in DEFECT_SPECIALISTS[DefectType.PERFORMANCE]

    def test_documentation_specialist_is_technical_writer(self):
        """Documentation defects have technical-writer as specialist."""
        assert "technical-writer" in DEFECT_SPECIALISTS[DefectType.DOCUMENTATION]

    def test_architecture_specialist_is_solutions_architect(self):
        """Architecture defects have solutions-architect as specialist."""
        assert "solutions-architect" in DEFECT_SPECIALISTS[DefectType.ARCHITECTURE]

    def test_testing_specialist_is_coverage_analyzer(self):
        """Testing defects have test-coverage-analyzer as specialist."""
        assert "test-coverage-analyzer" in DEFECT_SPECIALISTS[DefectType.TESTING]

    def test_requirements_specialist_is_program_manager(self):
        """Requirements defects have software-program-manager as specialist."""
        assert "software-program-manager" in DEFECT_SPECIALISTS[DefectType.REQUIREMENTS]

    def test_specialists_are_strings(self):
        """All specialist entries are non-empty strings."""
        for defect_type, specialists in DEFECT_SPECIALISTS.items():
            for s in specialists:
                assert (
                    isinstance(s, str) and len(s) > 0
                ), f"Invalid specialist entry {s!r} for {defect_type.name}"


# ---------------------------------------------------------------------------
# defect_type_from_string()
# ---------------------------------------------------------------------------


class TestDefectTypeFromString:
    """Tests for defect_type_from_string() classification function."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("SQL injection vulnerability", DefectType.SECURITY),
            ("XSS attack detected", DefectType.SECURITY),
            ("authentication bypass", DefectType.SECURITY),
            ("Slow query in database", DefectType.PERFORMANCE),
            ("memory leak detected", DefectType.PERFORMANCE),
            ("high CPU usage", DefectType.PERFORMANCE),
            ("missing unit tests", DefectType.TESTING),
            ("insufficient test coverage", DefectType.TESTING),
            ("flaky test failure", DefectType.TESTING),
            ("missing docstring", DefectType.DOCUMENTATION),
            ("outdated documentation", DefectType.DOCUMENTATION),
            ("code style violation", DefectType.CODE_QUALITY),
            ("high cyclomatic complexity", DefectType.CODE_QUALITY),
            ("duplicate code detected", DefectType.CODE_QUALITY),
            ("missing requirement implementation", DefectType.REQUIREMENTS),
            ("incorrect feature behavior", DefectType.REQUIREMENTS),
            ("architecture violation", DefectType.ARCHITECTURE),
            ("circular dependency detected", DefectType.ARCHITECTURE),
            ("missing alt text", DefectType.ACCESSIBILITY),
            ("WCAG compliance issue", DefectType.ACCESSIBILITY),
            ("cross-browser compatibility issue", DefectType.COMPATIBILITY),
            ("data validation missing", DefectType.DATA_INTEGRITY),
            ("potential data loss", DefectType.DATA_INTEGRITY),
        ],
    )
    def test_classification(self, text: str, expected: DefectType):
        """defect_type_from_string correctly classifies known patterns."""
        result = defect_type_from_string(text)
        assert (
            result == expected
        ), f"Expected {expected.name} for {text!r}, got {result.name}"

    def test_empty_string_returns_unknown(self):
        """Empty string returns UNKNOWN."""
        assert defect_type_from_string("") == DefectType.UNKNOWN

    def test_none_returns_unknown(self):
        """None input returns UNKNOWN."""
        assert defect_type_from_string(None) == DefectType.UNKNOWN

    def test_unrecognised_text_returns_unknown(self):
        """Random text without matching keywords returns UNKNOWN."""
        assert (
            defect_type_from_string("random gibberish xyz qwerty") == DefectType.UNKNOWN
        )

    def test_case_insensitive_matching(self):
        """Classification is case-insensitive."""
        assert (
            defect_type_from_string("SQL INJECTION VULNERABILITY")
            == DefectType.SECURITY
        )
        assert defect_type_from_string("Memory Leak") == DefectType.PERFORMANCE

    def test_partial_keyword_match(self):
        """Partial keyword matches within longer words are detected."""
        # "sql injection" matches "Found SQL injection in login form"
        result = defect_type_from_string("Found SQL injection in login form handling")
        assert result == DefectType.SECURITY


# ---------------------------------------------------------------------------
# get_defect_keywords()
# ---------------------------------------------------------------------------


class TestGetDefectKeywords:
    """Tests for get_defect_keywords() utility function."""

    def test_returns_list_for_known_type(self):
        """Returns a non-empty list for known DefectType."""
        keywords = get_defect_keywords(DefectType.SECURITY)
        assert isinstance(keywords, list)
        assert len(keywords) > 0

    def test_returns_empty_for_unknown(self):
        """Returns empty list (or a list) for UNKNOWN type."""
        keywords = get_defect_keywords(DefectType.UNKNOWN)
        assert isinstance(keywords, list)

    @pytest.mark.parametrize(
        "defect_type", [dt for dt in DefectType if dt != DefectType.UNKNOWN]
    )
    def test_all_types_have_keywords(self, defect_type: DefectType):
        """All non-UNKNOWN types return at least one keyword."""
        keywords = get_defect_keywords(defect_type)
        assert len(keywords) > 0


# ---------------------------------------------------------------------------
# get_defect_specialists()
# ---------------------------------------------------------------------------


class TestGetDefectSpecialists:
    """Tests for get_defect_specialists() utility function."""

    def test_returns_list_for_known_type(self):
        """Returns a non-empty list for known DefectType."""
        specialists = get_defect_specialists(DefectType.SECURITY)
        assert isinstance(specialists, list)
        assert len(specialists) > 0

    @pytest.mark.parametrize("defect_type", list(DefectType))
    def test_all_types_have_at_least_one_specialist(self, defect_type: DefectType):
        """All DefectType values return at least one specialist."""
        specialists = get_defect_specialists(defect_type)
        assert len(specialists) > 0


# ---------------------------------------------------------------------------
# detect_defect_types()
# ---------------------------------------------------------------------------


class TestDetectDefectTypes:
    """Tests for detect_defect_types() multi-type detection.

    NOTE: The source detect_defect_types(texts: List[str]) -> Dict[str, DefectType]
    accepts a list of text strings and returns a dict mapping each text to its
    detected DefectType.  These tests wrap single strings in a list and inspect
    the resulting dict accordingly.
    """

    def test_single_type_detected(self):
        """Single defect type detected from clear description."""
        text = "SQL injection vulnerability in login"
        result = detect_defect_types([text])
        assert isinstance(result, dict)
        assert result[text] == DefectType.SECURITY

    def test_multiple_texts_detected(self):
        """Multiple texts each classified correctly."""
        texts = [
            "SQL injection causing data breach",
            "memory leak causing high cpu usage",
        ]
        result = detect_defect_types(texts)
        assert result[texts[0]] == DefectType.SECURITY
        assert result[texts[1]] == DefectType.PERFORMANCE

    def test_empty_list_returns_empty_dict(self):
        """Empty list input returns empty dict."""
        result = detect_defect_types([])
        assert isinstance(result, dict)
        assert result == {}

    def test_unrecognised_text_returns_unknown(self):
        """Text without matching keywords returns UNKNOWN."""
        text = "random gibberish qwerty xyz"
        result = detect_defect_types([text])
        assert result[text] == DefectType.UNKNOWN

    def test_returns_dict_of_defect_types(self):
        """Result values are always DefectType members."""
        texts = ["SQL injection vulnerability", "random gibberish xyz"]
        result = detect_defect_types(texts)
        assert isinstance(result, dict)
        for value in result.values():
            assert isinstance(value, DefectType)


# ---------------------------------------------------------------------------
# get_all_defect_types()
# ---------------------------------------------------------------------------


class TestGetAllDefectTypes:
    """Tests for get_all_defect_types() completeness.

    NOTE: The source implementation explicitly excludes UNKNOWN from the
    returned list (it is documented as a fallback type, not a primary defect
    category). Tests verify that all non-UNKNOWN members are present.
    """

    def test_returns_non_unknown_types(self):
        """Returns a collection containing all non-UNKNOWN DefectType members."""
        all_types = get_all_defect_types()
        for dt in DefectType:
            if dt == DefectType.UNKNOWN:
                continue  # UNKNOWN is intentionally excluded by the source
            assert dt in all_types, f"{dt.name} missing from get_all_defect_types()"

    def test_unknown_excluded(self):
        """UNKNOWN type is excluded from get_all_defect_types() by design."""
        all_types = get_all_defect_types()
        assert DefectType.UNKNOWN not in all_types

    def test_returns_iterable(self):
        """Return value is iterable."""
        all_types = get_all_defect_types()
        assert hasattr(all_types, "__iter__")


# ---------------------------------------------------------------------------
# get_defect_type_info()
# ---------------------------------------------------------------------------


class TestGetDefectTypeInfo:
    """Tests for get_defect_type_info() dictionary structure."""

    def test_returns_dict(self):
        """Returns a dictionary for a known DefectType."""
        info = get_defect_type_info(DefectType.SECURITY)
        assert isinstance(info, dict)

    def test_info_contains_expected_keys(self):
        """Info dict contains 'keywords' and 'specialists' keys at minimum."""
        info = get_defect_type_info(DefectType.PERFORMANCE)
        assert "keywords" in info
        assert "specialists" in info

    def test_info_keywords_match_mapping(self):
        """Info keywords match DEFECT_KEYWORDS mapping."""
        info = get_defect_type_info(DefectType.TESTING)
        assert set(info["keywords"]) == set(DEFECT_KEYWORDS.get(DefectType.TESTING, []))

    def test_info_specialists_match_mapping(self):
        """Info specialists match DEFECT_SPECIALISTS mapping."""
        info = get_defect_type_info(DefectType.DOCUMENTATION)
        assert set(info["specialists"]) == set(
            DEFECT_SPECIALISTS.get(DefectType.DOCUMENTATION, [])
        )

    @pytest.mark.parametrize("defect_type", list(DefectType))
    def test_info_available_for_all_types(self, defect_type: DefectType):
        """get_defect_type_info() does not raise for any DefectType."""
        info = get_defect_type_info(defect_type)
        assert isinstance(info, dict)
