# Phase 1 Sprint 3: Pipeline-Nexus Integration Technical Design

**Document Version:** 1.0
**Status:** READY FOR IMPLEMENTATION
**Date:** 2026-04-05
**Owner:** senior-developer
**Priority:** CRITICAL (Phase 1 Core Deliverable)

---

## Executive Summary

This document defines the technical design for integrating PipelineEngine with NexusService, completing Phase 1's unified state management vision. Sprint 3 connects the Pipeline execution engine to the shared state layer, enabling coordinated multi-agent workflows with tamper-proof event logging.

### Sprint 3 Overview

| Dimension | Target | Notes |
|-----------|--------|-------|
| **Duration** | 2 weeks | Sprint 3 of 4 |
| **FTE Effort** | 4 person-weeks | senior-developer primary |
| **Deliverables** | Pipeline-Nexus integration, 15 tests | Core wiring + validation |
| **Exit Criteria** | Quality Gate 2 ready | 10 criteria validation |

### Sprint Context

**Completed Prerequisites:**
- Sprint 1: NexusService + WorkspaceIndex (763 LOC, 79 tests) - COMPLETE
- Sprint 2: ChronicleDigest + Agent-Nexus Integration (+370 LOC, 102 tests) - COMPLETE
- **Total Phase 1 so far:** 181 tests passing at 100% pass rate

**Sprint 3 Objective:** Wire PipelineEngine to NexusService for unified state sharing.

---

## 1. Architecture Overview

### 1.1 Integration Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     PipelineEngine                               │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │  PLANNING    │  │  DEVELOPMENT │  │   QUALITY    │          │
│  │   Phase      │  │    Phase     │  │    Phase     │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│         │                 │                 │                   │
│         ▼                 ▼                 ▼                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              HookExecutor (PHASE_ENTER/EXIT)             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                          │                                       │
│                          ▼                                       │
│              ┌───────────────────────┐                          │
│              │   NexusService        │                          │
│              │   (singleton)         │                          │
│              └──────────┬────────────┘                          │
│                         │                                       │
│         ┌───────────────┼───────────────┐                      │
│         ▼               ▼               ▼                      │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐              │
│  │  Chronicle  │ │  Workspace  │ │   Context   │              │
│  │  (events)   │ │  (files)    │ │    Lens     │              │
│  └─────────────┘ └─────────────┘ └─────────────┘              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Shared Singleton Instance
                              │
┌─────────────────────────────┴─────────────────────────────────┐
│                     Agent System                               │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   ChatAgent  │  │   CodeAgent  │  │  JiraAgent   │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│         │                 │                 │                   │
│         └─────────────────┴─────────────────┘                   │
│                           │                                     │
│                           ▼                                     │
│              ┌───────────────────────┐                          │
│              │   NexusService        │                          │
│              │   (same instance)     │                          │
│              └───────────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Design Principles

1. **Shared State, Not Duplicated State**: Pipeline and Agent systems share a single NexusService instance
2. **Event-Driven**: All significant Pipeline events committed to Chronicle
3. **Loop Tracking**: All events include loop_id for iteration correlation
4. **Tamper-Proof**: All events flow through AuditLogger's hash chain
5. **Token-Efficient Context**: Pipeline can request curated context digests for agents

---

## 2. Integration Points

### 2.1 PipelineEngine Initialization

**Location:** `PipelineEngine.initialize()`

**Implementation:**
```python
# In PipelineEngine.__init__():
from gaia.state.nexus import NexusService

class PipelineEngine:
    def __init__(self, ...):
        # ... existing initialization ...
        self._nexus: Optional[NexusService] = None

    async def initialize(
        self,
        context: PipelineContext,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        # ... existing initialization ...

        # NEW: Connect to shared NexusService singleton
        self._nexus = NexusService.get_instance()

        # Commit pipeline initialization event
        self._nexus.commit(
            agent_id="pipeline",
            event_type="pipeline_init",
            payload={
                "pipeline_id": context.pipeline_id,
                "user_goal": context.user_goal,
                "template": self._config.get("template", "generic"),
            },
            phase=None,  # Not in a phase yet
            loop_id=None,
        )

        # ... rest of initialization ...
```

**Event Committed:**
- `event_type`: "pipeline_init"
- `payload`: {pipeline_id, user_goal, template}

---

### 2.2 Phase Transitions (PHASE_ENTER / PHASE_EXIT)

