"""
GAIA Requirements Validators

Validators for the Requirements Coverage dimension (RC-01 through RC-04).
"""

from typing import Dict, List, Any, Optional
import re

from gaia.quality.validators.base import BaseValidator, ValidationResult


class FeatureCompletenessValidator(BaseValidator):
    """
    RC-01: Feature Completeness Validator

    Checks that all requirements have been implemented.

    Scoring:
    - 100%: All features implemented
    - 75%: Core features implemented
    - 50%: Partial implementation
    - 0%: Missing core features
    """

    category_id = "RC-01"
    category_name = "Feature Completeness"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate feature completeness."""
        requirements = context.get("requirements", [])
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []

        if not requirements:
            return self._create_validation_result(
                score=80.0,
                details={"note": "No requirements provided for comparison"},
            )

        implemented = []
        missing = []

        for req in requirements:
            # Check if requirement keywords appear in code
            keywords = self._extract_keywords(req)
            matches = sum(1 for kw in keywords if kw.lower() in code.lower())
            match_ratio = matches / len(keywords) if keywords else 0

            if match_ratio >= 0.5:
                implemented.append(req)
            else:
                missing.append(req)

        # Calculate score
        if not requirements:
            score = 100.0
        else:
            score = (len(implemented) / len(requirements)) * 100

        # Add defects for missing requirements
        for req in missing[:5]:
            defects.append(
                self._create_defect(
                    description=f"Requirement may not be implemented: {req[:100]}",
                    severity="high",
                    suggestion="Ensure all requirements are addressed in the implementation",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(requirements),
            tests_passed=len(implemented),
            details={
                "requirements": len(requirements),
                "implemented": len(implemented),
                "missing": len(missing),
            },
            defects=defects,
        )

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract significant keywords from text."""
        # Remove common stop words
        stop_words = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "must", "shall", "can", "need",
            "it", "its", "this", "that", "these", "those", "i", "you", "he",
            "she", "we", "they", "what", "which", "who", "whom", "whose",
        }

        # Extract words
        words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
        return [w for w in words if w not in stop_words]


class EdgeCaseValidator(BaseValidator):
    """
    RC-02: Edge Case Handling Validator

    Checks for null/undefined checks, boundary conditions, and invalid input handling.

    Scoring:
    - 100%: All edge cases covered
    - 75%: Most edge cases covered
    - 50%: Basic handling present
    - 0%: No edge case handling
    """

    category_id = "RC-02"
    category_name = "Edge Case Handling"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate edge case handling."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []
        checks = []

        # Check for None/null handling
        has_none_check = (
            "is None" in code or
            "is not None" in code or
            "if not " in code or
            "!= None" in code or
            "== None" in code
        )
        checks.append(has_none_check)

        # Check for empty collection handling
        has_empty_check = (
            "len(" in code or
            "if not " in code or
            "== []" in code or
            "== {}" in code or
            '== ""' in code
        )
        checks.append(has_empty_check)

        # Check for boundary conditions
        has_boundary_check = any(
            op in code for op in [">=", "<=", ">", "<", "== 0", "!= 0"]
        )
        checks.append(has_boundary_check)

        # Check for input validation
        has_validation = any(
            kw in code for kw in [
                "isinstance", "validate", "assert", "raise",
                "if not isinstance", "TypeError", "ValueError"
            ]
        )
        checks.append(has_validation)

        # Check for error handling on risky operations
        has_error_handling = "try:" in code and "except" in code
        checks.append(has_error_handling)

        # Calculate score
        score = self._score_from_checks(checks)

        # Add defects for missing checks
        if not has_none_check:
            defects.append(
                self._create_defect(
                    description="No None/null checks detected",
                    severity="medium",
                    suggestion="Add checks for None values before use",
                )
            )

        if not has_validation:
            defects.append(
                self._create_defect(
                    description="No input validation detected",
                    severity="medium",
                    suggestion="Validate input parameters",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "none_check": has_none_check,
                "empty_check": has_empty_check,
                "boundary_check": has_boundary_check,
                "validation": has_validation,
                "error_handling": has_error_handling,
            },
            defects=defects,
        )


