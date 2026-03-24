"""
GAIA Production Validation Hooks

Pre-action and post-action validation hooks for pipeline quality gates.
"""

from typing import Dict, List, Any, Optional

from gaia.hooks.base import BaseHook, HookContext, HookResult, HookPriority
from gaia.utils.logging import get_logger


logger = get_logger(__name__)


class PreActionValidationHook(BaseHook):
    """
    Validates preconditions before agent action.

    This is a blocking hook that ensures:
    - Required context is present
    - State is valid for the action
    - No blocking defects exist

    If validation fails, the pipeline is halted to prevent
    proceeding with invalid state.
    """

    name = "pre_action_validation"
    event = "AGENT_EXECUTE"
    priority = HookPriority.HIGH
    blocking = True
    description = "Validates preconditions before agent execution"

    # Required context keys
    REQUIRED_CONTEXT = ["user_goal", "current_phase"]

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute pre-action validation.

        Args:
            context: Hook context

        Returns:
            HookResult with validation outcome
        """
        logger.info(
            f"Running pre-action validation for pipeline {context.pipeline_id}",
            extra={"pipeline_id": context.pipeline_id, "agent_id": context.agent_id},
        )

        # Check required context
        missing_context = self._check_required_context(context.state)
        if missing_context:
            return HookResult.failure_result(
                error_message=f"Missing required context: {missing_context}",
                blocking=True,
                halt_pipeline=True,
            )

        # Check for blocking defects
        blocking_defects = self._get_blocking_defects(context.state)
        if blocking_defects:
            return HookResult.failure_result(
                error_message=f"Blocking defects present: {len(blocking_defects)}",
                blocking=True,
                halt_pipeline=True,
                defects=blocking_defects,
            )

        # Check state validity
        state_valid = self._validate_state(context.state)
        if not state_valid:
            return HookResult.failure_result(
                error_message="Invalid pipeline state for agent execution",
                blocking=True,
                halt_pipeline=True,
            )

        logger.info(
            "Pre-action validation passed",
            extra={"pipeline_id": context.pipeline_id},
        )

        return HookResult.success_result(
            metadata={"validation": "passed"}
        )

    def _check_required_context(self, state: Dict[str, Any]) -> List[str]:
        """
        Check for required context keys.

        Args:
            state: Pipeline state dictionary

        Returns:
            List of missing context keys
        """
        missing = []
        for key in self.REQUIRED_CONTEXT:
            if key not in state:
                missing.append(key)
        return missing

    def _get_blocking_defects(
        self,
        state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Get defects that block execution.

        Args:
            state: Pipeline state dictionary

        Returns:
            List of blocking defects
        """
        defects = state.get("defects", [])
        return [
            d for d in defects
            if d.get("blocking", False) or d.get("severity") == "critical"
        ]

    def _validate_state(self, state: Dict[str, Any]) -> bool:
        """
        Validate pipeline state for agent execution.

        Args:
            state: Pipeline state dictionary

        Returns:
            True if state is valid
        """
        # Check for basic state validity
        if not state.get("current_phase"):
            return False

        # Check iteration count hasn't exceeded max
        iteration_count = state.get("iteration_count", 0)
        max_iterations = state.get("max_iterations", 10)
        if max_iterations > 0 and iteration_count >= max_iterations:
            return False

        return True


class PostActionValidationHook(BaseHook):
    """
    Validates agent output after execution.

    This hook ensures:
    - Output format is valid
    - Required artifacts were created
    - No new critical defects were introduced

    Unlike PreActionValidationHook, this is non-blocking
    and records defects for later processing.
    """

    name = "post_action_validation"
    event = "AGENT_COMPLETE"
    priority = HookPriority.NORMAL
    blocking = False
    description = "Validates agent output after execution"

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute post-action validation.

        Args:
            context: Hook context

        Returns:
            HookResult with validation outcome
        """
        logger.debug(
            f"Running post-action validation for agent {context.agent_id}",
            extra={"agent_id": context.agent_id},
        )

        output = context.data.get("output", {})
        defects = []

        # Validate output exists
        if not output:
            defects.append(
                self._create_defect(
                    description="No output generated by agent",
                    severity="high",
                    category="output_validation",
                )
            )
            return HookResult(
                success=False,
                defects=defects,
                metadata={"validation": "failed"},
            )

        # Check for expected artifacts
        expected_artifacts = context.metadata.get("expected_artifacts", [])
        for artifact in expected_artifacts:
            if artifact not in output:
                defects.append(
                    self._create_defect(
                        description=f"Expected artifact not created: {artifact}",
                        severity="medium",
                        category="missing_artifact",
                        suggestion="Ensure agent creates all required artifacts",
                    )
                )

        # Validate output format
        format_valid = self._validate_output_format(output)
        if not format_valid:
            defects.append(
                self._create_defect(
                    description="Output format validation failed",
                    severity="medium",
                    category="format_error",
                )
            )

        # Check for error indicators in output
        errors = output.get("errors", [])
        if errors:
            for error in errors[:5]:  # Limit to first 5
                defects.append(
                    self._create_defect(
                        description=f"Agent error: {error}",
                        severity="high",
                        category="agent_error",
                    )
                )

        logger.debug(
            f"Post-action validation complete: {len(defects)} defects found",
            extra={"defect_count": len(defects)},
        )

        return HookResult(
            success=len(defects) == 0,
            defects=defects,
            metadata={
                "validation": "passed" if not defects else "failed",
                "defects_found": len(defects),
            },
        )

    def _validate_output_format(self, output: Dict[str, Any]) -> bool:
        """
        Validate output format.

        Args:
            output: Agent output dictionary

        Returns:
            True if format is valid
        """
        # Basic format validation
        if not isinstance(output, dict):
            return False

        # Check for at least one of content, artifact, or result
        valid_keys = ["content", "artifact", "result", "output", "data"]
        return any(key in output for key in valid_keys)

    def _create_defect(
        self,
        description: str,
        severity: str = "medium",
        category: Optional[str] = None,
        suggestion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a defect record."""
        return {
            "category": category or "validation",
            "description": description,
            "severity": severity,
            "suggestion": suggestion,
            "source": "post_action_validation",
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        }
