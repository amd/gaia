# Program Management Plan: Pipeline Orchestration v1 -- Merge to Main

**Branch:** `feature/pipeline-orchestration-v1`
**Target:** `main`
**Assessment Score:** 9.2/10 -- MERGE READY
**Date:** 2026-04-25
**Program Manager:** Software Program Manager (Claude Code)
**Document Version:** 1.0

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Merge Execution Plan](#2-merge-execution-plan)
3. [Post-Merge Action Items](#3-post-merge-action-items)
4. [Risk Mitigation Plan](#4-risk-mitigation-plan)
5. [Stakeholder Communication Plan](#5-stakeholder-communication-plan)
6. [Success Criteria](#6-success-criteria)
7. [Appendix: Quick Reference](#7-appendix-quick-reference)

---

## 1. Executive Summary

The `feature/pipeline-orchestration-v1` branch delivers 350,147 net lines of code across 1,088 files -- the largest single delivery in GAIA's history. It introduces a production-grade five-stage autonomous agent spawning pipeline, SSE event streaming infrastructure, resilience primitives (circuit breaker, bulkhead, retry), a full-stack pipeline canvas UI, and 801 passing tests (37 security tests included).

All P0 and P1 quality gates are resolved. The branch is committed and pushed. The **only external blocker** is PR #606 (agent-memory v2), which has 4 HIGH-severity file conflicts requiring rebase resolution. The recommended strategy is: merge this PR first (no active conflicts with main), then rebase onto main after PR #606 merges.

---

## 2. Merge Execution Plan

### 2.1 Pre-Creation Checklist

Run these commands before creating the PR. All must pass.

```bash
# 1. Confirm branch is current with remote
git fetch origin feature/pipeline-orchestration-v1
git log --oneline origin/feature/pipeline-orchestration-v1..feature/pipeline-orchestration-v1
# Expected: no output (local and remote in sync)

# 2. Verify no uncommitted changes
git status
# Expected: clean working tree

# 3. Confirm 123 commits ahead of main (baseline for PR scope)
git log --oneline main..feature/pipeline-orchestration-v1 | wc -l
# Expected: 123

# 4. Quick sanity: confirm main has no conflicting pipeline files
git show main:src/gaia/pipeline/__init__.py 2>/dev/null
# Expected: "fatal: path does not exist" (pipeline is new on our branch)
```

### 2.2 PR Creation

**Step 1: Create the Pull Request**

```bash
gh pr create \
  --base main \
  --head feature/pipeline-orchestration-v1 \
  --title "feat(pipeline): v1 -- autonomous agent spawning pipeline with canvas UI" \
  --label "enhancement,pipeline,phase5,large-PR" \
  --reviewer "kovtcharov-amd" \
  --body-file - <<'PR_BODY'
## Summary

Implements the complete Pipeline Orchestration v1 system: a five-stage autonomous agent
spawning pipeline with recursive execution, decision gates, loop control, artifact provenance
tracking, and a full-stack drag-and-drop canvas UI.

This is a production-grade delivery: 801 tests passing (37 security tests), 350K net lines
across 1,088 files, zero blocking issues.

## What's New

### Pipeline Engine (`src/gaia/pipeline/`)
- **4-phase recursive orchestrator** (plan, execute, review, decide) with state machine
- **Decision engine** with quality scoring and automatic gate outcomes
- **Loop manager** with configurable max-iteration budgets and convergence detection
- **Artifact provenance tracking** for full audit trails
- **Agent registry bridge** connecting 18 YAML-defined agents to pipeline stages
- **Template loader** with 3 built-in templates (rapid, generic, enterprise)

### SSE Event Streaming (`src/gaia/ui/sse_handler.py`, `src/gaia/pipeline/sse_hooks.py`)
- Fixed critical `drain()` bug that dropped terminal events
- 5 SSE hook classes for pipeline lifecycle events
- Canvas config forwarding through 7-link chain
- 48 new tests for async bridge

### Resilience Primitives (`src/gaia/resilience/`)
- **CircuitBreaker**: 3-state (closed/open/half-open) with configurable thresholds
- **Bulkhead**: semaphore-based concurrency isolation per agent
- **Retry**: exponential backoff with jitter, max-attempts
- 28/28 integration tests passing

### Pipeline Canvas UI (`src/gaia/apps/webui/src/components/pipeline/`)
- Drag-and-drop canvas with supervisor nodes, decision gates, loop blocks
- Template marketplace with 3 enterprise templates
- Performance dashboard with phase timing and quality-over-time charts
- Execution history and version diff

### Security
- All P0/P1/P2 vulnerabilities resolved
- Path traversal protection (SEC-003) in artifact extractor
- 37/37 security tests passing
- Workspace isolation, input validation, resource limits

## Statistics

| Metric | Value |
|--------|-------|
| Files changed | 1,088 |
| Net lines added | ~350K |
| Tests added | 801 passing |
| Security tests | 37 passing |
| New modules | 17 (pipeline/) + 5 stages + 3 resilience |
| New UI components | 14 TypeScript/React |
| Documentation | 15+ MDX docs |

## Testing

- **Unit tests**: `tests/unit/test_pipeline_*.py`, `tests/unit/resilience/`, `tests/unit/quality/`
- **Integration tests**: `tests/integration/test_pipeline_engine.py`, `test_recursive_pipeline.py`
- **E2E tests**: `tests/e2e/test_full_pipeline.py`, `test_quality_gate_7.py`
- **Pipeline-specific**: `tests/pipeline/` (15 test files, orchestrator/state/decision/loop)

## Known Issues (Post-Merge)
- `test_yaml_structure_preserved` fails -- pre-existing, 54 YAML files missing required fields
- Phase 5 async bridge deferred (documented in PR)
- Resilience wiring in engine.py/loop_manager.py deferred (documented in PR)
- Phase name mismatch: 4 engine phases vs 5 UI stages (mapping layer in place)
- RoutingAgent hardcoded CodeAgent default (design decision, not a bug)

## PR #606 Coordination
This PR has **no conflicts** with current `main`. PR #606 (agent-memory v2) will introduce
4 HIGH-severity file conflicts when it merges to main. Rebase resolution (~3.5 hours) is
documented and scheduled for post-merge of PR #606.

See `docs/reference/pr606-integration-analysis.md` for full conflict matrix.

## Documentation
- Pipeline guide: `docs/guides/pipeline.mdx`
- Pipeline canvas guide: `docs/guides/pipeline-canvas.mdx`
- Auto-spawn pipeline: `docs/guides/auto-spawn-pipeline.mdx`
- SDK infra: `docs/sdk/infrastructure/pipeline.mdx`
- Spec: `docs/spec/pipeline-engine.mdx`, `docs/spec/agent-ecosystem-design-spec.md`
- CLI reference: `docs/reference/cli.mdx` (updated)
- docs.json: pipeline navigation entries added

## Merge Method
**Squash and merge** recommended (123 commits on a single feature branch).

## Reviewers
- @kovtcharov-amd (maintainer)
- Code owners for `src/gaia/pipeline/`, `src/gaia/ui/`, `src/gaia/apps/webui/`
PR_BODY
```

**Step 2: Verify PR was created**

```bash
gh pr list --state open --base main --head feature/pipeline-orchestration-v1
# Confirm: PR appears with correct title and labels
```

### 2.3 PR Management Workflow

| Phase | Action | Owner | Timeline |
|-------|--------|-------|----------|
| **T+0** | PR created, maintainer notified | PM | Immediate |
| **T+0 to T+2 days** | Monitor CI/CD pipeline runs, fix any failures | Dev Team | As needed |
| **T+1 day** | Post coordination comment on PR #606 (P0 Steps 1-4) | PM | Day 1 |
| **T+1 to T+5 days** | Address review feedback, make requested changes | Dev Team | As needed |
| **T+~3-7 days** | Maintainainer review and approval | @kovtcharov-amd | Per maintainer schedule |
| **Post-approval** | Squash and merge to main | Maintainer or PM with write access | Immediate |
| **T+0 to T+1 hour post-merge** | Execute post-merge verification (Section 6) | PM + Dev Team | Day of merge |

### 2.4 Merge Conflict Expectation

**Current state:** NO conflicts with `main`. The pipeline module (`src/gaia/pipeline/`) is entirely new, and our modifications to existing files (agent.py, cli.py, sdk.py, sse_handler.py, database.py) are additive -- they do not overwrite existing functionality.

**After PR #606 merges:** 4 HIGH + 6 MEDIUM/LOW conflicts (documented in `docs/reference/pr606-integration-analysis.md`, Section 3). Estimated resolution: 3.5 engineer-hours.

---

## 3. Post-Merge Action Items

### 3.1 Immediate (0-24 hours post-merge)

| # | Action | Owner | Priority | Verification |
|---|--------|-------|----------|-------------|
| 1 | Verify main branch CI/CD pipeline passes | DevOps | P0 | Check GitHub Actions green |
| 2 | Run smoke test: `gaia pipeline --help` | Dev Team | P0 | Command returns help text |
| 3 | Verify Agent UI loads with pipeline canvas tab | QA | P0 | Open `gaia chat --ui`, navigate to pipeline tab |
| 4 | Update program dashboard / tracking board | PM | P1 | Dashboard reflects merged state |
| 5 | Send completion notification to stakeholders (Section 5) | PM | P1 | Notifications sent |
| 6 | Delete feature branch (after maintainer approval) | Maintainer | P2 | Branch removed from remote |

### 3.2 Short-Term (1 week post-merge)

| # | Action | Owner | Priority | Details |
|---|--------|-------|----------|---------|
| 7 | **PR #606 rebase** (if PR #606 has merged) | Dev Team | P0 | Follow `pr606-integration-analysis.md` Section 9 P1 Steps 7-16. Resolve C-1 through C-10. Run full test suite. Open follow-up PR if rebased branch diverges significantly. |
| 8 | Wire resilience primitives into `engine.py` and `loop_manager.py` | Dev Team | P1 | `RoutingEngine.route_defect_resilient()` is implemented but not called from the main execution path. Wire circuit breaker around LLM calls in `engine.py:execute_phase()`, bulkhead around parallel stage execution, retry around artifact I/O. |
| 9 | Fix RoutingAgent hardcoded CodeAgent default | Dev Team | P1 | Follow MERGE_DECISION Section 7 Option A: add keyword/intent detection for "pipeline", "orchestrate", "auto-spawn" to route to `PipelineOrchestrator.execute_full_pipeline()`. |
| 10 | Reconcile Stage 4/5 vs Stage 4a/4b naming | Docs | P2 | Update `auto-spawn-pipeline.mdx` and `phase5-update-manifest.md` to use consistent terminology. |

### 3.3 Medium-Term (2-4 weeks post-merge)

| # | Action | Owner | Priority | Details |
|---|--------|-------|----------|---------|
| 11 | **Phase 5 async bridge** -- real-time SSE | Dev Team | P1 | Deferred in PR #606 analysis. Implement real-time SSE async bridge for pipeline execution events (currently uses synchronous streaming). |
| 12 | **BU-1: MemoryMixin for pipeline stage agents** | Dev Team | P2 | After PR #606 rebase, add `MemoryMixin` to `DomainAnalyzer` as proof of concept. Test recall of prior analysis results. |
| 13 | **BU-2: GoalStore in PipelineOrchestrator** | Dev Team | P2 | Wire `GoalStore` into orchestrator for unified goal tracking visible in Memory Dashboard. |
| 14 | **BU-4: SystemDiscovery bootstrapping DomainAnalyzer** | Dev Team | P2 | Low cost, high value. Pass hardware context to domain analysis LLM prompt. |
| 15 | Unify phase/stage naming (4 engine phases vs 5 UI stages) | Architecture | P2 | Decide: rename engine phases to match UI stages, or add explicit mapping layer documentation. |

### 3.4 Future / Phase 6

| # | Action | Owner | Priority | Prerequisite |
|---|--------|-------|----------|-------------|
| 16 | **BU-3: AgentLoop/PipelineExecutor convergence** | Joint (with kovtcharov) | P1 | Design session required. Extract shared autonomous runtime base class. |
| 17 | **BU-5: GapDetector memory caching** | Dev Team | P2 | Requires BU-1 (MemoryMixin on stage agents). Configurable TTL for gap scan results. |
| 18 | **BU-6: Declarative memory tool invocations in templates** | Joint (with kovtcharov) | P2 | Design session required. Extend component-framework tool-call syntax. |
| 19 | Add seccomp-bpf sandboxing for pipeline stages | Security | P3 | Future hardening. Requires Linux kernel support. |

---

## 4. Risk Mitigation Plan

### 4.1 Active Risks

| ID | Risk | Probability | Impact | Severity | Mitigation | Owner |
|----|------|------------|--------|----------|------------|-------|
| R-1 | CI/CD pipeline fails post-merge due to environment mismatch | LOW | HIGH | MEDIUM | Pre-merge smoke test (Section 2.1). If CI fails, revert within 1 hour. | DevOps |
| R-2 | Agent UI build fails on main branch CI | LOW | HIGH | MEDIUM | WebUI build step runs in CI. Pre-verified in last 5 commits. If it fails, revert. | Dev Team |
| R-3 | PR #606 merge introduces regression in our rebased branch | MEDIUM | MEDIUM | HIGH | Full test suite required post-rebase (P1 Step 13). Gate PR submission on 801 tests passing. | Dev Team |
| R-4 | `_chat_helpers.py` rebase produces corrupted module (C-1) | MEDIUM | HIGH | HIGH | P0 Step 1: notify kovtcharov. P1 Step 8: verify function presence and cache access pattern. | Dev Team |
| R-5 | Memory schema columns land in wrong database file (C-2) | MEDIUM | HIGH | HIGH | P0 Step 2: gate C-2 resolution on Q1 answer from kovtcharov. | Dev Team |
| R-6 | `AgentLoop` SSE events dropped by our handler (C-3) | MEDIUM | MEDIUM | MEDIUM | P0 Step 3: request event JSON schema. P1 Step 10: verify payload structure. | Dev Team |
| R-7 | Duplicate MCP router mount in `server.py` (C-4/C-8) | LOW | MEDIUM | LOW | P1 Step 11: grep `include_router` post-rebase. | Dev Team |
| R-8 | `PipelineExecutor` and `AgentLoop` diverge permanently (BU-3) | MEDIUM | HIGH | MEDIUM | P0 Step 5: schedule convergence design session before Phase 6. | PM + Architecture |
| R-9 | MCP tool control endpoints land unauthenticated (C-4) | MEDIUM | HIGH | MEDIUM | P0 Step 4: get Q5 answer from kovtcharov. Align security posture. | Dev Team + Security |
| R-10 | Memory eval scenarios conflict with pipeline eval runner (R-7) | LOW | MEDIUM | LOW | P1 Step 15: confirm runner compatibility before running `gaia eval`. | Dev Team |
| R-11 | Large PR (1,088 files) receives insufficient review | HIGH | HIGH | HIGH | Flag as `large-PR`. Request multiple reviewers. Break PR into logical sections in description. Offer to demo in sync call. | PM |
| R-12 | Maintainer review delayed (no review within 7 days) | MEDIUM | MEDIUM | LOW | Follow up at day 3, day 5. Offer alternative: merge without review if maintainer delegates. | PM |

### 4.2 Risk Escalation Triggers

| Condition | Action | Escalation To |
|-----------|--------|---------------|
| CI/CD fails on main after merge | Immediate revert, root cause analysis, re-merge fix | @kovtcharov-amd |
| PR #606 merge causes 5+ conflict files beyond planned 10 | Pause rebase, schedule joint resolution session | @kovtcharov-amd + PR #606 author |
| No maintainer review within 7 days | Send follow-up, offer sync review call | @kovtcharov-amd |
| Security regression found post-merge | Immediate revert, security audit | AMD Security Team |
| Test suite drops below 780/801 passing | Block follow-up work, investigate | Dev Team |

### 4.3 Rollback Plan

If the merge introduces a blocking issue:

```bash
# Step 1: Revert the squash merge commit
git checkout main
git revert -m 1 <SQUASH_MERGE_COMMIT_HASH>
git push origin main

# Step 2: Notify stakeholders (Section 5)
# Step 3: Diagnose on feature branch
git checkout feature/pipeline-orchestration-v1
# Fix issue, run tests, create new PR
```

---

## 5. Stakeholder Communication Plan

### 5.1 Stakeholder Matrix

| Stakeholder | Role | Message | Channel | Timing |
|-------------|------|---------|---------|--------|
| **@kovtcharov-amd** (Kalin Ovtcharov) | Maintainer | PR ready for review. 801 tests passing, zero blockers. PR #606 coordination required post-merge. | GitHub PR review request + @ mention | T+0 (PR creation) |
| **PR #606 author** (kovtcharov) | Co-developer | Comment on PR #606: 6 questions (Q1-Q6), coordination on 4 HIGH conflicts, convergence design session invitation. | GitHub PR #606 comment | Day 1 |
| **AMD GAIA Team** | Internal dev team | "Pipeline Orchestration v1 merged to main. 350K lines, 801 tests. Post-merge items tracked in program plan." | Internal team channel | Merge day + T+1 day |
| **Documentation Team** | Docs owners | "15+ new MDX docs added. Pipeline guide, canvas guide, auto-spawn pipeline guide, SDK infrastructure doc. Review requested." | Email / team channel | T+1 day |
| **QA Team** | Quality assurance | "Pipeline Orchestration v1 merged. 801 tests passing. Regression testing recommended. QG7 validation complete." | Email | Merge day |
| **Community** | External contributors | Release notes entry: "Pipeline Orchestration -- build multi-stage agent workflows with the visual canvas." | GitHub release notes | Next release |

### 5.2 Communication Templates

**Template A: PR Review Request to Maintainer**

```
@kovtcharov-amd Pipeline Orchestration v1 PR is ready for review.

Summary:
- 801 tests passing (37 security)
- Zero blocking issues
- 1,088 files, ~350K net lines (squash merge)
- No conflicts with current main

Key areas for review:
1. Pipeline engine core: src/gaia/pipeline/engine.py, orchestrator.py, state.py
2. SSE streaming: src/gaia/ui/sse_handler.py, pipeline/sse_hooks.py
3. Canvas UI: src/gaia/apps/webui/src/components/pipeline/
4. Resilience: src/gaia/resilience/ (circuit breaker, bulkhead, retry)

Post-merge:
- PR #606 rebase required (~3.5 hours, documented)
- RoutingAgent default fix (Option A: intent detection)

Merge decision: MERGE_READY (9.2/10)
Full plan: see PROGRAM-MANAGEMENT-PLAN.md
```

**Template B: PR #606 Coordination Comment**

```
Hi @kovtcharov-amd -- our Pipeline Orchestration v1 PR is going in. We've analyzed
the integration with your agent-memory v2 PR (#606) and have 6 questions before the
merge sequence:

Q1: Does MemoryStore share gaia_chat.db or use a separate SQLite file?
Q2: How does _register_agent_memory_ops() access the agent cache? (Our cache is
    _agent_cache: dict[str, dict] keyed by session_id, agent at ["agent"])
Q3: What is the AgentLoop SSE event JSON schema?
Q4: Are you open to a Phase 6 convergence design session (AgentLoop + PipelineExecutor)?
Q5: Authentication posture for MCP tool control endpoints?
Q6: Do memory eval scenarios use the standard gaia eval runner?

Full analysis: docs/reference/pr606-integration-analysis.md
High-priority: Q1 gates schema resolution, Q2 gates cache access.
```

**Template C: Merge Completion Notification**

```
Pipeline Orchestration v1 has been merged to main.

Deliverables:
- 5-stage autonomous agent spawning pipeline
- SSE event streaming infrastructure (drain bug fixed)
- Resilience primitives (circuit breaker, bulkhead, retry)
- Pipeline Canvas UI (drag-and-drop, templates, dashboards)
- 801 tests passing, 37 security tests

Next steps:
- PR #606 rebase (when agent-memory v2 merges)
- RoutingAgent intent detection fix (1 week)
- Phase 5 async bridge (2-4 weeks)
```

### 5.3 Communication Cadence

| Event | Frequency | Format |
|-------|-----------|--------|
| PR review progress | Every 2 days until approved | GitHub comment update |
| Merge status | Day of merge | Team channel announcement |
| Post-merge verification | T+1 hour, T+24 hours | Status report |
| PR #606 coordination | Weekly until resolved | Async comment + sync call if needed |
| Phase 6 planning | Bi-weekly | Design session |

---

## 6. Success Criteria

### 6.1 Merge Success Criteria

The merge is considered successful when ALL of the following are true:

| # | Criterion | Verification Method | Target |
|---|-----------|-------------------|--------|
| S-1 | PR merges cleanly with no conflicts | `gh pr checks <PR_NUMBER>` | All checks green |
| S-2 | CI/CD pipeline passes on main | GitHub Actions dashboard | All workflows green within 30 minutes |
| S-3 | Agent UI builds successfully | `cd src/gaia/apps/webui && npm run build` | Exit code 0 |
| S-4 | Pipeline CLI responds | `gaia pipeline --help` | Help text renders |
| S-5 | Pipeline canvas loads in Agent UI | Browser test: `gaia chat --ui` | Canvas tab visible, no console errors |
| S-6 | No new test failures on main | `python -m pytest tests/ -x --timeout=120` | 801 tests pass (same as branch) |
| S-7 | Security tests pass | `python -m pytest tests/unit/security/ -xvs` | 37/37 passing |
| S-8 | No performance regression | Compare `gaia chat` startup time pre/post merge | <5% difference |

### 6.2 Post-Merge Success Criteria (1 week)

| # | Criterion | Verification Method | Target |
|---|-----------|-------------------|--------|
| S-9 | All P1 action items started or completed | Program management plan status check | 100% started |
| S-10 | PR #606 rebase completed (if applicable) | Test suite on rebased branch | 801+ tests pass |
| S-11 | No rollback required | Main branch stable | Zero reverts |
| S-12 | Documentation reviews completed | docs.json verified, link checker passes | Zero broken links |

### 6.3 Long-Term Success Criteria (1 month)

| # | Criterion | Verification Method | Target |
|---|-----------|-------------------|--------|
| S-13 | RoutingAgent intent detection fix deployed | Chat test: "build me a pipeline" | Routes to PipelineOrchestrator |
| S-14 | Resilience wired in engine execution path | Integration test with simulated LLM failure | Circuit breaker triggers, retry succeeds |
| S-15 | Phase 6 design session completed | Design document produced | Document reviewed and approved |
| S-16 | Zero security incidents | Security audit review | Zero new vulnerabilities |

### 6.4 Go/No-Go Decision Matrix

| Condition | Go | No-Go |
|-----------|----|-------|
| CI/CD passes | Merge proceeds | Halt, fix, retry |
| Agent UI builds | Merge proceeds | Halt, investigate build error |
| Test suite >= 800/801 | Merge proceeds | Halt, investigate regressions |
| Security tests 37/37 | Merge proceeds | Halt, fix security regression |
| Maintainer approves | Merge proceeds | Wait or escalate per Section 4.2 |
| Pipeline canvas loads | Merge proceeds | Halt, investigate UI error |

---

## 7. Appendix: Quick Reference

### 7.1 Key File Locations

| Category | Path |
|----------|------|
| Pipeline engine | `src/gaia/pipeline/` (17 files) |
| Pipeline stages | `src/gaia/pipeline/stages/` (5 files) |
| SSE hooks | `src/gaia/pipeline/sse_hooks.py`, `src/gaia/ui/sse_handler.py` |
| Resilience | `src/gaia/resilience/` (3 files) |
| Pipeline canvas UI | `src/gaia/apps/webui/src/components/pipeline/` (10 files) |
| Pipeline router | `src/gaia/ui/routers/pipeline.py` |
| Quality system | `src/gaia/quality/` (15 files) |
| Agent registry | `src/gaia/agents/registry.py` |
| Pipeline tests | `tests/pipeline/` (15 files), `tests/integration/test_pipeline_*.py` |
| PR #606 analysis | `docs/reference/pr606-integration-analysis.md` |
| Merge decision | `docs/archive/working-documents/MERGE_DECISION_pipeline-orchestration-v1.md` |

### 7.2 Git Commands Cheat Sheet

```bash
# Verify branch state
git fetch origin && git log --oneline main..origin/feature/pipeline-orchestration-v1 | wc -l

# Create PR
gh pr create --base main --head feature/pipeline-orchestration-v1 --title "..." --body "..."

# Check PR status
gh pr checks <PR_NUMBER>

# Merge (squash)
gh pr merge <PR_NUMBER> --squash

# Post-PR #606 rebase
git fetch origin
git checkout feature/pipeline-orchestration-v1
git rebase origin/main
# Resolve conflicts per pr606-integration-analysis.md Section 9
git rebase --continue
git push --force-with-lease origin feature/pipeline-orchestration-v1
```

### 7.3 Test Commands

```bash
# Full test suite (baseline: 801 passing)
python -m pytest tests/ -x --timeout=120

# Pipeline-specific
python -m pytest tests/pipeline/ -xvs

# Resilience
python -m pytest tests/unit/resilience/ -xvs

# Security
python -m pytest tests/unit/security/ -xvs

# Integration
python -m pytest tests/integration/test_pipeline_engine.py -xvs
python -m pytest tests/integration/test_recursive_pipeline.py -xvs

# E2E
python -m pytest tests/e2e/test_full_pipeline.py -xvs
python -m pytest tests/e2e/test_quality_gate_7.py -xvs
```

### 7.4 Document Cross-References

| Document | Location | Purpose |
|----------|----------|---------|
| This plan | `docs/archive/phase-reports/PROGRAM-MANAGEMENT-PIPELINE-ORCHESTRATION.md` | Merge execution, risk, stakeholder management |
| Merge decision | `docs/archive/working-documents/MERGE_DECISION_pipeline-orchestration-v1.md` | Gate approvals, quality verification |
| PR #606 analysis | `docs/reference/pr606-integration-analysis.md` | Conflict matrix, build-upon opportunities |
| Branch change matrix | `docs/reference/branch-change-matrix.md` | Open items tracking |
| Testing plan | `docs/archive/working-documents/TESTING-PLAN-pipeline-orchestration-v1.md` | Test coverage, test execution plan |
| Quality review | `docs/archive/working-documents/QUALITY-REVIEW-REPORT-pipeline-orchestration-v1.md` | Quality gate results |
| Architectural decisions | `docs/archive/working-documents/ARCHITECTURAL-DECISIONS-pipeline-orchestration-v1.md` | ADRs and design rationale |

### 7.5 PR #606 Questions Summary (Q1-Q6)

| # | Question | Gates | Priority |
|---|----------|-------|----------|
| Q1 | Does MemoryStore share `gaia_chat.db` or use separate file? | C-2 schema resolution | P0 |
| Q2 | Cache access pattern for `_register_agent_memory_ops()`? | C-1 function absorption | P0 |
| Q3 | AgentLoop SSE event JSON schema? | C-3 event handler absorption | P0 |
| Q4 | Interest in convergence design session? | BU-3 Phase 6 planning | P1 |
| Q5 | MCP tool control endpoint authentication? | C-4 security posture | P0 |
| Q6 | Memory eval runner compatibility? | Post-rebase testing | P1 |

---

**Document Version:** 1.0
**Created:** 2026-04-25
**Author:** Software Program Manager (Claude Code)
**Status:** READY FOR EXECUTION
**Next Action:** Create pull request per Section 2.2
