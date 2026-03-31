"""
GAIA PhaseContract

Defines explicit input/output contracts between pipeline phases, ensuring that each phase
receives the required artifacts before execution and produces the expected outputs upon completion.

This enables:
- Type-safe phase handoffs with explicit contracts
- Automated validation of phase prerequisites
- Clear accountability for phase responsibilities
- Recursive loop-back support with defect accumulation
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, Generic, List, Optional, Type, TypeVar

from gaia.exceptions import GAIAException
from gaia.pipeline.defect_router import Defect
from gaia.pipeline.state import PipelineSnapshot, PipelineState
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


T = TypeVar("T")


class ContractViolationSeverity(Enum):
    """
    Severity levels for contract violations.

    Attributes:
        WARNING: Non-blocking, log only
        ERROR: Should block, but can be overridden
        CRITICAL: Must block, cannot proceed
    """

    WARNING = auto()  # Non-blocking, log only
    ERROR = auto()  # Should block, but can be overridden
    CRITICAL = auto()  # Must block, cannot proceed


class InputType(Enum):
    """
    Classification of input types.

    Attributes:
        REQUIRED: Must exist before phase execution
        OPTIONAL: Nice to have, enhances output
        CONDITIONAL: Required based on conditions
    """

    REQUIRED = auto()  # Must exist before phase execution
    OPTIONAL = auto()  # Nice to have, enhances output
    CONDITIONAL = auto()  # Required based on conditions


class ContractViolationError(GAIAException):
    """
    Raised when a phase contract is violated.

    Attributes:
        phase: Name of the phase where violation occurred
        violations: List of violation messages
        severity: Severity level of the violation
        timestamp: When the violation was detected
    """

    def __init__(
        self,
        message: str,
        phase: str,
        violations: List[str],
        severity: ContractViolationSeverity,
    ):
        self.message = message
        self.phase = phase
        self.violations = violations
        self.severity = severity
        self.timestamp = datetime.now(timezone.utc)
        super().__init__(
            message,
            {
                "phase": phase,
                "violations": violations,
                "severity": severity.name,
                "timestamp": self.timestamp.isoformat(),
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for logging."""
        return {
            "error": "ContractViolationError",
            "phase": self.phase,
            "violations": self.violations,
            "severity": self.severity.name,
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
        }


class PhaseExecutionError(GAIAException):
    """
    Raised when phase execution fails.

    Attributes:
        phase: Name of the phase that failed
        cause: Optional underlying exception
        missing_outputs: List of missing output artifacts
    """

    def __init__(
        self,
        message: str,
        phase: str,
        cause: Optional[Exception] = None,
        missing_outputs: Optional[List[str]] = None,
    ):
        self.phase = phase
        self.cause = cause
        self.missing_outputs = missing_outputs or []
        super().__init__(
            message,
            {
                "phase": phase,
                "missing_outputs": self.missing_outputs,
            },
        )


