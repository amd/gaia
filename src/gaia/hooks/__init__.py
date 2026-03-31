"""
GAIA Hooks Module

Hook system for pipeline event interception and modification.
"""

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult
from gaia.hooks.production.context_hooks import (
    ContextInjectionHook,
    OutputProcessingHook,
)
from gaia.hooks.production.quality_hooks import (
    ChronicleHarvestHook,
    DefectExtractionHook,
    PipelineNotificationHook,
    QualityGateHook,
)
from gaia.hooks.production.validation_hooks import (
    PostActionValidationHook,
    PreActionValidationHook,
)
from gaia.hooks.registry import HookExecutor, HookRegistry

__all__ = [
    # Base
    "BaseHook",
    "HookContext",
    "HookResult",
    "HookPriority",
    # Registry
    "HookRegistry",
    "HookExecutor",
    # Production Hooks
    "PreActionValidationHook",
    "PostActionValidationHook",
    "ContextInjectionHook",
    "OutputProcessingHook",
    "QualityGateHook",
    "DefectExtractionHook",
    "PipelineNotificationHook",
    "ChronicleHarvestHook",
]
