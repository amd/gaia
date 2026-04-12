# Implementation Summary: Senior Developer Agent Tasks

**Prepared By:** Jordan Lee, Senior Software Developer  
**Date:** 2026-04-11  
**Status:** Ready for Execution

---

## Overview

This document summarizes the detailed implementation plans for all assigned issues from the Program Management Plan. The plans are organized by priority and execution order.

---

## Assigned Issues Summary

| Issue ID | Priority | Status | Effort | Plan Document |
|----------|----------|--------|--------|---------------|
| **B3-C** | P0 (Merge-Blocking) | UNBLOCKED | 4-6 hours | `implementation-plan-B3-C-agent-ui-pipeline.md` |
| **WIRE-1** | P1 (Post-Merge) | UNBLOCKED | 2-3 hours | `implementation-plan-WIRE-1-resilience-wiring.md` |
| **ARCH-2** | P1 (Post-Merge) | UNBLOCKED | 2-3 hours | `implementation-plan-ARCH-2-capability-migration.md` |
| **INT-1** | P0 (Merge-Blocking) | BLOCKED | 3.5 hours | See Program Plan |
| **INT-3** | P0 (Merge-Blocking) | BLOCKED | 15 min | See Program Plan |
| **ARCH-1** | P1 (Post-Merge) | DECISION | Decision only | See Program Plan |
| **WIRE-3** | P1 (Post-Merge) | DECISION | Decision only | See Program Plan |
| **BU-1, BU-2, BU-4, BU-5** | P2 (Phase 6) | BLOCKED | 10-14 hours | See Program Plan |

---

## Execution Order

### Phase 1: Immediate (Unblocked P0/P1)

**Execute these tasks FIRST - no external dependencies:**

1. **B3-C: Agent UI Pipeline Integration** (4-6 hours)
   - Create backend SSE endpoint for pipeline execution
   - Build frontend PipelinePanel component
   - Wire into Agent UI
   - **Blocks:** WIRE-2 (RoutingAgent update)
   - **Plan:** `implementation-plan-B3-C-agent-ui-pipeline.md`

2. **WIRE-1: Resilience Primitives Wiring** (2-3 hours)
   - Wrap routing_engine.py with CircuitBreaker, Bulkhead, Retry
   - Add resilience monitoring metrics
   - **Plan:** `implementation-plan-WIRE-1-resilience-wiring.md`

3. **ARCH-2: Capability Vocabulary Migration** (2-3 hours)
   - Run migration script on 18 YAML files
   - Create unified capability reference
   - Add validation to agent registry
   - **Plan:** `implementation-plan-ARCH-2-capability-migration.md`

**Total Phase 1 Effort:** 8-12 hours

---

### Phase 2: Blocked on PR #606 Merge (P0)

**Execute AFTER PR #606 merges to main:**

