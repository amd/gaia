"""
GAIA Pipeline Templates Package

Pipeline template configurations (separate from quality templates).
"""

from gaia.quality.templates.pipeline_templates import (
    PIPELINE_TEMPLATES,
    PipelineTemplate,
    get_pipeline_template,
)

__all__ = [
    "PipelineTemplate",
    "PIPELINE_TEMPLATES",
    "get_pipeline_template",
]
