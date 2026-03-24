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
)
from gaia.quality.templates import (
    QualityTemplate,
    QUALITY_TEMPLATES,
    get_template,
)

__all__ = [
    "QualityScorer",
    "CategoryScore",
    "DimensionScore",
    "QualityReport",
    "CertificationStatus",
    "QualityTemplate",
    "QUALITY_TEMPLATES",
    "get_template",
]
