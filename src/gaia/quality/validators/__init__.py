"""
GAIA Quality Validators

Validators for each of the 27 quality categories.
"""

from gaia.quality.validators.base import BaseValidator, ValidationResult
from gaia.quality.validators.code_validators import (
    CodeStyleValidator,
    ComplexityValidator,
    DryValidator,
    ErrorHandlingValidator,
    SolidValidator,
    SyntaxValidator,
    TypeSafetyValidator,
)
from gaia.quality.validators.docs_validators import (
    ApiDocumentationValidator,
    DocstringsValidator,
    ReadmeValidator,
    UsageExamplesValidator,
)
from gaia.quality.validators.requirements_validators import (
    AcceptanceCriteriaValidator,
    EdgeCaseValidator,
    FeatureCompletenessValidator,
    UserStoryAlignmentValidator,
)
from gaia.quality.validators.security_validators import (
    AccessibilityValidator,
    ConfigurationValidator,
    LoggingMonitoringValidator,
    PerformanceValidator,
    SecurityValidator,
)
from gaia.quality.validators.test_validators import (
    IntegrationTestCoverageValidator,
    MockStubValidator,
    TestQualityValidator,
    UnitTestCoverageValidator,
)

__all__ = [
    # Base
    "BaseValidator",
    "ValidationResult",
    # Code Quality
    "SyntaxValidator",
    "CodeStyleValidator",
    "ComplexityValidator",
    "DryValidator",
    "SolidValidator",
    "ErrorHandlingValidator",
    "TypeSafetyValidator",
    # Requirements
    "FeatureCompletenessValidator",
    "EdgeCaseValidator",
    "AcceptanceCriteriaValidator",
    "UserStoryAlignmentValidator",
    # Testing
    "UnitTestCoverageValidator",
    "IntegrationTestCoverageValidator",
    "TestQualityValidator",
    "MockStubValidator",
    # Documentation
    "DocstringsValidator",
    "ReadmeValidator",
    "ApiDocumentationValidator",
    "UsageExamplesValidator",
    # Best Practices
    "SecurityValidator",
    "PerformanceValidator",
    "AccessibilityValidator",
    "LoggingMonitoringValidator",
    "ConfigurationValidator",
]
