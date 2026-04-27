"""
ProjectOrchestrator — Core dispatch loop for objective-driven orchestration.

The orchestrator manages the full project lifecycle:
1. Load objectives from .gaia/objectives.yaml
2. Find next ready objective (deps met, QUEUED status)
3. Dispatch to PipelineEngine via OrchestratorPipelineAdapter
4. Evaluate result (rule-based by default, LLM opt-in)
5. Update objective status and artifacts
6. Save objectives atomically
7. Optional git commit with auto_commit=False default

Hook integration:
    The orchestrator has its OWN HookRegistry separate from PipelineEngine's.
    Orchestrator hooks fire at:
    - OBJECTIVE_START: Before dispatching an objective
    - OBJECTIVE_COMPLETE: After a successful objective execution
    - OBJECTIVE_FAILED: After a failed objective execution
    - PHASE_COMPLETE: After all objectives in a phase finish
    - CYCLE_COMPLETE: After a full dispatch-evaluate-update cycle

Git integration:
    - auto_commit defaults to False — requires explicit opt-in
    - git user.name and user.email read from `git config`
    - Falls back to "GAIA Orchestrator" / "gaia-orchestrator@local"
    - dry_run mode previews commits without executing them
"""

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.hooks.base import HookContext, HookPriority, HookResult
from gaia.hooks.registry import HookExecutor, HookRegistry
from gaia.state.nexus import NexusService
from gaia.utils.logging import get_logger

from gaia.orchestration.adapters import ExecutionResult, OrchestratorPipelineAdapter
from gaia.orchestration.models import (
    DependencyGraph,
    Objective,
    ObjectiveStatus,
    ProjectObjectives,
)
from gaia.orchestration.supervisor import (
    ObjectiveOutcome,
    ProjectSupervisor,
    SupervisorConfig,
    Verdict,
)
from gaia.orchestration.supervisors import GitSupervisor, SupervisorRegistry

logger = get_logger(__name__)

# Orchestrator-specific hook events (not PipelineEngine events)
OBJECTIVE_START = "OBJECTIVE_START"
OBJECTIVE_COMPLETE = "OBJECTIVE_COMPLETE"
OBJECTIVE_FAILED = "OBJECTIVE_FAILED"
PHASE_COMPLETE = "PHASE_COMPLETE"
CYCLE_COMPLETE = "CYCLE_COMPLETE"
ORCHESTRATOR_START = "ORCHESTRATOR_START"
ORCHESTRATOR_COMPLETE = "ORCHESTRATOR_COMPLETE"


@dataclass
class OrchestratorConfig:
    """
    Configuration for ProjectOrchestrator.

    Attributes:
        objectives_path: Path to objectives YAML file
        auto_commit: Whether to auto-commit after objective completion (default: False)
        dry_run: If True, preview actions without executing
        enable_evaluation: Whether to run post-execution evaluation (default: False)
        max_cycle_iterations: Maximum dispatch-evaluate cycles before stopping
        enable_nexus: Whether to integrate with NexusService
        enable_supervisor: Whether to enable ProjectSupervisor governance
        supervisor_config: Custom SupervisorConfig (defaults used if None)
        enable_git_supervisor: Whether to enable GitSupervisor with CircuitBreaker
    """

    objectives_path: str = ".gaia/objectives.yaml"
    auto_commit: bool = False  # CRITICAL: default to False
    dry_run: bool = False
    enable_evaluation: bool = False
    max_cycle_iterations: int = 100
    enable_nexus: bool = True
    enable_supervisor: bool = False
    supervisor_config: Optional[SupervisorConfig] = None
    enable_git_supervisor: bool = False  # GitSupervisor with CircuitBreaker


@dataclass
class OrchestratorState:
    """
    Runtime state of the orchestrator.

    Tracks paused status, cycle count, and execution history.
    """

    paused: bool = False
    cycle_count: int = 0
    objectives_processed: int = 0
    objectives_failed: int = 0
    execution_history: List[Dict[str, Any]] = field(default_factory=list)
    objective_branches: Dict[str, str] = field(default_factory=dict)

    def record_cycle(self, objective_id: str, success: bool) -> None:
        """Record a completed dispatch-evaluate cycle."""
        self.cycle_count += 1
        self.execution_history.append(
            {
                "cycle": self.cycle_count,
                "objective_id": objective_id,
                "success": success,
            }
        )
        if success:
            self.objectives_processed += 1
        else:
            self.objectives_failed += 1