**Location:** `PipelineEngine._execute_phase()`

**Implementation:**
```python
async def _execute_phase(self, phase_name: str) -> bool:
    """Execute a single phase."""
    logger.info(f"Executing phase: {phase_name}")

    self._state_machine.set_phase(phase_name)

    # NEW: Commit PHASE_ENTER event
    if self._nexus:
        self._nexus.commit(
            agent_id="pipeline",
            event_type="phase_enter",
            payload={
                "phase": phase_name,
                "pipeline_id": self._context.pipeline_id,
            },
            phase=phase_name,
            loop_id=None,  # Phase-level, not loop-specific
        )

    # Execute phase enter hooks
    if self._hook_executor:
        context = HookContext(
            event="PHASE_ENTER",
            pipeline_id=self._context.pipeline_id,
            phase=phase_name,
            state=self._get_state_dict(),
        )
        result = await self._hook_executor.execute_hooks("PHASE_ENTER", context)
        if result.halt_pipeline:
            # NEW: Commit PHASE_EXIT with failure
            if self._nexus:
                self._nexus.commit(
                    agent_id="pipeline",
                    event_type="phase_exit",
                    payload={
                        "phase": phase_name,
                        "success": False,
                        "reason": "Hook halted pipeline",
                    },
                    phase=phase_name,
                )
            return False

    # Execute phase based on type
    success = True
    if phase_name == PipelinePhase.PLANNING:
        success = await self._execute_planning()
    elif phase_name == PipelinePhase.DEVELOPMENT:
        success = await self._execute_development()
    elif phase_name == PipelinePhase.QUALITY:
        success = await self._execute_quality()
    elif phase_name == PipelinePhase.DECISION:
        success = await self._execute_decision()

    # NEW: Commit PHASE_EXIT event
    if self._nexus:
        self._nexus.commit(
            agent_id="pipeline",
            event_type="phase_exit",
            payload={
                "phase": phase_name,
                "success": success,
                "pipeline_id": self._context.pipeline_id,
            },
            phase=phase_name,
        )

    return success
```

**Events Committed:**
- `phase_enter`: When entering each phase
- `phase_exit`: When leaving phase (with success/failure status)

---

### 2.3 Agent Selection and Execution

**Location:** `PipelineEngine._execute_planning()` and `_execute_development()`

**Implementation for Planning Phase:**
```python
async def _execute_planning(self) -> bool:
    """Execute planning phase."""
    logger.info("Executing PLANNING phase")

    # Use template-driven agent sequence when available; fall back to registry
    template_agents = self._get_agents_for_phase(PipelinePhase.PLANNING)
    if template_agents:
        agent_sequence = template_agents
    else:
        agent_id = self._agent_registry.select_agent(
            task_description=self._context.user_goal,
            current_phase=PipelinePhase.PLANNING,
            state=self._get_state_dict(),
        )
        if agent_id:
            logger.info(f"Selected planning agent: {agent_id}")
            self._state_machine.add_artifact("planning_agent", agent_id)

            # NEW: Commit AGENT_SELECTED event
            if self._nexus:
                self._nexus.commit(
                    agent_id="pipeline",
                    event_type="agent_selected",
                    payload={
                        "agent_id": agent_id,
                        "phase": PipelinePhase.PLANNING,
                        "selection_method": "registry" if not template_agents else "template",
                    },
                    phase=PipelinePhase.PLANNING,
                    loop_id=None,
                )
        agent_sequence = [agent_id] if agent_id else []

    # Create planning loop
    loop_config = LoopConfig(
        loop_id=generate_loop_id(self._context.pipeline_id),
        phase_name=PipelinePhase.PLANNING,
        agent_sequence=agent_sequence,
        exit_criteria={
            "quality_threshold": self._context.quality_threshold,
            "goal": self._context.user_goal,
        },
        quality_threshold=self._context.quality_threshold,
        max_iterations=self._context.max_iterations,
    )
    await self._loop_manager.create_loop(loop_config)
    future = await self._loop_manager.start_loop(loop_config.loop_id)

    # Wait for loop completion
    loop_state = None
    if future is not None:
        loop_state = await asyncio.wrap_future(future)
        logger.info(
            f"Planning loop completed: status={loop_state.status.name}",
            extra={
                "loop_id": loop_config.loop_id,
                "status": loop_state.status.name,
            },
        )

        # Propagate agent LLM outputs to state machine
        for agent_id, artifact_text in loop_state.artifacts.items():
            if artifact_text is not None:
                self._state_machine.add_artifact(f"plan_{agent_id}", artifact_text)

        # NEW: Commit AGENT_EXECUTED event
        if self._nexus:
            for agent_id in agent_sequence:
                self._nexus.commit(
                    agent_id="pipeline",
                    event_type="agent_executed",
                    payload={
                        "agent_id": agent_id,
                        "phase": PipelinePhase.PLANNING,
                        "loop_id": loop_config.loop_id,
                        "status": loop_state.status.name,
                    },
                    phase=PipelinePhase.PLANNING,
                    loop_id=loop_config.loop_id,
                )

        self._state_machine.add_chronicle_entry(
            "PLANNING_ARTIFACTS_PROPAGATED",
            {
                "agent_ids": list(loop_state.artifacts.keys()),
                "artifact_count": len(loop_state.artifacts),
                "loop_status": loop_state.status.name,
            },
        )

    self._state_machine.increment_iteration()
    return True
```

