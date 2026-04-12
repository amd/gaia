# Program Management Plan: Pipeline Orchestration Branch Merge

**Document Type:** Program Execution Plan
**Branch:** `feature/pipeline-orchestration-v1`
**Target Branch:** `main`
**Date:** 2026-04-11
**Prepared By:** Software Program Manager
**Version:** 1.0

---

## Executive Summary

This program management plan organizes **19 outstanding issues** across the `feature/pipeline-orchestration-v1` branch into a coherent execution roadmap for achieving merge-readiness and post-merge optimization. The issues span documentation gaps, integration conflicts with upstream PRs, architectural decisions, and build-upon opportunities.

**Critical Path Summary:**
- **5 P0 Merge-Blocking Issues:** DOC-1, DOC-3, INT-1, INT-3, B3-C
- **Blocked Dependencies:** INT-1 and INT-3 require PR #606 to merge to main first
- **Highest Risk:** INT-1 (4 HIGH-severity conflict files requiring careful absorption)
- **Total Effort:** ~35-40 hours for merge-readiness, ~25 hours for Phase 6 enhancements

**Recommended Sequence:**
1. Execute documentation fixes (DOC-1, DOC-3) immediately
2. Implement Agent UI pipeline integration (B3-C)
3. Await PR #606 merge, then rebase and resolve conflicts (INT-1, INT-3)
4. Complete architecture decisions (ARCH-1, INT-2, WIRE-3)
5. Execute post-merge integration work (ARCH-2, WIRE-1)
6. Phase 6 enhancements (BU-1 through BU-6)

---

## Program Roadmap

### Milestone 1: Pre-Merge Documentation & Integration (P0)
**Target:** Merge review gate readiness
**Dependencies:** None (unblocked)

| Gate | Deliverable | Owner | Effort | Status |
|------|-------------|-------|--------|--------|
| 1.1 | Documentation fixes complete (DOC-1, DOC-3) | technical-writer-expert | 1.5 hours | OPEN |
| 1.2 | Agent UI pipeline integration (B3-C) | senior-developer + frontend-developer | 4-6 hours | OPEN |
| 1.3 | Architecture decisions documented (ARCH-1, INT-2, WIRE-3) | enhanced-senior-developer | Decision only | OPEN |

### Milestone 2: PR #606 Integration (P0, Blocked)
**Target:** Rebase complete with conflict resolution
**Dependencies:** PR #606 merged to main by kovtcharov

| Gate | Deliverable | Owner | Effort | Status |
|------|-------------|-------|--------|--------|
| 2.1 | PR #606 merged to main | kovtcharov (external) | N/A | BLOCKED |
| 2.2 | Rebase onto updated main | senior-developer | 3.5 hours | BLOCKED |
| 2.3 | Conflict resolution complete (INT-1, INT-3) | senior-developer | 3.5 hours | BLOCKED |

### Milestone 3: Post-Merge Integration (P1)
**Target:** Full pipeline functionality integrated
**Dependencies:** Milestone 2 complete

| Gate | Deliverable | Owner | Effort | Status |
|------|-------------|-------|--------|--------|
| 3.1 | Capability vocabulary migration (ARCH-2) | senior-developer | 2-3 hours | OPEN |
| 3.2 | Resilience primitives wired (WIRE-1) | senior-developer | 2-3 hours | OPEN |
| 3.3 | Architecture decisions implemented | enhanced-senior-developer | Varies | OPEN |

### Milestone 4: Phase 6 Enhancements (P2)
**Target:** Memory infrastructure integration
**Dependencies:** PR #606 on main, Milestone 2 complete

| Gate | Deliverable | Owner | Effort | Status |
|------|-------------|-------|--------|--------|
| 4.1 | Memory infrastructure complete (BU-1, BU-2, BU-5) | senior-developer | 10-14 hours | OPEN |
| 4.2 | System integration complete (BU-3, BU-4) | senior-developer | 5-8 hours | OPEN |
| 4.3 | Template enhancements (BU-6) | enhanced-senior-developer | Design only | OPEN |

---

## Issue Breakdown with Task Assignments

### P0: Merge-Blocking Issues

#### DOC-1: YAML Frontmatter Missing from 9 Spec Files
**Priority:** P0 | **Effort:** 30 minutes | **Owner:** technical-writer-expert