class ProjectOrchestrator:
    """
    Core project orchestrator.

    Manages objective-driven project execution with:
    - Dependency-aware scheduling via DependencyGraph
    - PipelineEngine dispatch via OrchestratorPipelineAdapter
    - Hook integration on its own HookRegistry
    - NexusService integration for unified state tracking
    - Git integration with auto_commit=False default

    Example:
        >>> orchestrator = ProjectOrchestrator()
        >>> # Register custom hooks
        >>> orchestrator.hook_registry.register(MyHook())
        >>> # Run the dispatch loop
        >>> await orchestrator.run()
    """

    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
        pipeline_adapter: Optional[OrchestratorPipelineAdapter] = None,
    ) -> None:
        """
        Initialize the orchestrator.

        Args:
            config: Orchestrator configuration (defaults used if None)
            pipeline_adapter: Optional pre-configured adapter
        """
        self._config = config or OrchestratorConfig()
        self._adapter = pipeline_adapter or OrchestratorPipelineAdapter()
        self._state = OrchestratorState()

        # Orchestrator's OWN HookRegistry — separate from PipelineEngine
        self._hook_registry = HookRegistry()
        self._hook_executor = HookExecutor(self._hook_registry)

        # Dependency graph — rebuilt each cycle
        self._dep_graph = DependencyGraph()

        # Project objectives (loaded from YAML)
        self._project: Optional[ProjectObjectives] = None

        # Git config (lazily resolved)
        self._git_user_name: Optional[str] = None
        self._git_user_email: Optional[str] = None

        # NexusService
        self._nexus: Optional[NexusService] = None

        # ProjectSupervisor (optional governance layer)
        self._supervisor: Optional[ProjectSupervisor] = None
        if self._config.enable_supervisor:
            self._supervisor = ProjectSupervisor(
                config=self._config.supervisor_config
            )
            logger.info("ProjectSupervisor enabled")

        # SupervisorRegistry + GitSupervisor (optional git automation)
        self._supervisor_registry = SupervisorRegistry()
        self._git_supervisor: Optional[GitSupervisor] = None
        if self._config.enable_git_supervisor:
            self._git_supervisor = GitSupervisor(
                repo_path=Path(self._config.objectives_path).parent.parent,
                git_user_name=self.git_user_name,
                git_user_email=self.git_user_email,
            )
            self._supervisor_registry.register("git", self._git_supervisor)
            logger.info("GitSupervisor enabled and registered")

        logger.info(
            "ProjectOrchestrator initialized",
            extra={
                "auto_commit": self._config.auto_commit,
                "dry_run": self._config.dry_run,
                "objectives_path": self._config.objectives_path,
            },
        )

    @property
    def hook_registry(self) -> HookRegistry:
        """Access the orchestrator's HookRegistry for hook registration."""
        return self._hook_registry

    @property
    def config(self) -> OrchestratorConfig:
        """Get orchestrator configuration."""
        return self._config

    @property
    def state(self) -> OrchestratorState:
        """Get current orchestrator state."""
        return self._state

    @property
    def project(self) -> Optional[ProjectObjectives]:
        """Get the loaded project objectives."""
        return self._project

    @property
    def supervisor(self) -> Optional[ProjectSupervisor]:
        """Get the supervisor if enabled."""
        return self._supervisor

    @property
    def git_supervisor(self) -> Optional[GitSupervisor]:
        """Get the GitSupervisor if enabled."""
        return self._git_supervisor

    @property
    def supervisor_registry(self) -> SupervisorRegistry:
        """Get the SupervisorRegistry."""
        return self._supervisor_registry

    # -----------------------------------------------------------------------
    # Git integration
    # -----------------------------------------------------------------------

    def _get_git_config(self, key: str, fallback: str) -> str:
        """
        Read a git config value, falling back to a default.

        Args:
            key: Git config key (e.g., "user.name")
            fallback: Default value if not found

        Returns:
            Config value or fallback
        """
        try:
            result = subprocess.run(
                ["git", "config", key],
                capture_output=True,
                text=True,
                timeout=5,
            )
            value = result.stdout.strip()
            if value:
                return value
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        return fallback

    @property
    def git_user_name(self) -> str:
        """Get git user.name from config or fallback."""
        if self._git_user_name is None:
            self._git_user_name = self._get_git_config(
                "user.name", "GAIA Orchestrator"
            )
        return self._git_user_name

    @property
    def git_user_email(self) -> str:
        """Get git user.email from config or fallback."""
        if self._git_user_email is None:
            self._git_user_email = self._get_git_config(
                "user.email", "gaia-orchestrator@local"
            )
        return self._git_user_email

    # -----------------------------------------------------------------------
    # NexusService integration
    # -----------------------------------------------------------------------

    def _init_nexus(self) -> None:
        """Initialize NexusService singleton if enabled."""
        if self._config.enable_nexus and self._nexus is None:
            self._nexus = NexusService.get_instance()

    def _commit_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        phase: Optional[str] = None,
    ) -> None:
        """Commit an event to NexusService Chronicle."""
        if self._nexus:
            self._nexus.commit(
                agent_id="ProjectOrchestrator",
                event_type=event_type,
                payload=payload,
                phase=phase,
            )

    # -----------------------------------------------------------------------
    # Objective loading
    # -----------------------------------------------------------------------

    def load_objectives(self) -> ProjectObjectives:
        """
        Load objectives from YAML file.

        Returns:
            ProjectObjectives instance
        """
        self._project = ProjectObjectives.load(self._config.objectives_path)
        self._dep_graph = DependencyGraph(self._project.objectives)
        logger.info(
            f"Loaded {len(self._project.objectives)} objectives",
            extra={"project_id": self._project.project_id},
        )
        self._init_nexus()
        if self._nexus:
            self._commit_event(
                "objectives_loaded",
                {
                    "project_id": self._project.project_id,
                    "objective_count": len(self._project.objectives),
                },
            )
        return self._project

    # -----------------------------------------------------------------------
    # Core dispatch loop
    # -----------------------------------------------------------------------

    async def run(self) -> OrchestratorState:
        """
        Run the full dispatch-evaluate-update cycle loop.

        Loop:
        1. Check pause state
        2. Find next ready objective
        3. If none, check if project is done
        4. Dispatch to pipeline adapter
        5. Evaluate result
        6. Update objective status
        7. Save objectives atomically
        8. Optional git commit

        Returns:
            Final OrchestratorState
        """
        if self._project is None:
            self.load_objectives()

        # Emit ORCHESTRATOR_START event
        await self._hook_executor.execute_hooks(
            ORCHESTRATOR_START,
            HookContext(
                event=ORCHESTRATOR_START,
                pipeline_id=f"orchestrator-{self._project.project_id if self._project else 'unknown'}",
            ),
        )

        logger.info("Starting orchestrator dispatch loop")
        self._commit_event("orchestrator_started", {"path": self._config.objectives_path})

        while self._state.cycle_count < self._config.max_cycle_iterations:
            # Check pause
            if self._state.paused:
                logger.info("Orchestrator paused, waiting...")
                await self._wait_for_resume()
                continue

            # Find next ready objective
            ready = self._project.get_ready_objectives()
            if not ready:
                # Check if project is done or blocked
                remaining = [
                    o
                    for o in self._project.objectives
                    if o.status not in (ObjectiveStatus.COMPLETED, ObjectiveStatus.CANCELLED)
                ]
                if not remaining:
                    logger.info("All objectives completed or cancelled — project done")
                    break
                else:
                    # Some objectives are blocked or in progress
                    in_progress = [
                        o for o in remaining if o.status == ObjectiveStatus.IN_PROGRESS
                    ]
                    blocked = [
                        o for o in remaining if o.status == ObjectiveStatus.BLOCKED
                    ]
                    queued = [
                        o for o in remaining if o.status == ObjectiveStatus.QUEUED
                    ]
                    logger.info(
                        f"No ready objectives — {len(in_progress)} in_progress, "
                        f"{len(blocked)} blocked, {len(queued)} queued (deps unmet)"
                    )
                    if not in_progress and not queued:
                        logger.warning("Project is stuck — all remaining blocked")
                        break
                    await asyncio.sleep(1)
                    continue

            # Pick highest priority ready objective
            objective = ready[0]

            # Fire OBJECTIVE_START hook
            hook_context = self._build_hook_context(
                OBJECTIVE_START, objective
            )
            hook_result = await self._hook_executor.execute_hooks(
                OBJECTIVE_START, hook_context
            )
            if hook_result.halt_pipeline:
                logger.warning(
                    f"Hook halted execution at objective '{objective.title}'"
                )
                break

            # Store branch name from hook inject_context (set by GitBranchHook)
            if hook_result.inject_context:
                branch = hook_result.inject_context.get("_git_branch")
                if branch:
                    self._state.objective_branches[objective.objective_id] = branch

            # Dispatch
            result = await self._adapter.execute_with_result_update(objective)

            # Fire success/failure hook
            event = OBJECTIVE_COMPLETE if result.success else OBJECTIVE_FAILED
            hook_context = self._build_hook_context(event, objective)
            hook_context.data["execution_result"] = result
            await self._hook_executor.execute_hooks(event, hook_context)

            # Record cycle
            self._state.record_cycle(objective.objective_id, result.success)

            # Supervisor evaluation (if enabled)
            if self._supervisor is not None:
                try:
                    outcome = ObjectiveOutcome(
                        objective_id=objective.objective_id,
                        success=result.success,
                        quality_score=result.quality_score,
                        phase=objective.phase,
                        error_message=result.error_message,
                    )
                    verdict = self._supervisor.evaluate_cycle(
                        outcome=outcome,
                        project=self._project,
                        dep_graph=self._dep_graph,
                    )
                    if verdict == Verdict.ABORT:
                        logger.error(
                            f"Supervisor ABORT: {self._supervisor.state.aborted_reason}"
                        )
                        break
                    elif verdict == Verdict.PAUSE:
                        logger.warning(
                            f"Supervisor PAUSE: {self._supervisor.state.paused_reason}"
                        )
                        self._state.paused = True
                        await self._wait_for_resume()
                        continue
                    elif verdict == Verdict.REMEDIATE:
                        logger.warning(
                            f"Supervisor REMEDIATE: quality trend declining"
                        )
                except Exception as e:
                    logger.error(f"Supervisor evaluation failed: {e}")

            # Check for circular dependency issues
            cycles = self._dep_graph.detect_cycles()
            if cycles:
                logger.error(
                    f"Circular dependencies detected: {cycles}",
                    extra={"cycles": cycles},
                )
                break

            # Save objectives atomically
            if not self._config.dry_run:
                self._project.save_atomic(self._config.objectives_path)

            # Optional git commit
            if self._config.auto_commit and result.success and not self._config.dry_run:
                await self._git_commit(objective)

            # Emit cycle complete event
            await self._hook_executor.execute_hooks(
                CYCLE_COMPLETE,
                self._build_hook_context(CYCLE_COMPLETE, objective),
            )

            # Phase completion check (if supervisor is enabled)
            if self._supervisor is not None:
                completed_phases = set()
                for o in self._project.objectives:
                    if o.status in (
                        ObjectiveStatus.COMPLETED,
                        ObjectiveStatus.CANCELLED,
                    ):
                        completed_phases.add(o.phase)

                # Check each unique phase for completion
                unique_phases = list(
                    {o.phase for o in self._project.objectives if o.phase}
                )
                for phase in unique_phases:
                    if self._supervisor.check_phase_completion(
                        self._project, phase
                    ):
                        await self._hook_executor.execute_hooks(
                            PHASE_COMPLETE,
                            HookContext(
                                event=PHASE_COMPLETE,
                                pipeline_id=f"orchestrator-{self._project.project_id}",
                                phase=phase,
                            ),
                        )

            self._commit_event(
                "cycle_complete",
                {
                    "objective_id": objective.objective_id,
                    "success": result.success,
                    "cycle_count": self._state.cycle_count,
                },
            )

        # Emit ORCHESTRATOR_COMPLETE event
        await self._hook_executor.execute_hooks(
            ORCHESTRATOR_COMPLETE,
            HookContext(
                event=ORCHESTRATOR_COMPLETE,
                pipeline_id=f"orchestrator-{self._project.project_id if self._project else 'unknown'}",
            ),
        )

        logger.info(
            "Orchestrator dispatch loop finished",
            extra={
                "cycles": self._state.cycle_count,
                "processed": self._state.objectives_processed,
                "failed": self._state.objectives_failed,
            },
        )
        self._commit_event(
            "orchestrator_finished",
            {
                "cycles": self._state.cycle_count,
                "processed": self._state.objectives_processed,
                "failed": self._state.objectives_failed,
            },
        )
        return self._state

    async def _wait_for_resume(self) -> None:
        """Wait until orchestrator is resumed."""
        while self._state.paused:
            await asyncio.sleep(1)

    # -----------------------------------------------------------------------
    # Evaluation
    # -----------------------------------------------------------------------

    def evaluate(
        self,
        result: ExecutionResult,
        objective: Objective,
    ) -> Dict[str, Any]:
        """
        Rule-based evaluation of execution result.

        By default, uses simple rules:
        - success=True AND quality_score >= 0.90 -> PASS
        - success=True but quality < 0.90 -> REVIEW
        - success=False -> FAIL

        LLM-based evaluation is opt-in only via config.enable_evaluation
        with Qwen3.5-35B-A3B-GGUF.

        Args:
            result: Execution result from the pipeline
            objective: The objective that was executed

        Returns:
            Evaluation dictionary with verdict and details
        """
        if not result.success:
            return {
                "verdict": "FAIL",
                "reason": result.error_message or "Pipeline execution failed",
                "objective_id": objective.objective_id,
            }

        score = result.quality_score
        if score is None:
            return {
                "verdict": "PASS",
                "reason": "Pipeline succeeded without quality score",
                "objective_id": objective.objective_id,
            }

        if score >= 0.90:
            return {
                "verdict": "PASS",
                "quality_score": score,
                "objective_id": objective.objective_id,
            }
        else:
            return {
                "verdict": "REVIEW",
                "quality_score": score,
                "reason": f"Quality score {score:.2f} below threshold 0.90",
                "objective_id": objective.objective_id,
            }

    # -----------------------------------------------------------------------
    # Pause / Resume
    # -----------------------------------------------------------------------

    def pause(self, reason: str = "") -> None:
        """Pause the orchestrator."""
        self._state.paused = True
        logger.info(f"Orchestrator paused: {reason}")
        self._commit_event("orchestrator_paused", {"reason": reason})

    def resume(self) -> None:
        """Resume the orchestrator."""
        self._state.paused = False
        logger.info("Orchestrator resumed")
        self._commit_event("orchestrator_resumed", {})

    # -----------------------------------------------------------------------
    # Git operations
    # -----------------------------------------------------------------------

    async def _git_commit(self, objective: Objective) -> None:
        """
        Create a git commit for a completed objective.

        Respects auto_commit=False and dry_run mode.
        Uses git config for user name/email with fallback.
        """
        if self._config.dry_run:
            logger.info(
                f"[DRY RUN] Would commit objective '{objective.title}'",
                extra={"objective_id": objective.objective_id},
            )
            return

        try:
            # Stage objectives file
            subprocess.run(
                ["git", "add", self._config.objectives_path],
                capture_output=True,
                text=True,
                timeout=10,
            )

            # Create commit with orchestrator identity
            commit_msg = (
                f"chore(orchestrator): complete objective '{objective.title}'"
            )
            subprocess.run(
                [
                    "git", "commit",
                    "-m", commit_msg,
                    "--author",
                    f"{self.git_user_name} <{self.git_user_email}>",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            logger.info(
                f"Committed: {commit_msg}",
                extra={"objective_id": objective.objective_id},
            )
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.warning(f"Git commit failed: {e}")

    # -----------------------------------------------------------------------
    # Slug utility
    # -----------------------------------------------------------------------

    @staticmethod
    def _build_objective_slug(title: str) -> str:
        """
        Convert an objective title to a URL-safe slug.

        Args:
            title: The objective title to slugify.

        Returns:
            Lowercase slug with hyphens, max 50 chars.
        """
        import re

        slug = re.sub(r'[^a-z0-9\s-]', '', title.lower().strip())
        slug = re.sub(r'\s+', '-', slug)
        slug = slug.strip('-')
        return slug[:50]

    # -----------------------------------------------------------------------
    # Hook context building
    # -----------------------------------------------------------------------

    def _build_hook_context(
        self, event: str, objective: Objective
    ) -> HookContext:
        """Build a HookContext for orchestrator events."""
        return HookContext(
            event=event,
            pipeline_id=f"orchestrator-{self._project.project_id if self._project else 'unknown'}",
            phase=objective.phase,
            state={
                "objective_id": objective.objective_id,
                "objective_status": objective.status.value,
                "cycle_count": self._state.cycle_count,
            },
            data={
                "objective_id": objective.objective_id,
                "objective_title": objective.title,
                "objective_description": objective.description,
                "git_supervisor": self._git_supervisor,
                "_git_branch": self._state.objective_branches.get(objective.objective_id),
            },
        )
