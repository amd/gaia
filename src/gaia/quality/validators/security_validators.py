"""
GAIA Security and Best Practices Validators

Validators for security practices and other best practices (BP-01 through BP-05).
"""

import re
from typing import Any, Dict, List, Optional

from gaia.quality.validators.base import BaseValidator, ValidationResult


class SecurityValidator(BaseValidator):
    """
    BP-01: Security Practices Validator

    Checks for input validation, SQL injection prevention, XSS prevention,
    authentication checks, and secret handling.

    Scoring:
    - 100%: All security practices followed
    - 75%: Mostly secure
    - 50%: Basic security
    - 0%: Vulnerable
    """

    category_id = "BP-01"
    category_name = "Security Practices"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate security practices."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []
        checks = []

        # Check for hardcoded secrets
        secrets_patterns = [
            (r'password\s*=\s*["\'][^"\']+["\']', "Hardcoded password"),
            (r'api_key\s*=\s*["\'][^"\']+["\']', "Hardcoded API key"),
            (r'secret\s*=\s*["\'][^"\']+["\']', "Hardcoded secret"),
            (r'token\s*=\s*["\'][^"\']+["\']', "Hardcoded token"),
            (r"AWS_SECRET", "AWS secret in code"),
        ]

        has_hardcoded_secrets = False
        for pattern, description in secrets_patterns:
            if re.search(pattern, code, re.IGNORECASE):
                has_hardcoded_secrets = True
                defects.append(
                    self._create_defect(
                        description=f"{description} detected",
                        severity="critical",
                        category="security",
                        suggestion="Use environment variables for secrets",
                    )
                )
        checks.append(not has_hardcoded_secrets)

        # Check for SQL injection prevention
        sql_keywords = ["SELECT", "INSERT", "UPDATE", "DELETE", "DROP"]
        has_raw_sql = any(
            kw in code.upper() and "execute(" in code for kw in sql_keywords
        )
        has_parameterized = any(
            kw in code
            for kw in [
                "parameterized",
                "prepared",
                "placeholder",
                "?, %s, :",
                "sqlalchemy",
                "orm",
            ]
        )
        sql_safe = not has_raw_sql or has_parameterized
        checks.append(sql_safe)

        if has_raw_sql and not has_parameterized:
            defects.append(
                self._create_defect(
                    description="Potential SQL injection risk - raw SQL without parameterization",
                    severity="high",
                    category="security",
                    suggestion="Use parameterized queries or ORM",
                )
            )

        # Check for input validation
        has_validation = any(
            kw in code
            for kw in [
                "validate",
                "sanitize",
                "escape",
                "html.escape",
                "isinstance",
                "assert",
                "schema",
                "validator",
            ]
        )
        checks.append(has_validation)

        # Check for authentication/authorization
        has_auth = any(
            kw in code
            for kw in [
                "authenticate",
                "authorize",
                "permission",
                "login",
                "auth",
                "session",
                "token",
                "jwt",
                "oauth",
            ]
        )
        # Only check auth if it's a web application
        is_web_app = any(
            kw in code
            for kw in ["route", "endpoint", "request", "flask", "django", "fastapi"]
        )
        if is_web_app:
            checks.append(has_auth)
            if not has_auth:
                defects.append(
                    self._create_defect(
                        description="No authentication/authorization detected for web application",
                        severity="medium",
                        category="security",
                        suggestion="Implement authentication for protected endpoints",
                    )
                )
        else:
            checks.append(True)  # N/A for non-web apps

        # Check for XSS prevention
        xss_safe = any(
            kw in code
            for kw in [
                "escape",
                "html.escape",
                "mark_safe",
                "sanitize",
                "XSS",
                "content_security_policy",
            ]
        )
        has_html_output = "html" in code.lower() or "render" in code.lower()
        if has_html_output:
            checks.append(xss_safe)
        else:
            checks.append(True)  # N/A

        score = self._score_from_checks(checks)

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "has_hardcoded_secrets": has_hardcoded_secrets,
                "sql_safe": sql_safe,
                "has_validation": has_validation,
                "has_auth": has_auth if is_web_app else "N/A",
                "xss_safe": xss_safe if has_html_output else "N/A",
            },
            defects=defects,
        )


