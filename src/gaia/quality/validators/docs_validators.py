"""
GAIA Documentation Validators

Validators for the Documentation dimension (DC-01 through DC-04).
"""

import re
from typing import Dict, List, Any, Optional

from gaia.quality.validators.base import BaseValidator, ValidationResult


class DocstringsValidator(BaseValidator):
    """
    DC-01: Docstrings/Comments Validator

    Checks for function docstrings, class docstrings, inline comments, and TODO markers.

    Scoring:
    - 100%: Comprehensive documentation
    - 75%: Good coverage
    - 50%: Basic documentation
    - 0%: No documentation
    """

    category_id = "DC-01"
    category_name = "Docstrings/Comments"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate docstrings and comments."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []

        # Parse code for functions and classes
        functions = re.findall(r"def (\w+)\s*\([^)]*\)\s*(?:->[^:]+)?:\s*\n\s*(['\"]{3})", code)
        classes = re.findall(r"class (\w+)[^(]*:", code)

        # Count docstrings
        docstring_pattern = r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')'
        docstrings = re.findall(docstring_pattern, code)

        # Count comments
        comment_count = len(re.findall(r"#\s*\S", code))

        # Count TODO markers
        todos = re.findall(r"#\s*(TODO|FIXME|XXX|HACK)", code, re.IGNORECASE)

        # Calculate coverage
        total_items = len(functions) + len(classes)
        docstring_coverage = (
            len(docstrings) / total_items * 100 if total_items > 0 else 100.0
        )

        # Determine score
        if docstring_coverage >= 90 and comment_count > 0:
            score = 100.0
        elif docstring_coverage >= 75:
            score = 75.0
        elif docstring_coverage >= 50:
            score = 50.0
        else:
            score = 25.0

        # Add defects
        if docstring_coverage < 75 and total_items > 0:
            defects.append(
                self._create_defect(
                    description=f"Low docstring coverage: {docstring_coverage:.1f}%",
                    severity="low",
                    suggestion="Add docstrings to public functions and classes",
                )
            )

        if len(todos) > 5:
            defects.append(
                self._create_defect(
                    description=f"Multiple TODO markers found: {len(todos)}",
                    severity="low",
                    suggestion="Address or document TODO items",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=4,
            tests_passed=sum([
                docstring_coverage >= 75,
                comment_count > 0,
                len(todos) <= 5,
                len(docstrings) > 0,
            ]),
            details={
                "functions": len(functions),
                "classes": len(classes),
                "docstrings": len(docstrings),
                "comments": comment_count,
                "todos": len(todos),
                "docstring_coverage": docstring_coverage,
            },
            defects=defects,
        )


class ReadmeValidator(BaseValidator):
    """
    DC-02: README Quality Validator

    Checks for installation steps, usage examples, API overview, and contributing guide.

    Scoring:
    - 100%: Complete README
    - 75%: Most sections present
    - 50%: Basic information
    - 0%: Missing README
    """

    category_id = "DC-02"
    category_name = "README Quality"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate README quality."""
        readme = context.get("readme", "")
        if not readme and isinstance(artifact, str):
            readme = artifact

        if not readme:
            return self._create_validation_result(
                score=0.0,
                defects=[
                    self._create_defect(
                        description="No README provided",
                        severity="medium",
                        suggestion="Add a README.md with project documentation",
                    )
                ],
            )

        defects = []
        checks = []

        # Check for installation section
        has_installation = any(
            kw in readme.lower() for kw in [
                "install", "setup", "installation", "requirements",
                "pip install", "npm install", "dependencies"
            ]
        )
        checks.append(has_installation)

        # Check for usage examples
        has_usage = any(
            kw in readme.lower() for kw in [
                "usage", "example", "quickstart", "getting started",
                "```", "code"
            ]
        )
        checks.append(has_usage)

        # Check for API overview
        has_api = any(
            kw in readme.lower() for kw in [
                "api", "reference", "methods", "functions", "classes",
                "interface", "endpoint"
            ]
        )
        checks.append(has_api)

        # Check for contributing guide
        has_contributing = any(
            kw in readme.lower() for kw in [
                "contribut", "develop", "development", "pull request",
                "issue", "license"
            ]
        )
        checks.append(has_contributing)

        # Check for project description
        has_description = len(readme.split()) > 50 and (
            "#" in readme or "##" in readme
        )
        checks.append(has_description)

        score = self._score_from_checks(checks)

        if not has_installation:
            defects.append(
                self._create_defect(
                    description="Missing installation instructions",
                    severity="medium",
                    suggestion="Add installation/setup instructions",
                )
            )

        if not has_usage:
            defects.append(
                self._create_defect(
                    description="Missing usage examples",
                    severity="medium",
                    suggestion="Add code examples showing how to use the project",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "has_installation": has_installation,
                "has_usage": has_usage,
                "has_api": has_api,
                "has_contributing": has_contributing,
                "has_description": has_description,
                "readme_length": len(readme.split()),
            },
            defects=defects,
        )


class ApiDocumentationValidator(BaseValidator):
    """
    DC-03: API Documentation Validator

    Checks for endpoint docs, parameter descriptions, response examples, and error codes.

    Scoring:
    - 100%: Full API documentation
    - 75%: Most documented
    - 50%: Basic documentation
    - 0%: No API documentation
    """

    category_id = "DC-03"
    category_name = "API Documentation"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate API documentation."""
        api_docs = context.get("api_docs", "") or context.get("documentation", "")
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []

        # Try to extract API endpoints from code if no docs provided
        if not api_docs:
            endpoints = self._extract_endpoints(code)
            if not endpoints:
                return self._create_validation_result(
                    score=50.0,
                    details={"note": "No API documentation or endpoints found"},
                )
        else:
            endpoints = self._extract_endpoints(api_docs)

        checks = []

        # Check for endpoint documentation
        has_endpoints = len(endpoints) > 0
        checks.append(has_endpoints)

        # Check for parameter descriptions
        has_params = any(
            kw in (api_docs or code).lower() for kw in [
                "param", "argument", "args", "request body",
                "query param", "path param"
            ]
        )
        checks.append(has_params)

        # Check for response documentation
        has_response = any(
            kw in (api_docs or code).lower() for kw in [
                "returns", "response", "return type", "example",
                "200", "400", "404", "500"
            ]
        )
        checks.append(has_response)

        # Check for error documentation
        has_errors = any(
            kw in (api_docs or code).lower() for kw in [
                "error", "exception", "raises", "throws",
                "status code", "http error"
            ]
        )
        checks.append(has_errors)

        score = self._score_from_checks(checks)

        if not has_params:
            defects.append(
                self._create_defect(
                    description="Missing parameter descriptions",
                    severity="medium",
                    suggestion="Document all API parameters",
                )
            )

        if not has_response:
            defects.append(
                self._create_defect(
                    description="Missing response documentation",
                    severity="medium",
                    suggestion="Document response format and examples",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "endpoints_documented": len(endpoints),
                "has_params": has_params,
                "has_response": has_response,
                "has_errors": has_errors,
            },
            defects=defects,
        )

    def _extract_endpoints(self, content: str) -> List[str]:
        """Extract API endpoints from content."""
        endpoints = []

        # Flask/FastAPI style routes
        flask_routes = re.findall(r'@\w+\.route\([\'"]([^\'"]+)[\'"]', content)
        endpoints.extend(flask_routes)

        # Express.js style routes
        express_routes = re.findall(r'\w+\.(get|post|put|delete)\([\'"]([^\'"]+)[\'"]', content)
        endpoints.extend([r[1] for r in express_routes])

        # OpenAPI/Swagger paths
        openapi_paths = re.findall(r'^\s{2}(/[\w{/}-]+):\s*$', content, re.MULTILINE)
        endpoints.extend(openapi_paths)

        return endpoints


class UsageExamplesValidator(BaseValidator):
    """
    DC-04: Usage Examples Validator

    Checks for code examples, tutorial content, common patterns, and edge case examples.

    Scoring:
    - 100%: Comprehensive examples
    - 75%: Good examples
    - 50%: Basic examples
    - 0%: No examples
    """

    category_id = "DC-04"
    category_name = "Usage Examples"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate usage examples."""
        docs = context.get("documentation", "") or context.get("examples", "")
        if not docs and isinstance(artifact, str):
            docs = artifact

        if not docs:
            return self._create_validation_result(
                score=0.0,
                defects=[
                    self._create_defect(
                        description="No documentation or examples provided",
                        severity="medium",
                        suggestion="Add usage examples and documentation",
                    )
                ],
            )

        defects = []
        checks = []

        # Check for code blocks
        code_blocks = re.findall(r"```[\s\S]*?```", docs)
        has_examples = len(code_blocks) > 0
        checks.append(has_examples)

        # Check for import statements (indicates runnable examples)
        has_imports = "import " in docs or "from " in docs
        checks.append(has_imports)

        # Check for step-by-step content
        has_steps = any(
            kw in docs.lower() for kw in [
                "step", "first", "then", "next", "finally",
                "1.", "2.", "3.", "##"
            ]
        )
        checks.append(has_steps)

        # Check for common patterns
        has_patterns = any(
            kw in docs.lower() for kw in [
                "pattern", "common", "typical", "example",
                "use case", "scenario"
            ]
        )
        checks.append(has_patterns)

        # Check for edge case examples
        has_edge_cases = any(
            kw in docs.lower() for kw in [
                "edge", "corner", "special", "boundary",
                "error", "invalid", "empty"
            ]
        )
        checks.append(has_edge_cases)

        score = self._score_from_checks(checks)

        if not has_examples:
            defects.append(
                self._create_defect(
                    description="No code examples found",
                    severity="medium",
                    suggestion="Add code examples showing typical usage",
                )
            )

        if not has_steps:
            defects.append(
                self._create_defect(
                    description="No step-by-step guide found",
                    severity="low",
                    suggestion="Add a quickstart or tutorial section",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "code_blocks": len(code_blocks),
                "has_imports": has_imports,
                "has_steps": has_steps,
                "has_patterns": has_patterns,
                "has_edge_cases": has_edge_cases,
            },
            defects=defects,
        )
