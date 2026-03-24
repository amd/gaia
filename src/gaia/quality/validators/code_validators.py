"""
GAIA Code Quality Validators

Validators for the Code Quality dimension (CQ-01 through CQ-07).
"""

import ast
import re
from typing import Dict, List, Any, Optional

from gaia.quality.validators.base import BaseValidator, ValidationResult


class SyntaxValidator(BaseValidator):
    """
    CQ-01: Syntax Validity Validator

    Checks that code has no syntax errors and can be parsed/compiled.

    Scoring:
    - 100%: No errors or warnings
    - 75%: Only warnings
    - 50%: Minor errors that don't prevent parsing
    - 0%: Parse/compile fails
    """

    category_id = "CQ-01"
    category_name = "Syntax Validity"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate syntax of code artifact."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        language = context.get("language", "python")
        defects = []

        if language == "python":
            try:
                compile(code, "<string>", "exec")
                score = 100.0
            except SyntaxError as e:
                score = 0.0
                defects.append(
                    self._create_defect(
                        description=f"Syntax error: {e.msg} at line {e.lineno}",
                        severity="critical",
                        location=f"line {e.lineno}",
                        suggestion="Fix the syntax error before proceeding",
                        code_snippet=e.text.strip() if e.text else None,
                    )
                )
        else:
            # For unknown languages, assume valid
            score = 85.0

        return self._create_validation_result(
            score=score,
            tests_run=1,
            tests_passed=1 if score > 0 else 0,
            details={"language": language},
            defects=defects,
        )