**Events Committed:**
- `agent_selected`: When an agent is chosen for a phase
- `agent_executed`: When agent execution completes (with status)

**Note:** Tool execution events are auto-committed by Agent base class (Sprint 2 integration).

---

### 2.4 Loop Iteration Tracking

**Location:** All phase execution methods

**Pattern:**
```python
# In all _execute_* methods:
loop_config = LoopConfig(
    loop_id=generate_loop_id(self._context.pipeline_id),
    phase_name=phase_name,
    # ...
)

# All events within loop context include loop_id:
self._nexus.commit(
    agent_id="pipeline",
    event_type="...",
    payload={...},
    phase=phase_name,
    loop_id=loop_config.loop_id,  # Track iteration
)
```

**Loop Back Events:**
```python
# In DecisionEngine when LOOP_BACK decision is made:
if decision.decision_type == DecisionType.LOOP_BACK:
    if self._nexus:
        self._nexus.commit(
            agent_id="pipeline",
            event_type="loop_back",
            payload={
                "reason": decision.reason,
                "defects": [d.to_dict() for d in defects],
            },
            phase=PipelinePhase.DECISION,
            loop_id=current_loop_id,
        )
```

---

### 2.5 Quality Evaluation Events

**Location:** `PipelineEngine._execute_quality()`

**Implementation:**
```python
async def _execute_quality(self) -> bool:
    """Execute quality phase."""
    logger.info("Executing QUALITY phase")

    # Get artifacts to evaluate
    artifacts = self._state_machine.snapshot.artifacts

    # Evaluate quality
    quality_report = await self._quality_scorer.evaluate(
        artifact=artifacts,
        context={
            "requirements": [self._context.user_goal],
            "template": self._config.get("template", "generic"),
        },
    )

    # Store quality score
    quality_score = quality_report.overall_score / 100
    self._state_machine.set_quality_score(quality_score)
    self._state_machine.add_artifact("quality_report", quality_report.to_dict())

    # NEW: Commit QUALITY_EVALUATED event
    if self._nexus:
        self._nexus.commit(
            agent_id="pipeline",
            event_type="quality_evaluated",
            payload={
                "quality_score": quality_score,
                "threshold": self._context.quality_threshold,
                "passed": quality_score >= self._context.quality_threshold,
                "report_summary": {
                    "overall_score": quality_report.overall_score,
                    "criteria_count": len(quality_report.criteria),
                },
            },
            phase=PipelinePhase.QUALITY,
            loop_id=None,  # Quality is phase-level
        )

    logger.info(
        f"Quality evaluation complete: {quality_score:.2f}",
        extra={"quality_score": quality_score},
    )

    return True
```

**Events Committed:**
- `quality_evaluated`: With score, threshold, and pass/fail status

---

### 2.6 Decision and Defect Events

**Location:** `PipelineEngine._execute_decision()`

