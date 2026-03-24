"""
GAIA Quality Validators

Validators for each of the 27 quality categories.
"""

from gaia.quality.validators.base import BaseValidator, ValidationResult
from gaia.quality.validators.code_validators import (
    SyntaxValidator,
    CodeStyleValidator,
    ComplexityValidator,
    DryValidator,
    SolidValidator,
    ErrorHandlingValidator,
    TypeSafetyValidator,
)
from gaia.quality.validators.requirements_validators import (
    FeatureCompletenessValidator,
    EdgeCaseValidator,
    AcceptanceCriteriaValidator,
    UserStoryAlignmentValidator,
)
from gaia.quality.validators.test_validators import (
    UnitTestCoverageValidator,
    IntegrationTestCoverageValidator,
    TestQualityValidator,
    MockStubValidator,
)
from gaia.quality.validators.docs_validators import (
    DocstringsValidator,
    ReadmeValidator,
    ApiDocumentationValidator,
    UsageExamplesValidator,
)
from gaia.quality.validators.security_validators import (
    SecurityValidator,
    PerformanceValidator,
    AccessibilityValidator,
    LoggingMonitoringValidator,
    ConfigurationValidator,
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
