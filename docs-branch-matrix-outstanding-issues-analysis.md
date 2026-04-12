# Docs-Branch-Matrix Outstanding Issues Analysis

**Document Type:** Strategic Issues Assessment for Software Program Manager
**Branch:** `feature/pipeline-orchestration-v1`
**Analysis Date:** 2026-04-11
**Prepared By:** Dr. Sarah Kim, Technical Product Strategist & Engineering Lead
**Purpose:** Comprehensive analysis of outstanding issues in branch-change-matrix.md and related documentation for handoff to software-program-manager agent

---

## Executive Summary

This analysis identifies **19 outstanding issues** across the GAIA repository documentation and codebase that require resolution before the `feature/pipeline-orchestration-v1` branch can be considered merge-ready to `main`. The issues span:

- **Documentation consistency gaps** (YAML frontmatter, spec coherence)
- **Integration prerequisites** (PR #606 and PR #720 conflict resolution)
- **Architectural deviations** (Python-class vs. MD-config agents)
- **Runtime dependencies** (MCP availability, Claude Code coupling)
- **Unwired resilience patterns** (circuit breakers, bulkheads not integrated)

**Critical Path:** Issues OI-5, OI-7, OI-9, and B3-C form the current merge-blocking critical path. All other issues are either post-merge work (Phase 6) or have documented mitigations.

---

## Section 1: Issue Inventory by Category

### 1.1 Documentation Issues (P0 - Merge Blocking)

| ID | Issue | Status | Evidence | Required Action |
|----|-------|--------|----------|-----------------|
| **DOC-1** | YAML frontmatter missing from 9 spec files | OPEN (Phase 6 resolution pending) | `docs/spec/agent-ui-eval-kpi-reference.md`, `agent-ui-eval-kpis.md`, `gaia-loom-architecture.md`, `nexus-gaia-native-integration-spec.md`, `pipeline-metrics-competitive-analysis.md`, `pipeline-metrics-kpi-reference.md`, `phase5_multi_stage_pipeline.md`, `component-framework-design-spec.md`, `component-framework-implementation-plan.md` all start with `#` heading, no `---` frontmatter | Add 3-line YAML frontmatter (`---`, `title: <name>`, `---`) to all 9 files. Estimated effort: 30 minutes. |
| **DOC-2** | Design spec coherence check incomplete | RESOLVED (Phase 6, commit `e28a922`) | `agent-ecosystem-design-spec.md` Section 2.2 "What Is Missing" items 3, 4, 5 updated to show "DELIVERED", "RESOLVED", "PARTIALLY RESOLVED" | Verify commit `e28a922` is present on branch. No further action required. |
| **DOC-3** | `branch-change-matrix.md` requires Phase 5/6 updates | PARTIAL | `phase5-update-manifest.md` Sections A-G provide exact edit instructions. Matrix shows 74 commits but some Open Item statuses outdated | Execute Sections A-G of `phase5-update-manifest.md` to synchronize matrix with current branch state. |

### 1.2 Integration Conflicts (P0 - Pre-Merge Coordination)

| ID | Issue | Status | Evidence | Required Action |
|----|-------|--------|----------|-----------------|
| **INT-1** | PR #606 HIGH-severity conflict absorption | OPEN (blocked on PR #606 merge) | `pr606-integration-analysis.md` Section 4 identifies 4 HIGH conflicts: `_chat_helpers.py` (1,144 lines vs. PR's +38), `database.py` (787 lines vs. PR's +31/-3), `sse_handler.py` (950 lines vs. PR's +115/-1), `routers/mcp.py` (425 lines vs. PR's +206/-1) | After PR #606 merges: rebase branch, absorb all 4 conflict targets into our larger modules. Estimated effort: 3.5 engineer-hours. See `pr606-integration-analysis.md` Section 9 P1 steps. |
| **INT-2** | PR #720 registry naming collision | OPEN (architectural decision required) | `pr720-integration-analysis.md` Section 4.1: both branches created `src/gaia/agents/registry.py` from scratch with incompatible designs (UI-facing vs. pipeline-facing) | Decision required: (a) rename our registry to `PipelineAgentRegistry` and relocate to `src/gaia/pipeline/agent_registry.py`, OR (b) rename PR #720's to `AgentDiscovery`. Recommend option (a) to preserve PR #720's user-facing naming. |
| **INT-3** | `model_load_lock` rename in `_chat_helpers.py` | OPEN (runtime break if skipped) | `pr720-integration-analysis.md` Section 4.3: PR #720 renames `_model_load_lock` to `model_load_lock` (public API). Our branch references private name at line 67. | After rebase: `grep -r "_model_load_lock" src/gaia/ui/` and update all occurrences to `model_load_lock`. Guaranteed `AttributeError` if skipped. |

### 1.3 Architectural Issues (P1 - Post-Merge Resolution)

| ID | Issue | Status | Evidence | Required Action |
|----|-------|--------|----------|-----------------|
| **ARCH-1** | Python-class vs. MD-config agent deviation | OPEN (design fork) | `phase5-update-manifest.md` Section F: Phase 5 built Python classes (`DomainAnalyzer(Agent)`, etc.) rather than MD-config files (`config/agents/domain-analyzer.md`) that `agent-ecosystem-design-spec.md` specified | Architectural decision needed: Are Python classes the permanent approach, or is MD-config still the target? If permanent, update design spec Section 5 to reflect. If interim, complete MD-config migration (Tasks 1-6 of `senior-dev-work-order.md`). |
| **ARCH-2** | Capability vocabulary bifurcation | PARTIALLY RESOLVED (Phase 6) | `branch-change-matrix.md` OI-4: 5 Python stage agents have MD configs with aligned vocabularies. 18 legacy YAML files remain with divergent vocabularies. Phase 6 commit `41ee396` added `unified-capability-model.md` but migration not executed | Execute migration: either (a) update 18 YAML files to match unified vocabulary, OR (b) migrate all to `.md` format. Risk reduced to MEDIUM with clear migration path documented. |
| **ARCH-3** | GapDetector Claude Code dependency | DOCUMENTED with mitigation | `branch-change-matrix.md` OI-8: `GapDetector` invokes `master-ecosystem-creator.md` (Claude Code subagent), creating external dependency. MCP availability check added (`gap_detector.py:278`), documentation added to `config/agents/gap-detector.md` and `docs/guides/auto-spawn-pipeline.mdx` | For standalone deployments: (a) pre-generate agents manually, (b) set `auto_spawn=False`, or (c) implement alternative agent generation. No code fix required - constraint is documented. |

### 1.4 Unwired Systems (P1 - Post-Merge Integration)

| ID | Issue | Status | Evidence | Required Action |
|----|-------|--------|----------|-----------------|
| **WIRE-1** | Resilience primitives not wired into pipeline engine | OPEN (medium risk) | `branch-change-matrix.md` OI-3: `CircuitBreaker`, `Bulkhead`, `Retry` exist in `src/gaia/resilience/` but not imported/invoked in `engine.py`, `loop_manager.py`, or `routing_engine.py` | Wire resilience wrappers around agent invocation call sites in `routing_engine.py`'s `route()` method. Target 3 call sites. Estimated effort: 2-3 hours. |
| **WIRE-2** | Agent UI has zero pipeline integration | OPEN (new OI, Session-2) | `branch-change-matrix.md` OI-19 (B3-C): `gaia pipeline` CLI is wired, but Agent UI (`gaia chat --ui`) has no route, widget, or chat-tool integration for pipeline. `RoutingAgent` does not route to pipeline. | Minimal implementation: (a) add `POST /pipeline/run` SSE endpoint in `src/gaia/ui/routers/pipeline.py`, (b) mount in `server.py`, (c) add pipeline panel component in `src/gaia/apps/webui/`. Priority: P1 before any Agent UI demo. |
| **WIRE-3** | `AgentOrchestrator` not built (routing level) | PARTIALLY RESOLVED | `branch-change-matrix.md` OI-1: `PipelineOrchestrator` delivers 5-stage orchestration with gap detection, but `RoutingAgent` retains hardcoded `CodeAgent` default fallback | These are now decoupled: pipeline orchestration resolved; routing-level dynamic selection remains open. Decision needed: is `PipelineOrchestrator` sufficient, or is routing-level `AgentOrchestrator` still required? |

### 1.5 Build-Upon Opportunities (P2 - Phase 6, Post-PR #606)

These are not blockers but represent strategic technical debt if deferred indefinitely.

| ID | Opportunity | Dependency | Value | Effort |
|----|-------------|------------|-------|--------|
| **BU-1** | Add `MemoryMixin` to pipeline stage agents | PR #606 on main | Eliminates redundant re-analysis across sessions | 4-6 hours |
| **BU-2** | Wire `GoalStore` into `PipelineOrchestrator` | PR #606 on main | Surfaces pipeline runs in Memory Dashboard goal tracker | 3-4 hours |
| **BU-3** | `AgentLoop`/`PipelineExecutor` convergence design | Coordination with kovtcharov | Prevents divergence of two autonomous execution runtimes | Design session required before implementation |
| **BU-4** | `SystemDiscovery` hardware calibration for `DomainAnalyzer` | PR #606 on main | Improves agent tier recommendations for AMD hardware users | 2-3 hours, highest value/effort ratio |
| **BU-5** | `GapDetector` memory caching with `MemoryStore` supersession | PR #606 + BU-1 complete | Eliminates redundant filesystem scans | 3-4 hours |
| **BU-6** | Declarative memory tool invocations in component templates | Design session with kovtcharov | Makes memory a first-class template capability | Design-dependent |

---

## Section 2: Root Cause Analysis

### 2.1 Documentation Gaps (DOC-1, DOC-3)

**Root Cause:** Phase 5 and Phase 6 delivery velocity exceeded documentation infrastructure capacity. The `phase5-update-manifest.md` (generated 2026-04-08) explicitly identifies 9 files requiring frontmatter updates, but the `quality-reviewer` agent execution was interrupted before completion.

**Contributing Factors:**
- Phase 5 delivered 9 commits in rapid succession (57ee63d → fa3ef98)
- Three new spec documents (`phase5_multi_stage_pipeline.md`, `component-framework-design-spec.md`, `component-framework-implementation-plan.md`) were added without frontmatter validation gate
- No CI/CD check exists for YAML frontmatter presence in `docs/spec/*.md`

**Recommended Fix:** Add a pre-commit hook or CI check that validates YAML frontmatter presence in all `docs/*.md` and `docs/spec/*.md` files.

### 2.2 Integration Conflicts (INT-1, INT-2, INT-3)

**Root Cause:** Two independent PRs (#606 and #720) developed in parallel with our branch, each modifying overlapping files without cross-branch coordination.

**INT-1 Specifics:** PR #606 (37,040 additions, 75 files) delivers agent memory infrastructure. Our branch created comprehensive new modules (`_chat_helpers.py` at 1,144 lines, `database.py` at 787 lines) where PR #606 made targeted additions against smaller upstream versions.

**INT-2 Specifics:** Both our branch and PR #720 created `src/gaia/agents/registry.py` as a new file. Git cannot auto-merge two new files with completely different content.

**INT-3 Specifics:** PR #720 renamed `_model_load_lock` to `model_load_lock` (public API change). Our branch references the private name. The rename is intentional for cross-module access in `server.py` boot-time preload.

**Recommended Fix Sequence:**
1. Await PR #606 merge to `main` (gate for INT-1)
2. Rebase our branch onto updated `main`
3. Execute conflict absorption per `pr606-integration-analysis.md` Section 9
4. Await PR #720 merge decision on registry naming (INT-2)
5. Execute lock rename (INT-3) as part of rebase

### 2.3 Architectural Deviations (ARCH-1, ARCH-2, ARCH-3)

**Root Cause (ARCH-1):** Phase 5 implementation plan (`phase5-implementation-plan.md`) specified MD-format config agents in `config/agents/`. Implementation team built Python classes (`DomainAnalyzer(Agent)`, etc.) instead. This is a valid architectural choice but creates a discrepancy with the design spec.

**Evidence:** `agent-ecosystem-design-spec.md` Section 2.2 "What Is Missing" still lists Workflow Modeler, Loom Builder as "do not exist" when they exist as Python classes in `src/gaia/pipeline/stages/`.

**Root Cause (ARCH-2):** 18 legacy YAML files in `config/agents/` use freeform capability strings (`requirements-analysis`, `full-stack-development`) that don't match the formal vocabulary in `src/gaia/core/capabilities.py`. Phase 5 created MD configs for 5 Python stages with aligned vocabularies but didn't migrate legacy files.

**Root Cause (ARCH-3):** `GapDetector` invokes `master-ecosystem-creator.md` (Claude Code subagent) when gaps are detected. This was a design decision to leverage Claude Code's agent scaffolding capability rather than building a native Python agent generator.

**Recommended Resolution:**
- **ARCH-1:** Architecture lead decision required. If Python classes are permanent, update `agent-ecosystem-design-spec.md` Section 5 to reflect Python-class approach. If MD-config is still target, complete Tasks 1-6 of `senior-dev-work-order.md`.
- **ARCH-2:** Execute vocabulary migration per Phase 6 commit `41ee396`'s `unified-capability-model.md`. Low effort, reduces future routing confusion.
- **ARCH-3:** No code fix required. Document is sufficient. Consider adding runtime warning if `auto_spawn=True` but MCP not available.

### 2.4 Unwired Systems (WIRE-1, WIRE-2, WIRE-3)

**Root Cause (WIRE-1):** Resilience primitives (`CircuitBreaker`, `Bulkhead`, `Retry`) were delivered as standalone modules in Phase 4 Week 2 with full test coverage but no integration wiring into pipeline engine call sites. This is a common pattern: infrastructure first, integration second.

**Root Cause (WIRE-2):** Pipeline orchestration was developed as CLI-first capability. Agent UI integration was scoped as Phase 6 work, contingent on PR #606 merge. The `RoutingAgent`'s hardcoded `CodeAgent` default means natural-language pipeline requests via chat are silently routed to `CodeAgent` instead of the pipeline engine.

**Root Cause (WIRE-3):** `PipelineOrchestrator` was delivered as a superset of the originally-scoped `AgentOrchestrator`. The routing-level `AgentOrchestrator` (for dynamic agent selection in `RoutingAgent`) was deprioritized when `PipelineOrchestrator` delivered capability-based orchestration at a higher layer.

**Recommended Wiring Sequence:**
1. **WIRE-1:** Wire resilience primitives into `routing_engine.py`'s `route()` method. Wrap each agent invocation with `CircuitBreaker.call()`, `Bulkhead.isolate()`, and `Retry.with_backoff()`.
2. **WIRE-2:** Implement minimal Agent UI pipeline integration (SSE endpoint, router mount, panel component). Then update `RoutingAgent` to route pipeline-capable requests to `PipelineOrchestrator`.
3. **WIRE-3:** Decision required: is `PipelineOrchestrator` sufficient, or is routing-level `AgentOrchestrator` still needed? If needed, scope as thin adapter: `PipelineOrchestrator.select_agent()` returns agent ID, then instantiate via PR #720's `AgentRegistry.get(agent_id).factory()`.

---

## Section 3: Dependencies and Ordering Constraints

### 3.1 Pre-Merge Dependencies (Must Complete Before Merge)

```
DOC-1 (YAML frontmatter) ─────────────────────────────┐
                                                      ├───→ Merge Review Gate
DOC-2 (Design spec coherence) ────────────────────────┘

INT-1 (PR #606 conflict absorption) ──────────────────┐
                                                      ├───→ Rebase Completion
INT-3 (model_lock_lock rename) ───────────────────────┘

B3-C (Agent UI pipeline integration) ─────────────────┘
```

### 3.2 Post-Merge Dependencies (Phase 6)

```
PR #606 merged to main
       │
       ▼
┌──────────────────────────────────────────┐
│  Rebase onto main + conflict resolution  │
│  (INT-1, INT-2, INT-3)                   │
└──────────────────────────────────────────┘
       │
       ├─────────────┬─────────────┬──────────────┐
       ▼             ▼             ▼              ▼
┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐
│ BU-1      │ │ BU-2      │ │ BU-4      │ │ WIRE-1    │
│ MemoryMix │ │ GoalStore │ │ SystemDis │ │ Resilience│
└───────────┘ └───────────┘ └───────────┘ └───────────┘
       │             │             │
       ▼             ▼             ▼
┌───────────┐ ┌───────────┐ ┌───────────┐
│ BU-5      │ │ BU-3      │ │ WIRE-2    │
│ GapCache  │ │ Converge  │ │ Agent UI  │
└───────────┘ └───────────┘ └───────────┘
       │
       ▼
┌───────────┐
│ BU-6      │
│ Templates │
└───────────┘
```

### 3.3 Critical Path Summary

**Merge-Blocking (P0):**
1. DOC-1: Add YAML frontmatter to 9 spec files (30 minutes)
2. DOC-3: Update `branch-change-matrix.md` per `phase5-update-manifest.md` Sections A-G (1 hour)
3. INT-1: Absorb PR #606 conflicts after merge (3.5 hours)
4. INT-3: Update `_model_load_lock` references (15 minutes)
5. B3-C: Implement minimal Agent UI pipeline integration (4-6 hours)

**Post-Merge (P1):**
1. ARCH-1: Architecture decision on Python-class vs. MD-config (decision only)
2. ARCH-2: Execute capability vocabulary migration (2-3 hours)
3. WIRE-1: Wire resilience primitives (2-3 hours)
4. WIRE-3: Decide on routing-level `AgentOrchestrator` (decision only)

**Phase 6 (P2):**
1. BU-1 through BU-6 (design-dependent, ~20-25 hours total)

---

## Section 4: Risk Register

| Risk ID | Trigger | Impact | Likelihood | Mitigation |
|---------|---------|--------|------------|------------|
| **R-DOC-1** | Merge without YAML frontmatter fix | Mintlify rendering fails for 9 spec pages | High | P0 fix: 30-minute edit, add CI check |
| **R-INT-1** | Incorrect `_chat_helpers.py` absorption | Memory operations fail silently at runtime | Medium | Follow `pr606-integration-analysis.md` Section 4.1 exact steps |
| **R-INT-2** | Incorrect `database.py` schema absorption | `OperationalError: no such column: knowledge.embedding` | High | Get Q1 answer from kovtcharov before rebase (shared vs. separate SQLite file) |
| **R-INT-3** | Missed `model_load_lock` rename | `AttributeError` at runtime | High | `grep -r "_model_load_lock" src/gaia/ui/` post-rebase |
| **R-ARCH-1** | No architectural decision on Python vs. MD | Confusion for future agent developers | Medium | Architecture lead decision required within 1 week of merge |
| **R-WIRE-2** | No Agent UI pipeline integration | Users cannot invoke pipeline via chat interface | Medium | P1 fix: implement minimal SSE endpoint + panel |
| **R-BU-3** | No convergence design session | `AgentLoop` and `PipelineExecutor` diverge permanently | Medium | Schedule design session before Phase 6 kickoff |

---

## Section 5: Recommended Action Plan

### 5.1 Immediate Actions (Before Merge)

| Step | Action | Owner | Effort | Blocks |
|------|--------|-------|--------|--------|
| 1 | Add YAML frontmatter to 9 spec files (DOC-1) | Technical Writer | 30 min | Merge review |
| 2 | Update `branch-change-matrix.md` per `phase5-update-manifest.md` Sections A-G (DOC-3) | Documentation | 1 hour | Merge review |
| 3 | Await PR #606 merge to `main` | kovtcharov | N/A | INT-1, INT-3 |
| 4 | Rebase onto updated `main`, resolve INT-1/INT-3 | Development | 3.5 hours | PR submission |
| 5 | Implement Agent UI pipeline integration (B3-C) | Frontend + Backend | 4-6 hours | User-facing demo |

### 5.2 Post-Merge Actions (Within 1 Week)

| Step | Action | Owner | Effort | Notes |
|------|--------|-------|--------|-------|
| 6 | Architecture decision on Python vs. MD agents (ARCH-1) | Architecture Lead | Decision only | Gates spec update |
| 7 | Execute capability vocabulary migration (ARCH-2) | Development | 2-3 hours | Low effort, reduces confusion |
| 8 | Wire resilience primitives (WIRE-1) | Development | 2-3 hours | Medium risk mitigation |
| 9 | Decide on routing-level `AgentOrchestrator` (WIRE-3) | Architecture Lead | Decision only | Gates BU-3 design session |

### 5.3 Phase 6 Actions (Within 1 Sprint)

| Step | Action | Owner | Effort | Dependency |
|------|--------|-------|--------|------------|
| 10 | Implement BU-1 (`MemoryMixin` for stage agents) | Development | 4-6 hours | PR #606 on main |
| 11 | Implement BU-2 (`GoalStore` wiring) | Development | 3-4 hours | PR #606 on main |
| 12 | Hold BU-3 convergence design session | Joint (kovtcharov + team) | 2 hours | Schedule before implementation |
| 13 | Implement BU-4 (`SystemDiscovery` calibration) | Development | 2-3 hours | PR #606 on main, highest value/effort |
| 14 | Implement BU-5 (`GapDetector` memory caching) | Development | 3-4 hours | Requires BU-1 complete |
| 15 | Design session for BU-6 (declarative memory tools) | Joint | Design only | Phase 6/7 boundary |

---

## Section 6: File References

### 6.1 Primary Analysis Documents

| File | Purpose | Key Sections |
|------|---------|--------------|
| `docs/reference/branch-change-matrix.md` | Authoritative change reference | Section 2 (Open Items), Section 3.13 (Phase 5 matrix) |
| `docs/spec/phase5-update-manifest.md` | Exact edit instructions for matrix/spec updates | Sections A-G (precise line-level edits) |
| `docs/reference/pr606-integration-analysis.md` | PR #606 conflict analysis | Section 3 (Conflict Matrix), Section 4 (HIGH conflicts), Section 9 (Action Plan) |
| `docs/reference/pr720-integration-analysis.md` | PR #720 conflict analysis | Section 2 (Overlap Surface), Section 4 (Conflict Analysis), Section 6 (Recommended Action Plan) |
| `MERGE_DECISION_pipeline-orchestration-v1.md` | Merge authorization document | Section: Open Items Status, Section: Post-Merge Action Items |
| `phase5-merge-verification.md` | Quality gate verification | Section: P0 Fixes Verification, Section: Blocking Issues |

### 6.2 Implementation Files

| File | Relevance |
|------|-----------|
| `src/gaia/pipeline/orchestrator.py` | `PipelineOrchestrator` implementation (518 LOC) |
| `src/gaia/pipeline/stages/*.py` | 5 Python stage agents (DomainAnalyzer, WorkflowModeler, LoomBuilder, GapDetector, PipelineExecutor) |
| `src/gaia/agents/registry.py` | Pipeline-facing agent registry (naming collision with PR #720) |
| `src/gaia/ui/_chat_helpers.py` | 1,144-line module (INT-1 conflict target) |
| `src/gaia/ui/database.py` | 787-line `ChatDatabase` class (INT-1 conflict target) |
| `src/gaia/ui/sse_handler.py` | 950-line SSE handler (INT-1 conflict target) |
| `src/gaia/ui/routers/mcp.py` | 425-line MCP catalog router (INT-1 conflict target) |
| `config/agents/*.yaml` | 18 legacy YAML files with divergent capability vocabulary |
| `config/agents/*.md` | 5 new MD configs for Python stage agents (aligned vocabulary) |

### 6.3 Documentation Files Requiring Updates

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
| `docs/spec/agent-ecosystem-design-spec.md` | ARCH-1 | Update Section 2.2, Section 5 (Python-class status) |
| `docs/reference/branch-change-matrix.md` | DOC-3 | Execute `phase5-update-manifest.md` Sections A-G |

---

## Section 7: Questions for Architecture Lead / Maintainers

The following decisions require input before merge or immediately post-merge:

**Q1 — Python-class vs. MD-config agents (ARCH-1).**
Phase 5 built Python classes (`DomainAnalyzer(Agent)`, etc.) rather than MD-config files (`config/agents/domain-analyzer.md`) that `agent-ecosystem-design-spec.md` specified. Is the Python-class approach the permanent architecture, or is MD-config still the target? This decision gates spec updates and Task 1-6 completion of `senior-dev-work-order.md`.

**Q2 — Routing-level `AgentOrchestrator` necessity (WIRE-3).**
`PipelineOrchestrator` delivers 5-stage orchestration with gap detection. Is a separate routing-level `AgentOrchestrator` (for dynamic agent selection in `RoutingAgent`) still required, or is `PipelineOrchestrator` sufficient? This decision gates BU-3 design session scheduling.

**Q3 — PR #720 registry naming (INT-2).**
Both our branch and PR #720 created `src/gaia/agents/registry.py`. Should we rename ours to `PipelineAgentRegistry` and relocate to `src/gaia/pipeline/agent_registry.py`, or should PR #720 rename to `AgentDiscovery`? Recommend option (a) to preserve PR #720's user-facing naming.

**Q4 — Agent UI pipeline integration priority (B3-C).**
Minimal implementation is 4-6 hours (SSE endpoint, router mount, panel component). Is this required before merge, or acceptable as P1 post-merge? Current status: CLI functional, UI silent.

---

## Section 8: Handoff to Software Program Manager

### 8.1 Summary for Program Manager

This analysis identifies 19 outstanding issues, of which **5 are merge-blocking (P0)**:

1. **DOC-1:** YAML frontmatter missing from 9 spec files (30-minute fix)
2. **DOC-3:** `branch-change-matrix.md` requires Phase 5/6 updates (1-hour fix)
3. **INT-1:** PR #606 HIGH-severity conflict absorption (3.5 hours, gated on PR #606 merge)
4. **INT-3:** `_model_load_lock` rename (15-minute fix, runtime break if skipped)
5. **B3-C:** Agent UI pipeline integration (4-6 hours, user-facing requirement)

**Recommended sequence:**
1. Execute DOC-1 and DOC-3 immediately (documentation fixes)
2. Await PR #606 merge to `main`
3. Rebase onto updated `main`, resolve INT-1 and INT-3
4. Implement B3-C (Agent UI integration)
5. Submit for merge review

**Post-merge priorities (Week 1):**
1. ARCH-1 decision (Python vs. MD agents)
2. ARCH-2 execution (capability vocabulary migration)
3. WIRE-1 execution (resilience wiring)
4. WIRE-3 decision (routing-level `AgentOrchestrator`)

**Phase 6 backlog (Sprint planning):**
- BU-1 through BU-6 (~20-25 hours total, design-dependent)

### 8.2 Recommended Next Agent Handoffs

1. **software-program-manager:** Review this analysis, prioritize issues, create sprint plan
2. **senior-developer:** Execute P0 technical fixes (INT-1, INT-3, B3-C)
3. **enhanced-senior-developer:** Architectural decisions (ARCH-1, WIRE-3)
4. **testing-quality-specialist:** Verify all fixes, add CI checks for YAML frontmatter
5. **quality-reviewer:** Final coherence check of all documentation
6. **technical-writer-expert:** Polish all updated documentation for Mintlify rendering

---

**Document Version:** 1.0
**Prepared By:** Dr. Sarah Kim, Technical Product Strategist & Engineering Lead (planning-analysis-strategist)
**Date:** 2026-04-11
**Next Reviewer:** software-program-manager

*All file paths are relative to repository root `C:\Users\antmi\gaia`.*