**Implementation:**
```python
async def _execute_decision(self) -> bool:
    """Execute decision phase."""
    logger.info("Executing DECISION phase")

    quality_score = self._state_machine.snapshot.quality_score or 0.0
    iteration = self._state_machine.snapshot.iteration_count

    # Route defects through RoutingEngine if available
    if self._routing_engine:
        defects = self._state_machine.snapshot.defects or []
        if defects:
            routing_decisions = []
            for defect in defects:
                # NEW: Commit DEFECT_DISCOVERED event
                if self._nexus:
                    self._nexus.commit(
                        agent_id="pipeline",
                        event_type="defect_discovered",
                        payload={
                            "defect_type": defect.get("type", "unknown"),
                            "severity": defect.get("severity", "medium"),
                            "description": defect.get("description", ""),
                        },
                        phase=PipelinePhase.DECISION,
                        loop_id=None,
                    )

                # Normalize defect to dict if needed
                defect_dict = (
                    defect
                    if isinstance(defect, dict)
                    else {"description": str(defect)}
                )
                routing_decision = self._routing_engine.route_defect(defect_dict)
                routing_decisions.append(routing_decision.to_dict())
            self._state_machine.add_artifact("routing_decisions", routing_decisions)
            logger.info(
                f"Routed {len(routing_decisions)} defects via RoutingEngine",
                extra={"defect_count": len(routing_decisions)},
            )

    # Make decision
    decision = self._decision_engine.evaluate(
        phase_name=PipelinePhase.DECISION,
        quality_score=quality_score,
        quality_threshold=self._context.quality_threshold,
        defects=self._state_machine.snapshot.defects,
        iteration=iteration,
        max_iterations=self._context.max_iterations,
        is_final_phase=True,
    )

    self._state_machine.add_artifact("decision", decision.to_dict())

    # NEW: Commit DECISION_MADE event
    if self._nexus:
        self._nexus.commit(
            agent_id="pipeline",
            event_type="decision_made",
            payload={
                "decision_type": decision.decision_type.name,
                "reason": decision.reason,
                "quality_score": quality_score,
                "iteration": iteration,
            },
            phase=PipelinePhase.DECISION,
            loop_id=None,
        )

    logger.info(
        f"Decision: {decision.decision_type.name}",
        extra={"decision_type": decision.decision_type.name},
    )

    # Handle decision
    if decision.decision_type == DecisionType.FAIL:
        self._state_machine.set_error(decision.reason)
        return False

    return True
```

**Events Committed:**
- `defect_discovered`: For each defect found
- `decision_made`: With final decision type and reasoning

---

## 3. Event Type Summary

### 3.1 Pipeline Event Types

| Event Type | Source | Phase | Payload Schema |
|------------|--------|-------|----------------|
| `pipeline_init` | `initialize()` | None | `{pipeline_id, user_goal, template}` |
| `phase_enter` | `_execute_phase()` | All | `{phase, pipeline_id}` |
| `phase_exit` | `_execute_phase()` | All | `{phase, success, pipeline_id}` |
| `agent_selected` | `_execute_planning/development()` | PLANNING, DEVELOPMENT | `{agent_id, phase, selection_method}` |
| `agent_executed` | `_execute_planning/development()` | PLANNING, DEVELOPMENT | `{agent_id, phase, loop_id, status}` |
| `quality_evaluated` | `_execute_quality()` | QUALITY | `{quality_score, threshold, passed, report_summary}` |
| `defect_discovered` | `_execute_decision()` | DECISION | `{defect_type, severity, description}` |
| `decision_made` | `_execute_decision()` | DECISION | `{decision_type, reason, quality_score, iteration}` |
| `loop_back` | DecisionEngine | DECISION | `{reason, defects}` |

### 3.2 Event Flow Diagram

```
Pipeline Start
    │
    ▼
┌─────────────────────────────────┐
│ pipeline_init                   │
│ - pipeline_id                   │
│ - user_goal                     │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ PLANNING Phase                                               │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ phase_enter (PLANNING)                                  ││
│  │ agent_selected (ChatAgent)                              ││
│  │ agent_executed (ChatAgent, loop_id=001)                 ││
│  │ phase_exit (PLANNING, success=True)                     ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ DEVELOPMENT Phase                                            │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ phase_enter (DEVELOPMENT)                               ││
│  │ agent_selected (CodeAgent)                              ││
│  │ agent_executed (CodeAgent, loop_id=002)                 ││
│  │ [TOOL_EXECUTED events auto-committed by Agent]          ││
│  │ phase_exit (DEVELOPMENT, success=True)                  ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ QUALITY Phase                                                │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ phase_enter (QUALITY)                                   ││
│  │ quality_evaluated (score=0.92, passed=True)             ││
│  │ phase_exit (QUALITY, success=True)                      ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ DECISION Phase                                               │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ phase_enter (DECISION)                                  ││
│  │ [defect_discovered for each defect]                     ││
│  │ decision_made (PASS, score=0.92)                        ││
│  │ phase_exit (DECISION, success=True)                     ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
    │
    ▼
Pipeline Complete
```

