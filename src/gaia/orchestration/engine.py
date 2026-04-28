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
    ConflictReport,
    DependencyGraph,
    LevelResult,
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
    enable_parallel_execution: bool = False
    max_parallel_objectives: int = 10
    serialize_hooks: bool = True
    enable_rollback: bool = True


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

        # Concurrency control for parallel execution
        self._hook_lock = asyncio.Lock()
        self._git_op_lock = asyncio.Lock()

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

        Branches to sequential or parallel mode based on config flag.

        Returns:
            Final OrchestratorState
        """
        if self._config.enable_parallel_execution:
            return await self._run_parallel_mode()
        else:
            return await self._run_sequential_mode()

    async def _run_sequential_mode(self) -> OrchestratorState:
        """
        Sequential dispatch-evaluate-update cycle loop.

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
    def _apply_status_transition(
        objective: Objective, target: ObjectiveStatus
    ) -> None:
        """
        Safely transition an objective to a terminal state.

        Uses the required intermediate IN_PROGRESS step to satisfy
        the status transition rules. Handles objectives already in
        terminal states.
        """
        try:
            if objective.status == ObjectiveStatus.QUEUED:
                objective.transition_to(ObjectiveStatus.IN_PROGRESS)
            objective.transition_to(target)
        except ValueError as e:
            logger.debug(
                f"Status transition skipped for objective "
                f"'{objective.objective_id}' -> {target.value}: {e}"
            )

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

    # -----------------------------------------------------------------------
    # Parallel execution engine
    # -----------------------------------------------------------------------

    async def _run_parallel_mode(self) -> OrchestratorState:
        """
        Parallel execution mode for the orchestrator.

        1. Load objectives, build DependencyGraph
        2. levels = dep_graph.partition_into_levels()
        3. For each level: run _run_level_parallel()
        4. If verdict == ABORT: break
        5. Emit ORCHESTRATOR_COMPLETE
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

        logger.info("Starting orchestrator dispatch loop (parallel mode)")
        self._commit_event("orchestrator_started", {"path": self._config.objectives_path})

        # Clean up stale worktrees from previous runs
        await self._cleanup_all_stale_worktrees()

        levels = self._dep_graph.partition_into_levels()

        for level_number, level_objective_ids in enumerate(levels):
            level_result = await self._run_level_parallel(
                level_objective_ids, level_number
            )

            # Cleanup worktrees for completed objectives in this level
            for obj_id, outcome in level_result.outcomes.items():
                branch = self._state.objective_branches.get(obj_id)
                if branch:
                    await self._cleanup_worktree(branch, obj_id)

            # Propagate failures to dependent objectives in remaining levels
            failed_ids = {
                oid for oid, outcome in level_result.outcomes.items()
                if not outcome.success
            }
            remaining = levels[level_number + 1:]
            self._propagate_failures_to_dependents(
                failed_ids, remaining, self._dep_graph,
            )

            # Supervisor-level evaluation
            if self._supervisor is not None:
                try:
                    outcomes_list = list(level_result.outcomes.values())
                    verdict_str = self._supervisor.evaluate_level(
                        outcomes=outcomes_list,
                        project=self._project,
                        dep_graph=self._dep_graph,
                        conflicts=level_result.conflicts,
                    )
                    level_result.verdict = verdict_str
                    if verdict_str == Verdict.ABORT.value:
                        logger.error(
                            f"Supervisor ABORT at level {level_number}: "
                            f"{self._supervisor.state.aborted_reason}"
                        )
                        # Rollback failed objectives before breaking
                        if self._config.enable_rollback:
                            failed_in_level = {
                                oid for oid, outcome in level_result.outcomes.items()
                                if not outcome.success
                            }
                            if failed_in_level:
                                rolled_back = await self._rollback_failed_objectives(
                                    failed_in_level, level_number
                                )
                                logger.info(
                                    f"Rolled back {rolled_back} failed objectives "
                                    f"at level {level_number}"
                                )
                        break
                    elif verdict_str == Verdict.PAUSE.value:
                        logger.warning(
                            f"Supervisor PAUSE at level {level_number}"
                        )
                        self._state.paused = True
                        # Rollback failed objectives on pause
                        if self._config.enable_rollback:
                            failed_in_level = {
                                oid for oid, outcome in level_result.outcomes.items()
                                if not outcome.success
                            }
                            if failed_in_level:
                                rolled_back = await self._rollback_failed_objectives(
                                    failed_in_level, level_number
                                )
                                logger.info(
                                    f"Rolled back {rolled_back} failed objectives "
                                    f"at level {level_number}"
                                )
                        await self._wait_for_resume()
                except Exception as e:
                    logger.error(f"Supervisor level evaluation failed: {e}")

            # Rollback on non-supervisor abort verdicts
            # (e.g., all objectives failed in a level). Only fires when
            # no supervisor is enabled — supervisor ABORT/PAUSE paths
            # above already handle rollback before break/continue.
            # Use .lower() for case-insensitive match: _run_level_parallel
            # emits "ABORT" (uppercase) while supervisor emits "abort".
            if (
                self._supervisor is None
                and level_result.verdict.lower() == "abort"
                and self._config.enable_rollback
            ):
                failed_in_level = {
                    oid for oid, outcome in level_result.outcomes.items()
                    if not outcome.success
                }
                if failed_in_level:
                    rolled_back = await self._rollback_failed_objectives(
                        failed_in_level, level_number
                    )
                    logger.info(
                        f"Rolled back {rolled_back} failed objectives "
                        f"at level {level_number}"
                    )

            if level_result.verdict.lower() == "abort":
                break

            # Batch save after each level
            if not self._config.dry_run:
                try:
                    self._project.save_atomic(self._config.objectives_path)
                except Exception as e:
                    logger.error(f"Failed to save objectives: {e}")

            await self._hook_executor.execute_hooks(
                CYCLE_COMPLETE,
                HookContext(
                    event=CYCLE_COMPLETE,
                    pipeline_id=f"orchestrator-{self._project.project_id}",
                ),
            )

            if self._state.cycle_count >= self._config.max_cycle_iterations:
                break

        # Emit ORCHESTRATOR_COMPLETE event
        await self._hook_executor.execute_hooks(
            ORCHESTRATOR_COMPLETE,
            HookContext(
                event=ORCHESTRATOR_COMPLETE,
                pipeline_id=f"orchestrator-{self._project.project_id if self._project else 'unknown'}",
            ),
        )

        logger.info(
            "Orchestrator dispatch loop finished (parallel mode)",
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

    async def _run_level_parallel(
        self,
        level_objectives: list,
        level_number: int,
    ) -> LevelResult:
        """
        Execute a single dependency level in parallel.

        Flow:
        1. Fire OBJECTIVE_START hooks for all objectives (serialized via lock)
        2. Launch executions via asyncio.gather() using execute_without_status_update()
        3. Batch-apply status transitions for each (objective, result) pair
        4. Fire OBJECTIVE_COMPLETE / OBJECTIVE_FAILED hooks (serialized via lock)
        5. Build LevelResult
        6. save_atomic() once
        """
        outcomes: dict = {}
        conflicts: list = []
        success_count = 0
        failure_count = 0

        # Resolve objective objects
        objectives_map: dict = {}
        for obj_id in level_objectives:
            obj = self._project.get_objective(obj_id)
            if obj is not None:
                objectives_map[obj_id] = obj

        # Create worktrees for objectives that don't have one yet
        for obj_id, obj in objectives_map.items():
            if obj_id not in self._state.objective_branches:
                branch = await self._create_worktree_for_objective(obj)
                if branch:
                    logger.debug(
                        f"Created worktree branch for objective '{obj_id}': {branch}"
                    )

        # Step 1: Fire OBJECTIVE_START hooks (serialized)
        for obj_id, obj in objectives_map.items():
            if self._config.serialize_hooks:
                async with self._hook_lock:
                    hook_context = self._build_hook_context(OBJECTIVE_START, obj)
                    hook_result = await self._hook_executor.execute_hooks(
                        OBJECTIVE_START, hook_context
                    )
            else:
                hook_context = self._build_hook_context(OBJECTIVE_START, obj)
                hook_result = await self._hook_executor.execute_hooks(
                    OBJECTIVE_START, hook_context
                )

            if hook_result.halt_pipeline:
                logger.warning(
                    f"Hook halted execution at objective '{obj.title}'"
                )
                outcomes[obj_id] = ObjectiveOutcome(
                    objective_id=obj_id,
                    success=False,
                    phase=obj.phase,
                    error_message="Halted by hook",
                )
                self._apply_status_transition(obj, ObjectiveStatus.BLOCKED)
                failure_count += 1

        # Step 2: Launch executions in parallel (bounded by max_parallel_objectives)
        halted_ids = set(outcomes.keys())
        tasks = {}
        for obj_id, obj in objectives_map.items():
            if obj_id in halted_ids:
                continue
            tasks[obj_id] = self._adapter.execute_without_status_update(obj)

        semaphore = asyncio.Semaphore(self._config.max_parallel_objectives)

        async def _bounded(task):
            async with semaphore:
                return await task

        bounded_tasks = [_bounded(t) for t in tasks.values()]
        results = await asyncio.gather(*bounded_tasks, return_exceptions=True)

        # Step 3: Batch-apply status transitions
        failed_ids: set[str] = set()
        for (obj_id, task), result in zip(tasks.items(), results):
            obj = objectives_map[obj_id]
            if isinstance(result, Exception):
                # Execution raised an exception
                self._apply_status_transition(obj, ObjectiveStatus.BLOCKED)
                obj.error_message = str(result)
                outcomes[obj_id] = ObjectiveOutcome(
                    objective_id=obj_id,
                    success=False,
                    phase=obj.phase,
                    error_message=str(result),
                )
                failed_ids.add(obj_id)
                failure_count += 1
                continue

            result_dict = result
            if result_dict["success"]:
                self._apply_status_transition(obj, ObjectiveStatus.COMPLETED)
                for artifact in result_dict.get("artifacts", []):
                    obj.add_artifact(artifact)
                outcomes[obj_id] = ObjectiveOutcome(
                    objective_id=obj_id,
                    success=True,
                    phase=obj.phase,
                )
                success_count += 1
            else:
                self._apply_status_transition(obj, ObjectiveStatus.BLOCKED)
                obj.error_message = result_dict.get("error")
                outcomes[obj_id] = ObjectiveOutcome(
                    objective_id=obj_id,
                    success=False,
                    phase=obj.phase,
                    error_message=result_dict.get("error"),
                )
                failed_ids.add(obj_id)
                failure_count += 1

        # Detect conflicts among successfully completed objectives
        completed_ids = [
            oid for oid, outcome in outcomes.items() if outcome.success
        ]
        if completed_ids and self._git_supervisor:
            branch_map = {
                oid: self._state.objective_branches.get(oid, "")
                for oid in completed_ids
            }
            branch_map = {k: v for k, v in branch_map.items() if v}
            conflicts = await self._detect_level_conflicts(completed_ids, branch_map)

        # Step 4: Fire completion hooks (serialized)
        for obj_id, outcome in outcomes.items():
            if obj_id not in objectives_map:
                continue
            obj = objectives_map[obj_id]
            event = OBJECTIVE_COMPLETE if outcome.success else OBJECTIVE_FAILED
            if self._config.serialize_hooks:
                async with self._hook_lock:
                    hook_context = self._build_hook_context(event, obj)
                    hook_context.data["execution_result"] = outcome
                    await self._hook_executor.execute_hooks(event, hook_context)
            else:
                hook_context = self._build_hook_context(event, obj)
                hook_context.data["execution_result"] = outcome
                await self._hook_executor.execute_hooks(event, hook_context)

            self._state.record_cycle(obj_id, outcome.success)

        # Step 5: Build LevelResult
        verdict = "CONTINUE"
        if failure_count == len(level_objectives):
            verdict = "ABORT"

        return LevelResult(
            level_number=level_number,
            objective_ids=level_objectives,
            outcomes=outcomes,
            conflicts=conflicts,
            success_count=success_count,
            failure_count=failure_count,
            verdict=verdict,
        )

    async def _detect_level_conflicts(
        self,
        completed_objective_ids: list[str],
        branch_map: dict[str, str],
    ) -> list:
        """
        Detect file-level conflicts within a completed level.

        For each pair of objectives that completed successfully:
        1. Call GitSupervisor.detect_changed_files(branch, base_branch)
        2. Compute set intersection of changed files
        3. If intersection non-empty, create ConflictReport

        Args:
            completed_objective_ids: IDs of objectives that completed successfully.
            branch_map: Mapping of objective_id -> branch_name.

        Returns:
            List of ConflictReport instances. Empty list if GitSupervisor
            is not enabled or no conflicts detected.
        """
        if self._git_supervisor is None:
            return []

        conflicts: list = []

        # Collect (objective_id, changed_files_set) for each completed objective
        objective_file_sets: list[tuple[str, set[str]]] = []

        for obj_id in completed_objective_ids:
            branch = branch_map.get(obj_id, "")
            if not branch:
                logger.debug(
                    f"No branch mapped for objective {obj_id}, skipping conflict check"
                )
                continue

            async with self._git_op_lock:
                changed_files = self._git_supervisor.detect_changed_files(
                    branch, "main"
                )

            if changed_files:
                objective_file_sets.append((obj_id, set(changed_files)))
                logger.debug(
                    f"Objective {obj_id} (branch {branch}) changed files: {changed_files}"
                )

        # Pairwise intersection of file sets
        for i in range(len(objective_file_sets)):
            for j in range(i + 1, len(objective_file_sets)):
                obj_id_a, files_a = objective_file_sets[i]
                obj_id_b, files_b = objective_file_sets[j]

                overlap = files_a & files_b
                if overlap:
                    report = ConflictReport(
                        conflicting_objective_ids=[obj_id_a, obj_id_b],
                        affected_files=overlap,
                    )
                    conflicts.append(report)
                    logger.warning(
                        f"Conflict detected between objectives {obj_id_a} and "
                        f"{obj_id_b}: {overlap}"
                    )

        return conflicts

    # -----------------------------------------------------------------------
    # Worktree lifecycle
    # -----------------------------------------------------------------------

    def _get_repo_root(self) -> Optional[Path]:
        """
        Determine the git repo root from the objectives path.

        Returns:
            Path to the repo root, or None if not determinable.
        """
        base_dir = Path(self._config.objectives_path).parent
        if base_dir.name == ".gaia":
            return base_dir.parent
        return None

    async def _create_worktree_for_objective(
        self,
        objective: Objective,
    ) -> Optional[str]:
        """
        Create a git worktree for an objective.

        - Creates branch: obj/{id}-{slug}
        - Creates worktree at: .gaia/worktrees/{objective_id}/
        - Returns branch name or None on failure

        Args:
            objective: The objective to create a worktree for.

        Returns:
            Branch name on success, None on failure.
        """
        objective_id = objective.objective_id
        slug = self._build_objective_slug(objective.title)
        branch_name = f"obj/{objective_id}-{slug}"

        # Worktree path relative to the objectives file parent (.gaia/)
        base_dir = Path(self._config.objectives_path).parent
        worktree_path = base_dir / "worktrees" / objective_id

        repo_root = self._get_repo_root()
        if repo_root is None:
            logger.warning(
                f"Cannot determine repo root for worktree creation: {objective_id}"
            )
            return None

        try:
            async with self._git_op_lock:
                # Create branch and worktree in one command (-b creates new branch)
                result = subprocess.run(
                    ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(repo_root),
                )
                if result.returncode != 0:
                    # Worktree might already exist from a stale registration
                    if "already exists" in result.stderr.lower():
                        # Check if the actual directory exists
                        if worktree_path.exists():
                            logger.info(
                                f"Worktree for objective '{objective_id}' already exists, reusing"
                            )
                        else:
                            # Stale registration — prune and retry
                            subprocess.run(
                                ["git", "worktree", "prune"],
                                capture_output=True,
                                text=True,
                                timeout=10,
                                cwd=str(repo_root),
                            )
                            # Force remove stale registration
                            subprocess.run(
                                ["git", "worktree", "remove", "--force", str(worktree_path)],
                                capture_output=True,
                                text=True,
                                timeout=10,
                                cwd=str(repo_root),
                            )
                            # Retry creation
                            result = subprocess.run(
                                ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
                                capture_output=True,
                                text=True,
                                timeout=30,
                                cwd=str(repo_root),
                            )
                            if result.returncode != 0:
                                logger.warning(
                                    f"Failed to create worktree for objective '{objective_id}': "
                                    f"{result.stderr.strip()}"
                                )
                                return None
                    else:
                        logger.warning(
                            f"Failed to create worktree for objective '{objective_id}': "
                            f"{result.stderr.strip()}"
                        )
                        return None

                self._state.objective_branches[objective_id] = branch_name
                logger.info(
                    f"Created worktree for objective '{objective_id}' "
                    f"(branch: {branch_name}, path: {worktree_path})"
                )
                return branch_name

        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.warning(f"Worktree creation failed for '{objective_id}': {e}")
            return None

    async def _cleanup_worktree(
        self,
        branch_name: str,
        objective_id: str,
    ) -> bool:
        """
        Delete the worktree directory for a completed objective.

        - Uses `git worktree remove <path>` to unregister
        - Branch is retained for audit/history
        - Serialized via _git_op_lock

        Args:
            branch_name: The branch associated with the worktree.
            objective_id: The objective ID for logging.

        Returns:
            True if cleanup succeeded, False otherwise.
        """
        base_dir = Path(self._config.objectives_path).parent
        worktree_path = base_dir / "worktrees" / objective_id

        repo_root = self._get_repo_root()
        if repo_root is None:
            logger.warning(
                f"Cannot determine repo root for worktree cleanup: {objective_id}"
            )
            return False

        try:
            async with self._git_op_lock:
                result = subprocess.run(
                    ["git", "worktree", "remove", str(worktree_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(repo_root),
                )
                if result.returncode != 0:
                    # Try with --force as fallback
                    result = subprocess.run(
                        ["git", "worktree", "remove", "--force", str(worktree_path)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=str(repo_root),
                    )
                    if result.returncode != 0:
                        logger.warning(
                            f"Failed to cleanup worktree for objective '{objective_id}': "
                            f"{result.stderr.strip()}"
                        )
                        return False

                logger.info(
                    f"Cleaned up worktree for objective '{objective_id}' "
                    f"(branch {branch_name} retained)"
                )
                return True

        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.warning(f"Worktree cleanup failed for '{objective_id}': {e}")
            return False

    async def _cleanup_all_stale_worktrees(self) -> None:
        """
        Clean up leftover worktrees from previous runs.

        Called at start of _run_parallel_mode().
        Lists all worktrees, removes those matching `obj/` prefix
        within our .gaia/worktrees directory. Also deletes associated
        branches to prevent conflicts on re-run.
        """
        base_dir = Path(self._config.objectives_path).parent
        worktrees_dir = base_dir / "worktrees"

        repo_root = self._get_repo_root()
        if repo_root is None:
            logger.warning("Cannot determine repo root for stale worktree cleanup")
            return

        try:
            async with self._git_op_lock:
                # List all worktrees in porcelain format
                result = subprocess.run(
                    ["git", "worktree", "list", "--porcelain"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(repo_root),
                )
                if result.returncode != 0:
                    logger.warning(
                        f"Failed to list worktrees: {result.stderr.strip()}"
                    )
                    return

                # Normalize worktrees_dir path for comparison
                worktrees_dir_str = str(worktrees_dir).replace("\\", "/")

                # Parse porcelain output to find worktrees with obj/ branches
                # that are within our .gaia/worktrees directory
                current_branch = None
                current_path = None
                worktrees_to_remove: list[tuple[str, str]] = []

                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("worktree "):
                        # Save previous entry if it matched
                        current_path_normalized = current_path.replace("\\", "/") if current_path else ""
                        if (current_path and current_branch
                                and current_branch.startswith("obj/")
                                and worktrees_dir_str in current_path_normalized):
                            worktrees_to_remove.append((current_path, current_branch))
                        current_path = line[len("worktree "):]
                        current_branch = None
                    elif line.startswith("branch "):
                        # branch refs/heads/obj/something -> obj/something
                        branch_ref = line[len("branch "):]
                        if "refs/heads/" in branch_ref:
                            current_branch = branch_ref.split("refs/heads/")[-1]
                        else:
                            current_branch = branch_ref

                # Don't forget the last entry
                current_path_normalized = current_path.replace("\\", "/") if current_path else ""
                if (current_path and current_branch
                        and current_branch.startswith("obj/")
                        and worktrees_dir_str in current_path_normalized):
                    worktrees_to_remove.append((current_path, current_branch))

                # Remove each stale worktree and its branch
                for wt_path, wt_branch in worktrees_to_remove:
                    try:
                        remove_result = subprocess.run(
                            ["git", "worktree", "remove", wt_path],
                            capture_output=True,
                            text=True,
                            timeout=30,
                            cwd=str(repo_root),
                        )
                        if remove_result.returncode != 0:
                            # Try with --force as fallback
                            remove_result = subprocess.run(
                                ["git", "worktree", "remove", "--force", wt_path],
                                capture_output=True,
                                text=True,
                                timeout=30,
                                cwd=str(repo_root),
                            )
                        if remove_result.returncode == 0:
                            logger.info(
                                f"Cleaned up stale worktree: {wt_path} (branch: {wt_branch})"
                            )
                        # Delete the branch to prevent conflicts on re-run
                        subprocess.run(
                            ["git", "branch", "-D", wt_branch],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            cwd=str(repo_root),
                        )
                    except (subprocess.SubprocessError, FileNotFoundError) as e:
                        logger.warning(f"Error removing stale worktree {wt_path}: {e}")

                # Also find and delete obj/ branches that have no associated worktree
                # (leftover from previous runs where worktree dir was deleted)
                branch_result = subprocess.run(
                    ["git", "branch", "--list", "obj/*"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=str(repo_root),
                )
                if branch_result.returncode == 0:
                    for line in branch_result.stdout.splitlines():
                        branch_name = line.strip().lstrip("* ")
                        if branch_name:
                            subprocess.run(
                                ["git", "branch", "-D", branch_name],
                                capture_output=True,
                                text=True,
                                timeout=10,
                                cwd=str(repo_root),
                            )
                            logger.debug(f"Deleted stale branch: {branch_name}")

        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.warning(f"Failed to list worktrees for stale cleanup: {e}")

    # -----------------------------------------------------------------------
    # Rollback for failed objectives
    # -----------------------------------------------------------------------

    async def _rollback_failed_objectives(
        self,
        failed_ids: set[str],
        level_number: int,
    ) -> int:
        """
        Rollback failed objectives within a level.

        Order:
        1. Fire OBJECTIVE_FAILED hook if not already fired
        2. Use GitSupervisor.rollback() if available (git reset --hard on branch)
        3. Remove worktree if worktrees enabled
        4. Update objectives.yaml (handled by caller's save_atomic)

        Only runs when:
        - enable_rollback is True
        - enable_parallel_execution is True

        Args:
            failed_ids: Set of objective_ids that failed
            level_number: Current level number for logging

        Returns:
            Number of objectives rolled back
        """
        if not self._config.enable_rollback:
            return 0

        rolled_back = 0

        for obj_id in failed_ids:
            branch = self._state.objective_branches.get(obj_id)

            if branch is None:
                logger.debug(
                    f"No branch mapped for failed objective {obj_id}, "
                    f"skipping rollback"
                )
                continue

            # GitSupervisor rollback
            if self._git_supervisor is not None:
                try:
                    async with self._git_op_lock:
                        success = self._git_supervisor.rollback(branch)
                    if success:
                        logger.info(
                            f"Rolled back objective '{obj_id}' "
                            f"(branch: {branch}) at level {level_number}"
                        )
                        rolled_back += 1
                    else:
                        logger.warning(
                            f"GitSupervisor.rollback() failed for objective "
                            f"'{obj_id}' on branch '{branch}'"
                        )
                except Exception as e:
                    logger.error(
                        f"Rollback error for objective '{obj_id}' "
                        f"on branch '{branch}': {e}"
                    )
            else:
                # No GitSupervisor — log and skip gracefully
                logger.debug(
                    f"GitSupervisor not enabled, skipping git rollback "
                    f"for objective '{obj_id}'"
                )

        return rolled_back

    def _propagate_failures_to_dependents(
        self,
        failed_ids: set[str],
        remaining_levels: list,
        dep_graph,
    ) -> None:
        """
        Mark objectives as BLOCKED if any dependency failed.

        Args:
            failed_ids: Set of objective_ids that failed
            remaining_levels: List of levels not yet executed
            dep_graph: The dependency graph
        """
        for level in remaining_levels:
            for obj_id in level:
                obj = self._project.get_objective(obj_id)
                if obj is None:
                    continue
                if obj.status != ObjectiveStatus.QUEUED:
                    continue
                # Check if any dependency is in failed_ids
                deps = dep_graph.get_dependencies(obj_id)
                if deps & failed_ids:
                    try:
                        obj.transition_to(ObjectiveStatus.BLOCKED)
                        obj.error_message = (
                            f"Dependency failed: {deps & failed_ids}"
                        )
                    except ValueError:
                        pass  # Already in a terminal state
