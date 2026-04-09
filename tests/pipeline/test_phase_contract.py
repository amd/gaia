"""
Tests for GAIA PhaseContract.

Tests cover:
- ContractTerm validation
- ValidationResult creation
- PhaseContract input/output validation
- PhaseContractRegistry operations
- Default contract creation
- Integration with PipelineState
- Defect routing validation
"""

from datetime import datetime
from typing import Any, Dict

import pytest

from gaia.pipeline.defect_router import Defect, DefectSeverity, DefectType
from gaia.pipeline.phase_contract import (
    ContractTerm,
    ContractViolationError,
    ContractViolationSeverity,
    InputType,
    PhaseContract,
    PhaseContractRegistry,
    PhaseExecutionError,
    ValidationResult,
    _validate_decision_context,
    _validate_quality_completeness,
    create_decision_contract,
    create_default_phase_contracts,
    create_development_contract,
    create_planning_contract,
    create_quality_contract,
    validate_defect_routing,
)
from gaia.pipeline.state import (
    PipelineContext,
    PipelineSnapshot,
    PipelineState,
    PipelineStateMachine,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def context() -> PipelineContext:
    """Create test pipeline context."""
    return PipelineContext(
        pipeline_id="test-pipeline-001",
        user_goal="Test goal for phase contract validation",
        quality_threshold=0.90,
    )


@pytest.fixture
def state_machine(context: PipelineContext) -> PipelineStateMachine:
    """Create test state machine."""
    return PipelineStateMachine(context)


@pytest.fixture
def state_with_planning_inputs(context: PipelineContext) -> PipelineStateMachine:
    """Create state machine with PLANNING phase inputs."""
    state = PipelineStateMachine(context)
    state.add_artifact("user_goal", "Build a REST API")
    state.add_artifact("context", {"language": "python", "framework": "fastapi"})
    return state


@pytest.fixture
def state_with_planning_outputs(context: PipelineContext) -> PipelineStateMachine:
    """Create state machine with PLANNING phase outputs."""
    state = PipelineStateMachine(context)
    state.add_artifact("user_goal", "Build a REST API")
    state.add_artifact("context", {"language": "python"})
    state.add_artifact(
        "planning_artifacts",
        {"plan": "test plan", "requirements": ["req1", "req2"]},
    )
    state.add_artifact("task_breakdown", ["task1", "task2", "task3"])
    state.add_artifact("complexity_analysis", {"overall": "medium", "score": 0.7})
    return state


@pytest.fixture
def state_with_development_outputs(context: PipelineContext) -> PipelineStateMachine:
    """Create state machine with DEVELOPMENT phase outputs."""
    state = PipelineStateMachine(context)
    state.add_artifact("user_goal", "Build a REST API")
    state.add_artifact("planning_artifacts", {"plan": "test plan"})
    state.add_artifact(
        "code_artifacts", {"main.py": "code content", "utils.py": "utils code"}
    )
    state.add_artifact(
        "test_artifacts", {"test_main.py": "test content", "coverage": 0.85}
    )
    state.add_artifact("documentation", {"README.md": "documentation"})
    return state


@pytest.fixture
def state_with_quality_outputs(context: PipelineContext) -> PipelineStateMachine:
    """Create state machine with QUALITY phase outputs."""
    state = PipelineStateMachine(context)
    state.add_artifact("planning_artifacts", {"plan": "test plan"})
    state.add_artifact("code_artifacts", {"main.py": "code"})
    state.add_artifact("quality_template", "STANDARD")
    state.add_artifact(
        "quality_report", {"overall": 0.92, "code_quality": 0.90, "test_coverage": 0.85}
    )
    state.add_artifact("defects", [{"id": "d1", "type": "MISSING_TESTS"}])
    state.set_quality_score(0.92)
    return state


# =============================================================================
# ContractTerm Tests
# =============================================================================


class TestContractTerm:
    """Tests for ContractTerm class."""

    def test_create_term(self):
        """Test basic term creation."""
        term = ContractTerm(
            name="test_field",
            expected_type=str,
            description="A test field",
        )
        assert term.name == "test_field"
        assert term.expected_type == str
        assert term.description == "A test field"
        assert term.input_type == InputType.REQUIRED
        assert term.default_value is None

    def test_create_term_with_defaults(self):
        """Test term creation with default values."""
        term = ContractTerm(
            name="optional_field",
            expected_type=dict,
            description="An optional field",
            input_type=InputType.OPTIONAL,
            default_value={"key": "value"},
        )
        assert term.input_type == InputType.OPTIONAL
        assert term.default_value == {"key": "value"}

    def test_validate_correct_type(self):
        """Test validation with correct type."""
        term = ContractTerm(
            name="count",
            expected_type=int,
            description="A count value",
        )
        is_valid, error = term.validate(42)
        assert is_valid is True
        assert error is None

    def test_validate_wrong_type(self):
        """Test validation with wrong type."""
        term = ContractTerm(
            name="count",
            expected_type=int,
            description="A count value",
        )
        is_valid, error = term.validate("not an int")
        assert is_valid is False
        assert "Expected int, got str" in error

    def test_validate_with_custom_validator(self):
        """Test validation with custom validator function."""

        def positive_validator(value: int) -> bool:
            return value > 0

        term = ContractTerm(
            name="positive_count",
            expected_type=int,
            description="A positive count",
            validator=positive_validator,
        )

        # Valid positive value
        is_valid, error = term.validate(10)
        assert is_valid is True

        # Invalid negative value
        is_valid, error = term.validate(-5)
        assert is_valid is False
        assert "Custom validation failed" in error

    def test_validate_with_metadata(self):
        """Test term with metadata."""
        term = ContractTerm(
            name="field_with_meta",
            expected_type=str,
            description="A field with metadata",
            metadata={"source": "user", "priority": "high"},
        )
        assert term.metadata["source"] == "user"
        assert term.metadata["priority"] == "high"


# =============================================================================
# ValidationResult Tests
# =============================================================================


class TestValidationResult:
    """Tests for ValidationResult class."""

    def test_create_success_result(self):
        """Test creating successful validation result."""
        result = ValidationResult(is_valid=True)
        assert result.is_valid is True
        assert len(result.violations) == 0
        assert len(result.warnings) == 0

    def test_create_failure_result(self):
        """Test creating failed validation result."""
        result = ValidationResult(
            is_valid=False,
            violations=["Violation 1", "Violation 2"],
            warnings=["Warning 1"],
        )
        assert result.is_valid is False
        assert len(result.violations) == 2
        assert len(result.warnings) == 1

    def test_success_factory_method(self):
        """Test success factory method."""
        result = ValidationResult.success(details={"key": "value"})
        assert result.is_valid is True
        assert result.details["key"] == "value"

    def test_failure_factory_method(self):
        """Test failure factory method."""
        result = ValidationResult.failure(
            violations=["Missing input"],
            warnings=["Optional missing"],
            details={"phase": "PLANNING"},
        )
        assert result.is_valid is False
        assert "Missing input" in result.violations
        assert "Optional missing" in result.warnings
        assert result.details["phase"] == "PLANNING"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        result = ValidationResult(
            is_valid=False,
            violations=["Test violation"],
            validator_name="test_validator",
        )
        data = result.to_dict()
        assert data["is_valid"] is False
        assert "Test violation" in data["violations"]
        assert data["validator_name"] == "test_validator"
        assert "validated_at" in data


# =============================================================================
# PhaseContract Tests
# =============================================================================


class TestPhaseContract:
    """Tests for PhaseContract class."""

    def test_create_basic_contract(self):
        """Test creating a basic contract."""
        contract = PhaseContract(
            phase_name="TEST",
            description="Test phase contract",
        )
        assert contract.phase_name == "TEST"
        assert contract.description == "Test phase contract"
        assert contract.version == "1.0.0"

    def test_add_required_input_fluent(self):
        """Test fluent interface for adding required inputs."""
        contract = PhaseContract(phase_name="TEST")
        contract.add_required_input(
            name="user_goal",
            expected_type=str,
            description="User's goal",
        )
        assert "user_goal" in contract.required_inputs
        assert contract.required_inputs["user_goal"].expected_type == str

    def test_add_optional_input_fluent(self):
        """Test fluent interface for adding optional inputs."""
        contract = PhaseContract(phase_name="TEST")
        contract.add_optional_input(
            name="context",
            expected_type=dict,
            description="Additional context",
            default_value={},
        )
        assert "context" in contract.optional_inputs
        assert contract.optional_inputs["context"].default_value == {}

    def test_add_expected_output_fluent(self):
        """Test fluent interface for adding expected outputs."""
        contract = PhaseContract(phase_name="TEST")
        contract.add_expected_output(
            name="result",
            expected_type=dict,
            description="Test result",
        )
        assert "result" in contract.expected_outputs

    def test_with_quality_criteria(self):
        """Test adding quality criteria."""
        contract = PhaseContract(phase_name="TEST")
        contract.with_quality_criteria("overall_quality", 0.85)
        assert contract.quality_criteria["overall_quality"] == 0.85

    def test_quality_criteria_invalid_threshold(self):
        """Test that invalid quality threshold raises error."""
        contract = PhaseContract(phase_name="TEST")
        with pytest.raises(ValueError):
            contract.with_quality_criteria("test", 1.5)
        with pytest.raises(ValueError):
            contract.with_quality_criteria("test", -0.1)

    def test_add_validator(self):
        """Test adding custom validator."""

        def custom_validator(state):
            return ValidationResult.success()

        contract = PhaseContract(phase_name="TEST")
        contract.add_validator(custom_validator)
        assert len(contract.validators) == 1

    def test_validate_inputs_missing_required(self, state_machine):
        """Test input validation with missing required inputs."""
        contract = PhaseContract(phase_name="TEST").add_required_input(
            name="required_field",
            expected_type=str,
            description="A required field",
        )
        result = contract.validate_inputs(state_machine)
        assert result.is_valid is False
        assert "Missing required input: required_field" in result.violations

    def test_validate_inputs_present(self, state_with_planning_inputs):
        """Test input validation with required inputs present."""
        contract = (
            PhaseContract(phase_name="PLANNING")
            .add_required_input("user_goal", str, "User goal")
            .add_required_input("context", dict, "Context")
        )
        result = contract.validate_inputs(state_with_planning_inputs)
        assert result.is_valid is True

    def test_validate_inputs_type_mismatch(self, state_machine):
        """Test input validation with type mismatch."""
        state_machine.add_artifact("user_goal", 123)  # Should be str

        contract = PhaseContract(phase_name="TEST").add_required_input(
            name="user_goal",
            expected_type=str,
            description="User goal",
        )
        result = contract.validate_inputs(state_machine)
        assert result.is_valid is False
        assert "Invalid input" in result.violations[0]

    def test_validate_outputs_missing(self, state_machine):
        """Test output validation with missing outputs."""
        contract = PhaseContract(phase_name="TEST").add_expected_output(
            name="result",
            expected_type=dict,
            description="Test result",
        )
        result = contract.validate_outputs(state_machine)
        assert result.is_valid is False
        assert "Missing expected output: result" in result.violations

    def test_validate_outputs_present(self, state_with_planning_outputs):
        """Test output validation with outputs present."""
        contract = create_planning_contract()
        result = contract.validate_outputs(state_with_planning_outputs)
        assert result.is_valid is True

    def test_validate_outputs_type_mismatch(self, state_machine):
        """Test output validation with type mismatch."""
        state_machine.add_artifact("result", "should be dict")

        contract = PhaseContract(phase_name="TEST").add_expected_output(
            name="result",
            expected_type=dict,
            description="Test result",
        )
        result = contract.validate_outputs(state_machine)
        assert result.is_valid is False
        assert "wrong type" in result.violations[0]

    def test_validate_quality_below_threshold(self, state_machine):
        """Test quality validation below threshold."""
        state_machine.set_quality_score(0.75)

        contract = PhaseContract(phase_name="TEST").with_quality_criteria(
            "overall_quality", 0.85
        )
        result = contract.validate_quality(state_machine)
        assert result.is_valid is False
        assert "below threshold" in result.violations[0]

    def test_validate_quality_meets_threshold(self, state_machine):
        """Test quality validation meeting threshold."""
        state_machine.set_quality_score(0.90)

        contract = PhaseContract(phase_name="TEST").with_quality_criteria(
            "overall_quality", 0.85
        )
        result = contract.validate_quality(state_machine)
        assert result.is_valid is True

    def test_get_missing_inputs(self, state_machine):
        """Test getting list of missing inputs."""
        contract = (
            PhaseContract(phase_name="TEST")
            .add_required_input("field1", str, "Field 1")
            .add_required_input("field2", str, "Field 2")
        )
        state_machine.add_artifact("field1", "value1")

        missing = contract.get_missing_inputs(state_machine)
        assert "field2" in missing
        assert "field1" not in missing

    def test_get_produced_outputs(self, state_machine):
        """Test getting list of produced outputs."""
        contract = (
            PhaseContract(phase_name="TEST")
            .add_expected_output("output1", dict, "Output 1")
            .add_expected_output("output2", dict, "Output 2")
        )
        state_machine.add_artifact("output1", {"data": "value"})

        produced = contract.get_produced_outputs(state_machine)
        assert "output1" in produced
        assert "output2" not in produced

    def test_validate_with_context_injected(self, context):
        """Test validation with context_injected data."""
        state = PipelineStateMachine(context)
        state.inject_context({"user_goal": "Injected goal"})

        contract = PhaseContract(phase_name="TEST").add_required_input(
            name="user_goal",
            expected_type=str,
            description="User goal",
        )
        result = contract.validate_inputs(state)
        assert result.is_valid is True

    def test_to_dict(self):
        """Test contract serialization."""
        contract = (
            PhaseContract(phase_name="TEST", description="Test contract")
            .add_required_input("input1", str, "Required input")
            .add_optional_input("optional1", dict, "Optional input", default_value={})
            .add_expected_output("output1", dict, "Expected output")
            .with_quality_criteria("quality", 0.85)
        )
        data = contract.to_dict()
        assert data["phase_name"] == "TEST"
        assert data["description"] == "Test contract"
        assert "input1" in data["required_inputs"]
        assert "optional1" in data["optional_inputs"]
        assert "output1" in data["expected_outputs"]
        assert "quality" in data["quality_criteria"]


# =============================================================================
# PhaseContractRegistry Tests
# =============================================================================


class TestPhaseContractRegistry:
    """Tests for PhaseContractRegistry class."""

    def test_register_and_get(self):
        """Test registering and retrieving contracts."""
        registry = PhaseContractRegistry()
        contract = PhaseContract(phase_name="TEST")
        registry.register(contract)

        retrieved = registry.get("TEST")
        assert retrieved is contract

    def test_get_nonexistent_raises_error(self):
        """Test that getting nonexistent contract raises KeyError."""
        registry = PhaseContractRegistry()
        with pytest.raises(KeyError):
            registry.get("NONEXISTENT")

    def test_get_or_none(self):
        """Test get_or_none method."""
        registry = PhaseContractRegistry()
        contract = PhaseContract(phase_name="TEST")
        registry.register(contract)

        assert registry.get_or_none("TEST") is contract
        assert registry.get_or_none("NONEXISTENT") is None

    def test_unregister(self):
        """Test unregistering a contract."""
        registry = PhaseContractRegistry()
        contract = PhaseContract(phase_name="TEST")
        registry.register(contract)

        removed = registry.unregister("TEST")
        assert removed is contract
        assert registry.get_or_none("TEST") is None

    def test_validate_phase_transition(self, state_with_planning_outputs):
        """Test phase transition validation."""
        registry = PhaseContractRegistry()
        registry.register_default_contracts()

        result = registry.validate_phase_transition(
            "PLANNING", "DEVELOPMENT", state_with_planning_outputs
        )
        assert result.is_valid is True

    def test_validate_phase_transition_missing_inputs(self, state_machine):
        """Test phase transition validation with missing inputs."""
        registry = PhaseContractRegistry()
        registry.register_default_contracts()

        # Try to transition without any artifacts
        result = registry.validate_phase_transition(
            "PLANNING", "DEVELOPMENT", state_machine
        )
        assert result.is_valid is False
        # The violation message mentions either source phase outputs or target phase inputs
        assert (
            "missing" in result.violations[0].lower()
            or "not produced" in result.violations[0].lower()
        )

    def test_get_all_contracts(self):
        """Test getting all registered contracts."""
        registry = PhaseContractRegistry()
        registry.register_default_contracts()

        contracts = registry.get_all_contracts()
        assert len(contracts) == 4
        assert "PLANNING" in contracts
        assert "DEVELOPMENT" in contracts
        assert "QUALITY" in contracts
        assert "DECISION" in contracts

    def test_register_default_contracts(self):
        """Test registering default contracts."""
        registry = PhaseContractRegistry()
        registry.register_default_contracts()

        # Verify all default contracts are registered
        planning = registry.get("PLANNING")
        development = registry.get("DEVELOPMENT")
        quality = registry.get("QUALITY")
        decision = registry.get("DECISION")

        assert planning is not None
        assert development is not None
        assert quality is not None
        assert decision is not None

    def test_thread_safety(self):
        """Test thread safety of registry operations."""
        import threading
        import time

        registry = PhaseContractRegistry()
        errors = []

        def register_contract(phase_name):
            try:
                contract = PhaseContract(phase_name=phase_name)
                registry.register(contract)
            except Exception as e:
                errors.append(e)

        # Create multiple threads registering contracts
        threads = []
        for i in range(10):
            t = threading.Thread(target=register_contract, args=(f"PHASE_{i}",))
            threads.append(t)
            t.start()

        # Wait for all threads to complete
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(registry.get_all_contracts()) == 10


# =============================================================================
# Default Contract Creation Tests
# =============================================================================


class TestCreateDefaultContracts:
    """Tests for default contract creation functions."""

    def test_create_default_phase_contracts(self):
        """Test creating all default phase contracts."""
        contracts = create_default_phase_contracts()
        assert len(contracts) == 4

        phase_names = [c.phase_name for c in contracts]
        assert "PLANNING" in phase_names
        assert "DEVELOPMENT" in phase_names
        assert "QUALITY" in phase_names
        assert "DECISION" in phase_names

    def test_create_planning_contract(self):
        """Test PLANNING contract structure."""
        contract = create_planning_contract()
        assert contract.phase_name == "PLANNING"
        assert "user_goal" in contract.required_inputs
        assert "context" in contract.required_inputs
        assert "previous_plan" in contract.optional_inputs
        assert "defects" in contract.optional_inputs
        assert "planning_artifacts" in contract.expected_outputs
        assert "task_breakdown" in contract.expected_outputs
        assert "complexity_analysis" in contract.expected_outputs
        assert "overall_quality" in contract.quality_criteria

    def test_create_development_contract(self):
        """Test DEVELOPMENT contract structure."""
        contract = create_development_contract()
        assert contract.phase_name == "DEVELOPMENT"
        assert "planning_artifacts" in contract.required_inputs
        assert "user_goal" in contract.required_inputs
        assert "defects" in contract.optional_inputs
        assert "existing_code" in contract.optional_inputs
        assert "code_artifacts" in contract.expected_outputs
        assert "test_artifacts" in contract.expected_outputs
        assert "documentation" in contract.expected_outputs
        assert "overall_quality" in contract.quality_criteria

    def test_create_quality_contract(self):
        """Test QUALITY contract structure."""
        contract = create_quality_contract()
        assert contract.phase_name == "QUALITY"
        assert "planning_artifacts" in contract.required_inputs
        assert "code_artifacts" in contract.required_inputs
        assert "quality_template" in contract.required_inputs
        assert len(contract.validators) >= 1  # Has completeness validator

    def test_create_decision_contract(self):
        """Test DECISION contract structure."""
        contract = create_decision_contract()
        assert contract.phase_name == "DECISION"
        assert "quality_report" in contract.required_inputs
        assert "defects" in contract.required_inputs
        assert "iteration_count" in contract.required_inputs
        assert "max_iterations" in contract.optional_inputs
        assert "decision" in contract.expected_outputs
        assert len(contract.validators) >= 1  # Has context validator


# =============================================================================
# Validator Function Tests
# =============================================================================


class TestValidatorFunctions:
    """Tests for internal validator functions."""

    def test_validate_quality_completeness_with_artifacts(
        self, state_with_quality_outputs
    ):
        """Test quality completeness validator with artifacts present."""
        result = _validate_quality_completeness(state_with_quality_outputs)
        assert result.is_valid is True

    def test_validate_quality_completeness_missing_code(self, context):
        """Test quality completeness validator with missing code artifacts."""
        state = PipelineStateMachine(context)
        state.add_artifact("planning_artifacts", {"plan": "test"})
        # Missing code_artifacts

        result = _validate_quality_completeness(state)
        assert result.is_valid is False
        assert "No code artifacts to evaluate" in result.violations

    def test_validate_quality_completeness_missing_planning(self, context):
        """Test quality completeness validator with missing planning artifacts."""
        state = PipelineStateMachine(context)
        state.add_artifact("code_artifacts", {"main.py": "code"})
        # Missing planning_artifacts

        result = _validate_quality_completeness(state)
        assert result.is_valid is False
        assert "planning artifacts" in result.violations[0].lower()

    def test_validate_decision_context_with_score(self, state_with_quality_outputs):
        """Test decision context validator with quality score."""
        result = _validate_decision_context(state_with_quality_outputs)
        assert result.is_valid is True

    def test_validate_decision_context_missing_score(self, context):
        """Test decision context validator without quality score."""
        state = PipelineStateMachine(context)
        # quality_score is None

        result = _validate_decision_context(state)
        assert result.is_valid is False
        assert "quality score" in result.violations[0].lower()


# =============================================================================
# Integration Tests
# =============================================================================


class TestPhaseContractIntegration:
    """Integration tests for PhaseContract with PipelineState."""

    def test_full_planning_phase_workflow(self, context):
        """Test complete PLANNING phase workflow."""
        state = PipelineStateMachine(context)
        contract = create_planning_contract()

        # Phase 1: Check inputs are missing
        assert not contract.validate_inputs(state).is_valid
        missing = contract.get_missing_inputs(state)
        assert "user_goal" in missing
        assert "context" in missing

        # Phase 2: Add required inputs
        state.add_artifact("user_goal", "Build REST API")
        state.add_artifact("context", {"framework": "fastapi"})

        # Phase 3: Validate inputs are satisfied
        input_result = contract.validate_inputs(state)
        assert input_result.is_valid is True

        # Phase 4: Execute phase (simulate by adding outputs)
        state.add_artifact("planning_artifacts", {"plan": "detailed plan"})
        state.add_artifact("task_breakdown", ["task1", "task2"])
        state.add_artifact("complexity_analysis", {"score": 0.7})
        state.set_quality_score(0.88)

        # Phase 5: Validate outputs
        output_result = contract.validate_outputs(state)
        assert output_result.is_valid is True

        # Phase 6: Validate quality
        quality_result = contract.validate_quality(state)
        assert quality_result.is_valid is True

    def test_full_development_phase_workflow(self, context):
        """Test complete DEVELOPMENT phase workflow."""
        state = PipelineStateMachine(context)
        contract = create_development_contract()

        # Add required inputs
        state.add_artifact("user_goal", "Build REST API")
        state.add_artifact(
            "planning_artifacts", {"tasks": ["implement endpoint", "add tests"]}
        )

        # Validate inputs
        input_result = contract.validate_inputs(state)
        assert input_result.is_valid is True

        # Add expected outputs
        state.add_artifact("code_artifacts", {"api.py": "code"})
        state.add_artifact("test_artifacts", {"test_api.py": "tests"})
        state.add_artifact("documentation", {"README.md": "docs"})
        state.set_quality_score(0.92)

        # Validate outputs
        output_result = contract.validate_outputs(state)
        assert output_result.is_valid is True

    def test_planning_to_development_transition(self, state_with_planning_outputs):
        """Test transition from PLANNING to DEVELOPMENT phase."""
        registry = PhaseContractRegistry()
        registry.register_default_contracts()

        # Validate transition
        result = registry.validate_phase_transition(
            "PLANNING", "DEVELOPMENT", state_with_planning_outputs
        )
        assert result.is_valid is True

        # Verify DEVELOPMENT contract can be retrieved
        dev_contract = registry.get("DEVELOPMENT")
        assert dev_contract is not None

    def test_quality_phase_with_defects(self, context):
        """Test QUALITY phase with defect generation."""
        state = PipelineStateMachine(context)

        # Add required inputs
        state.add_artifact("planning_artifacts", {"requirements": ["req1"]})
        state.add_artifact("code_artifacts", {"main.py": "code"})
        state.add_artifact("quality_template", "STANDARD")

        # Execute quality evaluation
        state.add_artifact("quality_report", {"code_quality": 0.85, "coverage": 0.70})
        state.add_artifact(
            "defects",
            [
                {"type": "MISSING_TESTS", "severity": "HIGH"},
                {"type": "CODE_COMPLEXITY", "severity": "MEDIUM"},
            ],
        )
        state.add_artifact(
            "quality_score", 0.75
        )  # Add as artifact for output validation
        state.set_quality_score(0.75)

        # Validate quality phase outputs
        quality_contract = create_quality_contract()
        output_result = quality_contract.validate_outputs(state)
        assert output_result.is_valid is True

        # Quality below threshold
        quality_result = quality_contract.validate_quality(state)
        assert quality_result.is_valid is False
        assert "below threshold" in quality_result.violations[0]

    def test_defect_routing_validation(self, context):
        """Test defect routing validation integration."""
        registry = PhaseContractRegistry()
        registry.register_default_contracts()

        # Create a defect
        defect = {
            "id": "defect-001",
            "type": "MISSING_TESTS",
            "severity": "HIGH",
            "description": "No unit tests",
            "target_phase": "DEVELOPMENT",
        }
        defect_obj = Defect.from_dict(defect)

        # Validate routing
        result = validate_defect_routing(defect_obj, registry)
        assert result.is_valid is True
        assert result.details["target_phase"] == "DEVELOPMENT"

    def test_loop_back_scenario_with_defects(self, context):
        """Test loop-back scenario where defects flow back to PLANNING."""
        state = PipelineStateMachine(context)
        registry = PhaseContractRegistry()
        registry.register_default_contracts()

        # Simulate defects from failed QUALITY phase
        defects = [
            {"type": "MISSING_REQUIREMENT", "severity": "HIGH"},
            {"type": "INCORRECT_IMPLEMENTATION", "severity": "MEDIUM"},
        ]
        state.add_artifact("defects", defects)
        state.inject_context({"defects": defects})

        # PLANNING should accept defects as optional input
        planning_contract = registry.get("PLANNING")
        input_result = planning_contract.validate_inputs(state)

        # Should pass - defects are optional (but note: PLANNING still needs user_goal and context)
        # The test verifies that defects don't cause validation failure
        assert input_result.is_valid or "defects" not in str(input_result.violations)


# =============================================================================
# ContractViolationError Tests
# =============================================================================


class TestContractViolationError:
    """Tests for ContractViolationError exception."""

    def test_create_error(self):
        """Test creating contract violation error."""
        error = ContractViolationError(
            message="Missing required inputs",
            phase="PLANNING",
            violations=["Missing user_goal", "Missing context"],
            severity=ContractViolationSeverity.CRITICAL,
        )

        assert error.phase == "PLANNING"
        assert len(error.violations) == 2
        assert error.severity == ContractViolationSeverity.CRITICAL

    def test_error_to_dict(self):
        """Test error serialization."""
        error = ContractViolationError(
            message="Test error",
            phase="TEST",
            violations=["Violation 1"],
            severity=ContractViolationSeverity.ERROR,
        )
        data = error.to_dict()
        assert data["error"] == "ContractViolationError"
        assert data["phase"] == "TEST"
        assert data["severity"] == "ERROR"
        assert "violations" in data

    def test_error_inherits_from_gaia_exception(self):
        """Test that ContractViolationError inherits from GAIAException."""
        from gaia.exceptions import GAIAException

        error = ContractViolationError(
            message="Test",
            phase="TEST",
            violations=[],
            severity=ContractViolationSeverity.WARNING,
        )
        assert isinstance(error, GAIAException)


# =============================================================================
# PhaseExecutionError Tests
# =============================================================================


class TestPhaseExecutionError:
    """Tests for PhaseExecutionError exception."""

    def test_create_error(self):
        """Test creating phase execution error."""
        error = PhaseExecutionError(
            message="Phase failed",
            phase="DEVELOPMENT",
            missing_outputs=["code_artifacts", "test_artifacts"],
        )

        assert error.phase == "DEVELOPMENT"
        assert len(error.missing_outputs) == 2

    def test_error_with_cause(self):
        """Test error with underlying cause."""
        cause = ValueError("Underlying error")
        error = PhaseExecutionError(
            message="Phase failed due to cause",
            phase="QUALITY",
            cause=cause,
        )

        assert error.cause is cause

    def test_error_inherits_from_gaia_exception(self):
        """Test that PhaseExecutionError inherits from GAIAException."""
        from gaia.exceptions import GAIAException

        error = PhaseExecutionError(
            message="Test",
            phase="TEST",
        )
        assert isinstance(error, GAIAException)


# =============================================================================
# ContractTerm Edge Cases Tests
# =============================================================================


class TestContractTermEdgeCases:
    """Edge case tests for ContractTerm."""

    def test_validate_none_value(self):
        """Test validation of None value."""
        term = ContractTerm(
            name="nullable",
            expected_type=str,
            description="A nullable field",
        )
        is_valid, error = term.validate(None)
        assert is_valid is False  # None is not a str

    def test_validate_subclass_type(self):
        """Test validation with subclass type."""

        class CustomDict(dict):
            pass

        term = ContractTerm(
            name="mapping",
            expected_type=dict,
            description="A mapping",
        )
        is_valid, error = term.validate(CustomDict())
        assert is_valid is True  # CustomDict is a dict subclass

    def test_validate_list_type(self):
        """Test validation of list type."""
        term = ContractTerm(
            name="items",
            expected_type=list,
            description="A list of items",
        )
        is_valid, error = term.validate([1, 2, 3])
        assert is_valid is True

        is_valid, error = term.validate("not a list")
        assert is_valid is False


# =============================================================================
# Quality Criteria Edge Cases Tests
# =============================================================================


class TestQualityCriteriaEdgeCases:
    """Edge case tests for quality criteria validation."""

    def test_quality_threshold_boundary_zero(self):
        """Test quality threshold at zero boundary."""
        contract = PhaseContract(phase_name="TEST")
        contract.with_quality_criteria("test_metric", 0.0)
        assert contract.quality_criteria["test_metric"] == 0.0

    def test_quality_threshold_boundary_one(self):
        """Test quality threshold at one boundary."""
        contract = PhaseContract(phase_name="TEST")
        contract.with_quality_criteria("test_metric", 1.0)
        assert contract.quality_criteria["test_metric"] == 1.0

    def test_quality_criteria_from_artifacts(self, context):
        """Test quality criteria evaluation from quality_report artifact."""
        state = PipelineStateMachine(context)
        state.add_artifact(
            "quality_report",
            {"code_quality": 0.88, "test_coverage": 0.95, "documentation": 0.80},
        )

        contract = (
            PhaseContract(phase_name="TEST")
            .with_quality_criteria("code_quality", 0.85)
            .with_quality_criteria("test_coverage", 0.90)
            .with_quality_criteria("documentation", 0.85)
        )

        result = contract.validate_quality(state)
        # code_quality passes (0.88 >= 0.85)
        # test_coverage passes (0.95 >= 0.90)
        # documentation fails (0.80 < 0.85)
        assert result.is_valid is False
        assert "documentation" in result.violations[0]