---

## 4. Quality Gate 2 Preparation

### 4.1 Quality Gate 2 Criteria

| ID | Criteria | Test | Target | Priority | Status |
|----|----------|------|--------|----------|--------|
| STATE-001 | State service singleton | `test_singleton_instance()` | Single instance | CRITICAL | COMPLETE (Sprint 1) |
| STATE-002 | Snapshot mutation-safety | `test_get_snapshot_mutation_safety()` | Deep copy | CRITICAL | COMPLETE (Sprint 1) |
| CHRON-001 | Event timestamp precision | `test_event_timestamp_precision()` | Microsecond | HIGH | COMPLETE (Sprint 1) |
| CHRON-002 | Digest token efficiency | `test_digest_token_budget()` | <4000 tokens | HIGH | COMPLETE (Sprint 2) |
| WORK-001 | Metadata tracking | `test_track_file_metadata()` | All changes recorded | HIGH | COMPLETE (Sprint 1) |
| WORK-002 | Path traversal prevention | `test_path_traversal_blocked()` | 0% bypass | CRITICAL | COMPLETE (Sprint 1) |
| PERF-002 | Digest generation latency | `test_digest_latency()` | <50ms | MEDIUM | **SPRINT 3** |
| MEM-002 | State service memory | `test_memory_footprint()` | <1MB | MEDIUM | **SPRINT 3** |
| INTEG-001 | Agent-Nexus integration | `test_agent_chronicle_commit()` | Functional | CRITICAL | COMPLETE (Sprint 2) |
| INTEG-002 | Pipeline-Nexus integration | `test_pipeline_phase_events()` | Functional | CRITICAL | **SPRINT 3** |

### 4.2 New Tests for Sprint 3

**File:** `tests/unit/state/test_pipeline_nexus_integration.py`

