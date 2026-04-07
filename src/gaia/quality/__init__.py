"""
GAIA Quality Module

Quality scoring system with 27 validation categories across 6 dimensions.
Phase 2 additions include SupervisorAgent for quality review orchestration.
"""

from gaia.quality.models import (
    CategoryScore,
    CertificationStatus,
    DimensionScore,
    QualityReport,
    QualityWeightConfig,
)
from gaia.quality.scorer import QualityScorer
from gaia.quality.supervisor import (
    SupervisorAgent,
    SupervisorDecision,
    SupervisorDecisionType,
)
from gaia.quality.templates import (
    QUALITY_TEMPLATES,
    QualityTemplate,
    get_template,
)
from gaia.quality.weight_config import QualityWeightConfigManager

__all__ = [
    "QualityScorer",
    "CategoryScore",
    "DimensionScore",
    "QualityReport",
    "CertificationStatus",
    "QualityTemplate",
    "QUALITY_TEMPLATES",
    "get_template",
    # P4 additions - weight configuration
    "QualityWeightConfig",
    "QualityWeightConfigManager",
    # Phase 2 additions - Supervisor Agent
    "SupervisorAgent",
    "SupervisorDecision",
    "SupervisorDecisionType",
]