**Affected Files:**
1. `docs/spec/agent-ui-eval-kpi-reference.md`
2. `docs/spec/agent-ui-eval-kpis.md`
3. `docs/spec/gaia-loom-architecture.md`
4. `docs/spec/nexus-gaia-native-integration-spec.md`
5. `docs/spec/pipeline-metrics-competitive-analysis.md`
6. `docs/spec/pipeline-metrics-kpi-reference.md`
7. `docs/spec/phase5_multi_stage_pipeline.md`
8. `docs/spec/component-framework-design-spec.md`
9. `docs/spec/component-framework-implementation-plan.md`

**Task:**
Add 3-line YAML frontmatter to each file:
```yaml
---
title: <FileNameWithoutExtension>
---
```

**Acceptance Criteria:**
- [ ] All 9 files start with `---` on line 1
- [ ] Each file has a `title:` field matching the filename
- [ ] `docs/docs.json` navigation references are valid
- [ ] Documentation build completes without errors

**Related Documents:** `docs/spec/phase5-update-manifest.md` Section F

---

#### DOC-3: Branch Change Matrix Update
**Priority:** P0 | **Effort:** 1 hour | **Owner:** technical-writer-expert

**Task:**
Execute Sections A through G of `docs/spec/phase5-update-manifest.md` to synchronize `docs/reference/branch-change-matrix.md` with current branch state.

**Specific Edits:**
- **Section A:** Update branch statistics (files changed: 970, lines inserted: 300,282, commits: 67)
- **Section B:** Add Phase 5 program description to Executive Summary
- **Section C:** Update all 9 Open Items with Phase 5 status
- **Section D:** Add Phase 5 matrix section (3.13)
- **Section E:** Update commit index with 9 new commits
- **Section F:** Apply design spec reconciliation edits
- **Section G:** Update senior-dev work order status table

**Acceptance Criteria:**
- [ ] Section 1 stats match `git diff --stat origin/main...HEAD`
- [ ] Phase 5 program described in Scope Statement
- [ ] All 9 Open Items reflect Phase 5 delivery status
- [ ] Phase 5 matrix section added with 24 rows
- [ ] Commit index includes commits 57ee63d through fa3ef98
- [ ] Design spec status updated to "Partially Implemented"

---

#### B3-C: Agent UI Pipeline Integration
**Priority:** P0 | **Effort:** 4-6 hours | **Owner:** senior-developer + frontend-developer

**Problem:** The `gaia pipeline` CLI is fully wired, but the Agent UI (`gaia chat --ui`) has no route, widget, or chat-tool integration for pipeline execution. Users cannot invoke the pipeline via the browser chat interface.

**Minimal Implementation Tasks:**

**Backend (senior-developer):**
1. Create `src/gaia/ui/routers/pipeline.py` with:
   - `POST /api/pipeline/run` endpoint accepting `{task: str, model?: str, no_spawn?: bool}`
   - SSE streaming of pipeline stage progress events
   - Integration with `PipelineOrchestrator.run_pipeline()`
2. Mount router in `src/gaia/ui/server.py`:
   ```python
   from .routers import pipeline
   app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
   ```
3. Add pipeline status endpoint: `GET /api/pipeline/status`

**Frontend (frontend-developer):**
1. Add pipeline panel component to `src/gaia/apps/webui/src/components/`:
   - `PipelinePanel.tsx` - Main panel with task input and progress display
   - `PipelinePanel.css` - Styling
2. Add pipeline API service: `src/gaia/apps/webui/src/services/pipeline.ts`
3. Integrate panel into ChatView or as separate tab
4. Add pipeline capability to RoutingAgent's routing logic

**Acceptance Criteria:**
- [ ] `POST /api/pipeline/run` endpoint responds with 200 OK
- [ ] SSE events stream pipeline stage progress (Stage 1-5)
- [ ] Pipeline panel visible in Agent UI
- [ ] Users can submit tasks and view results in browser
- [ ] `RoutingAgent` routes pipeline-capable requests to `PipelineOrchestrator`
- [ ] Error handling for Lemonade unavailability

**Related Documents:** `docs/reference/pr606-integration-analysis.md` (for SSE patterns)

---