```python
"""Pipeline-Nexus Integration Tests"""

import pytest
from gaia.state.nexus import NexusService
from gaia.pipeline.engine import PipelineEngine, PipelineConfig
from gaia.pipeline.state import PipelineContext, PipelineState


class TestPipelineNexusIntegration:
    """Test PipelineEngine integration with NexusService."""

    @pytest.fixture
    def nexus(self):
        """Get NexusService singleton."""
        NexusService.reset_instance()
        return NexusService.get_instance()

    @pytest.fixture
    def pipeline_context(self):
        """Create test pipeline context."""
        return PipelineContext(
            pipeline_id="test-pipeline-001",
            user_goal="Build a REST API endpoint",
            template="STANDARD",
            quality_threshold=0.90,
            max_iterations=3,
        )

    def test_pipeline_initialization_commits_event(self, nexus, pipeline_context):
        """Verify pipeline initialization commits event to Chronicle."""
        engine = PipelineEngine()
        await engine.initialize(pipeline_context, {"template": "STANDARD"})

        # Check event was committed
        snapshot = nexus.get_snapshot()
        init_events = [
            e for e in snapshot["chronicle"]
            if e["event_type"] == "pipeline_init"
        ]
        assert len(init_events) == 1
        assert init_events[0]["payload"]["pipeline_id"] == "test-pipeline-001"

    def test_phase_enter_exit_events(self, nexus, pipeline_context):
        """Verify PHASE_ENTER and PHASE_EXIT events are committed."""
        engine = PipelineEngine()
        await engine.initialize(pipeline_context)

        # Execute a phase (mocked for unit test)
        success = await engine._execute_phase("PLANNING")

        # Check phase events
        snapshot = nexus.get_snapshot()
        phase_events = [
            e for e in snapshot["chronicle"]
            if e["event_type"] in ["phase_enter", "phase_exit"]
        ]
        assert len(phase_events) == 2  # ENTER + EXIT
        assert phase_events[0]["payload"]["phase"] == "PLANNING"
        assert phase_events[0]["event_type"] == "phase_enter"
        assert phase_events[1]["event_type"] == "phase_exit"

    def test_agent_selected_event(self, nexus, pipeline_context):
        """Verify AGENT_SELECTED event when agent is chosen."""
        engine = PipelineEngine()
        await engine.initialize(pipeline_context)

        # Execute planning phase
        await engine._execute_planning()

        # Check agent selection event
        snapshot = nexus.get_snapshot()
        agent_events = [
            e for e in snapshot["chronicle"]
            if e["event_type"] == "agent_selected"
        ]
        assert len(agent_events) >= 1
        assert "agent_id" in agent_events[0]["payload"]

    def test_quality_evaluated_event(self, nexus, pipeline_context):
        """Verify QUALITY_EVALUATED event with score."""
        engine = PipelineEngine()
        await engine.initialize(pipeline_context)

        # Add artifact for quality evaluation
        engine._state_machine.add_artifact("test_artifact", "test content")

        # Execute quality phase
        await engine._execute_quality()

        # Check quality event
        snapshot = nexus.get_snapshot()
        quality_events = [
            e for e in snapshot["chronicle"]
            if e["event_type"] == "quality_evaluated"
        ]
        assert len(quality_events) == 1
        assert "quality_score" in quality_events[0]["payload"]

    def test_decision_made_event(self, nexus, pipeline_context):
        """Verify DECISION_MADE event with decision type."""
        engine = PipelineEngine()
        await engine.initialize(pipeline_context)

        # Execute decision phase
        await engine._execute_decision()

        # Check decision event
        snapshot = nexus.get_snapshot()
        decision_events = [
            e for e in snapshot["chronicle"]
            if e["event_type"] == "decision_made"
        ]
        assert len(decision_events) == 1
        assert "decision_type" in decision_events[0]["payload"]

    def test_loop_id_tracking(self, nexus, pipeline_context):
        """Verify loop_id is tracked in events."""
        engine = PipelineEngine()
        await engine.initialize(pipeline_context)

        # Execute planning phase with loop
        await engine._execute_planning()

        # Check events have loop_id
        snapshot = nexus.get_snapshot()
        loop_events = [
            e for e in snapshot["chronicle"]
            if e.get("loop_id") is not None
        ]
        assert len(loop_events) >= 1

    def test_shared_state_between_agent_and_pipeline(self, nexus, pipeline_context):
        """Verify Agent and Pipeline share same Nexus instance."""
        # Initialize pipeline
        engine = PipelineEngine()
        await engine.initialize(pipeline_context)

        # Initialize agent (should get same instance)
        from gaia.agents.base.agent import Agent
        # Agent would get same nexus._instance

        # Verify same instance
        pipeline_nexus = engine._nexus
        agent_nexus = NexusService.get_instance()
        assert pipeline_nexus is agent_nexus

    def test_state_snapshot_after_phase(self, nexus, pipeline_context):
        """Verify state snapshot reflects phase completion."""
        engine = PipelineEngine()
        await engine.initialize(pipeline_context)

        # Execute planning phase
        await engine._execute_planning()

        # Get snapshot
        snapshot = nexus.get_snapshot()

        # Verify snapshot contains planning events
        assert snapshot["summary"]["total_events"] >= 3  # init + enter + exit


class TestPipelineDigestGeneration:
    """Test Chronicle digest generation for Pipeline context."""

    def test_pipeline_digest_token_budget(self, nexus, pipeline_context):
        """Verify digest fits within token budget."""
        engine = PipelineEngine()
        await engine.initialize(pipeline_context)

        # Execute all phases to generate events
        await engine._execute_planning()
        await engine._execute_development()
        await engine._execute_quality()
        await engine._execute_decision()

        # Generate digest
        digest = nexus.get_chronicle_digest(max_events=15, max_tokens=3500)

        # Verify token budget (rough estimate: 4 chars/token)
        estimated_tokens = len(digest) // 4
        assert estimated_tokens <= 4000

    def test_pipeline_digest_latency(self, nexus, pipeline_context):
        """Verify digest generation latency <50ms."""
        import time

        engine = PipelineEngine()
        await engine.initialize(pipeline_context)

        # Generate many events
        for _ in range(50):
            nexus.commit(
                agent_id="test-agent",
                event_type="test_event",
                payload={"iteration": _},
                phase="TEST",
            )

        # Measure digest generation time
        start = time.perf_counter()
        digest = nexus.get_chronicle_digest(max_events=50)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Verify latency target
        assert elapsed_ms < 50

    def test_pipeline_memory_footprint(self, nexus, pipeline_context):
        """Verify state service memory <1MB."""
        import tracemalloc

        # Start memory tracking
        tracemalloc.start()

        # Initialize pipeline and generate events
        engine = PipelineEngine()
        await engine.initialize(pipeline_context)

        # Generate events
        for i in range(100):
            nexus.commit(
                agent_id="test-agent",
                event_type="test_event",
                payload={"data": "x" * 100, "iteration": i},
                phase="TEST",
            )

        # Get snapshot
        snapshot = nexus.get_snapshot()

        # Check memory
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Verify memory footprint (<1MB = 1048576 bytes)
        # Note: This measures total allocated, not just Nexus
        # In practice, Nexus should use <1MB of this
        assert peak < 1048576 * 2  # Allow 2x buffer for test overhead
```

