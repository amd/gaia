"""
GAIA Hooks Module

Hook system for pipeline event interception and modification.
"""

from gaia.hooks.base import BaseHook, HookContext, HookResult, HookPriority
from gaia.hooks.registry import HookRegistry, HookExecutor
from gaia.hooks.production.validation_hooks import (
    PreActionValidationHook,
    PostActionValidationHook,
)
from gaia.hooks.production.context_hooks import (
    ContextInjectionHook,
    OutputProcessingHook,
)
from gaia.hooks.production.quality_hooks import (
    QualityGateHook,
    DefectExtractionHook,
    PipelineNotificationHook,
    ChronicleHarvestHook,
)

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
