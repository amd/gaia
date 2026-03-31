"""
GAIA Production Context Hooks

Context injection and output processing hooks for pipeline data flow.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class ContextInjectionHook(BaseHook):
    """
    Injects additional context at execution points.

    This hook enriches agent execution context with:
    - Previous iteration results
    - Related chronicle entries
    - Memory retrievals
    - Defect history

    This enables agents to make informed decisions based on
    the full execution history.
    """

    name = "context_injection"
    event = "AGENT_EXECUTE"
    priority = HookPriority.NORMAL
    blocking = False
    description = "Injects additional context for agent execution"

    # Maximum items to inject for each category
    MAX_PREVIOUS_RESULTS = 5
    MAX_CHRONICLE_ENTRIES = 10
    MAX_DEFECT_HISTORY = 10

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute context injection.

        Args:
            context: Hook context

        Returns:
            HookResult with injected context
        """
        logger.debug(
            f"Injecting context for agent {context.agent_id}",
            extra={"agent_id": context.agent_id},
        )

        injected: Dict[str, Any] = {}

        # Inject previous iteration results
        previous_results = self._get_previous_results(context.state)
        if previous_results:
            injected["previous_results"] = previous_results

        # Inject chronicle entries
        chronicle = self._get_relevant_chronicle(context.state)
        if chronicle:
            injected["chronicle"] = chronicle

        # Inject defect history
        defect_history = self._get_defect_history(context.state)
        if defect_history:
            injected["defect_history"] = defect_history

        # Inject memory retrievals if available
        memory = context.state.get("memory_retrievals")
        if memory:
            injected["memory"] = memory

        # Inject quality scores from previous iterations
        quality_history = self._get_quality_history(context.state)
        if quality_history:
            injected["quality_history"] = quality_history

        logger.debug(
            f"Context injection complete: {len(injected)} items",
            extra={"injected_keys": list(injected.keys())},
        )

        return HookResult.success_result(
            inject_context=injected,
            metadata={"injected_keys": list(injected.keys())},
        )

    def _get_previous_results(
        self,
        state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Get results from previous iterations.

        Args:
            state: Pipeline state

        Returns:
            List of previous results
        """
        results = state.get("previous_results", [])
        if not results:
            # Try alternative key
            results = state.get("iteration_results", [])

        return results[-self.MAX_PREVIOUS_RESULTS :]

    def _get_relevant_chronicle(
        self,
        state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Get relevant chronicle entries.

        Args:
            state: Pipeline state

        Returns:
            List of chronicle entries
        """
        chronicle = state.get("chronicle", [])
        if not chronicle:
            return []

        # Return most recent entries
        return chronicle[-self.MAX_CHRONICLE_ENTRIES :]

    def _get_defect_history(
        self,
        state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Get defect history for current phase.

        Args:
            state: Pipeline state

        Returns:
            List of defects
        """
        defects = state.get("defects", [])
        if not defects:
            return []

        # Filter by current phase if available
        current_phase = state.get("current_phase")
        if current_phase:
            phase_defects = [d for d in defects if d.get("phase") == current_phase]
            if phase_defects:
                return phase_defects[-self.MAX_DEFECT_HISTORY :]

        return defects[-self.MAX_DEFECT_HISTORY :]

    def _get_quality_history(
        self,
        state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Get quality score history.

        Args:
            state: Pipeline state

        Returns:
            List of quality score records
        """
        quality_scores = state.get("quality_scores", [])
        if not quality_scores:
            # Try alternative format
            quality_report = state.get("quality_report")
            if quality_report:
                quality_scores = [quality_report]

        return quality_scores[-self.MAX_PREVIOUS_RESULTS :]


class OutputProcessingHook(BaseHook):
    """
    Processes and formats agent output.

    This hook standardizes output format by:
    - Normalizing format across different agents
    - Extracting artifacts from output
    - Enriching with metadata
    - Preparing for downstream consumption
    """

    name = "output_processing"
    event = "OUTPUT_PROCESS"
    priority = HookPriority.LOW
    blocking = False
    description = "Processes and formats agent output"

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute output processing.

        Args:
            context: Hook context

        Returns:
            HookResult with processed output
        """
        logger.debug(
            f"Processing output for agent {context.agent_id}",
            extra={"agent_id": context.agent_id},
        )

        output = context.data.get("output", {})

        # Normalize output format
        processed = self._normalize_output(output, context)

        # Extract artifacts
        artifacts = self._extract_artifacts(processed)

        # Enrich with metadata
        processed["metadata"] = self._enrich_metadata(
            processed.get("metadata", {}),
            context,
        )

        # Store extracted artifacts
        if artifacts:
            processed["artifacts"] = artifacts

        logger.debug(
            "Output processing complete",
            extra={"artifact_count": len(artifacts)},
        )

        return HookResult.success_result(
            modify_data={"output": processed},
            metadata={
                "processed": True,
                "artifact_count": len(artifacts),
            },
        )

    def _normalize_output(
        self,
        output: Any,
        context: HookContext,
    ) -> Dict[str, Any]:
        """
        Normalize output to standard format.

        Args:
            output: Raw output (may be any type)
            context: Hook context

        Returns:
            Normalized output dictionary
        """
        if not output:
            return {"content": "", "artifacts": {}}

        if isinstance(output, str):
            return {
                "content": output,
                "artifacts": {},
            }

        if isinstance(output, dict):
            # Already a dict - ensure standard keys exist
            normalized = dict(output)
            if "content" not in normalized and "output" in normalized:
                normalized["content"] = normalized["output"]
            return normalized

        # Unknown type - convert to string
        return {
            "content": str(output),
            "artifacts": {},
        }

    def _extract_artifacts(
        self,
        output: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Extract artifacts from output.

        Args:
            output: Normalized output dictionary

        Returns:
            Dictionary of extracted artifacts
        """
        artifacts = {}

        # Check for explicit artifacts key
        if "artifacts" in output:
            artifacts.update(output["artifacts"])

        # Check for common artifact patterns
        artifact_keys = ["file", "files", "code", "document", "spec"]
        for key in artifact_keys:
            if key in output and key not in artifacts:
                artifacts[key] = output[key]

        # Check for nested artifact indicators
        if "result" in output and isinstance(output["result"], dict):
            for key, value in output["result"].items():
                if key not in artifacts:
                    artifacts[key] = value

        return artifacts

    def _enrich_metadata(
        self,
        existing_metadata: Dict[str, Any],
        context: HookContext,
    ) -> Dict[str, Any]:
        """
        Enrich output with metadata.

        Args:
            existing_metadata: Existing metadata
            context: Hook context

        Returns:
            Enriched metadata dictionary
        """
        metadata = dict(existing_metadata)

        # Add processing timestamp
        metadata["processed_at"] = datetime.now(timezone.utc).isoformat()

        # Add agent info
        if context.agent_id:
            metadata["agent_id"] = context.agent_id

        # Add phase info
        if context.phase:
            metadata["phase"] = context.phase

        # Add pipeline info
        metadata["pipeline_id"] = context.pipeline_id

        # Add correlation ID for tracing
        if context.correlation_id:
            metadata["correlation_id"] = context.correlation_id

        return metadata
