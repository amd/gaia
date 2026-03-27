"""
GAIA Pipeline Engine

Core pipeline engine components for orchestration and execution.
"""

# Direct imports that don't trigger the full agent dependency chain
from gaia.pipeline.state import (
    PipelineState,
    PipelineContext,
    PipelineSnapshot,
    PipelineStateMachine,
)
from gaia.pipeline.decision_engine import (
    DecisionEngine,
    Decision,
    DecisionType,
)
from gaia.pipeline.defect_router import (
    DefectRouter,
    Defect,
    DefectType,
    DefectSeverity,
    DefectStatus,
    create_defect,
)
from gaia.pipeline.defect_remediation_tracker import (
    DefectRemediationTracker,
    DefectStatusChange,
    DefectStatusTransition,
    InvalidStatusTransitionError,
    DefectStatus as RemediationDefectStatus,
)
from gaia.pipeline.phase_contract import (
    PhaseContract,
    PhaseContractRegistry,
    ContractTerm,
    ContractViolationSeverity,
    InputType,
    ValidationResult,
    ContractViolationError,
    PhaseExecutionError,
    create_default_phase_contracts,
    create_planning_contract,
    create_development_contract,
    create_quality_contract,
    create_decision_contract,
    validate_defect_routing,
)
from gaia.pipeline.audit_logger import (
    AuditLogger,
    AuditEvent,
    AuditEventType,
    IntegrityVerificationError,
)

# Lazy imports for components with complex dependencies
def __getattr__(name):
    """Lazy loading for components with complex dependencies."""
    if name == "PipelineEngine":
        from gaia.pipeline.engine import PipelineEngine
        return PipelineEngine
    elif name == "LoopManager":
        from gaia.pipeline.loop_manager import LoopManager
        return LoopManager
    elif name == "LoopConfig":
        from gaia.pipeline.loop_manager import LoopConfig
        return LoopConfig
    elif name == "LoopState":
        from gaia.pipeline.loop_manager import LoopState
        return LoopState
    elif name == "LoopStatus":
        from gaia.pipeline.loop_manager import LoopStatus
        return LoopStatus
    elif name == "RoutingEngine":
        from gaia.pipeline.routing_engine import RoutingEngine
        return RoutingEngine
    elif name == "DefectRoutingRule":
        from gaia.pipeline.routing_engine import RoutingRule as DefectRoutingRule
        return DefectRoutingRule
    elif name == "RoutingDecision":
        from gaia.pipeline.routing_engine import RoutingDecision
        return RoutingDecision
    elif name == "RecursivePipelineTemplate":
        from gaia.pipeline.recursive_template import RecursivePipelineTemplate
        return RecursivePipelineTemplate
    elif name == "TemplateRoutingRule":
        from gaia.pipeline.recursive_template import RoutingRule as TemplateRoutingRule
        return TemplateRoutingRule
    elif name == "TemplateLoader":
        from gaia.pipeline.template_loader import TemplateLoader
        return TemplateLoader
    elif name == "DefectTypeTaxonomy":
        # The comprehensive DefectType from defect_types.py (different from
        # defect_router's DefectType which is already in __all__ as DefectType)
        from gaia.pipeline.defect_types import DefectType as DefectTypeTaxonomy
        return DefectTypeTaxonomy
    elif name == "AgentCategory":
        from gaia.pipeline.recursive_template import AgentCategory
        return AgentCategory
    elif name == "get_recursive_template":
        from gaia.pipeline.recursive_template import get_recursive_template
        return get_recursive_template
    raise AttributeError(f"module {__name__} has no attribute {name}")


__all__ = [
    # State machine
    "PipelineState",
    "PipelineContext",
    "PipelineSnapshot",
    "PipelineStateMachine",
    # Phase Contract
    "PhaseContract",
    "PhaseContractRegistry",
    "ContractTerm",
    "ContractViolationSeverity",
    "InputType",
    "ValidationResult",
    "ContractViolationError",
    "PhaseExecutionError",
    # Audit Logger
    "AuditLogger",
    "AuditEvent",
    "AuditEventType",
    "IntegrityVerificationError",
    # Contract factories
    "create_default_phase_contracts",
    "create_planning_contract",
    "create_development_contract",
    "create_quality_contract",
    "create_decision_contract",
    # Defect routing and remediation
    "DefectRouter",
    "Defect",
    "DefectType",
    "DefectSeverity",
    "DefectStatus",
    "DefectStatusChange",
    "DefectStatusTransition",
    "RemediationDefectStatus",
    "InvalidStatusTransitionError",
    "create_defect",
    "DefectRemediationTracker",
    # Validation
    "validate_defect_routing",
    # Decision engine
    "DecisionEngine",
    "Decision",
    "DecisionType",
    # Lazy loaded
    "PipelineEngine",
    "LoopManager",
    "LoopConfig",
    "LoopState",
    "LoopStatus",
    # P4 additions - routing engine
    "RoutingEngine",
    "DefectRoutingRule",
    "RoutingDecision",
    # P4 additions - recursive template
    "RecursivePipelineTemplate",
    "TemplateRoutingRule",
    # P4 additions - template loader
    "TemplateLoader",
    # P4 additions - defect taxonomy (aliased to avoid conflict with defect_router's DefectType)
    "DefectTypeTaxonomy",
    # P6 additions - agent category enum and template lookup helper
    "AgentCategory",
    "get_recursive_template",
]