class PerformanceValidator(BaseValidator):
    """
    BP-02: Performance Optimization Validator

    Checks for algorithm efficiency, memory usage, database queries, and caching.

    Scoring:
    - 100%: Optimized
    - 75%: Good performance
    - 50%: Acceptable
    - 0%: Poor performance
    """

    category_id = "BP-02"
    category_name = "Performance Optimization"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate performance optimization."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []
        checks = []

        # Check for inefficient patterns
        inefficient_patterns = [
            (
                r"for\s+\w+\s+in\s+range\(len\(",
                "Use enumerate() instead of range(len())",
            ),
            (r"\.append\([^)]*\)\s*inside\s*loop", "Consider list comprehension"),
            (r"while\s+True:", "Potential infinite loop"),
        ]

        has_inefficient = False
        for pattern, suggestion in inefficient_patterns:
            if re.search(pattern, code):
                has_inefficient = True
                defects.append(
                    self._create_defect(
                        description=suggestion,
                        severity="low",
                        suggestion=suggestion,
                    )
                )
        checks.append(not has_inefficient)

        # Check for caching
        has_caching = any(
            kw in code
            for kw in [
                "cache",
                "lru_cache",
                "memoize",
                "redis",
                "memcached",
                "@cache",
                "@lru_cache",
            ]
        )
        checks.append(has_caching)

        # Check for database optimization
        has_db = any(kw in code for kw in ["SELECT", "query", "database", "db."])
        has_optimization = any(
            kw in code
            for kw in [
                "index",
                "join",
                "select_related",
                "prefetch_related",
                "LIMIT",
                "batch",
                "bulk",
            ]
        )
        if has_db:
            checks.append(has_optimization)
            if not has_optimization:
                defects.append(
                    self._create_defect(
                        description="Database queries may not be optimized",
                        severity="medium",
                        suggestion="Consider indexing and query optimization",
                    )
                )
        else:
            checks.append(True)  # N/A

        # Check for lazy loading / generators
        has_generator = "yield " in code or "generator" in code.lower()
        has_iter = any(kw in code for kw in ["iter(", "itertools"])
        checks.append(has_generator or has_iter)

        # Check for complexity (O(n^2) patterns)
        nested_loops = code.count("for ") >= 2 or code.count("while ") >= 2
        if nested_loops:
            defects.append(
                self._create_defect(
                    description="Multiple nested loops detected - check time complexity",
                    severity="low",
                    suggestion="Consider algorithmic optimization",
                )
            )

        score = self._score_from_checks(checks)

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "has_caching": has_caching,
                "has_optimization": has_optimization if has_db else "N/A",
                "has_generator": has_generator,
                "nested_loops": nested_loops,
            },
            defects=defects,
        )


class AccessibilityValidator(BaseValidator):
    """
    BP-03: Accessibility Compliance Validator

    Checks for WCAG compliance, ARIA labels, keyboard navigation, and color contrast.

    Scoring:
    - 100%: WCAG AA+ compliant
    - 75%: WCAG A compliant
    - 50%: Basic accessibility
    - 0%: No accessibility
    """

    category_id = "BP-03"
    category_name = "Accessibility Compliance"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate accessibility compliance."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []

        # Check if this is UI code
        is_ui_code = any(
            kw in code.lower()
            for kw in [
                "html",
                "jsx",
                "react",
                "vue",
                "angular",
                "component",
                "<div",
                "<button",
                "<input",
                "<a ",
                "render",
            ]
        )

        if not is_ui_code:
            return self._create_validation_result(
                score=100.0,
                details={"note": "Not UI code - accessibility N/A"},
            )

        checks = []

        # Check for alt text on images
        has_images = "<img" in code
        has_alt = "alt=" in code
        if has_images:
            checks.append(has_alt)
            if not has_alt:
                defects.append(
                    self._create_defect(
                        description="Images missing alt text",
                        severity="medium",
                        category="accessibility",
                        suggestion="Add descriptive alt attributes to images",
                    )
                )
        else:
            checks.append(True)

        # Check for ARIA labels
        has_interactive = any(
            kw in code for kw in ["<button", "<a ", "onClick", "onPress"]
        )
        has_aria = "aria-" in code or "role=" in code
        if has_interactive:
            checks.append(has_aria)
            if not has_aria:
                defects.append(
                    self._create_defect(
                        description="Interactive elements missing ARIA labels",
                        severity="low",
                        category="accessibility",
                        suggestion="Add ARIA labels for screen readers",
                    )
                )
        else:
            checks.append(True)

        # Check for form labels
        has_forms = "<input" in code or "<form" in code or "<select" in code
        has_labels = "<label" in code or "htmlFor" in code or "aria-label" in code
        if has_forms:
            checks.append(has_labels)
            if not has_labels:
                defects.append(
                    self._create_defect(
                        description="Form inputs missing labels",
                        severity="medium",
                        category="accessibility",
                        suggestion="Add labels to form inputs",
                    )
                )
        else:
            checks.append(True)

        score = self._score_from_checks(checks)

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "has_images": has_images,
                "has_alt": has_alt,
                "has_interactive": has_interactive,
                "has_aria": has_aria,
                "has_forms": has_forms,
                "has_labels": has_labels,
            },
            defects=defects,
        )