#### INT-1: PR #606 Conflict Absorption
**Priority:** P0 | **Effort:** 3.5 hours | **Owner:** senior-developer
**Status:** BLOCKED (requires PR #606 merged to main first)

**Conflict Files (4 HIGH-severity):**

**C-1: `src/gaia/ui/_chat_helpers.py`**
- PR #606 adds: `_register_agent_memory_ops()` function (38 lines)
- Our branch: 1,144-line comprehensive module
- **Resolution:** Accept our version as base, add PR #606's function at end of module
- **Steps:**
  1. During rebase, accept our 1,144-line version as base
  2. Add `_register_agent_memory_ops()` after existing cache management functions
  3. Verify imports match our module structure
  4. Run `python -m pytest tests/unit/test_chat_helpers.py -xvs`

**C-2: `src/gaia/ui/database.py`**
- PR #606 adds: Memory schema columns (`embedding BLOB`, `superseded_by TEXT`, `consolidated_at TEXT`)
- Our branch: 787-line `ChatDatabase` class
- **Resolution:** Add column definitions to `SCHEMA_SQL` constant
- **Steps:**
  1. Confirm if `MemoryStore` shares SQLite file or uses separate file (Q1 for kovtcharov)
  2. If shared: add columns to `SCHEMA_SQL` after `settings` table
  3. Add migration guards to `_migrate()` method
  4. Run `python -m pytest tests/unit/test_database.py -xvs`

**C-3: `src/gaia/ui/sse_handler.py`**
- PR #606 adds: `AgentLoop` state-change event handlers (115 lines)
- Our branch: 950-line SSE handler module
- **Resolution:** Accept our version as base, add event constants and handlers
- **Steps:**
  1. Accept our 950-line version as base
  2. Add event constants after existing constants (alphabetical order)
  3. Add handler methods following existing pattern
  4. Avoid circular import with `agent_loop.py`

**C-4: `src/gaia/ui/routers/mcp.py`**
- PR #606 adds: Health/tool/control endpoints (206 lines)
- Our branch: 425-line MCP catalog router
- **Resolution:** Merge both routers into one file
- **Steps:**
  1. Accept our 425-line version as base
  2. Add PR #606's imports
  3. Add health/tool/control endpoints after catalog endpoints
  4. Verify `server.py` mounts router only once
  5. Run `python -m pytest tests/mcp/ -xvs`

**Acceptance Criteria:**
- [ ] All 4 conflict files resolved without functionality loss
- [ ] Unit tests pass for all affected modules
- [ ] No `AttributeError` or `ImportError` at runtime
- [ ] Memory schema columns present (if shared database)
- [ ] AgentLoop SSE events functional

**Related Documents:** `docs/reference/pr606-integration-analysis.md` Sections 4 and 9

---

#### INT-3: model_load_lock Rename
**Priority:** P0 | **Effort:** 15 minutes | **Owner:** senior-developer
**Status:** BLOCKED (must be done during/after rebase)

**Problem:** PR #720 renames `_model_load_lock` to `model_load_lock` (public API). Our branch references the private name at line 67 of `_chat_helpers.py`. Skipping this causes guaranteed `AttributeError` at runtime.

**Task:**
1. After rebase complete, run: `grep -r "_model_load_lock" src/gaia/ui/`
2. Update all occurrences to `model_load_lock`
3. Verify `server.py` boot-time preload uses public name

**Acceptance Criteria:**
- [ ] Zero occurrences of `_model_load_lock` in codebase
- [ ] All references use `model_load_lock`
- [ ] No `AttributeError` at server startup
- [ ] Model caching functional after rename

**Related Documents:** `docs/reference/pr720-integration-analysis.md` Section 4.3

---

### P1: Post-Merge Integration Issues

#### ARCH-1: Python-class vs MD-config Agent Architecture
**Priority:** P1 | **Owner:** enhanced-senior-developer | **Type:** Architectural Decision

**Problem:** Phase 5 built Python classes (`DomainAnalyzer(Agent)`, etc.) rather than MD-config files (`config/agents/domain-analyzer.md`) that `agent-ecosystem-design-spec.md` specified. This creates a discrepancy between design spec and implementation.

**Decision Required:**
- **Option A:** Python classes are permanent architecture
  - Update `agent-ecosystem-design-spec.md` Section 5 to reflect Python-class approach
  - Document advantages: type safety, IDE support, runtime validation
- **Option B:** MD-config is still target, Python classes are interim
  - Complete Tasks 1-6 of `senior-dev-work-order.md`
  - Migrate Python classes to MD-config format

**Recommendation:** Option A - Python classes provide better type safety and are consistent with GAIA's agent pattern. The MD-config approach was experimental; Python classes are the proven pattern.

**Acceptance Criteria:**
- [ ] Architecture decision documented in `docs/spec/architecture-decisions/`
- [ ] Design spec updated to reflect decision
- [ ] Future agent development follows decided pattern
- [ ] Team notified of decision

---

#### INT-2: PR #720 Registry Naming Collision
**Priority:** P1 | **Owner:** enhanced-senior-developer | **Type:** Architectural Decision

**Problem:** Both our branch and PR #720 created `src/gaia/agents/registry.py` from scratch with incompatible designs:
- **PR #720:** UI-facing, 3-source discovery (builtin, custom Python, YAML manifests)
- **Our branch:** Pipeline-facing, YAML-only from `config/agents/`, capability-based selection

**Decision Required:**
- **Option A (Recommended):** Rename our registry to `PipelineAgentRegistry`, relocate to `src/gaia/pipeline/agent_registry.py`
  - Preserves PR #720's user-facing naming
  - Clear separation of concerns (UI vs pipeline)
  - Both registries can coexist with different purposes
- **Option B:** Rename PR #720's to `AgentDiscovery`
  - Conflicts with PR #720's established naming
  - Requires coordination with itomek

**Recommended Architecture (Two-Registry System):**
```
PR #720's AgentRegistry (agents/registry.py)
  └─> Answers: "Given agent ID user chose, give me factory"
  └─> Method: registry.get(agent_id).factory()

Our PipelineAgentRegistry (pipeline/agent_registry.py)
  └─> Answers: "Given task, which agent should handle it?"
  └─> Method: select_agent(task, phase, state) -> returns agent_id

Bridge Pattern:
  PipelineOrchestrator.select_agent() -> agent_id
  └─> AgentRegistry.get(agent_id).factory() -> instantiate agent
```

**Acceptance Criteria:**
- [ ] Decision documented
- [ ] Our registry relocated to `src/gaia/pipeline/agent_registry.py`
- [ ] Import paths updated throughout codebase
- [ ] Bridge pattern implemented between registries
- [ ] Coordination with itomek if Option B selected

**Related Documents:** `docs/reference/pr720-integration-analysis.md` Section 4.1

---

#### WIRE-1: Resilience Primitives Not Wired
**Priority:** P1 | **Effort:** 2-3 hours | **Owner:** senior-developer

**Problem:** `CircuitBreaker`, `Bulkhead`, and `Retry` exist in `src/gaia/resilience/` with full test coverage but are not imported or invoked in `engine.py`, `loop_manager.py`, or `routing_engine.py`.

**Task:**
Wire resilience wrappers around agent invocation call sites in `routing_engine.py`'s `route()` method.

**Target Call Sites (3):**
1. `routing_engine.py:route()` - agent selection and invocation
2. `engine.py:_execute_phase()` - phase execution
3. `loop_manager.py:_run_iteration()` - iteration loop

**Implementation Pattern:**
```python
from gaia.resilience import CircuitBreaker, Bulkhead, Retry

@CircuitBreaker.call(failure_threshold=5, recovery_timeout=60)
@Bulkhead.isolate(max_concurrent=3)
@Retry.with_backoff(max_retries=3, base_delay=1.0)
async def execute_agent(agent, task, state):
    return await agent.execute(task)
```

**Acceptance Criteria:**
- [ ] All 3 call sites wrapped with resilience primitives
- [ ] Circuit breaker trips after 5 consecutive failures
- [ ] Bulkhead limits concurrent agent execution to 3
- [ ] Retry with exponential backoff on transient failures
- [ ] Integration tests verify resilience behavior
- [ ] No performance regression (>10% latency increase)

**Related Documents:** `src/gaia/resilience/` module

---

#### WIRE-3: AgentOrchestrator Scope Decision
**Priority:** P1 | **Owner:** enhanced-senior-developer | **Type:** Architectural Decision

**Problem:** `PipelineOrchestrator` was delivered as a superset of the originally-scoped `AgentOrchestrator`. The routing-level `AgentOrchestrator` (for dynamic agent selection in `RoutingAgent`) was deprioritized.

**Decision Required:**
- **Option A:** `PipelineOrchestrator` is sufficient
  - Pipeline-level orchestration resolves the core need
  - Routing-level dynamic selection is lower priority
  - Close issue as resolved
- **Option B:** Routing-level `AgentOrchestrator` still needed
  - Implement as thin adapter:
    ```python
    class AgentOrchestrator:
        def select_agent(self, task, routing_context):
            pipeline_orchestrator = PipelineOrchestrator()
            agent_id = pipeline_orchestrator.select_agent(task)
            return AgentRegistry.get(agent_id).factory()
    ```

**Recommendation:** Option A - `PipelineOrchestrator` delivers capability-based orchestration at a higher layer. The routing-level need is partially addressed by INT-2's two-registry bridge pattern.

**Acceptance Criteria:**
- [ ] Decision documented
- [ ] If Option B: thin adapter implemented
- [ ] `RoutingAgent` updated to use orchestrator (if applicable)
- [ ] Open Item 1 in branch-change-matrix updated

---

#### ARCH-2: Capability Vocabulary Migration
**Priority:** P1 | **Effort:** 2-3 hours | **Owner:** senior-developer

**Problem:** 18 legacy YAML files in `config/agents/` use freeform capability strings that don't match the formal vocabulary in `src/gaia/core/capabilities.py`. Phase 5 created MD configs for 5 Python stages with aligned vocabularies but didn't migrate legacy files.

**Task:**
Execute migration per Phase 6 commit 41ee396's `unified-capability-model.md`:
1. Review `docs/spec/unified-capability-model.md` for target vocabulary
2. Update 18 YAML files to match unified vocabulary
3. Alternative: migrate all 18 to `.md` format (larger effort)

**Files to Update:**
All 18 YAML files in `config/agents/` directory.

**Acceptance Criteria:**
- [ ] All 18 YAML files use vocabulary from `src/gaia/core/capabilities.py`
- [ ] No freeform capability strings remain
- [ ] `AgentRegistry` capability index matches all files
- [ ] Routing tests pass with updated vocabulary

---

### P2: Phase 6 Enhancements

#### BU-1: Add MemoryMixin to Pipeline Stage Agents
**Priority:** P2 | **Effort:** 4-6 hours | **Owner:** senior-developer
**Dependency:** PR #606 on main

**Task:**
Add `MemoryMixin` inheritance to `DomainAnalyzer`, `WorkflowModeler`, `LoomBuilder`, `GapDetector`, and `PipelineExecutor` to persist analysis results across sessions.

**Implementation:**
```python
from gaia.agents.base.memory import MemoryMixin

class DomainAnalyzer(Agent, MemoryMixin):
    # Inherits remember(), recall(), update_memory() tools
    # Persists domain analysis results to MemoryStore
```

**Acceptance Criteria:**
- [ ] All 5 stage agents inherit `MemoryMixin`
- [ ] Domain analysis persisted after Stage 1 completion
- [ ] Subsequent runs recall previous analysis (no redundant work)
- [ ] Memory tools available in stage tool registry

---

#### BU-2: Wire GoalStore into PipelineOrchestrator
**Priority:** P2 | **Effort:** 3-4 hours | **Owner:** senior-developer
**Dependency:** PR #606 on main

**Task:**
Write pipeline execution goals and phase completion to `GoalStore` using PENDING/ACTIVE/COMPLETED/FAILED state mapping.

**Implementation:**
```python
from gaia.agents.base.goal_store import GoalStore, GoalState

class PipelineOrchestrator(Agent):
    def __init__(self):
        self.goal_store = GoalStore()

    async def run_pipeline(self, task):
        goal = self.goal_store.create_goal(task, state=GoalState.PENDING)
        # ... execution ...
        self.goal_store.update_state(goal.id, GoalState.COMPLETED)
```

**Acceptance Criteria:**
- [ ] Pipeline runs visible in Memory Dashboard goal tracker
- [ ] Goal state transitions match pipeline state
- [ ] No additional UI work required for visibility

---

#### BU-3: AgentLoop/PipelineExecutor Convergence Design
**Priority:** P2 | **Effort:** 2 hours (design session) | **Owner:** enhanced-senior-developer (joint with kovtcharov)
**Type:** Design Session

**Problem:** PR #606's `AgentLoop` (442 lines) and our `PipelineExecutor` share an autonomous background execution pattern. Risk of permanent divergence without shared runtime abstraction.

**Task:**
Schedule and conduct design session with kovtcharov to produce convergence design document.

**Discussion Topics:**
- Shared execution runtime abstraction
- State machine alignment (AgentLoop vs PipelineEngine states)
- Event streaming patterns (SSE)
- Goal integration (GoalStore)

**Acceptance Criteria:**
- [ ] Design session scheduled and held
- [ ] Convergence design document produced
- [ ] Implementation roadmap defined
- [ ] No duplicate execution patterns in codebase

---

#### BU-4: SystemDiscovery Hardware Calibration for DomainAnalyzer
**Priority:** P2 | **Effort:** 2-3 hours | **Owner:** senior-developer
**Dependency:** PR #606 on main

**Task:**
Import `SystemDiscovery` into `DomainAnalyzer` and use cached hardware context (NPU availability, GPU model, driver version) to calibrate domain agent tier recommendations.

**Implementation:**
```python
from gaia.agents.base.discovery import SystemDiscovery

class DomainAnalyzer(Agent):
    def __init__(self):
        self.system_discovery = SystemDiscovery()
        self.hardware_context = self.system_discovery.get_cached_profile()

    def _analyze_domains(self, task):
        # Use hardware_context to calibrate recommendations
        if self.hardware_context.npu_available:
            recommend_npu_agents()
```

**Acceptance Criteria:**
- [ ] `DomainAnalyzer` imports `SystemDiscovery`
- [ ] Hardware context cached and used for recommendations
- [ ] AMD hardware users receive calibrated recommendations
- [ ] Highest value/effort ratio of all BU items

---

#### BU-5: GapDetector Memory Caching with MemoryStore
**Priority:** P2 | **Effort:** 3-4 hours | **Owner:** senior-developer
**Dependency:** PR #606 on main, BU-1 complete

**Task:**
Cache gap scan results in `MemoryStore` with configurable TTL. When gap is filled, call `update_memory()` with `supersedes` parameter.

**Implementation:**
```python
from gaia.agents.base.memory_store import MemoryStore

class GapDetector(Agent):
    def __init__(self):
        self.memory_store = MemoryStore()

    async def scan_gaps(self, loom):
        cached = self.memory_store.search(f"gap_scan:{loom.id}", ttl=3600)
        if cached:
            return cached

        gaps = await self._scan(loom)
        self.memory_store.remember(f"gap_scan:{loom.id}", gaps)
        return gaps

    def on_gap_filled(self, gap_id):
        self.memory_store.update_memory(
            f"gap:{gap_id}",
            status="filled",
            supersedes=f"gap:{gap_id}:open"
        )
```

**Acceptance Criteria:**
- [ ] Gap scan results cached with TTL
- [ ] Redundant filesystem scans eliminated
- [ ] Supersession lineage tracked in `knowledge.superseded_by`
- [ ] Cache hit rate >80% for repeated scans

---

#### BU-6: Declarative Memory Tool Invocations in Templates
**Priority:** P2 | **Effort:** Design only | **Owner:** enhanced-senior-developer (joint with kovtcharov)
**Dependency:** Design session required

**Task:**
Extend `component-framework` tool-call fenced block syntax to recognize PR #606's five memory tools as first-class declarative invocations.

**Memory Tools:**
- `remember` - ADD operation
- `recall` - RETRIEVE operation
- `update_memory` - UPDATE operation
- `forget` - DELETE operation
- `search_past_conversations` - SEARCH operation

**Acceptance Criteria:**
- [ ] Design session held with kovtcharov
- [ ] Template syntax extension documented
- [ ] LLM-evaluated tool calls recognized
- [ ] Phase 6/7 boundary decision made

---

## Dependency Map

### Critical Path Dependencies

```
DOC-1, DOC-3 (Documentation fixes)
       │
       ▼
   Merge Review Gate
       │
       ▼
   PR #606 merged to main (EXTERNAL - kovtcharov)
       │
       ├─────────────────┬─────────────────┐
       ▼                 ▼                 ▼
INT-1 (Rebase)    INT-3 (Lock rename)  B3-C (Agent UI)
       │                 │                 │
       └─────────────────┴─────────────────┘
                         │
                         ▼
                 Milestone 2 Complete
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
ARCH-2 (Vocab)    WIRE-1 (Resilience)  ARCH-1 (Decision)
       │                 │                 │
       ▼                 ▼                 ▼
                 Milestone 3 Complete
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
BU-1 (MemoryMixin)  BU-2 (GoalStore)  BU-4 (SystemDiscovery)
       │
       ▼
BU-5 (GapCache) ────► BU-3 (Convergence) ────► BU-6 (Templates)
```

### Parallelization Opportunities

**Can be executed in parallel (no dependencies):**
- DOC-1 and DOC-3 (both documentation, independent files)
- ARCH-1, INT-2, WIRE-3 decisions (all architectural, independent)
- B3-C backend and frontend tasks (split across specializations)
- INT-1 conflict resolution across 4 files (can split across engineers)
- BU-1, BU-2, BU-4 (all independent memory integrations)

**Must be sequential:**
- INT-3 must happen during/after rebase (not before)
- BU-5 requires BU-1 complete (MemoryMixin before caching)
- BU-6 requires design session (cannot implement before design)
- Milestone 3 requires Milestone 2 (post-merge requires rebase)

---

## Risk Register

| Risk ID | Trigger | Impact | Likelihood | Mitigation | Owner |
|---------|---------|--------|------------|------------|-------|
| R-DOC-1 | Merge without YAML frontmatter | Mintlify rendering fails for 9 pages | High | Execute DOC-1 before merge review | technical-writer-expert |
| R-INT-1 | Incorrect _chat_helpers.py absorption | Memory operations fail at runtime | Medium | Follow pr606-integration-analysis.md Section 4.1 exact steps | senior-developer |
| R-INT-2 | Incorrect database.py schema absorption | OperationalError: no such column | High | Get Q1 answer from kovtcharov before rebase (shared vs separate SQLite) | senior-developer |
| R-INT-3 | Missed model_load_lock rename | AttributeError at runtime | High | grep -r "_model_load_lock" post-rebase | senior-developer |
| R-ARCH-1 | No architectural decision | Confusion for future developers | Medium | Decision required within 1 week of merge | enhanced-senior-developer |
| R-WIRE-2 | No Agent UI integration | Users cannot invoke pipeline via chat | Medium | Implement minimal SSE endpoint + panel | senior-developer |
| R-BU-3 | No convergence design session | AgentLoop and PipelineExecutor diverge | Medium | Schedule before Phase 6 kickoff | enhanced-senior-developer |
| R-PR606 | PR #606 merge delayed | All blocked work cannot proceed | Medium | Escalate to kovtcharov for timeline | Software Program Manager |

### Risk Mitigation Triggers

**Immediate Escalation Required:**
- PR #606 not merged within 5 business days
- Any P0 fix takes >2x estimated effort
- Runtime errors discovered during acceptance testing

**Weekly Review:**
- Phase 6 progress against estimates
- Architecture decision status
- Team capacity for parallel work streams

---

## Resource Plan

### Agent Assignments

| Agent | Assigned Issues | Total Effort | Priority Focus |
|-------|-----------------|--------------|----------------|
| **technical-writer-expert** | DOC-1, DOC-3 | 1.5 hours | P0 documentation |
| **senior-developer** | B3-C (backend), INT-1, INT-3, WIRE-1, ARCH-2, BU-1, BU-2, BU-4, BU-5 | ~25 hours | P0/P1 technical implementation |
| **frontend-developer** | B3-C (frontend panel) | 2-3 hours | P0 Agent UI |
| **enhanced-senior-developer** | ARCH-1 decision, INT-2 decision, WIRE-3 decision, BU-3, BU-6 | ~5 hours + decisions | P1/P2 architecture |
| **quality-reviewer** | All acceptance criteria verification | ~4 hours | Quality gates |
| **testing-quality-specialist** | Test updates for all changes | ~6 hours | Test coverage |

### Effort Summary by Phase

| Phase | Issues | Total Effort |
|-------|--------|--------------|
| P0 (Pre-Merge) | DOC-1, DOC-3, B3-C | 5.5-7.5 hours |
| P0 (Blocked) | INT-1, INT-3 | 3.5 hours |
| P1 (Post-Merge) | ARCH-1, INT-2, WIRE-1, WIRE-3, ARCH-2 | 5-6 hours + decisions |
| P2 (Phase 6) | BU-1 through BU-6 | ~20-25 hours |
| **Total** | **19 issues** | **~34-42 hours** |

---

## Success Metrics

### Merge-Readiness Criteria (Must all be true)
- [ ] All 5 P0 issues resolved (DOC-1, DOC-3, INT-1, INT-3, B3-C)
- [ ] Documentation builds without errors (Mintlify compatible)
- [ ] All tests passing (E2E, QG7, unit)
- [ ] Agent UI pipeline integration functional
- [ ] No known runtime-breaking changes

### Post-Merge Success Criteria (Week 1)
- [ ] Architecture decisions documented and communicated
- [ ] Capability vocabulary migration complete
- [ ] Resilience primitives wired and tested
- [ ] No regressions in pipeline execution

### Phase 6 Success Criteria (Sprint)
- [ ] Memory infrastructure integrated (BU-1, BU-2, BU-5)
- [ ] System discovery calibration complete (BU-4)
- [ ] Convergence design session held (BU-3)
- [ ] Template enhancement design complete (BU-6)

---

## Communication Plan

### Stakeholder Notifications

| Stakeholder | Trigger | Method | Content |
|-------------|---------|--------|---------|
| kovtcharov | PR #606 status | GitHub comment | Request timeline for merge to unblock downstream work |
| AMD GAIA Team | Merge complete | Team channel | Phase 5 pipeline merged, user documentation links |
| Documentation Team | DOC fixes complete | Email | New documentation requires Mintlify review |
| QA Team | Milestone 2 complete | Email | Regression testing recommended post-rebase |

### Status Reporting

**Weekly Status Report Format:**
```
## Week of [Date]

### Completed
- [List of issues resolved]

### In Progress
- [List of issues being worked]

### Blocked
- [List of blocked issues, dependencies]

### Next Week
- [Planned work]

### Risks/Escalations
- [Any risks requiring attention]
```

---

## Appendix: File References

### Primary Input Documents
- `docs-branch-matrix-outstanding-issues-analysis.md` - Strategic issues assessment
- `MERGE_DECISION_pipeline-orchestration-v1.md` - Merge authorization
- `phase5-merge-verification.md` - Quality gate verification
- `docs/reference/branch-change-matrix.md` - Authoritative change reference
- `docs/spec/phase5-update-manifest.md` - Exact edit instructions

### Integration Analysis Documents
- `docs/reference/pr606-integration-analysis.md` - PR #606 conflict analysis
- `docs/reference/pr720-integration-analysis.md` - PR #720 conflict analysis

### Implementation Files
- `src/gaia/pipeline/orchestrator.py` - PipelineOrchestrator (518 LOC)
- `src/gaia/pipeline/stages/*.py` - 5 Python stage agents
- `src/gaia/agents/registry.py` - Pipeline-facing registry (naming collision)
- `src/gaia/ui/_chat_helpers.py` - 1,144-line module (INT-1 conflict target)
- `src/gaia/ui/database.py` - 787-line ChatDatabase class
- `src/gaia/ui/sse_handler.py` - 950-line SSE handler
- `src/gaia/ui/routers/mcp.py` - 425-line MCP catalog router

### Documentation Files Requiring Updates
| File | Issue | Required Edit |
|------|-------|---------------|
| `docs/spec/agent-ui-eval-kpi-reference.md` | DOC-1 | Add YAML frontmatter |
| `docs/spec/agent-ui-eval-kpis.md` | DOC-1 | Add YAML frontmatter |
| `docs/spec/gaia-loom-architecture.md` | DOC-1 | Add YAML frontmatter |
| `docs/spec/nexus-gaia-native-integration-spec.md` | DOC-1 | Add YAML frontmatter |
| `docs/spec/pipeline-metrics-competitive-analysis.md` | DOC-1 | Add YAML frontmatter |
| `docs/spec/pipeline-metrics-kpi-reference.md` | DOC-1 | Add YAML frontmatter |
| `docs/spec/phase5_multi_stage_pipeline.md` | DOC-1 | Add YAML frontmatter |
| `docs/spec/component-framework-design-spec.md` | DOC-1 | Add YAML frontmatter |
| `docs/spec/component-framework-implementation-plan.md` | DOC-1 | Add YAML frontmatter |
| `docs/reference/branch-change-matrix.md` | DOC-3 | Execute phase5-update-manifest.md Sections A-G |
| `docs/spec/agent-ecosystem-design-spec.md` | ARCH-1 | Update Section 2.2, Section 5 |

---

**Document Version:** 1.0
**Prepared By:** Software Program Manager
**Review Status:** Ready for execution
**Next Action:** Dispatch to senior-developer and enhanced-senior-developer for P0 execution
