"""
GAIA Pipeline Engine

Core pipeline engine components for orchestration and execution.
"""

from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.loop_manager import (
    LoopManager,
    LoopConfig,
    LoopState,
    LoopStatus,
)
from gaia.pipeline.decision_engine import (
    DecisionEngine,
    Decision,
    DecisionType,
)
from gaia.pipeline.state import (
    PipelineState,
    PipelineContext,
    PipelineStateMachine,
)
from gaia.pipeline.defect_router import (
    DefectRouter,
    Defect,
    DefectType,
    DefectSeverity,
    DefectStatus,
    RoutingRule,
    create_defect,
)
from gaia.pipeline.defect_remediation_tracker import (
    DefectRemediationTracker,
    DefectStatusChange,
    DefectStatusTransition,
    InvalidStatusTransitionError,
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

__all__ = [
    # Engine
    "PipelineEngine",
    # Loop management
    "LoopManager",
    "LoopConfig",
    "LoopState",
    "LoopStatus",
    # Decision
    "DecisionEngine",
    "Decision",
    "DecisionType",
    # State
    "PipelineState",
    "PipelineContext",
    "PipelineStateMachine",
    # Defect routing
    "DefectRouter",
    "Defect",
    "DefectType",
    "DefectSeverity",
    "DefectStatus",
    "RoutingRule",
    "create_defect",
    # Defect remediation
    "DefectRemediationTracker",
    "DefectStatusChange",
    "DefectStatusTransition",
    "InvalidStatusTransitionError",
    # Phase Contract
    "PhaseContract",
    "PhaseContractRegistry",
    "ContractTerm",
    "ContractViolationSeverity",
    "InputType",
    "ValidationResult",
    "ContractViolationError",
    "PhaseExecutionError",
    # Contract factories
    "create_default_phase_contracts",
    "create_planning_contract",
    "create_development_contract",
    "create_quality_contract",
    "create_decision_contract",
    # Validation
    "validate_defect_routing",
    # Audit Logger
    "AuditLogger",
    "AuditEvent",
    "AuditEventType",
    "IntegrityVerificationError",
]