class LoggingMonitoringValidator(BaseValidator):
    """
    BP-04: Logging/Monitoring Validator

    Checks for log statements, log levels, structured logging, and metrics.

    Scoring:
    - 100%: Comprehensive logging
    - 75%: Good logging
    - 50%: Basic logs
    - 0%: No logging
    """

    category_id = "BP-04"
    category_name = "Logging/Monitoring"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate logging and monitoring."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []
        checks = []

        # Check for logging imports
        has_logging = any(
            kw in code for kw in ["import logging", "from logging", "logger", "log."]
        )
        checks.append(has_logging)

        # Check for multiple log levels
        log_levels = ["debug", "info", "warning", "error", "critical"]
        used_levels = sum(1 for level in log_levels if f".{level}(" in code.lower())
        has_multiple_levels = used_levels >= 2
        checks.append(has_multiple_levels)

        # Check for structured logging
        has_structured = (
            any(
                kw in code
                for kw in ["json", "extra=", "context", "structured", "fields"]
            )
            and has_logging
        )
        checks.append(has_structured)

        # Check for error logging in exception handlers
        has_exception_logging = "except" in code and any(
            f".{level}(" in code for level in ["error", "exception", "critical"]
        )
        has_try = "try:" in code
        if has_try:
            checks.append(has_exception_logging)
            if not has_exception_logging:
                defects.append(
                    self._create_defect(
                        description="Exception handlers missing error logging",
                        severity="medium",
                        suggestion="Log exceptions for debugging",
                    )
                )
        else:
            checks.append(True)  # N/A

        # Check for metrics/telemetry
        has_metrics = any(
            kw in code
            for kw in [
                "metrics",
                "telemetry",
                "prometheus",
                "statsd",
                "counter",
                "histogram",
                "gauge",
                "trace",
                "span",
            ]
        )
        checks.append(has_metrics)

        score = self._score_from_checks(checks)

        if not has_logging:
            defects.append(
                self._create_defect(
                    description="No logging detected",
                    severity="medium",
                    suggestion="Add logging for debugging and monitoring",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "has_logging": has_logging,
                "log_levels_used": used_levels,
                "has_structured": has_structured,
                "has_exception_logging": has_exception_logging,
                "has_metrics": has_metrics,
            },
            defects=defects,
        )


class ConfigurationValidator(BaseValidator):
    """
    BP-05: Configuration Management Validator

    Checks for environment variables, config files, default values, and validation.

    Scoring:
    - 100%: Well configured
    - 75%: Good configuration
    - 50%: Basic configuration
    - 0%: Hardcoded values
    """

    category_id = "BP-05"
    category_name = "Configuration Management"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """Validate configuration management."""
        code = artifact if isinstance(artifact, str) else str(artifact)
        defects = []
        checks = []

        # Check for environment variable usage
        has_env_vars = any(
            kw in code
            for kw in [
                "os.environ",
                "os.getenv",
                "getenv",
                "env(",
                "process.env",
                "environ[",
            ]
        )
        checks.append(has_env_vars)

        # Check for config file usage
        has_config = any(
            kw in code
            for kw in [
                ".yaml",
                ".yml",
                ".json",
                ".toml",
                ".ini",
                ".cfg",
                "config",
                "settings",
                "Config",
            ]
        )
        checks.append(has_config)

        # Check for default values
        has_defaults = (
            "default=" in code or "or " in code or "?? " in code or "get(" in code
        )
        checks.append(has_defaults)

        # Check for configuration validation
        has_validation = any(
            kw in code
            for kw in [
                "validate",
                "validator",
                "schema",
                "pydantic",
                "marshmallow",
                "cerberus",
                "check",
                "verify",
            ]
        )
        checks.append(has_validation)

        # Check for hardcoded values that should be config
        hardcoded_issues = []
        patterns = [
            (r'[\'"]localhost[\'"]', "Hardcoded localhost"),
            (r'[\'"]127\.0\.0\.1[\'"]', "Hardcoded IP address"),
            (r"port\s*=\s*\d{4,5}", "Hardcoded port number"),
            (r'[\'"]postgres://[^$]', "Hardcoded database URL"),
        ]

        for pattern, description in patterns:
            if re.search(pattern, code):
                hardcoded_issues.append(description)

        if hardcoded_issues:
            checks.append(False)
            for issue in hardcoded_issues[:3]:
                defects.append(
                    self._create_defect(
                        description=issue,
                        severity="low",
                        suggestion="Move to environment variable or config file",
                    )
                )
        else:
            checks.append(True)

        score = self._score_from_checks(checks)

        if not has_env_vars and not has_config:
            defects.append(
                self._create_defect(
                    description="No external configuration detected",
                    severity="medium",
                    suggestion="Use environment variables or config files",
                )
            )

        return self._create_validation_result(
            score=score,
            tests_run=len(checks),
            tests_passed=sum(checks),
            details={
                "has_env_vars": has_env_vars,
                "has_config": has_config,
                "has_defaults": has_defaults,
                "has_validation": has_validation,
                "hardcoded_issues": len(hardcoded_issues),
            },
            defects=defects,
        )