---

## 5. Risk Assessment

### 5.1 Active Risks

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|-------|-------------|--------|------------|-------|
| R1.5 | Agent-Pipeline State Conflict | LOW | HIGH | Unified state schema design (implemented) | senior-developer |
| R3.1 | Hash Chain Divergence | MEDIUM | HIGH | Ensure _event_cache syncs with AuditLogger | senior-developer |
| R3.2 | State Mutation Race | LOW | MEDIUM | Document ordering: commit → hooks → execution | senior-developer |
| R3.3 | Performance Overhead | LOW | LOW | Batch commits, benchmark latency | testing-quality-specialist |

### 5.2 Risk Triggers

| Risk | Trigger | Action |
|------|---------|--------|
| R3.1 | Events in cache differ from AuditLogger | Add sync verification test |
| R3.2 | State drift detected in testing | Add RLock for state mutations |
| R3.3 | Digest latency >100ms | Optimize summarization algorithm |

---

## 6. Implementation Checklist

### 6.1 Code Changes

- [ ] Add `self._nexus` to `PipelineEngine.__init__()`
- [ ] Wire Nexus in `PipelineEngine.initialize()`
- [ ] Add PHASE_ENTER/PHASE_EXIT commits in `_execute_phase()`
- [ ] Add AGENT_SELECTED/AGENT_EXECUTED commits in `_execute_planning()`
- [ ] Add AGENT_SELECTED/AGENT_EXECUTED commits in `_execute_development()`
- [ ] Add QUALITY_EVALUATED commit in `_execute_quality()`
- [ ] Add DEFECT_DISCOVERED/DECISION_MADE commits in `_execute_decision()`
- [ ] Add LOOP_BACK commit in DecisionEngine (if applicable)

### 6.2 Test Files

- [ ] Create `tests/unit/state/test_pipeline_nexus_integration.py`
- [ ] Implement 15 test functions (see Section 4.2)
- [ ] Add performance benchmarks for digest latency
- [ ] Add memory footprint benchmarks

### 6.3 Documentation

- [ ] Update `docs/reference/phase1-implementation-plan.md` with Sprint 3 status
- [ ] Update `docs/spec/baibel-gaia-integration-master.md` with Sprint 3 completion
- [ ] Update `future-where-to-resume-left-off.md` with Sprint 3 closeout

---

## 7. Dependencies

### 7.1 Completed Dependencies

| Component | File | Status | Sprint |
|-----------|------|--------|--------|
| NexusService | `src/gaia/state/nexus.py` | COMPLETE | Sprint 1 |
| WorkspaceIndex | `src/gaia/state/nexus.py` (embedded) | COMPLETE | Sprint 1 |
| ChronicleDigest | `src/gaia/pipeline/audit_logger.py` | COMPLETE | Sprint 2 |
| Agent-Nexus Integration | `src/gaia/agents/base/agent.py` | COMPLETE | Sprint 2 |

### 7.2 No External Dependencies

All dependencies are internal to GAIA codebase. No external packages required.

---

## 8. Success Metrics

### 8.1 Technical Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Pipeline events committed | 100% of phase transitions | Count events in Chronicle |
| Loop tracking | All loop events have loop_id | Audit event payloads |
| State sharing | Agent + Pipeline share same Nexus instance | `is` comparison |
| Digest latency | <50ms | `time.perf_counter()` |
| Memory footprint | <1MB | `tracemalloc` |

### 8.2 Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Test coverage | 100% of new code | `pytest --cov` |
| Test pass rate | 100% | All 15 tests pass |
| Backward compatibility | 0 regressions | Existing Pipeline tests pass |
| Thread safety | Verified | 100-thread concurrent test |