@dataclass
class ContractTerm(Generic[T]):
    """
    Single term in a phase contract.

    A ContractTerm defines a single input or output requirement for a phase,
    including type information, validation rules, and metadata.

    Attributes:
        name: Term identifier (e.g., "user_goal", "planning_artifacts")
        expected_type: Expected Python type for the artifact
        description: Human-readable description of the term
        input_type: Whether this is required, optional, or conditional
        default_value: Default value if optional and not provided
        validator: Optional custom validator function
        metadata: Additional metadata about the term

    Example:
        >>> term = ContractTerm(
        ...     name="user_goal",
        ...     expected_type=str,
        ...     description="User's goal statement",
        ...     input_type=InputType.REQUIRED
        ... )
        >>> is_valid, error = term.validate("Build a REST API")
        >>> print(is_valid)  # True
    """

    name: str
    expected_type: Type[T]
    description: str
    input_type: InputType = InputType.REQUIRED
    default_value: Optional[T] = None
    validator: Optional[Callable[[T], bool]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        """
        Validate a value against this contract term.

        Args:
            value: Value to validate

        Returns:
            Tuple of (is_valid, error_message) where is_valid indicates
            whether validation passed and error_message contains details
            if validation failed

        Example:
            >>> term = ContractTerm(name="count", expected_type=int, description="A count")
            >>> term.validate(42)
            (True, None)
            >>> term.validate("not an int")
            (False, "Expected int, got str")
        """
        # Type check
        if not isinstance(value, self.expected_type):
            return (
                False,
                f"Expected {self.expected_type.__name__}, got {type(value).__name__}",
            )

        # Custom validator
        if self.validator and not self.validator(value):
            return False, f"Custom validation failed for {self.name}"

        return True, None


@dataclass
class ValidationResult:
    """
    Result of contract validation.

    ValidationResult encapsulates the outcome of validating a phase contract,
    including any violations found and warnings raised.

    Attributes:
        is_valid: Whether validation passed
        violations: List of contract violations found
        warnings: List of warnings (non-blocking issues)
        validated_at: When validation occurred
        validator_name: Name of validator that produced this result
        details: Additional validation details

    Example:
        >>> result = ValidationResult(is_valid=True)
        >>> print(result.is_valid)
        True
        >>> result = ValidationResult.failure(["Missing input: user_goal"])
        >>> print(result.is_valid)
        False
    """

    is_valid: bool
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    validated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    validator_name: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for serialization.

        Returns:
            Dictionary representation of the validation result
        """
        return {
            "is_valid": self.is_valid,
            "violations": self.violations,
            "warnings": self.warnings,
            "validated_at": self.validated_at.isoformat(),
            "validator_name": self.validator_name,
            "details": self.details,
        }

    @classmethod
    def success(cls, details: Optional[Dict[str, Any]] = None) -> "ValidationResult":
        """
        Create a successful validation result.

        Args:
            details: Optional additional details

        Returns:
            ValidationResult indicating success
        """
        return cls(is_valid=True, details=details or {})

    @classmethod
    def failure(
        cls,
        violations: List[str],
        warnings: Optional[List[str]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> "ValidationResult":
        """
        Create a failed validation result.

        Args:
            violations: List of violation messages
            warnings: Optional list of warning messages
            details: Optional additional details

        Returns:
            ValidationResult indicating failure
        """
        return cls(
            is_valid=False,
            violations=violations,
            warnings=warnings or [],
            details=details or {},
        )


@dataclass
class PhaseContract:
    """
    Contract defining phase input/output requirements.

    The PhaseContract ensures that each pipeline phase has explicit
    requirements for what inputs it needs and what outputs it produces.
    This enables fail-fast behavior and clear accountability for each phase.

    Attributes:
        phase_name: Name of the phase this contract applies to
        required_inputs: Inputs that must exist before execution
        optional_inputs: Inputs that enhance output if present
        expected_outputs: Outputs that must be produced
        quality_criteria: Quality thresholds for outputs
        validators: Custom validation functions
        description: Human-readable description of the contract
        version: Contract version for tracking changes
        metadata: Additional contract metadata

    Example:
        >>> contract = PhaseContract(
        ...     phase_name="PLANNING",
        ...     description="Requirements analysis phase"
        ... )
        >>> contract.add_required_input("user_goal", str, "User's goal")
        >>> contract.add_expected_output("plan", dict, "Planning output")
    """

    phase_name: str
    required_inputs: Dict[str, ContractTerm] = field(default_factory=dict)
    optional_inputs: Dict[str, ContractTerm] = field(default_factory=dict)
    expected_outputs: Dict[str, ContractTerm] = field(default_factory=dict)
    quality_criteria: Dict[str, float] = field(default_factory=dict)
    validators: List[Callable[[PipelineState], ValidationResult]] = field(
        default_factory=list
    )
    description: str = ""
    version: str = "1.0.0"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate_inputs(self, state: PipelineState) -> ValidationResult:
        """
        Validate that all required inputs are present.

        Checks both required and optional inputs against the current pipeline state,
        running any custom validators registered for this contract.

        Args:
            state: Current pipeline state

        Returns:
            ValidationResult with any violations found

        Example:
            >>> contract = create_planning_contract()
            >>> result = contract.validate_inputs(state)
            >>> if not result.is_valid:
            ...     print(f"Missing inputs: {result.violations}")
        """
        violations = []
        warnings = []
        snapshot = state.snapshot

        # Validate required inputs
        for name, term in self.required_inputs.items():
            value = snapshot.artifacts.get(name)
            if value is None:
                # Check if it's in context_injected
                value = snapshot.context_injected.get(name)

            if value is None and term.default_value is None:
                violations.append(f"Missing required input: {name}")
            elif value is not None:
                # Validate the value
                is_valid, error = term.validate(value)
                if not is_valid:
                    violations.append(f"Invalid input '{name}': {error}")

        # Validate optional inputs (warn if type mismatch)
        for name, term in self.optional_inputs.items():
            value = snapshot.artifacts.get(name)
            if value is not None:
                is_valid, error = term.validate(value)
                if not is_valid:
                    warnings.append(
                        f"Optional input '{name}' has unexpected type: {error}"
                    )

        # Run custom validators
        for validator in self.validators:
            try:
                result = validator(state)
                if not result.is_valid:
                    violations.extend(result.violations)
                    warnings.extend(result.warnings)
            except Exception as e:
                logger.error(
                    f"Validator error in {self.phase_name}: {str(e)}",
                    phase=self.phase_name,
                )
                violations.append(f"Validator error: {str(e)}")

        return ValidationResult(
            is_valid=len(violations) == 0,
            violations=violations,
            warnings=warnings,
            validator_name=f"{self.phase_name}_input_validator",
        )

    def validate_outputs(self, state: PipelineState) -> ValidationResult:
        """
        Validate that all expected outputs were produced.

        Checks that the phase has produced all expected output artifacts
        with the correct types.

        Args:
            state: Current pipeline state

        Returns:
            ValidationResult with any missing outputs

        Example:
            >>> contract = create_development_contract()
            >>> result = contract.validate_outputs(state)
            >>> if not result.is_valid:
            ...     print(f"Missing outputs: {result.violations}")
        """
        violations = []
        snapshot = state.snapshot

        for name, term in self.expected_outputs.items():
            value = snapshot.artifacts.get(name)
            if value is None:
                violations.append(f"Missing expected output: {name}")
            elif not isinstance(value, term.expected_type):
                violations.append(
                    f"Output '{name}' has wrong type: "
                    f"expected {term.expected_type.__name__}, "
                    f"got {type(value).__name__}"
                )

        return ValidationResult(
            is_valid=len(violations) == 0,
            violations=violations,
            validator_name=f"{self.phase_name}_output_validator",
        )

    def validate_quality(self, state: PipelineState) -> ValidationResult:
        """
        Validate that quality criteria are met.

        Checks quality scores against defined thresholds for this contract.

        Args:
            state: Current pipeline state

        Returns:
            ValidationResult with quality assessment

        Example:
            >>> contract = create_quality_contract()
            >>> result = contract.validate_quality(state)
            >>> if not result.is_valid:
            ...     print(f"Quality issues: {result.violations}")
        """
        violations = []
        snapshot = state.snapshot

        for criteria_name, threshold in self.quality_criteria.items():
            # Get the quality score
            if criteria_name == "overall_quality":
                score = snapshot.quality_score
                if score is None:
                    violations.append("Quality score not available")
                elif score < threshold:
                    violations.append(
                        f"Quality score {score:.2f} below threshold {threshold:.2f}"
                    )
            else:
                # Check for other quality metrics in artifacts
                quality_report = snapshot.artifacts.get("quality_report", {})
                if isinstance(quality_report, dict):
                    score = quality_report.get(criteria_name)
                    if score is not None and score < threshold:
                        violations.append(
                            f"{criteria_name} score {score:.2f} below threshold {threshold:.2f}"
                        )

        return ValidationResult(
            is_valid=len(violations) == 0,
            violations=violations,
            validator_name=f"{self.phase_name}_quality_validator",
        )

    def get_missing_inputs(self, state: PipelineState) -> List[str]:
        """
        Get list of missing required inputs.

        Args:
            state: Current pipeline state

        Returns:
            List of missing input names

        Example:
            >>> contract = create_planning_contract()
            >>> missing = contract.get_missing_inputs(state)
            >>> if missing:
            ...     print(f"Need to provide: {missing}")
        """
        missing = []
        snapshot = state.snapshot

        for name, term in self.required_inputs.items():
            value = snapshot.artifacts.get(name)
            if value is None:
                value = snapshot.context_injected.get(name)
            if value is None and term.default_value is None:
                missing.append(name)

        return missing

    def get_produced_outputs(self, state: PipelineState) -> List[str]:
        """
        Get list of expected outputs that have been produced.

        Args:
            state: Current pipeline state

        Returns:
            List of output names that exist

        Example:
            >>> contract = create_development_contract()
            >>> produced = contract.get_produced_outputs(state)
            >>> print(f"Completed outputs: {produced}")
        """
        produced = []
        snapshot = state.snapshot

        for name in self.expected_outputs:
            if name in snapshot.artifacts:
                produced.append(name)

        return produced

    def add_required_input(
        self,
        name: str,
        expected_type: Type,
        description: str,
        validator: Optional[Callable] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "PhaseContract":
        """
        Fluent method to add required input.

        Args:
            name: Input name
            expected_type: Expected Python type
            description: Human-readable description
            validator: Optional custom validator function
            metadata: Optional additional metadata

        Returns:
            Self for method chaining

        Example:
            >>> contract = PhaseContract(phase_name="TEST")
            >>> contract.add_required_input("user_goal", str, "User's goal")
        """
        self.required_inputs[name] = ContractTerm(
            name=name,
            expected_type=expected_type,
            description=description,
            input_type=InputType.REQUIRED,
            validator=validator,
            metadata=metadata or {},
        )
        return self

    def add_optional_input(
        self,
        name: str,
        expected_type: Type,
        description: str,
        default_value: Any = None,
        validator: Optional[Callable] = None,
    ) -> "PhaseContract":
        """
        Fluent method to add optional input.

        Args:
            name: Input name
            expected_type: Expected Python type
            description: Human-readable description
            default_value: Default value if not provided
            validator: Optional custom validator function

        Returns:
            Self for method chaining

        Example:
            >>> contract = PhaseContract(phase_name="TEST")
            >>> contract.add_optional_input("context", dict, "Additional context", default_value={})
        """
        self.optional_inputs[name] = ContractTerm(
            name=name,
            expected_type=expected_type,
            description=description,
            input_type=InputType.OPTIONAL,
            default_value=default_value,
            validator=validator,
        )
        return self

    def add_expected_output(
        self,
        name: str,
        expected_type: Type,
        description: str,
        quality_threshold: float = 0.0,
    ) -> "PhaseContract":
        """
        Fluent method to add expected output.

        Args:
            name: Output name
            expected_type: Expected Python type
            description: Human-readable description
            quality_threshold: Optional quality threshold (0-1)

        Returns:
            Self for method chaining

        Example:
            >>> contract = PhaseContract(phase_name="TEST")
            >>> contract.add_expected_output("result", dict, "Test result")
        """
        self.expected_outputs[name] = ContractTerm(
            name=name,
            expected_type=expected_type,
            description=description,
            input_type=InputType.REQUIRED,  # Outputs are required
        )
        if quality_threshold > 0:
            self.quality_criteria[name] = quality_threshold
        return self

    def with_quality_criteria(
        self,
        criteria_name: str,
        threshold: float,
    ) -> "PhaseContract":
        """
        Fluent method to add quality criteria.

        Args:
            criteria_name: Name of the quality criterion
            threshold: Minimum threshold value (0-1)

        Returns:
            Self for method chaining

        Raises:
            ValueError: If threshold is not between 0 and 1

        Example:
            >>> contract = PhaseContract(phase_name="TEST")
            >>> contract.with_quality_criteria("overall_quality", 0.85)
        """
        if not 0 <= threshold <= 1:
            raise ValueError("Quality threshold must be between 0 and 1")
        self.quality_criteria[criteria_name] = threshold
        return self

    def add_validator(
        self, validator: Callable[[PipelineState], ValidationResult]
    ) -> "PhaseContract":
        """
        Add a custom validator function.

        Args:
            validator: Function that takes PipelineState and returns ValidationResult

        Returns:
            Self for method chaining

        Example:
            >>> def custom_validator(state):
            ...     if "critical_artifact" not in state.snapshot.artifacts:
            ...         return ValidationResult.failure(["Missing critical artifact"])
            ...     return ValidationResult.success()
            >>> contract.add_validator(custom_validator)
        """
        self.validators.append(validator)
        return self

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert contract to dictionary for serialization.

        Returns:
            Dictionary representation of the contract

        Example:
            >>> contract = create_planning_contract()
            >>> data = contract.to_dict()
            >>> print(data["phase_name"])  # "PLANNING"
        """
        return {
            "phase_name": self.phase_name,
            "description": self.description,
            "version": self.version,
            "required_inputs": {
                name: {
                    "type": term.expected_type.__name__,
                    "description": term.description,
                    "input_type": term.input_type.name,
                }
                for name, term in self.required_inputs.items()
            },
            "optional_inputs": {
                name: {
                    "type": term.expected_type.__name__,
                    "description": term.description,
                    "default_value": term.default_value,
                }
                for name, term in self.optional_inputs.items()
            },
            "expected_outputs": {
                name: {
                    "type": term.expected_type.__name__,
                    "description": term.description,
                }
                for name, term in self.expected_outputs.items()
            },
            "quality_criteria": self.quality_criteria,
            "metadata": self.metadata,
        }


class PhaseContractRegistry:
    """
    Registry for managing phase contracts.

    The registry stores contracts for all phases and provides
    validation services for phase transitions. It is thread-safe
    and supports registering custom contracts as well as default
    contracts for all pipeline phases.

    Example:
        >>> registry = PhaseContractRegistry()
        >>> registry.register_default_contracts()
        >>> contract = registry.get("PLANNING")
        >>> result = contract.validate_inputs(state)
        >>> if not result.is_valid:
        ...     print(f"Validation failed: {result.violations}")
    """

    def __init__(self):
        """Initialize the contract registry."""
        self._contracts: Dict[str, PhaseContract] = {}
        self._lock = threading.RLock()

    def register(self, contract: PhaseContract) -> None:
        """
        Register a phase contract.

        Args:
            contract: Contract to register

        Raises:
            ValueError: If contract with same name already exists

        Example:
            >>> registry = PhaseContractRegistry()
            >>> contract = PhaseContract(phase_name="CUSTOM")
            >>> registry.register(contract)
        """
        with self._lock:
            if contract.phase_name in self._contracts:
                logger.warning(
                    f"Contract for phase '{contract.phase_name}' already registered, overwriting"
                )
            self._contracts[contract.phase_name] = contract
            logger.info(f"Registered contract for phase: {contract.phase_name}")

    def get(self, phase_name: str) -> PhaseContract:
        """
        Get contract for a phase.

        Args:
            phase_name: Name of the phase

        Returns:
            PhaseContract for the phase

        Raises:
            KeyError: If contract not found

        Example:
            >>> registry = PhaseContractRegistry()
            >>> registry.register_default_contracts()
            >>> contract = registry.get("PLANNING")
        """
        with self._lock:
            if phase_name not in self._contracts:
                raise KeyError(f"No contract registered for phase: {phase_name}")
            return self._contracts[phase_name]

    def get_or_none(self, phase_name: str) -> Optional[PhaseContract]:
        """
        Get contract or return None if not found.

        Args:
            phase_name: Name of the phase

        Returns:
            PhaseContract or None

        Example:
            >>> registry = PhaseContractRegistry()
            >>> contract = registry.get_or_none("PLANNING")
            >>> if contract is None:
            ...     print("No contract found")
        """
        with self._lock:
            return self._contracts.get(phase_name)

    def validate_phase_transition(
        self,
        from_phase: str,
        to_phase: str,
        state: PipelineState,
    ) -> ValidationResult:
        """
        Validate that a phase transition is valid.

        This checks that:
        1. The source phase has produced all expected outputs
        2. The target phase has all required inputs available

        Args:
            from_phase: Source phase name
            to_phase: Target phase name
            state: Current pipeline state

        Returns:
            ValidationResult with transition validation

        Example:
            >>> registry = PhaseContractRegistry()
            >>> registry.register_default_contracts()
            >>> result = registry.validate_phase_transition("PLANNING", "DEVELOPMENT", state)
            >>> if not result.is_valid:
            ...     print(f"Cannot transition: {result.violations}")
        """
        violations = []

        with self._lock:
            # Validate source phase outputs
            if from_phase in self._contracts:
                source_contract = self._contracts[from_phase]
                output_result = source_contract.validate_outputs(state)
                if not output_result.is_valid:
                    violations.extend(
                        [
                            f"Phase '{from_phase}' has not produced required outputs: {v}"
                            for v in output_result.violations
                        ]
                    )

            # Validate target phase inputs
            if to_phase in self._contracts:
                target_contract = self._contracts[to_phase]
                input_result = target_contract.validate_inputs(state)
                if not input_result.is_valid:
                    violations.extend(
                        [
                            f"Phase '{to_phase}' is missing required inputs: {v}"
                            for v in input_result.violations
                        ]
                    )

        return ValidationResult(
            is_valid=len(violations) == 0,
            violations=violations,
            validator_name="phase_transition_validator",
        )

    def get_all_contracts(self) -> Dict[str, PhaseContract]:
        """
        Get all registered contracts.

        Returns:
            Dictionary mapping phase names to contracts

        Example:
            >>> registry = PhaseContractRegistry()
            >>> registry.register_default_contracts()
            >>> contracts = registry.get_all_contracts()
            >>> print(list(contracts.keys()))
            ['PLANNING', 'DEVELOPMENT', 'QUALITY', 'DECISION']
        """
        with self._lock:
            return dict(self._contracts)

    def register_default_contracts(self) -> None:
        """
        Register default contracts for all pipeline phases.

        This creates and registers contracts for PLANNING, DEVELOPMENT,
        QUALITY, and DECISION phases using the standard GAIA definitions.

        Example:
            >>> registry = PhaseContractRegistry()
            >>> registry.register_default_contracts()
            >>> planning = registry.get("PLANNING")
            >>> development = registry.get("DEVELOPMENT")
        """
        contracts = create_default_phase_contracts()
        for contract in contracts:
            self.register(contract)
        logger.info(f"Registered {len(contracts)} default phase contracts")

    def unregister(self, phase_name: str) -> Optional[PhaseContract]:
        """
        Unregister a contract by phase name.

        Args:
            phase_name: Name of the phase to unregister

        Returns:
            The unregistered contract, or None if not found

        Example:
            >>> registry = PhaseContractRegistry()
            >>> registry.register(PhaseContract(phase_name="CUSTOM"))
            >>> removed = registry.unregister("CUSTOM")
        """
        with self._lock:
            contract = self._contracts.pop(phase_name, None)
            if contract:
                logger.info(f"Unregistered contract for phase: {phase_name}")
            return contract


def create_default_phase_contracts() -> List[PhaseContract]:
    """
    Create default phase contracts for the GAIA pipeline.

    Returns:
        List of PhaseContract instances for all phases

    Example:
        >>> contracts = create_default_phase_contracts()
        >>> print(len(contracts))  # 4
        >>> print([c.phase_name for c in contracts])
        ['PLANNING', 'DEVELOPMENT', 'QUALITY', 'DECISION']
    """
    return [
        create_planning_contract(),
        create_development_contract(),
        create_quality_contract(),
        create_decision_contract(),
    ]


def create_planning_contract() -> PhaseContract:
    """
    Create contract for PLANNING phase.

    PLANNING phase contract defines:
    - Required inputs: user_goal, context
    - Optional inputs: previous_plan, defects (for loop-back)
    - Expected outputs: planning_artifacts, task_breakdown, complexity_analysis
    - Quality criteria: overall_quality >= 0.85

    Returns:
        PhaseContract for PLANNING phase
    """
    return (
        PhaseContract(
            phase_name="PLANNING",
            description="Requirements analysis and planning phase",
        )
        .add_required_input(
            name="user_goal",
            expected_type=str,
            description="User's goal or requirement statement",
        )
        .add_required_input(
            name="context",
            expected_type=dict,
            description="Additional context for planning",
        )
        .add_optional_input(
            name="previous_plan",
            expected_type=dict,
            description="Plan from previous iteration (for loop-back)",
            default_value={},
        )
        .add_optional_input(
            name="defects",
            expected_type=list,
            description="Defects from previous iteration",
            default_value=[],
        )
        .add_expected_output(
            name="planning_artifacts",
            expected_type=dict,
            description="Planning deliverables including plan, tasks, and analysis",
        )
        .add_expected_output(
            name="task_breakdown",
            expected_type=list,
            description="List of tasks derived from requirements",
        )
        .add_expected_output(
            name="complexity_analysis",
            expected_type=dict,
            description="Complexity assessment and estimates",
        )
        .with_quality_criteria(criteria_name="overall_quality", threshold=0.85)
    )


def create_development_contract() -> PhaseContract:
    """
    Create contract for DEVELOPMENT phase.

    DEVELOPMENT phase contract defines:
    - Required inputs: planning_artifacts, user_goal
    - Optional inputs: defects, existing_code
    - Expected outputs: code_artifacts, test_artifacts, documentation
    - Quality criteria: overall_quality >= 0.90

    Returns:
        PhaseContract for DEVELOPMENT phase
    """
    return (
        PhaseContract(
            phase_name="DEVELOPMENT",
            description="Implementation and development phase",
        )
        .add_required_input(
            name="planning_artifacts",
            expected_type=dict,
            description="Planning output with tasks and requirements",
        )
        .add_required_input(
            name="user_goal",
            expected_type=str,
            description="Original user goal being implemented",
        )
        .add_optional_input(
            name="defects",
            expected_type=list,
            description="Defects to address from previous iteration",
            default_value=[],
        )
        .add_optional_input(
            name="existing_code",
            expected_type=str,
            description="Existing code to modify or extend",
            default_value="",
        )
        .add_expected_output(
            name="code_artifacts",
            expected_type=dict,
            description="Generated code files and modules",
        )
        .add_expected_output(
            name="test_artifacts",
            expected_type=dict,
            description="Test files and test coverage data",
        )
        .add_expected_output(
            name="documentation",
            expected_type=dict,
            description="Documentation artifacts",
        )
        .with_quality_criteria(criteria_name="overall_quality", threshold=0.90)
    )


def create_quality_contract() -> PhaseContract:
    """
    Create contract for QUALITY phase.

    QUALITY phase contract defines:
    - Required inputs: planning_artifacts, code_artifacts, quality_template
    - Optional inputs: test_artifacts, documentation
    - Expected outputs: quality_report, defects, quality_score
    - Quality criteria: overall_quality >= 0.90

    Returns:
        PhaseContract for QUALITY phase
    """
    return (
        PhaseContract(
            phase_name="QUALITY",
            description="Quality evaluation and assessment phase",
        )
        .add_required_input(
            name="planning_artifacts",
            expected_type=dict,
            description="Planning output for requirements validation",
        )
        .add_required_input(
            name="code_artifacts",
            expected_type=dict,
            description="Code to evaluate",
        )
        .add_required_input(
            name="quality_template",
            expected_type=str,
            description="Quality template name (STANDARD, RAPID, etc.)",
        )
        .add_optional_input(
            name="test_artifacts",
            expected_type=dict,
            description="Test results for evaluation",
            default_value={},
        )
        .add_optional_input(
            name="documentation",
            expected_type=dict,
            description="Documentation to evaluate",
            default_value={},
        )
        .add_expected_output(
            name="quality_report",
            expected_type=dict,
            description="Comprehensive quality evaluation report",
        )
        .add_expected_output(
            name="defects",
            expected_type=list,
            description="List of defects identified",
        )
        .add_expected_output(
            name="quality_score",
            expected_type=float,
            description="Overall quality score (0-1)",
        )
        .with_quality_criteria(criteria_name="overall_quality", threshold=0.90)
        .add_validator(_validate_quality_completeness)
    )


def create_decision_contract() -> PhaseContract:
    """
    Create contract for DECISION phase.

    DECISION phase contract defines:
    - Required inputs: quality_report, defects, iteration_count
    - Optional inputs: max_iterations
    - Expected outputs: decision
    - Custom validator for decision context

    Returns:
        PhaseContract for DECISION phase
    """
    return (
        PhaseContract(
            phase_name="DECISION",
            description="Decision-making and pipeline progression phase",
        )
        .add_required_input(
            name="quality_report",
            expected_type=dict,
            description="Quality evaluation report",
        )
        .add_required_input(
            name="defects",
            expected_type=list,
            description="Defects from quality evaluation",
        )
        .add_required_input(
            name="iteration_count",
            expected_type=int,
            description="Current iteration number",
        )
        .add_optional_input(
            name="max_iterations",
            expected_type=int,
            description="Maximum allowed iterations",
            default_value=10,
        )
        .add_expected_output(
            name="decision",
            expected_type=dict,
            description="Decision output (type, reason, target_phase)",
        )
        .add_validator(_validate_decision_context)
    )


def _validate_quality_completeness(state: PipelineState) -> ValidationResult:
    """
    Validate that quality phase has all required artifacts.

    Args:
        state: Current pipeline state

    Returns:
        ValidationResult
    """
    violations = []
    snapshot = state.snapshot

    # Check that we have something to evaluate
    if "code_artifacts" not in snapshot.artifacts:
        violations.append("No code artifacts to evaluate")

    if "planning_artifacts" not in snapshot.artifacts:
        violations.append("No planning artifacts for requirements validation")

    return ValidationResult(
        is_valid=len(violations) == 0,
        violations=violations,
        validator_name="quality_completeness_validator",
    )


def _validate_decision_context(state: PipelineState) -> ValidationResult:
    """
    Validate that decision phase has proper context.

    Args:
        state: Current pipeline state

    Returns:
        ValidationResult
    """
    violations = []
    snapshot = state.snapshot

    # Need quality score
    if snapshot.quality_score is None:
        violations.append("Quality score not available for decision")

    return ValidationResult(
        is_valid=len(violations) == 0,
        violations=violations,
        validator_name="decision_context_validator",
    )


def validate_defect_routing(
    defect: Defect, contract_registry: PhaseContractRegistry
) -> ValidationResult:
    """
    Validate that a defect can be routed to a target phase.

    This function checks if the target phase for a defect has
    the capability to handle defects (i.e., accepts defects as
    optional input).

    Args:
        defect: Defect to validate routing for
        contract_registry: Contract registry for phase lookups

    Returns:
        ValidationResult indicating if routing is valid

    Example:
        >>> registry = PhaseContractRegistry()
        >>> registry.register_default_contracts()
        >>> defect = Defect(id="d1", type=DefectType.MISSING_TESTS, ...)
        >>> result = validate_defect_routing(defect, registry)
        >>> print(result.is_valid)
        True
    """
    target_phase = defect.target_phase or "DEVELOPMENT"

    contract = contract_registry.get_or_none(target_phase)
    if contract is None:
        return ValidationResult.failure(
            [f"No contract registered for target phase: {target_phase}"]
        )

    # Check if target phase can accept defects
    if (
        "defects" not in contract.optional_inputs
        and "defects" not in contract.required_inputs
    ):
        return ValidationResult.failure(
            [f"Phase '{target_phase}' does not accept defects as input"]
        )

    return ValidationResult.success(
        details={"target_phase": target_phase, "defect_id": defect.id}
    )
