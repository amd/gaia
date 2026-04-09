# Phase 1 Implementation Plan

**Document Version:** 1.0
**Date:** 2026-04-05
**Status:** READY FOR IMPLEMENTATION
**Duration:** 8 weeks (Sprint 1-4)
**Owner:** senior-developer

---

## Executive Summary

Phase 1 (State Unification) implements the Nexus Service pattern to unify state management across GAIA's Agent and Pipeline systems. This 8-week implementation creates a shared state layer that enables coordinated multi-agent workflows with token-efficient context management.

### Phase 1 Overview

| Dimension | Target | Notes |
|-----------|--------|-------|
| **Duration** | 8 weeks | 4 sprints (2 weeks each) |
| **FTE Effort** | 16 person-weeks | senior-developer primary |
| **Deliverables** | 4 components | Nexus, Workspace, Chronicle, Integration |
| **Exit Criteria** | Quality Gate 2 | 8 criteria, 135+ tests |

---

## 1. Implementation Scope

### 1.1 Components

| Component | File | LOC Estimate | Priority | Sprint |
|-----------|------|--------------|----------|--------|
| **NexusService** | `src/gaia/state/nexus.py` | ~300 | P0 | Sprint 1-2 |
| **WorkspaceIndex** | `src/gaia/state/workspace.py` | ~150 | P0 | Sprint 3-4 |
| **ChronicleDigest** | Extension to `src/gaia/pipeline/audit_logger.py` | ~200 | P1 | Sprint 3-4 |
| **Agent Integration** | `src/gaia/agents/base/agent.py` | ~50 | P0 | Sprint 5-6 |
| **Pipeline Integration** | `src/gaia/pipeline/engine.py` | ~100 | P0 | Sprint 7-8 |

### 1.2 What's In Scope

- NexusService singleton state management
- Workspace metadata index for artifact tracking
- Chronicle event logging with token-efficient digest
- Agent system integration with Nexus
- Pipeline system integration with Nexus
- Comprehensive test suite (135+ functions)

### 1.3 What's Out of Scope

- Supervisor Agent (Phase 2)
- Full Agent-as-Data refactoring (Phase 3)
- Service Layer Decoupling (Phase 3)
- Context Lens advanced summarization (Phase 3)

---

## 2. Sprint Schedule

### Sprint 1-2: Core State Service (Weeks 1-4)

**Objective:** Implement NexusService singleton and foundational state management.

#### Week 1: NexusService Core

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/state/nexus.py` | senior-developer | NexusService skeleton |
| 3 | Implement singleton pattern with thread safety | senior-developer | Thread-safe singleton |
| 4-5 | Implement `commit()` method wrapping AuditLogger | senior-developer | Event logging |

#### Week 2: NexusService Operations

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Implement `get_snapshot()` with deep copy | senior-developer | Mutation-safe snapshot |
| 3-4 | Implement `get_context_for_agent()` method | senior-developer | Context curation |
| 5 | Unit tests for NexusService | testing-quality-specialist | 35 test functions |

#### Week 3-4: WorkspaceIndex

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/state/workspace.py` | senior-developer | WorkspaceIndex skeleton |
| 3-4 | Implement `validate_path()` with traversal protection | senior-developer | Security enforcement |
| 5 | Implement `write_file()` with metadata tracking | senior-developer | File operations |
| 6-7 | Implement `get_index()` and `get_recent()` | senior-developer | Metadata queries |
| 8-9 | Unit tests for WorkspaceIndex | testing-quality-specialist | 25 test functions |
| 10 | Integration tests | testing-quality-specialist | Nexus+Workspace tests |

### Sprint 3-4: Chronicle & Integration (Weeks 5-8)

#### Week 5: ChronicleDigest Extension

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Add `get_digest()` to AuditLogger | senior-developer | Token-efficient summary |
| 3-4 | Implement event summarization logic | senior-developer | Digest generation |
| 5 | Unit tests for ChronicleDigest | testing-quality-specialist | 20 test functions |

#### Week 6: Agent Integration

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Update `Agent.__init__()` to create Nexus reference | senior-developer | Agent-Nexus wiring |
| 3-4 | Update `Agent._run_step()` to use curated context | senior-developer | Context-aware execution |
| 5 | Integration tests | testing-quality-specialist | Agent-Nexus tests |

#### Week 7: Pipeline Integration

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Update `PipelineEngine` to share Nexus instance | senior-developer | Pipeline-Nexus wiring |
| 3-4 | Implement state sharing between Agent/Pipeline | senior-developer | Unified state |
| 5 | Integration tests | testing-quality-specialist | Pipeline-Nexus tests |