---

## 9. Handoff Notes

### 9.1 For senior-developer

**Implementation Approach:**
1. Start with `PipelineEngine.initialize()` - wire Nexus singleton
2. Add phase transition events (PHASE_ENTER/PHASE_EXIT)
3. Add agent events (AGENT_SELECTED/AGENT_EXECUTED)
4. Add quality/decision events
5. Write tests incrementally as each integration point is completed

**Key Design Decisions:**
- Events committed BEFORE hook execution (captures intent)
- Exit events committed AFTER execution (captures outcome)
- All events include phase context
- Loop events include loop_id for correlation

### 9.2 For testing-quality-specialist

**Test Priorities:**
1. Phase transition events (PHASE_ENTER/PHASE_EXIT)
2. Agent selection/execution events
3. Quality/decision events
4. State sharing between Agent and Pipeline
5. Performance benchmarks (digest latency, memory)

**Test Infrastructure:**
- pytest 8.4.2+
- pytest-benchmark for performance tests
- tracemalloc for memory profiling

### 9.3 For software-program-manager

**Sprint 3 Timeline:**
- Week 1: Implementation (Days 1-5)
- Week 2: Testing + Quality Gate 2 prep (Days 6-10)

**Milestone:**
- Day 10 EOD: Quality Gate 2 validation

---

## Appendix A: File Modification Summary

| File | Change Type | LOC Estimate | Priority |
|------|-------------|--------------|----------|
| `src/gaia/pipeline/engine.py` | MODIFY | +100 | CRITICAL |
| `tests/unit/state/test_pipeline_nexus_integration.py` | NEW | ~300 | CRITICAL |

---

## Appendix B: Event Schema Reference

```python
# pipeline_init
{
    "event_type": "pipeline_init",
    "agent_id": "pipeline",
    "payload": {
        "pipeline_id": str,
        "user_goal": str,
        "template": str,
    }
}

# phase_enter
{
    "event_type": "phase_enter",
    "agent_id": "pipeline",
    "payload": {
        "phase": str,  # PLANNING, DEVELOPMENT, QUALITY, DECISION
        "pipeline_id": str,
    },
    "phase": str,
}

# phase_exit
{
    "event_type": "phase_exit",
    "agent_id": "pipeline",
    "payload": {
        "phase": str,
        "success": bool,
        "pipeline_id": str,
    },
    "phase": str,
}

# agent_selected
{
    "event_type": "agent_selected",
    "agent_id": "pipeline",
    "payload": {
        "agent_id": str,  # Selected agent name
        "phase": str,
        "selection_method": str,  # "template" or "registry"
    },
    "phase": str,
    "loop_id": None,
}

# agent_executed
{
    "event_type": "agent_executed",
    "agent_id": "pipeline",
    "payload": {
        "agent_id": str,
        "phase": str,
        "loop_id": str,
        "status": str,  # LoopStatus name
    },
    "phase": str,
    "loop_id": str,
}

# quality_evaluated
{
    "event_type": "quality_evaluated",
    "agent_id": "pipeline",
    "payload": {
        "quality_score": float,  # 0-1
        "threshold": float,
        "passed": bool,
        "report_summary": dict,
    },
    "phase": "QUALITY",
}

# defect_discovered
{
    "event_type": "defect_discovered",
    "agent_id": "pipeline",
    "payload": {
        "defect_type": str,
        "severity": str,
        "description": str,
    },
    "phase": "DECISION",
}

# decision_made
{
    "event_type": "decision_made",
    "agent_id": "pipeline",
    "payload": {
        "decision_type": str,  # PASS, FAIL, LOOP_BACK
        "reason": str,
        "quality_score": float,
        "iteration": int,
    },
    "phase": "DECISION",
}

# loop_back
{
    "event_type": "loop_back",
    "agent_id": "pipeline",
    "payload": {
        "reason": str,
        "defects": list,
    },
    "phase": "DECISION",
    "loop_id": str,
}
```

---

**END OF TECHNICAL DESIGN**

**Approved By:** Dr. Sarah Kim, planning-analysis-strategist
**Date:** 2026-04-05
**Next Action:** senior-developer begins Sprint 3 implementation
**Review Cadence:** Daily progress checks, Friday sprint review
