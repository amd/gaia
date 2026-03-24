"""
GAIA Production Quality Hooks

Quality gate and defect extraction hooks for pipeline quality management.
"""

from datetime import datetime
from typing import Dict, List, Any, Optional

from gaia.hooks.base import BaseHook, HookContext, HookResult, HookPriority
from gaia.utils.logging import get_logger


logger = get_logger(__name__)


class QualityGateHook(BaseHook):
    """
    Enforces quality gates at phase boundaries.

    This blocking hook ensures:
    - Minimum quality score is met
    - No critical defects exist
    - All required validations passed

    If quality gate fails, the pipeline loops back to
    address defects rather than proceeding.
    """

    name = "quality_gate"
    event = "PHASE_EXIT"
    priority = HookPriority.HIGH
    blocking = True
    description = "Enforces quality gates at phase exit"

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute quality gate validation.

        Args:
            context: Hook context

        Returns:
            HookResult with gate validation outcome
        """
        logger.info(
            f"Running quality gate for phase {context.phase}",
            extra={"phase": context.phase, "pipeline_id": context.pipeline_id},
        )

        quality_report = context.data.get("quality_report")

        if not quality_report:
            return HookResult.failure_result(
                error_message="No quality report available for phase exit",
                blocking=True,
                halt_pipeline=False,  # Loop back instead of halt
                metadata={"gate": "quality_report_missing"},
            )

        # Check minimum score
        min_score = context.metadata.get("min_quality_score", 0.75)
        overall_score = quality_report.get("overall_score", 0)

        if overall_score < min_score * 100:
            return HookResult(
                success=False,
                blocking=True,
                halt_pipeline=False,  # Loop back to fix
                error_message=(
                    f"Quality score {overall_score:.1f} below threshold {min_score * 100:.1f}"
                ),
                metadata={
                    "gate": "score_below_threshold",
                    "score": overall_score,
                    "threshold": min_score * 100,
                },
            )

        # Check critical defects
        critical_defects = quality_report.get("critical_defects", 0)
        if critical_defects > 0:
            return HookResult.failure_result(
                error_message=f"{critical_defects} critical defects found",
                blocking=True,
                halt_pipeline=True,  # Critical defects halt pipeline
                metadata={
                    "gate": "critical_defects",
                    "critical_defects": critical_defects,
                },
            )

        logger.info(
            f"Quality gate passed for phase {context.phase}",
            extra={"score": overall_score},
        )

        return HookResult.success_result(
            metadata={
                "gate": "passed",
                "score": overall_score,
            }
        )


class DefectExtractionHook(BaseHook):
    """
    Extracts and categorizes defects from agent output.

    This hook parses agent outputs to identify:
    - Runtime errors
    - Quality issues
    - Validation failures
    - Missing requirements

    Extracted defects are added to the pipeline state
    for tracking and resolution.
    """

    name = "defect_extraction"
    event = "DEFECT_EXTRACT"
    priority = HookPriority.NORMAL
    blocking = False
    description = "Extracts defects from agent output"

    # Defect severity patterns
    SEVERITY_PATTERNS = {
        "critical": ["critical", "fatal", "security", "data loss", "breaking"],
        "high": ["error", "fail", "exception", "crash"],
        "medium": ["warning", "issue", "problem", "concern"],
        "low": ["minor", "cosmetic", "nit", "suggestion"],
    }

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute defect extraction.

        Args:
            context: Hook context

        Returns:
            HookResult with extracted defects
        """
        logger.debug(
            f"Extracting defects for agent {context.agent_id}",
            extra={"agent_id": context.agent_id},
        )

        output = context.data.get("output", {})
        defects = []

        # Extract from error messages
        errors = output.get("errors", [])
        for error in errors:
            defect = self._extract_from_error(error)
            if defect:
                defects.append(defect)

        # Extract from quality issues
        quality_issues = output.get("quality_issues", [])
        for issue in quality_issues:
            defect = self._extract_from_quality_issue(issue)
            if defect:
                defects.append(defect)

        # Extract from validation failures
        validation_failures = output.get("validation_failures", [])
        for failure in validation_failures:
            defect = self._extract_from_validation_failure(failure)
            if defect:
                defects.append(defect)

        # Extract from explicit defect markers
        explicit_defects = output.get("defects", [])
        for explicit in explicit_defects:
            if isinstance(explicit, dict):
                defects.append(explicit)
            else:
                defects.append(self._create_defect(str(explicit)))

        logger.debug(
            f"Defect extraction complete: {len(defects)} defects found",
            extra={"defect_count": len(defects)},
        )

        return HookResult.success_result(
            defects=defects,
            metadata={"defects_extracted": len(defects)},
        )

    def _extract_from_error(self, error: Any) -> Optional[Dict[str, Any]]:
        """Extract defect from error message."""
        if isinstance(error, dict):
            message = error.get("message", str(error))
            source = error.get("source", "unknown")
        else:
            message = str(error)
            source = "unknown"

        severity = self._determine_severity(message)

        return self._create_defect(
            description=message,
            severity=severity,
            category="runtime_error",
            source=source,
        )

    def _extract_from_quality_issue(
        self,
        issue: Any,
    ) -> Optional[Dict[str, Any]]:
        """Extract defect from quality issue."""
        if isinstance(issue, dict):
            description = issue.get("description", issue.get("message", ""))
            issue_type = issue.get("type", issue.get("category", "quality"))
            severity = issue.get("severity", "medium")
        else:
            description = str(issue)
            issue_type = "quality"
            severity = "medium"

        return self._create_defect(
            description=description,
            severity=severity,
            category=issue_type,
        )

    def _extract_from_validation_failure(
        self,
        failure: Any,
    ) -> Optional[Dict[str, Any]]:
        """Extract defect from validation failure."""
        if isinstance(failure, dict):
            description = failure.get("message", failure.get("description", ""))
            validator = failure.get("validator", "unknown")
        else:
            description = str(failure)
            validator = "unknown"

        return self._create_defect(
            description=description,
            severity="medium",
            category="validation_failure",
            source=validator,
        )

    def _determine_severity(self, message: str) -> str:
        """Determine defect severity from message."""
        message_lower = message.lower()

        for severity, patterns in self.SEVERITY_PATTERNS.items():
            if any(pattern in message_lower for pattern in patterns):
                return severity

        return "medium"  # Default severity

    def _create_defect(
        self,
        description: str,
        severity: str = "medium",
        category: Optional[str] = None,
        source: Optional[str] = None,
        suggestion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a defect record."""
        return {
            "category": category or "general",
            "description": description,
            "severity": severity,
            "source": source or "defect_extraction",
            "suggestion": suggestion,
            "timestamp": datetime.utcnow().isoformat(),
        }


class PipelineNotificationHook(BaseHook):
    """
    Sends notifications at pipeline milestones.

    This hook sends notifications for:
    - Pipeline start
    - Phase completion
    - Pipeline complete
    - Pipeline failure

    Notifications can be configured for different channels
    (console, log, external services).
    """

    name = "pipeline_notification"
    event = "*"  # Listen to all events
    priority = HookPriority.LOW
    blocking = False
    description = "Sends notifications at pipeline milestones"

    # Events that trigger notifications
    NOTIFY_EVENTS = [
        "PIPELINE_START",
        "PIPELINE_COMPLETE",
        "PIPELINE_FAILED",
        "PIPELINE_CANCELLED",
    ]

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute notification.

        Args:
            context: Hook context

        Returns:
            HookResult (always success for non-blocking)
        """
        if context.event not in self.NOTIFY_EVENTS:
            return HookResult.success_result()

        notification = self._create_notification(context)

        # Log notification
        self._log_notification(notification)

        # In production, would send to external services
        # self._send_to_slack(notification)
        # self._send_to_email(notification)

        return HookResult.success_result(
            metadata={"notification_sent": True, "event": context.event},
        )

    def _create_notification(
        self,
        context: HookContext,
    ) -> Dict[str, Any]:
        """Create notification payload."""
        return {
            "event": context.event,
            "pipeline_id": context.pipeline_id,
            "phase": context.phase,
            "agent_id": context.agent_id,
            "timestamp": datetime.utcnow().isoformat(),
            "correlation_id": context.correlation_id,
            "metadata": context.metadata,
        }

    def _log_notification(self, notification: Dict[str, Any]) -> None:
        """Log notification to appropriate logger."""
        event = notification["event"]

        if "COMPLETE" in event:
            logger.info(
                f"Pipeline {notification['pipeline_id']} {event.lower()}",
                extra=notification,
            )
        elif "FAIL" in event or "CANCEL" in event:
            logger.warning(
                f"Pipeline {notification['pipeline_id']} {event.lower()}",
                extra=notification,
            )
        else:
            logger.info(
                f"Pipeline notification: {event}",
                extra=notification,
            )


class ChronicleHarvestHook(BaseHook):
    """
    Harvests important events to Chronicle.

    This hook captures significant pipeline events:
    - Phase transitions
    - Quality results
    - Decision points
    - Loop iterations

    The chronicle provides a complete audit trail
    for pipeline execution.
    """

    name = "chronicle_harvest"
    event = "*"
    priority = HookPriority.LOW
    blocking = False
    description = "Harvests events to chronicle"

    # Events to harvest
    HARVEST_EVENTS = [
        "PHASE_ENTER",
        "PHASE_EXIT",
        "QUALITY_RESULT",
        "DECISION_MAKE",
        "LOOP_START",
        "LOOP_END",
        "PIPELINE_COMPLETE",
        "PIPELINE_FAILED",
    ]

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute chronicle harvest.

        Args:
            context: Hook context

        Returns:
            HookResult with chronicle entry
        """
        if context.event not in self.HARVEST_EVENTS:
            return HookResult.success_result()

        # Create chronicle entry
        entry = self._create_chronicle_entry(context)

        # Store in metadata for pipeline to pick up
        chronicle_entries = context.metadata.setdefault("chronicle_entries", [])
        chronicle_entries.append(entry)

        logger.debug(
            f"Harvested event {context.event} to chronicle",
            extra={"pipeline_id": context.pipeline_id},
        )

        return HookResult.success_result(
            metadata={"chronicle_entry": entry},
        )

    def _create_chronicle_entry(
        self,
        context: HookContext,
    ) -> Dict[str, Any]:
        """Create chronicle entry from context."""
        return {
            "event": context.event,
            "pipeline_id": context.pipeline_id,
            "phase": context.phase,
            "loop_id": context.loop_id,
            "agent_id": context.agent_id,
            "data": context.data,
            "timestamp": datetime.utcnow().isoformat(),
            "correlation_id": context.correlation_id,
        }