#### Week 8: Testing & Quality Gate 2

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Full regression testing | testing-quality-specialist | All tests passing |
| 3-4 | Performance benchmarks | testing-quality-specialist | Perf metrics |
| 5 | Quality Gate 2 validation | quality-reviewer | QG2 decision |

---

## 3. Technical Design

### 3.1 NexusService Architecture

```python
# src/gaia/state/nexus.py
class NexusService:
    """Python singleton state service wrapping AuditLogger.

    Provides unified state management across Agent and Pipeline systems.
    Implements blackboard pattern for multi-agent state sharing.
    """

    _instance: Optional["NexusService"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "NexusService":
        """Thread-safe singleton creation using double-checked locking."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize state service internals (only once)."""
        if self._initialized:
            return

        from gaia.pipeline.audit_logger import AuditLogger
        self._audit_logger = AuditLogger.get_instance()
        self._workspace = WorkspaceIndex()
        self._state_lock = threading.RLock()
        self._initialized = True

    def commit(self, agent_id: str, event_type: str, payload: dict) -> None:
        """Commit event to Chronicle (via AuditLogger).

        Args:
            agent_id: Source agent/pipeline identifier
            event_type: Type of event (THOUGHT, TOOL_CALL, etc.)
            payload: Event data payload
        """
        with self._state_lock:
            entry = {
                "id": str(uuid.uuid4()),
                "timestamp": time.time(),
                "agent_id": agent_id,
                "event_type": event_type,
                "payload": payload,
            }
            self._audit_logger.log_event(entry)

    def get_snapshot(self) -> dict:
        """Return deep copy of state (mutation-safe)."""
        import copy
        with self._state_lock:
            return copy.deepcopy({
                "chronicle": self._audit_logger.get_events(),
                "workspace": self._workspace.get_index(),
            })

    def get_context_for_agent(self, agent_id: str) -> dict:
        """Curate context for specific agent.

        Provides token-efficient context with:
        - Chronicle digest (recent events summary)
        - Relevant files (most recently modified)
        - Recent raw events
        """
        return {
            "chronicle_digest": self.get_digest(15),
            "relevant_files": self._workspace.get_recent(5),
            "recent_events": self._audit_logger.get_recent(3),
        }

    def get_digest(self, max_events: int = 15) -> str:
        """Generate token-efficient summary of recent events."""
        return self._audit_logger.get_digest(max_events)
```

### 3.2 WorkspaceIndex Design

```python
# src/gaia/state/workspace.py
class WorkspaceIndex:
    """Metadata index for agent-produced artifacts.

    Tracks file changes in workspace directory with:
    - Path validation (traversal protection)
    - Metadata tracking (size, mtime, modified_by)
    - Checksum for integrity verification
    """

    def __init__(self):
        self._root = Path("./workspace")
        self._index: Dict[str, WorkspaceFile] = {}
        self._lock = threading.RLock()

    def validate_path(self, relative_path: str) -> Path:
        """Resolve and validate path is within workspace."""
        resolved = (self._root / relative_path).resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            raise SecurityError("Path traversal detected")
        return resolved

    def write_file(self, relative_path: str, content: bytes,
                   modified_by: str) -> WorkspaceFile:
        """Write file, update index, commit to chronicle."""
        with self._lock:
            full_path = self.validate_path(relative_path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(content)

            # Update index
            stat = full_path.stat()
            file_entry = WorkspaceFile(
                path=relative_path,
                size=stat.st_size,
                last_modified=stat.st_mtime,
                modified_by=modified_by,
                checksum=hashlib.sha256(content).hexdigest(),
            )
            self._index[relative_path] = file_entry
            return file_entry

    def get_index(self) -> List[WorkspaceFile]:
        """Return current file metadata index."""
        with self._lock:
            return list(self._index.values())

    def get_recent(self, limit: int = 5) -> List[WorkspaceFile]:
        """Return most recently modified files."""
        with self._lock:
            sorted_files = sorted(
                self._index.values(),
                key=lambda f: f.last_modified,
                reverse=True
            )
            return sorted_files[:limit]
```

### 3.3 Event Types for Chronicle

| Event Type | Source | Payload Schema |
|------------|--------|----------------|
| `THOUGHT` | Agent | `{thought_process, summary}` |
| `TOOL_CALL` | Agent | `{tool_name, arguments}` |
| `TOOL_RESULT` | Agent | `{result, success}` |
| `PHASE_TRANSITION` | Pipeline | `{from_phase, to_phase, quality_score}` |
| `CONSENSUS` | Quality Gate | `{decision, feedback}` |
| `ERROR` | Any | `{error_type, message, traceback}` |