class AcceptanceCriteriaValidator(BaseValidator):
    """
    RC-03: Acceptance Criteria Validator

    Checks that acceptance criteria have been met and verified.

    Scoring:
    - 100%: All AC met and verified
    - 75%: Most AC met
    - 50%: Some AC met
    - 0%: No AC met
    """

    category_id = "RC-03"
    category_name = "Acceptance Criteria Met"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate acceptance criteria."""
        acceptance_criteria = context.get("acceptance_criteria", [])
        tests = context.get("tests", "")
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []

        if not acceptance_criteria:
            return self._create_validation_result(
                score=80.0,
                details={"note": "No acceptance criteria provided"},
            )

        verified = []
        partial = []
        unverified = []

        for ac in acceptance_criteria:
            ac_lower = ac.lower()

            # Check if AC is mentioned in code or tests
            in_code = any(
                kw in code.lower() for kw in self._extract_keywords(ac)
            )
            in_tests = any(
                kw in tests.lower() for kw in self._extract_keywords(ac)
            ) if tests else False

            if in_tests:
                verified.append(ac)
            elif in_code:
                partial.append(ac)
            else:
                unverified.append(ac)

        # Calculate score
        total = len(acceptance_criteria)
        score = (
            (len(verified) * 1.0 + len(partial) * 0.5) / total * 100
            if total > 0 else 80.0
        )

        # Add defects for unverified criteria
        for ac in unverified[:5]:
            defects.append(
                self._create_defect(
                    description=f"Acceptance criterion not verified: {ac[:100]}",
                    severity="high",
                    suggestion="Add tests to verify this acceptance criterion",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(acceptance_criteria),
            tests_passed=len(verified),
            details={
                "total_criteria": len(acceptance_criteria),
                "verified": len(verified),
                "partial": len(partial),
                "unverified": len(unverified),
            },
            defects=defects,
        )

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract significant keywords from text."""
        stop_words = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        }
        words = re.findall(r"\b[a-zA-Z]{4,}\b", text.lower())
        return [w for w in words if w not in stop_words]


class UserStoryAlignmentValidator(BaseValidator):
    """
    RC-04: User Story Alignment Validator

    Checks that implementation aligns with user story and delivers user value.

    Scoring:
    - 100%: Full alignment with user story
    - 75%: Good alignment
    - 50%: Partial alignment
    - 0%: Misaligned
    """

    category_id = "RC-04"
    category_name = "User Story Alignment"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate user story alignment."""
        user_story = context.get("user_story", "")
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []

        if not user_story:
            return self._create_validation_result(
                score=80.0,
                details={"note": "No user story provided"},
            )

        # Extract key elements from user story
        # User stories typically follow: As a [role], I want [feature], So that [benefit]
        story_elements = self._parse_user_story(user_story)
        code_elements = self._analyze_code_purpose(code)

        # Check alignment
        alignment_score = self._calculate_alignment(story_elements, code_elements)

        # Check for user-facing functionality
        has_user_value = self._check_user_value(code)

        # Adjust score based on user value
        if has_user_value:
            alignment_score = min(100, alignment_score + 10)
        else:
            alignment_score = max(0, alignment_score - 20)

        # Add defects if alignment is poor
        if alignment_score < 50:
            defects.append(
                self._create_defect(
                    description="Implementation may not align with user story",
                    severity="high",
                    suggestion="Review user story and ensure implementation addresses user needs",
                )
            )

        return self._create_validation_result(
            score=alignment_score,
            tests_run=2,
            tests_passed=1 if alignment_score >= 75 else 0,
            details={
                "story_elements": story_elements,
                "code_elements": code_elements,
                "has_user_value": has_user_value,
            },
            defects=defects,
        )

    def _parse_user_story(self, story: str) -> Dict[str, Any]:
        """Parse user story into elements."""
        role_match = re.search(r"As a ([^,]+)", story)
        want_match = re.search(r"(?:I want|I need) (.+?)(?:,|$)", story)
        benefit_match = re.search(r"So that (.+)", story)

        return {
            "role": role_match.group(1).strip() if role_match else "",
            "feature": want_match.group(1).strip() if want_match else "",
            "benefit": benefit_match.group(1).strip() if benefit_match else "",
        }

    def _analyze_code_purpose(self, code: str) -> Dict[str, Any]:
        """Analyze what the code is designed to do."""
        # Extract function and class names
        functions = re.findall(r"def (\w+)\s*\(", code)
        classes = re.findall(r"class (\w+)", code)

        return {
            "functions": functions,
            "classes": classes,
        }

    def _calculate_alignment(
        self,
        story: Dict[str, Any],
        code: Dict[str, Any],
    ) -> float:
        """Calculate alignment score between story and code."""
        score = 50.0  # Base score

        # Check if feature keywords appear in code
        if story["feature"]:
            feature_words = story["feature"].lower().split()
            code_text = " ".join(code["functions"] + code["classes"]).lower()
            matches = sum(1 for w in feature_words if w in code_text and len(w) > 3)
            if feature_words:
                score += (matches / len(feature_words)) * 50

        return min(100, score)

    def _check_user_value(self, code: str) -> bool:
        """Check if code provides user-facing value."""
        # Look for API endpoints, UI components, or business logic
        indicators = [
            "route", "endpoint", "view", "template", "render",
            "request", "response", "api", "controller",
            "service", "handler", "process", "create", "update", "delete",
        ]
        return any(ind in code.lower() for ind in indicators)
