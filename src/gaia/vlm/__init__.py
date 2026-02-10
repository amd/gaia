"""VLM Tools - Vision Language Model capabilities for GAIA agents."""

from gaia.vlm.mixin import VLMToolsMixin
from gaia.vlm.structured_extraction import StructuredVLMExtractor

# Only export high-level APIs - utilities are internal
__all__ = ["VLMToolsMixin", "StructuredVLMExtractor"]
