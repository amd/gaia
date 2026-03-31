"""
GAIA Testing Validators

Validators for the Testing dimension (TS-01 through TS-04).
"""

import re
from typing import Any, Dict, List, Optional

from gaia.quality.validators.base import BaseValidator, ValidationResult


class UnitTestCoverageValidator(BaseValidator):
    """
    TS-01: Unit Test Coverage Validator

    Checks line coverage, branch coverage, and function coverage.

    Scoring:
    - 100%: >90% coverage
    - 75%: 75-90% coverage
    - 50%: 50-75% coverage
    - 0%: <50% coverage
    """

    category_id = "TS-01"
    category_name = "Unit Test Coverage"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate unit test coverage."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        tests = context.get("tests", "")
        coverage_report = context.get("coverage_report", {})
        defects = []

        # Use provided coverage report if available
        if coverage_report:
            line_coverage = coverage_report.get("line_coverage", 0)
            branch_coverage = coverage_report.get("branch_coverage", 0)
            function_coverage = coverage_report.get("function_coverage", 0)
        else:
            # Estimate coverage from test content
            line_coverage, branch_coverage, function_coverage = self._estimate_coverage(
                code, tests
            )

        # Calculate overall coverage score
        overall_coverage = (line_coverage + branch_coverage + function_coverage) / 3

        # Determine score category
        if overall_coverage >= 90:
            score = 100.0
        elif overall_coverage >= 75:
            score = 75.0
        elif overall_coverage >= 50:
            score = 50.0
        else:
            score = 25.0

        # Add defects for low coverage
        if line_coverage < 75:
            defects.append(
                self._create_defect(
                    description=f"Low line coverage: {line_coverage:.1f}%",
                    severity="medium",
                    suggestion="Add more unit tests to increase coverage",
                )
            )

        if branch_coverage < 50:
            defects.append(
                self._create_defect(
                    description=f"Low branch coverage: {branch_coverage:.1f}%",
                    severity="medium",
                    suggestion="Add tests for different code paths",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=3,
            tests_passed=sum(
                [
                    line_coverage >= 75,
                    branch_coverage >= 50,
                    function_coverage >= 75,
                ]
            ),
            details={
                "line_coverage": line_coverage,
                "branch_coverage": branch_coverage,
                "function_coverage": function_coverage,
                "overall_coverage": overall_coverage,
            },
            defects=defects,
        )

    def _estimate_coverage(
        self,
        code: str,
        tests: str,
    ) -> tuple[float, float, float]:
        """Estimate coverage from test content."""
        if not tests:
            return 0.0, 0.0, 0.0

        # Extract function names from code
        code_functions = set(re.findall(r"def (\w+)\s*\(", code))

        # Extract tested function names
        tested_functions = set()
        for func in code_functions:
            if f"test_{func}" in tests or f"_{func}(" in tests:
                tested_functions.add(func)

        # Estimate function coverage
        func_coverage = (
            len(tested_functions) / len(code_functions) * 100
            if code_functions
            else 100.0
        )

        # Estimate line coverage (rough approximation)
        test_lines = len(tests.splitlines())
        code_lines = len(code.splitlines())
        ratio = test_lines / code_lines if code_lines > 0 else 0
        line_coverage = min(100, ratio * 100)

        # Branch coverage is typically lower than line coverage
        branch_coverage = line_coverage * 0.7

        return line_coverage, branch_coverage, func_coverage


class IntegrationTestCoverageValidator(BaseValidator):
    """
    TS-02: Integration Test Coverage Validator

    Checks API tests, component integration, and end-to-end flows.

    Scoring:
    - 100%: Comprehensive integration tests
    - 75%: Good coverage
    - 50%: Basic tests present
    - 0%: No integration tests
    """

    category_id = "TS-02"
    category_name = "Integration Test Coverage"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate integration test coverage."""
        tests = context.get("integration_tests", "")
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []
        checks = []

        # Check for API tests
        has_api_tests = any(
            kw in tests.lower()
            for kw in [
                "api",
                "endpoint",
                "route",
                "request",
                "response",
                "client.get",
                "client.post",
                "http",
            ]
        )
        checks.append(has_api_tests)

        # Check for component integration
        has_component_tests = any(
            kw in tests.lower()
            for kw in [
                "integration",
                "component",
                "service",
                "database",
                "repository",
                "mock",
                "fixture",
            ]
        )
        checks.append(has_component_tests)

        # Check for end-to-end flows
        has_e2e_tests = any(
            kw in tests.lower()
            for kw in [
                "e2e",
                "end-to-end",
                "end to end",
                "workflow",
                "scenario",
                "flow",
                "full",
            ]
        )
        checks.append(has_e2e_tests)

        # Check for external service mocking
        has_service_mocking = any(
            kw in tests
            for kw in ["patch", "Mock", "MagicMock", "responses", "httpretty", "vcr"]
        )
        checks.append(has_service_mocking)

        score = self._score_from_checks(checks)

        if not has_api_tests:
            defects.append(
                self._create_defect(
                    description="No API/integration tests detected",
                    severity="medium",
                    suggestion="Add tests that verify component interactions",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "api_tests": has_api_tests,
                "component_tests": has_component_tests,
                "e2e_tests": has_e2e_tests,
                "service_mocking": has_service_mocking,
            },
            defects=defects,
        )


class TestQualityValidator(BaseValidator):
    """
    TS-03: Test Quality Validator

    Checks for meaningful assertions, test isolation, and flaky test patterns.

    Scoring:
    - 100%: High quality tests
    - 75%: Good quality
    - 50%: Acceptable quality
    - 0%: Poor quality
    """

    category_id = "TS-03"
    category_name = "Test Quality/Assertions"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate test quality."""
        tests = context.get("tests", "") or context.get("test_content", "")
        if not tests:
            return self._create_validation_result(
                score=50.0,
                details={"note": "No test content provided"},
            )

        defects = []
        checks = []

        # Check for assertions
        has_assertions = any(
            kw in tests
            for kw in [
                "assert",
                "assertEquals",
                "assertTrue",
                "assertFalse",
                "assertThat",
                "expect",
                "should",
            ]
        )
        checks.append(has_assertions)

        # Count assertions per test
        assertion_count = tests.count("assert")
        test_count = tests.count("def test_") or tests.count("def test_") or 1
        assertions_per_test = assertion_count / max(test_count, 1)
        good_assertion_density = 1 <= assertions_per_test <= 5
        checks.append(good_assertion_density)

        # Check for test isolation (setup/teardown or fixtures)
        has_isolation = any(
            kw in tests
            for kw in [
                "setUp",
                "tearDown",
                "@pytest.fixture",
                "fixture",
                "beforeEach",
                "afterEach",
            ]
        )
        checks.append(has_isolation)

        # Check for proper test naming
        test_functions = re.findall(r"def (test_\w+)\s*\(", tests)
        well_named = all(
            len(name) > 8 for name in test_functions
        )  # Tests should have descriptive names
        checks.append(well_named)

        # Check for no sleeps (indicates potential flakiness)
        no_sleeps = "time.sleep" not in tests and "sleep(" not in tests
        checks.append(no_sleeps)

        score = self._score_from_checks(checks)

        if not has_assertions:
            defects.append(
                self._create_defect(
                    description="Tests missing assertions",
                    severity="high",
                    suggestion="Add meaningful assertions to verify behavior",
                )
            )

        if not no_sleeps:
            defects.append(
                self._create_defect(
                    description="Tests contain sleep() calls (potential flakiness)",
                    severity="low",
                    suggestion="Use proper waiting mechanisms instead of sleep",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "test_count": test_count,
                "assertion_count": assertion_count,
                "assertions_per_test": assertions_per_test,
                "has_isolation": has_isolation,
                "well_named": well_named,
                "no_sleeps": no_sleeps,
            },
            defects=defects,
        )


class MockStubValidator(BaseValidator):
    """
    TS-04: Mock/Stub Appropriateness Validator

    Checks for appropriate mocking, test doubles, and dependency isolation.

    Scoring:
    - 100%: Optimal mocking usage
    - 75%: Good mocking practices
    - 50%: Some issues
    - 0%: Poor mocking
    """

    category_id = "TS-04"
    category_name = "Mock/Stub Appropriateness"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate mock/stub usage."""
        tests = context.get("tests", "") or context.get("test_content", "")
        if not tests:
            return self._create_validation_result(
                score=80.0,
                details={"note": "No test content provided"},
            )

        defects = []
        checks = []

        # Check for mock usage
        has_mocks = any(
            kw in tests for kw in ["Mock", "MagicMock", "mock", "patch", "stub", "fake"]
        )
        checks.append(has_mocks)

        # Check for proper mock configuration
        has_proper_config = any(
            kw in tests for kw in ["return_value", "side_effect", "spec=", "wraps="]
        )
        checks.append(has_proper_config)

        # Check for mock assertions
        has_mock_assertions = any(
            kw in tests
            for kw in [
                "assert_called",
                "assert_not_called",
                "assert_called_with",
                "called_once",
                "call_count",
            ]
        )
        checks.append(has_mock_assertions)

        # Check for over-mocking (all external calls mocked)
        # This is a heuristic - too many mocks might indicate poor test design
        mock_count = tests.lower().count("mock")
        patch_count = tests.count("patch")
        over_mocked = mock_count > 10 and patch_count > 5
        checks.append(not over_mocked)

        score = self._score_from_checks(checks)

        if not has_mocks and any(
            kw in tests for kw in ["db", "database", "api", "external", "service"]
        ):
            defects.append(
                self._create_defect(
                    description="External dependencies not mocked",
                    severity="medium",
                    suggestion="Use mocks for external dependencies to isolate tests",
                )
            )

        if over_mocked:
            defects.append(
                self._create_defect(
                    description="Excessive mocking detected",
                    severity="low",
                    suggestion="Consider testing more real interactions",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "has_mocks": has_mocks,
                "proper_config": has_proper_config,
                "mock_assertions": has_mock_assertions,
                "over_mocked": over_mocked,
                "mock_count": mock_count,
                "patch_count": patch_count,
            },
            defects=defects,
        )
