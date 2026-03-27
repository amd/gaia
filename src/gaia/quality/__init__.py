"""
GAIA Quality Module

Quality scoring system with 27 validation categories across 6 dimensions.
"""

from gaia.quality.scorer import QualityScorer
from gaia.quality.models import (
    CategoryScore,
    DimensionScore,
    QualityReport,
    CertificationStatus,
    QualityWeightConfig,
)
from gaia.quality.templates import (
    QualityTemplate,
    QUALITY_TEMPLATES,
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
]