### 3.4 Integration Points

**Agent Integration:**
```python
# In Agent.__init__():
from gaia.state.nexus import NexusService

class Agent:
    def __init__(self, ...):
        # ... existing initialization ...
        self._nexus = NexusService.get_instance()

    async def _run_step(self, topic: str, context: dict) -> dict:
        # Get curated context from state service
        curated = self._nexus.get_context_for_agent(self.agent_id)

        # Execute with curated context
        response = await self.llm_client.chat(
            system_prompt=self.system_prompt,
            user_message=f"Topic: {topic}\nContext: {curated}",
            tools=self._get_available_tools(),
        )

        # Commit to Chronicle
        self._nexus.commit(
            agent_id=self.agent_id,
            event_type="THOUGHT",
            payload=response.dict(),
        )

        return response.dict()
```

**Pipeline Integration:**
```python
# In PipelineEngine._execute_phase():
from gaia.state.nexus import NexusService

class PipelineEngine:
    def __init__(self, ...):
        # ... existing initialization ...
        self._nexus = NexusService.get_instance()

    async def _execute_phase(self, context: PipelineContext) -> PhaseResult:
        # Share state with Agent system
        state = self._nexus.get_snapshot()

        # Execute phase with unified state
        result = await self._run_phase(context, state)

        # Commit phase transition to Chronicle
        self._nexus.commit(
            agent_id="pipeline",
            event_type="PHASE_TRANSITION",
            payload={
                "from_phase": context.current_phase,
                "to_phase": context.next_phase,
                "quality_score": result.quality_score,
            },
        )

        return result
```

---

## 4. Test Strategy

### 4.1 Test Matrix

| Test File | Functions | Coverage | Priority |
|-----------|-----------|----------|----------|
| `test_nexus_service.py` | 35 | NexusService | CRITICAL |
| `test_workspace_index.py` | 25 | WorkspaceIndex | CRITICAL |
| `test_chronicle_digest.py` | 20 | ChronicleDigest | HIGH |
| `test_state_consistency.py` | 10 | Cross-component | CRITICAL |
| `test_agent_nexus_integration.py` | 15 | Agent-Nexus | CRITICAL |
| `test_pipeline_nexus_integration.py` | 15 | Pipeline-Nexus | CRITICAL |
| `test_security.py` | 15 | Security tests | CRITICAL |
| `test_performance.py` | 10 | Benchmarks | MEDIUM |
| **Total** | **145** | **Full coverage** | |

### 4.2 Key Test Functions

**NexusService Tests:**
```python
class TestNexusService:
    def test_singleton_instance(self):
        """Verify singleton pattern returns same instance."""

    def test_singleton_thread_safety(self):
        """Test singleton is thread-safe with 100 concurrent threads."""

    def test_commit_event(self):
        """Test committing event to Chronicle."""

    def test_get_snapshot_mutation_safety(self):
        """Test snapshot is deep copy (mutation-safe)."""

    def test_get_context_for_agent(self):
        """Test context curation for agent."""
```

**WorkspaceIndex Tests:**
```python
class TestWorkspaceIndex:
    def test_validate_path_traversal_blocked(self):
        """Test path traversal is blocked."""

    def test_write_file_updates_index(self):
        """Test file write updates metadata index."""

    def test_get_recent_files(self):
        """Test getting most recently modified files."""
```

**Security Tests:**
```python
class TestWorkspaceSecurity:
    def test_path_traversal_blocked(self):
        """Test ../path traversal is blocked."""

    def test_absolute_path_blocked(self):
        """Test absolute paths outside workspace are blocked."""

    def test_special_chars_in_path(self):
        """Test special characters in path handled safely."""
```

### 4.3 Quality Gate 2 Criteria

| Criteria | Test | Target | Priority |
|----------|------|--------|----------|
| **STATE-001** | State service singleton | Single instance | CRITICAL |
| **STATE-002** | Snapshot mutation-safety | Deep copy | CRITICAL |
| **CHRON-001** | Event timestamp precision | Microsecond | HIGH |
| **CHRON-002** | Digest token efficiency | <4000 tokens | HIGH |
| **WORK-001** | Metadata tracking | All changes recorded | HIGH |
| **WORK-002** | Path traversal prevention | 0% bypass | CRITICAL |
| **PERF-002** | Digest generation latency | <50ms | MEDIUM |
| **MEM-002** | State service memory | <1MB | MEDIUM |

---