4. **INT-1: PR #606 Conflict Absorption** (3.5 hours)
   - Rebase onto updated main
   - Resolve 4 HIGH-severity conflicts:
     - `_chat_helpers.py` (1,144 lines vs. PR's +38)
     - `database.py` (787 lines vs. PR's +31/-3)
     - `sse_handler.py` (950 lines vs. PR's +115/-1)
     - `routers/mcp.py` (425 lines vs. PR's +206/-1)
   - Run unit tests for all affected modules

5. **INT-3: model_load_lock Rename** (15 min)
   - Run: `grep -r "_model_load_lock" src/gaia/ui/`
   - Update all occurrences to `model_load_lock`
   - Verify server.py boot-time preload

**Total Phase 2 Effort:** 3.75 hours  
**Dependency:** PR #606 merged by kovtcharov

---

### Phase 3: Architecture Decisions Required (P1)

**Requires enhanced-senior-developer decision:**

6. **ARCH-1: Python-class vs. MD-config Architecture** (Decision only)
   - Decision: Are Python classes permanent or interim?
   - If permanent: Update `agent-ecosystem-design-spec.md` Section 5
   - If interim: Complete Tasks 1-6 of `senior-dev-work-order.md`

7. **WIRE-3: AgentOrchestrator Scope** (Decision only)
   - Decision: Is `PipelineOrchestrator` sufficient?
   - If yes: Close issue as resolved
   - If no: Implement thin adapter for routing-level orchestration

**Total Phase 3 Effort:** Decision time only (1-2 hours discussion)

---

### Phase 4: Phase 6 Enhancements (P2)

**Blocked on PR #606 and Phase 1 complete:**

8. **BU-1: MemoryMixin for Pipeline Stages** (4-6 hours)
   - Add MemoryMixin to 5 stage agents
   - Persist analysis results across sessions

9. **BU-2: GoalStore Integration** (3-4 hours)
   - Wire GoalStore into PipelineOrchestrator
   - Map pipeline states to goal states

10. **BU-4: SystemDiscovery Calibration** (2-3 hours)
    - Import SystemDiscovery into DomainAnalyzer
    - Use hardware context for recommendations

11. **BU-5: GapDetector Memory Caching** (3-4 hours)
    - Cache gap scan results with TTL
    - Implement supersession tracking

**Total Phase 4 Effort:** 12-17 hours  
**Dependencies:** PR #606 on main, BU-1 before BU-5

---

## Detailed Implementation Plans

The following detailed implementation plans are provided:

### 1. B3-C: Agent UI Pipeline Integration
**File:** `implementation-plan-B3-C-agent-ui-pipeline.md`

**Key Components:**
- Backend: `POST /api/v1/pipeline/run` SSE endpoint
- Frontend: PipelinePanel.tsx component
- Service: pipeline.ts API wrapper
- Integration: Add to App.tsx routing

**Files to Create/Modify:**
| File | Action | Lines |
|------|--------|-------|
| `src/gaia/ui/routers/pipeline.py` | MODIFY | +80 |
| `src/gaia/apps/webui/src/services/pipeline.ts` | CREATE | 180 |
| `src/gaia/apps/webui/src/components/PipelinePanel.tsx` | CREATE | 200 |
| `src/gaia/apps/webui/src/components/PipelinePanel.css` | CREATE | 150 |

**Acceptance Criteria:**
- [ ] SSE endpoint streams pipeline stage progress
- [ ] Pipeline panel visible in Agent UI
- [ ] Users can submit tasks and view results

---

### 2. WIRE-1: Resilience Primitives Wiring
**File:** `implementation-plan-WIRE-1-resilience-wiring.md`

**Key Components:**
- CircuitBreaker: Opens after 5 consecutive failures
- Bulkhead: Limits to 10 concurrent operations
- Retry: 3 retries with exponential backoff
- Monitoring: `get_resilience_stats()` method

**Files to Create/Modify:**
| File | Action | Lines |
|------|--------|-------|
| `src/gaia/pipeline/routing_engine.py` | MODIFY | +120 |
| `tests/unit/test_routing_engine_resilience.py` | CREATE | 80 |

**Acceptance Criteria:**
- [ ] All 3 call sites wrapped with resilience primitives
- [ ] Circuit breaker trips correctly
- [ ] Bulkhead limits concurrency
- [ ] Retry with backoff functional

---

### 3. ARCH-2: Capability Vocabulary Migration
**File:** `implementation-plan-ARCH-2-capability-migration.md`

**Key Components:**
- Migration script for 18 YAML files
- Unified capability reference document
- Validation in agent registry

**Files to Create/Modify:**
| File | Action | Lines |
|------|--------|-------|
| `config/agents/*.yaml` (18 files) | MODIFY | ~5 each |
| `config/agents/README-capabilities.md` | CREATE | 150 |
| `util/migrate-capabilities.py` | CREATE | 200 |
| `src/gaia/agents/registry.py` | MODIFY | 50 |

**Acceptance Criteria:**
- [ ] All 18 YAML files use unified vocabulary
- [ ] Migration script runs without errors
- [ ] No duplicate capabilities
- [ ] Validation functional

---

## Risk Register

| Risk ID | Issue | Impact | Likelihood | Mitigation |
|---------|-------|--------|------------|------------|
| R-B3C-1 | SSE streaming fails on Windows | HIGH | LOW | Use thread pool executor |
| R-B3C-2 | Frontend build fails | MEDIUM | LOW | Run `npm run build` after changes |
| R-WIRE-1 | Decorator issues with self | MEDIUM | MEDIUM | Use inline wrapper approach |
| R-WIRE-1 | Circuit opens during normal ops | HIGH | LOW | Calibrate thresholds |
| R-ARCH-2 | Migration breaks YAML | LOW | MEDIUM | Manual review after script |
| R-ARCH-2 | Routing rules break | MEDIUM | LOW | Update rules to unified vocab |
| R-INT-1 | Incorrect conflict absorption | HIGH | MEDIUM | Follow pr606-integration-analysis.md |
| R-INT-3 | Missed lock rename | HIGH | LOW | grep verification post-rebase |

---

## Testing Requirements

### Unit Tests Required

1. **B3-C:**
   - `tests/unit/test_pipeline_router.py` - Test SSE endpoint
   - `src/gaia/apps/webui/src/components/__tests__/PipelinePanel.test.tsx`
   - `src/gaia/apps/webui/src/services/__tests__/pipeline.test.ts`

2. **WIRE-1:**
   - `tests/unit/test_routing_engine_resilience.py` - Test resilience behavior

3. **ARCH-2:**
   - `tests/unit/test_capability_migration.py` - Test migration correctness

### Integration Tests Required

1. **B3-C:**
   - End-to-end pipeline execution via Agent UI
   - SSE event streaming verification

2. **WIRE-1:**
   - Circuit breaker recovery testing
   - Bulkhead permit release testing

3. **ARCH-2:**
   - Routing engine with unified capabilities
   - Agent registry validation

---

## Quality Gates

### Before Committing

- [ ] All new files have copyright headers
- [ ] TypeScript compiles without errors
- [ ] Python linting passes (`python util/lint.py --all`)
- [ ] Unit tests pass (`python -m pytest tests/unit/ -xvs`)

### Before Merge

- [ ] All acceptance criteria met for each issue
- [ ] Frontend builds successfully (`npm run build`)
- [ ] Integration tests pass
- [ ] Documentation updated
- [ ] Migration script verified (for ARCH-2)

---

## Dependencies Summary

```
Phase 1 (UNBLOCKED - Execute First)
├── B3-C (Agent UI Pipeline)
├── WIRE-1 (Resilience Wiring)
└── ARCH-2 (Capability Migration)

Phase 2 (BLOCKED on PR #606)
├── INT-1 (Conflict Absorption)
└── INT-3 (Lock Rename)

Phase 3 (DECISION Required)
├── ARCH-1 (Python vs. MD)
└── WIRE-3 (Orchestrator Scope)

Phase 4 (BLOCKED on Phase 1 + PR #606)
├── BU-1 (MemoryMixin)
├── BU-2 (GoalStore)
├── BU-4 (SystemDiscovery)
└── BU-5 (GapCache)
```

---

## File Reference Summary

### All Implementation Plan Documents

1. `implementation-plan-B3-C-agent-ui-pipeline.md` - Agent UI pipeline integration
2. `implementation-plan-WIRE-1-resilience-wiring.md` - Resilience primitives wiring
3. `implementation-plan-ARCH-2-capability-migration.md` - Capability vocabulary migration
4. `IMPLEMENTATION-SUMMARY-senior-developer.md` - This summary document

### Input Documents (Already Read)

1. `docs-branch-matrix-outstanding-issues-analysis.md` - Strategic issues assessment
2. `docs/program-management-plan-pipeline-orchestration.md` - Program management plan

### Key Source Files Referenced

1. `src/gaia/pipeline/orchestrator.py` - PipelineOrchestrator implementation
2. `src/gaia/pipeline/routing_engine.py` - Routing engine (WIRE-1 target)
3. `src/gaia/ui/routers/pipeline.py` - Pipeline router (B3-C target)
4. `src/gaia/ui/server.py` - Agent UI server
5. `src/gaia/resilience/*.py` - Resilience primitives
6. `config/agents/*.yaml` (18 files) - Agent capability definitions
7. `docs/spec/unified-capability-model.md` - Unified capability vocabulary

---

## Next Steps

1. **Review** all implementation plans for accuracy
2. **Prioritize** Phase 1 tasks (B3-C, WIRE-1, ARCH-2)
3. **Execute** unblocked tasks in order of priority
4. **Monitor** PR #606 status for blocked tasks
5. **Escalate** architecture decisions to enhanced-senior-developer

---

## Handoff to enhanced-senior-developer

**For Code Review:**
All implementation plans are ready for review. Each plan includes:
- Current state analysis
- Specific code changes
- Test strategy
- Risk assessment
- Acceptance criteria

**For Architectural Decisions:**
Please review and provide decisions on:
- **ARCH-1:** Python-class vs. MD-config agent architecture
- **WIRE-3:** AgentOrchestrator scope (is PipelineOrchestrator sufficient?)

**Questions for Architecture Lead:**
See `docs-branch-matrix-outstanding-issues-analysis.md` Section 7 for detailed questions.

---

**Status:** All implementation plans complete and ready for execution.  
**Total Estimated Effort:** 24-32 hours (excluding Phase 6 enhancements)