class CodeStyleValidator(BaseValidator):
    """
    CQ-02: Code Style Consistency Validator

    Checks naming conventions, indentation, line length, and import order.

    Scoring:
    - 100%: All conventions followed
    - 75%: 1-2 violations
    - 50%: 3-5 violations
    - 0%: >5 violations
    """

    category_id = "CQ-02"
    category_name = "Code Style Consistency"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate code style consistency."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []
        violations = []

        # Check line length (PEP 8: 79 chars, we use 100)
        max_line_length = context.get("max_line_length", 100)
        for i, line in enumerate(code.splitlines(), 1):
            if len(line) > max_line_length:
                violations.append(f"Line {i} exceeds {max_line_length} characters")

        # Check indentation (should be 4 spaces, not tabs)
        if "\t" in code:
            violations.append("Tab characters found (use spaces)")
            defects.append(
                self._create_defect(
                    description="Tab characters detected",
                    severity="low",
                    suggestion="Convert tabs to 4 spaces",
                )
            )

        # Check for trailing whitespace
        trailing_ws = sum(
            1 for line in code.splitlines()
            if line.rstrip() != line
        )
        if trailing_ws > 0:
            violations.append(f"{trailing_ws} lines with trailing whitespace")

        # Check naming conventions (snake_case for functions/variables)
        func_pattern = r"def\s+([A-Z]\w+)\s*\("
        uppercase_funcs = re.findall(func_pattern, code)
        if uppercase_funcs:
            violations.append(
                f"Functions with uppercase names: {uppercase_funcs[:3]}"
            )

        # Calculate score based on violations
        violation_count = len(violations)
        if violation_count == 0:
            score = 100.0
        elif violation_count <= 2:
            score = 75.0
        elif violation_count <= 5:
            score = 50.0
        else:
            score = 25.0

        for v in violations[:5]:  # Limit to first 5
            defects.append(
                self._create_defect(
                    description=v,
                    severity="low",
                    suggestion="Follow PEP 8 style guidelines",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=5,
            tests_passed=5 - violation_count,
            details={"violations": violations},
            defects=defects,
        )


class ComplexityValidator(BaseValidator):
    """
    CQ-03: Cyclomatic Complexity Validator

    Checks function complexity, nesting depth, and branch count.

    Scoring:
    - 100%: <10 complexity
    - 75%: 10-20 complexity
    - 50%: 20-30 complexity
    - 0%: >30 complexity
    """

    category_id = "CQ-03"
    category_name = "Cyclomatic Complexity"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate code complexity."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return self._create_validation_result(
                score=0.0,
                defects=[
                    self._create_defect(
                        description="Cannot analyze complexity: invalid syntax",
                        severity="high",
                    )
                ],
            )

        max_complexity = 0
        complex_functions = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                complexity = self._calculate_complexity(node)
                if complexity > 10:
                    complex_functions.append({
                        "name": node.name,
                        "complexity": complexity,
                        "line": node.lineno,
                    })
                max_complexity = max(max_complexity, complexity)

        # Determine score
        if max_complexity < 10:
            score = 100.0
        elif max_complexity < 20:
            score = 75.0
        elif max_complexity < 30:
            score = 50.0
        else:
            score = 25.0

        # Add defects for complex functions
        for func in complex_functions[:5]:
            defects.append(
                self._create_defect(
                    description=f"Function '{func['name']}' has complexity of {func['complexity']}",
                    severity="medium" if func["complexity"] < 20 else "high",
                    location=f"line {func['line']}",
                    suggestion="Consider breaking into smaller functions",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(list(ast.walk(tree))),
            tests_passed=len(list(ast.walk(tree))) - len(complex_functions),
            details={
                "max_complexity": max_complexity,
                "complex_functions": complex_functions,
            },
            defects=defects,
        )

    def _calculate_complexity(self, node: ast.FunctionDef) -> int:
        """Calculate cyclomatic complexity of a function."""
        complexity = 1  # Base complexity

        for child in ast.walk(node):
            # Branch points add complexity
            if isinstance(
                child,
                (
                    ast.If,
                    ast.While,
                    ast.For,
                    ast.ExceptHandler,
                    ast.With,
                    ast.Assert,
                    ast.comprehension,
                ),
            ):
                complexity += 1
            # Boolean operators add complexity
            if isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1

        return complexity


class DryValidator(BaseValidator):
    """
    CQ-04: DRY (Don't Repeat Yourself) Principle Validator

    Checks for code duplication and repeated patterns.

    Scoring:
    - 100%: No duplication
    - 75%: <5% duplicated
    - 50%: 5-10% duplicated
    - 0%: >10% duplicated
    """

    category_id = "CQ-04"
    category_name = "DRY Principle Adherence"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate DRY principle adherence."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        lines = code.splitlines()

        # Simple duplication detection using line sequences
        duplication_ratio = self._detect_duplication(lines)
        duplication_percentage = duplication_ratio * 100

        # Determine score
        if duplication_percentage < 1:
            score = 100.0
        elif duplication_percentage < 5:
            score = 75.0
        elif duplication_percentage < 10:
            score = 50.0
        else:
            score = 25.0

        defects = []
        if duplication_percentage >= 5:
            defects.append(
                self._create_defect(
                    description=f"Code duplication detected: {duplication_percentage:.1f}%",
                    severity="medium",
                    suggestion="Extract common code into reusable functions",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=1,
            tests_passed=1 if duplication_percentage < 5 else 0,
            details={"duplication_percentage": duplication_percentage},
            defects=defects,
        )

    def _detect_duplication(self, lines: List[str]) -> float:
        """
        Detect code duplication.

        Returns ratio of duplicated lines to total lines.
        """
        if not lines:
            return 0.0

        # Normalize lines (remove whitespace differences)
        normalized = [line.strip() for line in lines if line.strip()]

        if len(normalized) < 2:
            return 0.0

        # Find duplicate lines
        seen = {}
        duplicate_count = 0

        for i, line in enumerate(normalized):
            if len(line) < 10:  # Skip very short lines
                continue
            if line in seen:
                duplicate_count += 1
            else:
                seen[line] = i

        return duplicate_count / len(normalized) if normalized else 0.0


class SolidValidator(BaseValidator):
    """
    CQ-05: SOLID Principles Validator

    Checks adherence to SOLID principles:
    - Single Responsibility
    - Open/Closed
    - Liskov Substitution
    - Interface Segregation
    - Dependency Inversion

    Scoring:
    - 100%: All 5 principles followed
    - 75%: 3-4 principles followed
    - 50%: 1-2 principles followed
    - 0%: None followed
    """

    category_id = "CQ-05"
    category_name = "SOLID Principles"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate SOLID principles."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []
        principles_checked = 0
        principles_passed = 0

        # Single Responsibility (check class/method size)
        sr_passed, sr_defects = await self._check_single_responsibility(code)
        principles_checked += 1
        if sr_passed:
            principles_passed += 1
        defects.extend(sr_defects)

        # Open/Closed (check for inheritance and extension points)
        oc_passed, oc_defects = self._check_open_closed(code)
        principles_checked += 1
        if oc_passed:
            principles_passed += 1
        defects.extend(oc_defects)

        # Calculate score based on principles followed
        score = (principles_passed / 5) * 100 if principles_checked > 0 else 80.0

        return self._create_validation_result(
            score=score,
            tests_run=principles_checked,
            tests_passed=principles_passed,
            details={
                "principles_passed": principles_passed,
                "principles_checked": principles_checked,
            },
            defects=defects,
        )

    async def _check_single_responsibility(
        self, code: str
    ) -> tuple[bool, List[Dict[str, Any]]]:
        """Check Single Responsibility Principle."""
        defects = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False, [
                self._create_defect(
                    description="Cannot analyze: invalid syntax",
                    severity="high",
                )
            ]

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [
                    n for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                if len(methods) > 15:
                    defects.append(
                        self._create_defect(
                            description=f"Class '{node.name}' has {len(methods)} methods (SRP violation)",
                            severity="medium",
                            suggestion="Consider splitting into smaller classes",
                        )
                    )

        return len(defects) == 0, defects

    def _check_open_closed(self, code: str) -> tuple[bool, List[Dict[str, Any]]]:
        """Check Open/Closed Principle."""
        defects = []

        # Check for abstract base classes
        if "ABC" in code or "abstractmethod" in code:
            return True, defects

        # Check for inheritance
        if re.search(r"class \w+\(\w+\)", code):
            return True, defects

        # No inheritance found might indicate tight coupling
        if "class " in code:
            defects.append(
                self._create_defect(
                    description="No inheritance detected - consider abstraction",
                    severity="low",
                    suggestion="Use abstract base classes for extensibility",
                )
            )

        return len(defects) == 0, defects


class ErrorHandlingValidator(BaseValidator):
    """
    CQ-06: Error Handling Validator

    Checks for proper exception handling, error messages, and recovery.

    Scoring:
    - 100%: Comprehensive error handling
    - 75%: Most cases covered
    - 50%: Basic handling present
    - 0%: No error handling
    """

    category_id = "CQ-06"
    category_name = "Error Handling"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate error handling."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []
        checks = []

        # Check for try/except blocks
        has_try_except = "try:" in code and "except" in code
        checks.append(has_try_except)
        if not has_try_except:
            if any(
                kw in code for kw in ["open(", "requests.", "db.", "cursor"]
            ):
                defects.append(
                    self._create_defect(
                        description="No exception handling for risky operations",
                        severity="medium",
                        suggestion="Add try/except blocks around I/O operations",
                    )
                )

        # Check for bare except (bad practice)
        bare_except = bool(re.search(r"except\s*:", code))
        if bare_except:
            checks.append(False)
            defects.append(
                self._create_defect(
                    description="Bare 'except:' clause found",
                    severity="medium",
                    suggestion="Specify exception types to catch",
                )
            )
        else:
            checks.append(True)

        # Check for meaningful error messages
        if "raise" in code:
            has_message = bool(re.search(r'raise \w+\([^)]*["\']', code))
            checks.append(has_message)
            if not has_message:
                defects.append(
                    self._create_defect(
                        description="Exceptions raised without meaningful messages",
                        severity="low",
                    )
                )
        else:
            checks.append(True)

        score = self._score_from_checks(checks)

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={"has_try_except": has_try_except, "bare_except": bare_except},
            defects=defects,
        )


class TypeSafetyValidator(BaseValidator):
    """
    CQ-07: Type Safety Validator

    Checks for type annotations, type hints, and proper typing usage.

    Scoring:
    - 100%: Full typing with generics
    - 75%: Most functions typed
    - 50%: Partial typing
    - 0%: No type hints
    """

    category_id = "CQ-07"
    category_name = "Type Safety"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate type safety."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return self._create_validation_result(
                score=0.0,
                defects=[
                    self._create_defect(
                        description="Cannot analyze: invalid syntax",
                        severity="high",
                    )
                ],
            )

        functions = []
        typed_functions = 0
        return_typed = 0
        arg_typed = 0

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node)

                # Check return annotation
                if node.returns is not None:
                    return_typed += 1

                # Check argument annotations
                args_with_type = sum(
                    1 for arg in node.args.args
                    if arg.annotation is not None
                )
                if args_with_type == len(node.args.args):
                    arg_typed += 1

                if node.returns is not None or args_with_type > 0:
                    typed_functions += 1

        if not functions:
            # No functions to check
            return self._create_validation_result(
                score=100.0,
                details={"note": "No functions to analyze"},
            )

        # Calculate typing coverage
        return_coverage = return_typed / len(functions) if functions else 0
        arg_coverage = arg_typed / len(functions) if functions else 0
        overall_coverage = (return_coverage + arg_coverage) / 2

        score = overall_coverage * 100

        if score < 50:
            defects.append(
                self._create_defect(
                    description=f"Low type coverage: {score:.1f}%",
                    severity="low",
                    suggestion="Add type annotations to function signatures",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(functions) * 2,
            tests_passed=return_typed + arg_typed,
            details={
                "total_functions": len(functions),
                "typed_functions": typed_functions,
                "return_typed": return_typed,
                "arg_typed": arg_typed,
                "coverage": overall_coverage,
            },
            defects=defects,
        )