## 5. Risk Management

### 5.1 Active Risks

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|------|-------------|--------|------------|-------|
| R1.1 | State Service Complexity | MEDIUM | HIGH | RLock throughout, concurrent tests | senior-developer |
| R1.2 | Performance Degradation | MEDIUM | MEDIUM | Benchmark early, shallow copies | testing-quality-specialist |
| R1.5 | Agent-Pipeline State Conflict | MEDIUM | HIGH | Unified state schema design | senior-developer |
| R1.7 | Thread Safety Race Conditions | MEDIUM | HIGH | RLock, 100-thread tests | testing-quality-specialist |

### 5.2 Risk Triggers

| Risk | Trigger | Action |
|------|---------|--------|
| R1.1 | State service shows race conditions in testing | Immediate review, add locking |
| R1.2 | Digest generation >100ms | Optimize summarization algorithm |
| R1.5 | Agent/Pipeline state models conflict | Design mediation layer |
| R1.7 | Concurrent tests fail | Add RLock, re-test |

---

## 6. Success Metrics

### 6.1 Technical Metrics

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| State instances | 2 (separate) | 1 (unified) | Count instances |
| Context token usage | Full dump | <4000 tokens | Token counter |
| Digest latency | N/A | <50ms | Benchmark |
| Memory footprint | N/A | <1MB | RSS delta |
| Event timestamp precision | Millisecond | Microsecond | `time.time()` |

### 6.2 Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Test coverage | 100% | `pytest --cov` |
| Test pass rate | 100% | All tests pass |
| Thread safety | Verified | 100-thread tests |
| Security | 0% bypass | Security tests |

---

## 7. Dependencies

### 7.1 Internal Dependencies

```
Phase 0 Complete
    │
    ▼
┌─────────────────┐
│  ToolRegistry   │
│  AgentScope     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  NexusService   │  Phase 1 Core
└────────┬────────┘
         │
    ┌────┴────┬────────────┐
    │         │            │
    ▼         ▼            ▼
┌────────┐ ┌────────┐ ┌─────────┐
│ Agent  │ │Pipeline│ │Workspace│
│Integration│Integration│ Index   │
└────────┘ └────────┘ └─────────┘
```

### 7.2 File Modifications

| File | Change Type | LOC | Sprint |
|------|-------------|-----|--------|
| `src/gaia/state/nexus.py` | NEW | ~300 | Sprint 1-2 |
| `src/gaia/state/workspace.py` | NEW | ~150 | Sprint 3-4 |
| `src/gaia/pipeline/audit_logger.py` | EXTEND | ~200 | Sprint 3-4 |
| `src/gaia/agents/base/agent.py` | MODIFY | ~50 | Sprint 5-6 |
| `src/gaia/pipeline/engine.py` | MODIFY | ~100 | Sprint 7-8 |

---

## 8. Handoff Notes

### 8.1 For software-program-manager

**Resource Allocation:**
- senior-developer: 8 weeks full-time
- testing-quality-specialist: 4 weeks (Sprint 2, 4, 6, 8)
- quality-reviewer: Week 8 for Quality Gate 2

**Milestone Tracking:**
- Weekly progress reviews every Friday
- Escalate R1.1/R1.5 risks immediately
- Track against sprint schedule

### 8.2 For senior-developer

**Implementation Notes:**
1. Start with NexusService core (Week 1)
2. Wrap AuditLogger, don't replace
3. Use RLock for all state operations
4. Test thread safety early with 100 threads

**Key Design Decisions:**
- Singleton pattern with double-checked locking
- Deep copy for snapshot (mutation-safe)
- Case-sensitive path validation
- Microsecond timestamps with `time.time()`

### 8.3 For testing-quality-specialist

**Test Priorities:**
1. Thread safety tests (100 concurrent threads)
2. Security tests (path traversal prevention)
3. Performance benchmarks (digest latency)
4. Integration tests (Agent-Nexus, Pipeline-Nexus)

**Test Infrastructure:**
- pytest 8.4.2+
- pytest-benchmark for performance
- pytest-asyncio for async tests

---

## 9. Approval & Sign-Off

**Prepared By:** Dr. Sarah Kim, planning-analysis-strategist
**Date:** 2026-04-05
**Next Action:** senior-developer begins Sprint 1

### Sign-Off Checklist

- [x] Technical feasibility confirmed
- [x] Resource allocation confirmed
- [x] Risk assessment acceptable
- [x] Test strategy comprehensive
- [x] Quality criteria defined
- [ ] **Team approval to begin Phase 1**

---

**END OF PLAN**
