# PR PLANS — ALL 132 SPEC SHEETS
# Program Manager: Node 2 — Production Plan
# Date: 2026-05-06

================================================================================
# STRATEGIC OVERVIEW
================================================================================

## MERGE WAVES (by dependency order)

Wave 1 (MERGE_ORDER 1-5): Foundation — no dependencies, independent features, new items
Wave 2 (MERGE_ORDER 6-15): Phase 3 core — modular architecture, DI, caching, observability
Wave 3 (MERGE_ORDER 16-25): Phase 4 — health, resilience, data protection, orchestration kernel
Wave 4 (MERGE_ORDER 26-40): Pipeline engine, supervisor hierarchy, auto-spawn stages
Wave 5 (MERGE_ORDER 41-55): Pipeline UI — runner, canvas, wiring, SSE
Wave 6 (MERGE_ORDER 56-70): Advanced UI — loops, templates, metrics, components
Wave 7 (MERGE_ORDER 71-80): Fixes, tests, security hardening
Wave 8 (MERGE_ORDER 81-87): Documentation, release, cleanup
Wave N (MERGE_ORDER 1-11): New entries — commits 88-132 (see new batch groups below)

## BATCHING STRATEGY

Small doc-only fixes can be batched. Security fixes are standalone. Feature
implementations with significant code are standalone. Related test fixes can
batch with their parent feature if they touch the same files.

================================================================================

PR-PLAN: pdf-bundle-generator
SOURCE_COMMIT: 07b0e88
ISSUE_TITLE: Add PDF bundle generator for all documentation pages
ISSUE_BODY: Add a Python script to generate PDF bundles of all 70 documentation pages from the branch. The script (docs/pdf/generate_all.py, 126 lines) produces static PDF output for every guide, SDK reference, spec, and release note. This includes 70 existing PDF files under docs/pdf/ that serve as offline-accessible documentation bundles for the GAIA project.

This is a documentation utility with no code dependencies on other features. The script should be independently testable and should not break the existing build pipeline.
ISSUE_LABELS: documentation, python, docs-build
BRANCH_NAME: pr-pdf-bundle-generator
BRANCH_BASE: main
PR_TITLE: docs: add PDF bundle generator for all 70 documentation pages
PR_BODY: ## Summary
- Add `docs/pdf/generate_all.py` script to generate PDF bundles from all doc pages
- Includes 70 PDF outputs covering guides, SDK refs, specs, and release notes
- Offline-accessible documentation for GAIA users

## Testing
- Verify script runs successfully: `python docs/pdf/generate_all.py`
- Confirm all 70 PDFs are generated without errors
- Validate PDF content matches source MDX pages

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 1)
MERGE_ORDER: 1
DEPENDS_ON:
BATCH_WITH: runtime-artifact-exclusions, docs-debt-cleanup

================================================================================

PR-PLAN: orchestration-user-guide
SOURCE_COMMIT: 8772238
ISSUE_TITLE: Add comprehensive orchestration user guide with 24 screenshots
ISSUE_BODY: Create a comprehensive orchestration user guide with 1826 lines of MDX documentation and 24 API response screenshots. The guide covers parallel execution, conflict detection, rollback, worktree lifecycle, health monitoring, SSE streaming, hooks, and state transitions. Documentation goes into docs/guides/orchestration.mdx with screenshots under docs/guides/screenshots/.

This guide depends on the core orchestration kernel (eb0a838) and parallel execution engine (e0ed934) being complete. It serves as the primary user-facing documentation for the orchestration feature set and must accurately reflect all implemented APIs and behaviors.
ISSUE_LABELS: documentation, mdx, user-guide
BRANCH_NAME: pr-orchestration-user-guide
BRANCH_BASE: main
PR_TITLE: docs: add comprehensive orchestration user guide (1826 lines, 24 screenshots)
PR_BODY: ## Summary
- Add 1826-line orchestration user guide at `docs/guides/orchestration.mdx`
- Include 24 API response screenshots covering all orchestration features
- Update `docs/docs.json` to register new guide page

## Coverage
- Parallel execution workflows
- Conflict detection and resolution
- Rollback procedures
- Worktree lifecycle management
- Health monitoring dashboards
- SSE streaming events
- Automation hooks usage
- State transition diagrams

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 2)
- Depends on: core-orchestration-kernel, parallel-execution-engine
MERGE_ORDER: 42
DEPENDS_ON: pr-core-orchestration-kernel, pr-parallel-execution-engine
BATCH_WITH:

================================================================================

PR-PLAN: orchestrator-ui-visibility
SOURCE_COMMIT: 5bd6ef8
ISSUE_TITLE: Add orchestrator UI visibility layer with REST API and SSE streaming
ISSUE_BODY: Add a REST API router and SSE streaming endpoints for the orchestrator, exposing objective management, state transitions, and execution history to the Agent UI. The implementation spans src/gaia/orchestration/engine.py, src/gaia/ui/routers/orchestrator.py (625 lines), src/gaia/ui/server.py, and includes comprehensive API tests in tests/unit/orchestration/test_orchestrator_api.py (598 lines).

This feature depends on the core orchestration kernel being in place. It provides the critical UI visibility layer that allows users to monitor and control orchestration execution through the web interface and API.
ISSUE_LABELS: feature, rest-api, sse, ui, orchestration
BRANCH_NAME: pr-orchestrator-ui-visibility
BRANCH_BASE: main
PR_TITLE: feat(ui): add orchestrator UI visibility layer — REST API + SSE streaming
PR_BODY: ## Summary
- Add REST API router at `src/gaia/ui/routers/orchestrator.py` (625 lines)
- Implement SSE streaming endpoints for real-time orchestration events
- Expose objective management, state transitions, and execution history
- Add comprehensive API tests (598 lines)

## Endpoints
- Objective CRUD operations
- State transition tracking
- Execution history queries
- SSE event stream subscription

## Testing
- All API endpoints return correct status codes
- SSE stream delivers events in real-time
- State transitions are properly recorded

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 3)
- Depends on: core-orchestration-kernel
MERGE_ORDER: 28
DEPENDS_ON: pr-core-orchestration-kernel
BATCH_WITH:

================================================================================

PR-PLAN: parallel-exec-edge-tests
SOURCE_COMMIT: b3d707e
ISSUE_TITLE: Add 7 edge-case tests for parallel execution engine
ISSUE_BODY: Add 7 edge-case test scenarios for the parallel execution engine in tests/unit/orchestration/test_parallel_execution.py (444 lines). Tests cover semaphore bounds validation, conflict overlap detection edge cases, rollback verdict scenarios, and worktree lifecycle management under unusual conditions.

These tests depend on the parallel execution engine (e0ed934) being implemented. They provide critical coverage for boundary conditions that could cause production failures under concurrent load.
ISSUE_LABELS: testing, orchestration, edge-cases
BRANCH_NAME: pr-parallel-exec-edge-tests
BRANCH_BASE: main
PR_TITLE: test(orchestration): add 7 edge-case tests for parallel execution engine
PR_BODY: ## Summary
- Add 7 edge-case test scenarios (444 lines)
- Coverage areas: semaphore bounds, conflict overlap, rollback verdicts, worktree lifecycle

## Test Scenarios
1. Semaphore bounds under extreme concurrency
2. Conflict overlap detection with overlapping resources
3. Rollback verdicts for partial failures
4. Worktree lifecycle under rapid create/destroy cycles
5. Edge cases in resource locking
6. Timeout handling in parallel branches
7. Recovery from supervisor failure

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 4)
- Depends on: parallel-execution-engine
MERGE_ORDER: 31
DEPENDS_ON: pr-parallel-execution-engine
BATCH_WITH:

================================================================================

PR-PLAN: parallel-execution-engine
SOURCE_COMMIT: e0ed934
ISSUE_TITLE: Implement Phase 4 parallel execution with conflict detection, rollback, and worktree lifecycle
ISSUE_BODY: Implement Phase 4 parallel execution engine with conflict detection, rollback mechanisms, and git worktree lifecycle management. The implementation spans src/gaia/orchestration/__init__.py, src/gaia/orchestration/adapters.py, src/gaia/orchestration/engine.py (873 lines), src/gaia/orchestration/models.py, src/gaia/orchestration/supervisor.py, and tests/unit/orchestration/test_parallel_execution.py (1642 lines). Hooks are refactored into a separate module, and adapters are added for pipeline integration.

This is a core orchestration feature that depends on the Phase 1 core kernel, supervisor hierarchy (both project and git), and serves as the foundation for all parallel execution capabilities. It includes 1642 lines of tests covering the full parallel execution lifecycle.
ISSUE_LABELS: feature, orchestration, phase4, parallel-execution
BRANCH_NAME: pr-parallel-execution-engine
BRANCH_BASE: main
PR_TITLE: feat(orchestration): Phase 4 parallel execution — conflict detection, rollback, worktree lifecycle
PR_BODY: ## Summary
- Implement parallel execution engine (873 lines in engine.py)
- Add conflict detection for concurrent resource access
- Implement rollback mechanisms for failed parallel branches
- Add git worktree lifecycle management
- Refactor hooks into separate module
- Add pipeline integration adapters
- 1642 lines of comprehensive tests

## Key Components
- `src/gaia/orchestration/engine.py` — core parallel execution logic
- `src/gaia/orchestration/adapters.py` — pipeline integration adapters
- `src/gaia/orchestration/supervisor.py` — supervisor coordination
- Conflict detection algorithm for resource contention
- Rollback verdict system for partial failures

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 5)
- Depends on: core-orchestration-kernel, project-supervisor, git-supervisor
MERGE_ORDER: 25
DEPENDS_ON: pr-core-orchestration-kernel, pr-project-supervisor-hierarchy, pr-git-supervisor-hierarchy
BATCH_WITH:

================================================================================

PR-PLAN: automation-hooks
SOURCE_COMMIT: 6f95323
ISSUE_TITLE: Implement Phase 3 automation hooks for git operations and task spawning
ISSUE_BODY: Implement Phase 3 automation hooks ("Hooks Recalculate") for git branch creation, commit, PR management, rollback, objective updates, and task spawning. The monolithic hooks.py is refactored into a modular hook system under src/gaia/orchestration/hooks/ with individual modules: git_branch.py, git_commit.py, git_pr.py, git_rollback.py, objective_update.py, and task_spawn.py. Includes tests in tests/unit/orchestration/test_hooks_git.py (787 lines).

This depends on the core orchestration kernel and git supervisor. The modular hook design enables extensible automation of git workflows driven by orchestration events.
ISSUE_LABELS: feature, orchestration, hooks, git-automation
BRANCH_NAME: pr-automation-hooks
BRANCH_BASE: main
PR_TITLE: feat(orchestration): Phase 3 automation hooks — git operations and task spawning
PR_BODY: ## Summary
- Refactor monolithic hooks.py into modular hook system
- Add git branch creation, commit, PR management hooks
- Add rollback, objective update, and task spawning hooks
- 787 lines of hook tests

## Hook Modules
- `hooks/git_branch.py` — automated branch creation
- `hooks/git_commit.py` — automated commit management
- `hooks/git_pr.py` — PR creation and management
- `hooks/git_rollback.py` — rollback operations
- `hooks/objective_update.py` — objective state updates
- `hooks/task_spawn.py` — dynamic task spawning

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 6)
- Depends on: core-orchestration-kernel, git-supervisor
MERGE_ORDER: 23
DEPENDS_ON: pr-core-orchestration-kernel, pr-git-supervisor-hierarchy
BATCH_WITH:

================================================================================

PR-PLAN: git-supervisor-hierarchy
SOURCE_COMMIT: dc02956
ISSUE_TITLE: Add Phase 2B GitSupervisor hierarchy with supervisor registry
ISSUE_BODY: Implement Phase 2B supervisor hierarchy with GitSupervisor (519 lines) and supervisor registry (130 lines). Adds custom exception types in src/gaia/exceptions.py and a dedicated supervisors package under src/gaia/orchestration/supervisors/. Includes tests for git supervisor (382 lines) and supervisor registry (177 lines).

This depends on the core orchestration kernel and ProjectSupervisor hierarchy. It provides git-specific supervision capabilities for the orchestration engine.
ISSUE_LABELS: feature, orchestration, phase2, supervisor, git
BRANCH_NAME: pr-git-supervisor-hierarchy
BRANCH_BASE: main
PR_TITLE: feat(orchestration): Phase 2B GitSupervisor hierarchy with supervisor registry
PR_BODY: ## Summary
- Implement GitSupervisor (519 lines) for git operation supervision
- Add supervisor registry (130 lines) for managing supervisor instances
- Add custom exception types for orchestration errors
- Create dedicated supervisors package structure
- 559 lines of tests

## Components
- `src/gaia/orchestration/supervisors/git.py` — GitSupervisor
- `src/gaia/orchestration/supervisors/registry.py` — Supervisor registry
- `src/gaia/exceptions.py` — Custom exception types

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 7)
- Depends on: core-orchestration-kernel, project-supervisor-hierarchy
MERGE_ORDER: 22
DEPENDS_ON: pr-core-orchestration-kernel, pr-project-supervisor-hierarchy
BATCH_WITH:

================================================================================

PR-PLAN: project-supervisor-hierarchy
SOURCE_COMMIT: dd1d314
ISSUE_TITLE: Add Phase 2A ProjectSupervisor hierarchy with 56 tests
ISSUE_BODY: Implement Phase 2A ProjectSupervisor hierarchy with 56 tests covering supervisor state management, health checks, and escalation policies. The implementation is in src/gaia/orchestration/supervisor.py (548 lines) with tests in tests/unit/orchestration/test_supervisor.py (862 lines). This forms the base supervisor class for all specialized supervisors.

This depends only on the core orchestration kernel. It establishes the supervisor pattern that all subsequent specialized supervisors (GitSupervisor, etc.) will extend.
ISSUE_LABELS: feature, orchestration, phase2, supervisor
BRANCH_NAME: pr-project-supervisor-hierarchy
BRANCH_BASE: main
PR_TITLE: feat(orchestration): Phase 2A ProjectSupervisor hierarchy (56 tests)
PR_BODY: ## Summary
- Implement ProjectSupervisor base class (548 lines)
- Add supervisor state management with health checks
- Implement escalation policies for failure handling
- 862 lines of tests (56 test cases)

## Coverage
- Supervisor lifecycle (start, stop, pause, resume)
- Health check integration and reporting
- Escalation policy evaluation
- State persistence and recovery

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 8)
- Depends on: core-orchestration-kernel
MERGE_ORDER: 21
DEPENDS_ON: pr-core-orchestration-kernel
BATCH_WITH:

================================================================================

PR-PLAN: core-orchestration-kernel
SOURCE_COMMIT: eb0a838
ISSUE_TITLE: Implement Phase 1 core orchestration kernel with 89 tests
ISSUE_BODY: Implement the Phase 1 core orchestration kernel with 89 tests. This establishes the fundamental orchestration engine including models (603 lines), adapters (322 lines), engine (583 lines), hooks (192 lines), and comprehensive test suites (515 + 1163 lines). Also includes 5 phase reports under docs/archive/phase-reports/.

This is the foundation for all subsequent orchestration phases (2A, 2B, 3, 4). No code dependencies — this is the root of the orchestration feature tree and must be merged first among orchestration features.
ISSUE_LABELS: feature, orchestration, phase1, foundation
BRANCH_NAME: pr-core-orchestration-kernel
BRANCH_BASE: main
PR_TITLE: feat(orchestration): Phase 1 core orchestration kernel (89 tests)
PR_BODY: ## Summary
- Implement core orchestration engine (583 lines)
- Define orchestration models (603 lines)
- Add pipeline adapters (322 lines)
- Implement base hooks system (192 lines)
- 89 tests across two test files (1678 lines total)

## Components
- `src/gaia/orchestration/engine.py` — core engine
- `src/gaia/orchestration/models.py` — data models
- `src/gaia/orchestration/adapters.py` — pipeline adapters
- `src/gaia/orchestration/hooks.py` — base hooks

## Testing
- 515 lines: objective and model tests
- 1163 lines: orchestrator integration tests
- Total: 89 test cases

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 9)
- Foundation: no dependencies
MERGE_ORDER: 20
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: resilience-error-consolidation
SOURCE_COMMIT: fa8b17d
ISSUE_TITLE: Consolidate ResilienceError and remove duplicate methods
ISSUE_BODY: Consolidate ResilienceError into a dedicated errors.py module, removing duplicate error handling methods scattered across bulkhead, circuit breaker, and retry modules. Also clean up .gitignore entries that were incorrectly tracking resilience artifacts.

This depends on the resilience patterns module being implemented. It is a cleanup/refactoring fix that improves code organization and reduces maintenance burden.
ISSUE_LABELS: bugfix, resilience, refactoring
BRANCH_NAME: pr-resilience-error-consolidation
BRANCH_BASE: main
PR_TITLE: fix(resilience): consolidate ResilienceError into dedicated errors.py module
PR_BODY: ## Summary
- Create `src/gaia/resilience/errors.py` for centralized error types
- Remove duplicate ResilienceError methods from bulkhead, circuit breaker, retry
- Clean up .gitignore entries for resilience artifacts

## Impact
- Reduces code duplication across 3 resilience modules
- Centralizes error type definitions for easier maintenance
- No behavioral changes — pure refactoring

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 10)
- Depends on: resilience-patterns
MERGE_ORDER: 17
DEPENDS_ON: pr-resilience-patterns
BATCH_WITH:

================================================================================

PR-PLAN: pipeline-sse-wiring
SOURCE_COMMIT: 97edfd7
ISSUE_TITLE: Wire PipelineEngine events to SSE stream and fix critical drain bug
ISSUE_BODY: Wire PipelineEngine events to SSE stream for real-time UI updates and fix a critical drain bug in SSE connection handling. Implementation includes src/gaia/pipeline/sse_hooks.py (229 lines), updates to engine.py, orchestrator.py, UI routers, pipeline templates, and frontend PipelineRunner.tsx. Comprehensive tests in test_sse_drain_fix.py (250 lines) and test_sse_hooks.py (591 lines).

This depends on the pipeline runner page and pipeline engine wiring being in place. The drain bug fix is critical for production stability of SSE connections.
ISSUE_LABELS: feature, sse, pipeline, bugfix
BRANCH_NAME: pr-pipeline-sse-wiring
BRANCH_BASE: main
PR_TITLE: feat(pipeline): wire PipelineEngine events to SSE stream and fix drain bug
PR_BODY: ## Summary
- Wire PipelineEngine events to SSE stream for real-time UI updates
- Fix critical drain bug in SSE connection handling
- Add SSE hooks (229 lines) and comprehensive tests (841 lines total)
- Update PipelineRunner.tsx for SSE event consumption

## Bug Fix
- Critical: SSE connection drain was not properly releasing resources
- Fix ensures clean connection teardown and resource cleanup

## Testing
- SSE drain fix tests (250 lines)
- SSE hooks tests (591 lines)

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 11)
- Depends on: pipeline-runner-page, pipeline-engine-wiring
MERGE_ORDER: 45
DEPENDS_ON: pr-pipeline-runner-page, pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: resilience-apis-fix
SOURCE_COMMIT: 5a37360
ISSUE_TITLE: Add resilience APIs and fix 28 integration tests
ISSUE_BODY: Add resilience APIs (bulkhead isolation, circuit breaker, retry patterns) and fix 28 integration tests that were failing due to missing resilience wiring in the orchestrator. Changes span src/gaia/pipeline/orchestrator.py, src/gaia/resilience/__init__.py, bulkhead.py, circuit_breaker.py, retry.py, and tests/pipeline/test_routing_engine_resilience.py.

This depends on the resilience patterns and pipeline engine wiring being in place. The 28 failing tests represent a significant quality gap that must be closed.
ISSUE_LABELS: bugfix, resilience, integration-tests, pipeline
BRANCH_NAME: pr-resilience-apis-fix
BRANCH_BASE: main
PR_TITLE: fix(pipeline): add resilience APIs and fix 28 integration tests
PR_BODY: ## Summary
- Add bulkhead, circuit breaker, and retry APIs to orchestrator
- Fix 28 failing integration tests from missing resilience wiring
- Wire resilience patterns into pipeline routing engine

## Test Fixes
- All 28 previously failing tests now pass
- Resilience pattern integration verified

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 12)
- Depends on: resilience-patterns, pipeline-engine-wiring
MERGE_ORDER: 19
DEPENDS_ON: pr-resilience-patterns, pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: sprint-integration-tests
SOURCE_COMMIT: 47c0c0c
ISSUE_TITLE: Add Sprint 1-2 integration tests (151 tests, 88% coverage)
ISSUE_BODY: Add 151 integration tests covering pipeline engine initialization, lifecycle, decision making, execution phases, loop management, and state machine transitions. Tests span 9 files (2003 lines) and achieve 88% code coverage across the pipeline engine.

This depends on the pipeline engine wiring and loop manager being implemented. The test suite provides the quality gate for all subsequent pipeline work.
ISSUE_LABELS: testing, integration-tests, pipeline, coverage
BRANCH_NAME: pr-sprint-integration-tests
BRANCH_BASE: main
PR_TITLE: test(pipeline): add Sprint 1-2 integration tests (151 tests, 88% coverage)
PR_BODY: ## Summary
- 151 integration tests across 9 files (2003 lines)
- 88% code coverage achieved

## Test Areas
- Engine initialization and lifecycle
- Decision engine behavior
- Execution phase transitions
- Loop management
- State machine transitions

## Files
- `tests/pipeline/test_decision_engine.py`
- `tests/pipeline/test_engine_decision.py`
- `tests/pipeline/test_engine_execution.py`
- `tests/pipeline/test_engine_init.py`
- `tests/pipeline/test_engine_lifecycle.py`
- `tests/pipeline/test_engine_nexus.py`
- `tests/pipeline/test_engine_phase_integration.py`
- `tests/pipeline/test_loop_manager.py`
- `tests/pipeline/test_state_machine.py`

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 13)
- Depends on: pipeline-engine-wiring, loop-manager-multi
MERGE_ORDER: 60
DEPENDS_ON: pr-pipeline-engine-wiring, pr-multiple-independent-loops
BATCH_WITH:

================================================================================

PR-PLAN: artifact-provenance
SOURCE_COMMIT: d3951f8
ISSUE_TITLE: Add artifact provenance tracking in PipelineSnapshot
ISSUE_BODY: Add artifact provenance tracking to PipelineSnapshot, enabling full traceability of artifacts back to their source pipeline stages and execution context. Changes are focused on src/gaia/pipeline/engine.py and src/gaia/pipeline/state.py.

This depends on the pipeline engine wiring. It provides critical audit trail capabilities for pipeline execution artifacts.
ISSUE_LABELS: feature, pipeline, provenance, audit
BRANCH_NAME: pr-artifact-provenance
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add artifact provenance tracking in PipelineSnapshot
PR_BODY: ## Summary
- Add provenance tracking to PipelineSnapshot
- Enable artifact traceability to source stages
- Track execution context for all pipeline artifacts

## Impact
- Full audit trail for pipeline outputs
- Debugging support for artifact lineage

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 14)
- Depends on: pipeline-engine-wiring
MERGE_ORDER: 46
DEPENDS_ON: pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: remove-pipeline-isolation
SOURCE_COMMIT: 03d15bd
ISSUE_TITLE: Remove PipelineIsolation waste and fix agent ID collisions
ISSUE_BODY: Remove unnecessary PipelineIsolation overhead from the pipeline engine and fix agent ID collisions that were causing conflicts in multi-agent scenarios. Changes are focused on src/gaia/pipeline/engine.py.

This depends on the pipeline engine wiring. It is a cleanup fix that removes wasteful code and resolves a functional bug.
ISSUE_LABELS: bugfix, pipeline, cleanup
BRANCH_NAME: pr-remove-pipeline-isolation
BRANCH_BASE: main
PR_TITLE: fix(pipeline): remove PipelineIsolation waste and fix agent ID collisions
PR_BODY: ## Summary
- Remove unnecessary PipelineIsolation overhead
- Fix agent ID collision bugs in multi-agent scenarios
- Reduce memory footprint of pipeline engine

## Impact
- Performance improvement from removing isolation overhead
- Bug fix for agent ID collisions

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 15)
- Depends on: pipeline-engine-wiring
MERGE_ORDER: 47
DEPENDS_ON: pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: sec-003-path-traversal
SOURCE_COMMIT: ee43966
ISSUE_TITLE: Add SEC-003 path traversal protection in artifact_extractor.py
ISSUE_BODY: Add SEC-003 path traversal protection to artifact_extractor.py, preventing directory escape attacks when extracting pipeline artifacts. Includes unit tests (86 lines) for the security fix.

This is a security-critical fix that depends on the artifact extractor being implemented. Path traversal vulnerabilities allow attackers to read/write files outside the intended directory.
ISSUE_LABELS: security, pipeline, path-traversal, sec-003
BRANCH_NAME: pr-sec-003-path-traversal
BRANCH_BASE: main
PR_TITLE: fix(security): add SEC-003 path traversal protection in artifact_extractor
PR_BODY: ## Summary
- Add path traversal protection to artifact extraction
- Prevent directory escape attacks during artifact extraction
- Add 86 lines of security unit tests

## Security Impact
- Prevents unauthorized file system access via crafted artifact paths
- Validates all extraction paths against allowed directory
- Blocks symlink-based escape attempts

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 16)
- Depends on: artifact-extractor
MERGE_ORDER: 56
DEPENDS_ON: pr-artifact-extractor
BATCH_WITH:

================================================================================

PR-PLAN: runtime-artifact-exclusions
SOURCE_COMMIT: ad4f7c6
ISSUE_TITLE: Add runtime artifact exclusions and untrack chroma DB
ISSUE_BODY: Add runtime artifact exclusions to .gitignore and untrack chroma DB files (chroma_data/chroma.sqlite3) to prevent unnecessary git tracking of generated artifacts.

This is a standalone chore with no code dependencies. It can be batched with other .gitignore and doc-only changes.
ISSUE_LABELS: chore, gitignore, cleanup
BRANCH_NAME: pr-runtime-artifact-exclusions
BRANCH_BASE: main
PR_TITLE: chore: add runtime artifact exclusions and untrack chroma DB
PR_BODY: ## Summary
- Update .gitignore to exclude runtime-generated artifacts
- Untrack chroma_data/chroma.sqlite3 from git
- Prevent unnecessary tracking of generated files

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 17)
- No dependencies
MERGE_ORDER: 1
DEPENDS_ON:
BATCH_WITH: pdf-bundle-generator, docs-debt-cleanup

================================================================================

PR-PLAN: webui-typescript-fix
SOURCE_COMMIT: 0ab5554
ISSUE_TITLE: Resolve TypeScript build errors in metrics and templates
ISSUE_BODY: Resolve TypeScript build errors in metrics dashboard components (MetricSummaryCards.tsx, MetricsDashboard.tsx, PhaseTimingChart.tsx, QualityOverTimeChart.tsx) and template editor dialog (TemplateEditorDialog.tsx). Also fix tsconfig.json configuration and metrics store (metricsStore.ts).

This depends on the metrics dashboard being implemented. It is a build fix required for the webui to compile successfully.
ISSUE_LABELS: bugfix, typescript, webui, build
BRANCH_NAME: pr-webui-typescript-fix
BRANCH_BASE: main
PR_TITLE: fix(webui): resolve TypeScript build errors in metrics and template components
PR_BODY: ## Summary
- Fix TypeScript errors in 5 metrics/template components
- Fix tsconfig.json configuration
- Fix metricsStore.ts type issues

## Files
- MetricSummaryCards.tsx, MetricsDashboard.tsx
- PhaseTimingChart.tsx, QualityOverTimeChart.tsx
- TemplateEditorDialog.tsx
- metricsStore.ts, tsconfig.json

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 18)
- Depends on: metrics-dashboard
MERGE_ORDER: 68
DEPENDS_ON: pr-metrics-dashboard
BATCH_WITH:

================================================================================

PR-PLAN: supervisor-decision-tests
SOURCE_COMMIT: c3ccc4f
ISSUE_TITLE: Add 35 unit tests for supervisor agent decisions
ISSUE_BODY: Add 35 unit tests for supervisor agent decision-making covering quality scoring, escalation policies, and defect routing validation. Tests are in tests/unit/quality/test_supervisor_agent.py (881 lines).

This depends on the supervisor agents being implemented. It provides quality validation for the supervisor decision logic.
ISSUE_LABELS: testing, supervisor, quality, unit-tests
BRANCH_NAME: pr-supervisor-decision-tests
BRANCH_BASE: main
PR_TITLE: test(quality): add 35 unit tests for supervisor agent decisions
PR_BODY: ## Summary
- 35 unit tests (881 lines) for supervisor agent decision-making
- Coverage: quality scoring, escalation policies, defect routing

## Test Areas
- Quality score calculation and thresholds
- Escalation policy evaluation
- Defect routing validation
- Decision boundary conditions

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 19)
- Depends on: supervisor-agents
MERGE_ORDER: 37
DEPENDS_ON: pr-supervisor-agents
BATCH_WITH:

================================================================================

PR-PLAN: component-registry-ui
SOURCE_COMMIT: c27e42e
ISSUE_TITLE: Add component framework registry UI and integration tests
ISSUE_BODY: Add component framework registry UI with file modal, CSS styling, and integration tests. Includes a comprehensive user guide (429 lines MDX). Changes span App.tsx, ComponentRegistry.tsx, ComponentRegistry.css, ComponentFileModal.tsx, and tests/integration/test_component_framework.py (1109 lines).

This depends on the component framework templates being in place. It provides the user interface for browsing and managing component framework templates.
ISSUE_LABELS: feature, webui, component-framework, ui
BRANCH_NAME: pr-component-registry-ui
BRANCH_BASE: main
PR_TITLE: feat(webui): add component framework registry UI and integration tests
PR_BODY: ## Summary
- Add Component Registry UI with drag-and-drop file modal
- Add CSS styling for registry components
- Add 429-line user guide documentation
- 1109 lines of integration tests

## Components
- ComponentRegistry.tsx — main registry view
- ComponentFileModal.tsx — file detail modal
- ComponentRegistry.css — styling

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 20)
- Depends on: component-framework-templates
MERGE_ORDER: 62
DEPENDS_ON: pr-component-framework-templates
BATCH_WITH:

================================================================================

PR-PLAN: canvas-ui-wiring-fix
SOURCE_COMMIT: 1ffd7a6
ISSUE_TITLE: Fix UI wiring for supervisor/loop canvas nodes, decision gates, and workspace visibility
ISSUE_BODY: Resolve UI wiring issues for supervisor/loop canvas nodes, decision gates, and workspace visibility. Fix canvas store state management, type definitions, and Agent UI pipeline runner rendering. Changes affect PipelineCanvas.tsx, PipelineRunner.tsx, DecisionGate.tsx, SupervisorNode.tsx, canvas stores, types, and backend SDK/LLM files.

This depends on the pipeline canvas and Tier 3 canvas completion. It fixes critical rendering and state management bugs in the canvas UI.
ISSUE_LABELS: bugfix, webui, canvas, wiring
BRANCH_NAME: pr-canvas-ui-wiring-fix
BRANCH_BASE: main
PR_TITLE: fix(webui): fix UI wiring for canvas nodes, decision gates, workspace visibility
PR_BODY: ## Summary
- Fix supervisor and loop canvas node wiring
- Fix decision gate rendering and interaction
- Fix workspace visibility in canvas
- Fix canvas store state management
- Update type definitions for canvas components

## Files
- DecisionGate.tsx, PipelineCanvas.css, PipelineRunner.css
- PipelineRunner.tsx, SupervisorNode.tsx
- pipelineCanvas.ts, pipelineCanvasStore.ts
- Type definitions and backend SDK updates

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 21)
- Depends on: pipeline-canvas, canvas-tier3-complete
MERGE_ORDER: 65
DEPENDS_ON: pr-visual-pipeline-canvas, pr-tier3-pipeline-canvas
BATCH_WITH:

================================================================================

PR-PLAN: final-quality-review-fixes
SOURCE_COMMIT: 9bc85ec
ISSUE_TITLE: Resolve final quality review issues — event loops and orchestrator
ISSUE_BODY: Resolve final quality review issues related to event loop consolidation in orchestrator and loop manager thread handling. Changes in src/gaia/pipeline/loop_manager.py and src/gaia/pipeline/orchestrator.py address thread safety and resource contention.

This depends on event loop consolidation and canvas loop path fix. It is the final cleanup pass after quality review.
ISSUE_LABELS: bugfix, quality-review, threading, orchestrator
BRANCH_NAME: pr-final-quality-review-fixes
BRANCH_BASE: main
PR_TITLE: fix(pipeline): resolve final quality review issues — event loops and orchestrator
PR_BODY: ## Summary
- Fix event loop consolidation issues in orchestrator
- Fix loop manager thread handling for thread safety
- Resolve resource contention in ThreadPoolExecutor threads

## Impact
- Eliminates race conditions in loop management
- Ensures proper thread lifecycle

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 22)
- Depends on: event-loop-consolidation, canvas-loop-path-fix
MERGE_ORDER: 55
DEPENDS_ON: pr-event-loop-consolidation, pr-canvas-loop-path-fix
BATCH_WITH:

================================================================================

PR-PLAN: event-loop-consolidation
SOURCE_COMMIT: 0ed82d4
ISSUE_TITLE: Consolidate event loops in ThreadPoolExecutor threads
ISSUE_BODY: Consolidate event loops in ThreadPoolExecutor threads to prevent resource contention and ensure proper thread lifecycle management. Changes are focused on src/gaia/pipeline/loop_manager.py.

This is a standalone fix with no code dependencies. It addresses a concurrency bug that could cause resource contention under load.
ISSUE_LABELS: bugfix, threading, concurrency, pipeline
BRANCH_NAME: pr-event-loop-consolidation
BRANCH_BASE: main
PR_TITLE: fix(pipeline): consolidate event loops in ThreadPoolExecutor threads
PR_BODY: ## Summary
- Consolidate event loops in ThreadPoolExecutor threads
- Prevent resource contention under concurrent load
- Ensure proper thread lifecycle management

## Impact
- Fixes race conditions in thread pool
- Reduces resource contention
- Improves stability under load

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 23)
- No dependencies
MERGE_ORDER: 53
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: canvas-loop-path-fix
SOURCE_COMMIT: 961c7d5
ISSUE_TITLE: Fix canvas loop path — artifact propagation and state safety
ISSUE_BODY: Fix canvas loop path to ensure proper artifact propagation and state safety during looped pipeline execution. Changes in src/gaia/pipeline/loop_manager.py address artifact flow through loop iterations.

This depends on event loop consolidation being in place. It fixes artifact propagation bugs in looped pipelines.
ISSUE_LABELS: bugfix, pipeline, loops, artifacts
BRANCH_NAME: pr-canvas-loop-path-fix
BRANCH_BASE: main
PR_TITLE: fix(pipeline): fix canvas loop path — artifact propagation and state safety
PR_BODY: ## Summary
- Fix artifact propagation through loop iterations
- Ensure state safety during looped pipeline execution
- Correct loop path resolution in canvas

## Impact
- Artifacts correctly flow through loop iterations
- State consistency maintained across loop boundaries

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 24)
- Depends on: event-loop-consolidation
MERGE_ORDER: 54
DEPENDS_ON: pr-event-loop-consolidation
BATCH_WITH:

================================================================================

PR-PLAN: canvas-wiring-quality
SOURCE_COMMIT: 574d142
ISSUE_TITLE: Resolve testing validation bugs in canvas wiring and quality scoring
ISSUE_BODY: Resolve testing validation bugs in canvas wiring and quality scoring to ensure proper quality gate evaluation during pipeline execution. Changes in src/gaia/pipeline/engine.py fix quality scoring calculation and validation.

This depends on pipeline engine wiring. It fixes quality gate evaluation that was incorrectly passing/failing pipelines.
ISSUE_LABELS: bugfix, pipeline, quality-scoring, testing
BRANCH_NAME: pr-canvas-wiring-quality
BRANCH_BASE: main
PR_TITLE: fix(pipeline): resolve validation bugs in canvas wiring and quality scoring
PR_BODY: ## Summary
- Fix quality scoring calculation in pipeline engine
- Resolve canvas wiring validation bugs
- Ensure proper quality gate evaluation

## Impact
- Quality gates now correctly evaluate pipeline output
- Prevents false pass/fail verdicts

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 25)
- Depends on: pipeline-engine-wiring
MERGE_ORDER: 49
DEPENDS_ON: pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: canvas-config-quality-bridge
SOURCE_COMMIT: 957a7cb
ISSUE_TITLE: Wire canvas config, bridge quality scoring, and enable resilience
ISSUE_BODY: Wire canvas configuration, bridge quality scoring between pipeline engine and recursive templates, and enable resilience features. Adds new file src/gaia/pipeline/recursive_template.py and updates engine.py and loop_manager.py.

This depends on pipeline engine wiring and Tier 3 canvas completion. It connects the configuration layer to quality scoring and resilience.
ISSUE_LABELS: bugfix, pipeline, configuration, quality-scoring
BRANCH_NAME: pr-canvas-config-quality-bridge
BRANCH_BASE: main
PR_TITLE: fix(pipeline): wire canvas config, bridge quality scoring, enable resilience
PR_BODY: ## Summary
- Wire canvas configuration to pipeline engine
- Bridge quality scoring with recursive templates
- Enable resilience features in pipeline
- Add recursive_template.py module

## Components
- `src/gaia/pipeline/recursive_template.py` — new template module
- Quality scoring bridge between engine and templates
- Resilience feature enablement

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 26)
- Depends on: pipeline-engine-wiring, canvas-tier3-complete
MERGE_ORDER: 66
DEPENDS_ON: pr-pipeline-engine-wiring, pr-tier3-pipeline-canvas
BATCH_WITH:

================================================================================

PR-PLAN: supervisor-agents
SOURCE_COMMIT: 214c314
ISSUE_TITLE: Add 5 new supervisor agents with embedded system prompts
ISSUE_BODY: Add 5 new supervisor agents with embedded system prompts: code, performance, planning, quality, security, and testing supervisors for pipeline orchestration. Agent configurations are in config/agents/ with both .md and .yaml formats for quality-supervisor.

This is a standalone feature with no code dependencies. The agent configurations define the behavior and system prompts for each supervisor type.
ISSUE_LABELS: feature, agents, supervisor, configuration
BRANCH_NAME: pr-supervisor-agents
BRANCH_BASE: main
PR_TITLE: feat(agents): add 5 supervisor agents with embedded system prompts
PR_BODY: ## Summary
- Add 5 supervisor agent configurations
- Embedded system prompts for each agent type
- Support both .md and .yaml config formats

## Agent Types
- Code Supervisor — code quality and standards
- Performance Supervisor — performance optimization
- Planning Supervisor — task planning and decomposition
- Quality Supervisor — quality gate evaluation
- Security Supervisor — security review and validation
- Testing Supervisor — test coverage and quality

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 27)
- No dependencies
MERGE_ORDER: 8
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: multiple-independent-loops
SOURCE_COMMIT: 55b890d
ISSUE_TITLE: Add multiple independent loops, custom agent selection, free supervisor placement
ISSUE_BODY: Add support for multiple independent loops, custom agent selection, and free supervisor placement in the pipeline canvas UI. Changes span LoopBlock.tsx, PipelineCanvas.css, PipelineCanvas.tsx, StageZone.tsx, pipelineCanvasStore.ts, types, backend routers, pipeline templates, and template service.

This depends on the pipeline canvas and supervisor agents being implemented. It significantly expands the canvas capability for complex pipeline configurations.
ISSUE_LABELS: feature, webui, canvas, loops
BRANCH_NAME: pr-multiple-independent-loops
BRANCH_BASE: main
PR_TITLE: feat(webui): add multiple independent loops, custom agent selection, supervisor placement
PR_BODY: ## Summary
- Support multiple independent loops in pipeline canvas
- Add custom agent selection per loop
- Enable free supervisor placement on canvas
- Update canvas store, types, and backend services

## Components
- LoopBlock.tsx — multi-loop UI component
- Updated PipelineCanvas.tsx with loop support
- Backend: pipeline router, templates, template service

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 28)
- Depends on: pipeline-canvas, supervisor-agents
MERGE_ORDER: 59
DEPENDS_ON: pr-visual-pipeline-canvas, pr-supervisor-agents
BATCH_WITH:

================================================================================

PR-PLAN: tier3-tracker-update
SOURCE_COMMIT: 7c3a6a4
ISSUE_TITLE: Update implementation tracker with Tier 3 completion details
ISSUE_BODY: Update the pipeline canvas implementation tracker (docs/PIPELINE-CANVAS-IMPLEMENTATION-TRACKER.md) with Tier 3 completion details, tracking progress across all implementation milestones.

This depends on Tier 3 canvas completion. It is a documentation-only change that can be batched with other doc updates.
ISSUE_LABELS: documentation, tracker, tier3
BRANCH_NAME: pr-tier3-tracker-update
BRANCH_BASE: main
PR_TITLE: docs: update implementation tracker with Tier 3 completion
PR_BODY: ## Summary
- Update PIPELINE-CANVAS-IMPLEMENTATION-TRACKER.md with Tier 3 details
- Track progress across all Tier 3 milestones

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 29)
- Depends on: canvas-tier3-complete
MERGE_ORDER: 67
DEPENDS_ON: pr-tier3-pipeline-canvas
BATCH_WITH:

================================================================================

PR-PLAN: tier3-pipeline-canvas
SOURCE_COMMIT: 856f1b2
ISSUE_TITLE: Complete Tier 3 pipeline canvas — template marketplace, performance dashboard, execution history
ISSUE_BODY: Complete Tier 3 pipeline canvas with template marketplace, performance dashboard, execution history, version diffing, and template versioning UI components. Extensive changes across ExecutionHistory.tsx, PipelineRunner.tsx, TemplateMarketplace.tsx, VersionDiff.tsx, VersionHistory.tsx, stores, types, backend routers, and schemas.

This depends on execution history and the base pipeline canvas. It completes the full Tier 3 feature set for the canvas UI.
ISSUE_LABELS: feature, webui, canvas, tier3
BRANCH_NAME: pr-tier3-pipeline-canvas
BRANCH_BASE: main
PR_TITLE: feat(webui): complete Tier 3 pipeline canvas — template marketplace, dashboard, history
PR_BODY: ## Summary
- Add template marketplace UI component
- Add performance dashboard components
- Add execution history and version diffing
- Update stores, types, backend routers, and schemas

## Components
- ExecutionHistory.tsx — execution timeline
- TemplateMarketplace.tsx — template browsing
- VersionDiff.tsx — version comparison
- VersionHistory.tsx — version timeline
- Updated metricsStore.ts, templateStore.ts

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 30)
- Depends on: execution-history, pipeline-canvas
MERGE_ORDER: 63
DEPENDS_ON: pr-execution-history-replay, pr-visual-pipeline-canvas
BATCH_WITH:

================================================================================

PR-PLAN: pipeline-canvas-guide-update
SOURCE_COMMIT: b1a15ec
ISSUE_TITLE: Update pipeline canvas guide with History tab and execution history
ISSUE_BODY: Update the pipeline canvas user guide with History tab documentation and execution history feature descriptions. Updates docs/PIPELINE-CANVAS-IMPLEMENTATION-TRACKER.md and docs/guides/pipeline-canvas.mdx.

This depends on execution history being implemented. It is a documentation-only change.
ISSUE_LABELS: documentation, pipeline-canvas, user-guide
BRANCH_NAME: pr-pipeline-canvas-guide-update
BRANCH_BASE: main
PR_TITLE: docs: update pipeline canvas guide with History tab and execution history
PR_BODY: ## Summary
- Update pipeline-canvas.mdx with History tab documentation
- Document execution history features
- Update implementation tracker

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 31)
- Depends on: execution-history
MERGE_ORDER: 61
DEPENDS_ON: pr-execution-history-replay
BATCH_WITH:

================================================================================

PR-PLAN: execution-history-replay
SOURCE_COMMIT: 9a85250
ISSUE_TITLE: Add execution history, replay, and template versioning (Tier 3)
ISSUE_BODY: Add execution history, replay functionality, and template versioning support for Tier 3 pipeline canvas features. Implementation includes ExecutionHistory.tsx (230 lines), PipelineRunner updates, and backend router changes (418 lines).

This depends on the base pipeline canvas. It provides the execution replay capability that users need to understand pipeline behavior.
ISSUE_LABELS: feature, webui, canvas, tier3, replay
BRANCH_NAME: pr-execution-history-replay
BRANCH_BASE: main
PR_TITLE: feat(webui): add execution history, replay, and template versioning (Tier 3)
PR_BODY: ## Summary
- Add execution history UI (230 lines)
- Implement pipeline replay functionality
- Add template versioning support
- Update backend router (418 lines)

## Components
- ExecutionHistory.tsx — timeline view of executions
- PipelineRunner updates — replay controls
- Backend router — history and replay endpoints

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 32)
- Depends on: pipeline-canvas
MERGE_ORDER: 60
DEPENDS_ON: pr-visual-pipeline-canvas
BATCH_WITH:

================================================================================

PR-PLAN: tier12-tracker-update
SOURCE_COMMIT: 3ce237c
ISSUE_TITLE: Update pipeline canvas implementation tracker with Tier 1-2 completion
ISSUE_BODY: Update the pipeline canvas implementation tracker with Tier 1 and Tier 2 completion status. Changes to docs/PIPELINE-CANVAS-IMPLEMENTATION-TRACKER.md.

This depends on the pipeline canvas being implemented. Documentation-only change.
ISSUE_LABELS: documentation, tracker, tier1, tier2
BRANCH_NAME: pr-tier12-tracker-update
BRANCH_BASE: main
PR_TITLE: docs: update pipeline canvas tracker with Tier 1-2 completion
PR_BODY: ## Summary
- Update implementation tracker with Tier 1 completion status
- Update implementation tracker with Tier 2 completion status

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 33)
- Depends on: pipeline-canvas
MERGE_ORDER: 58
DEPENDS_ON: pr-visual-pipeline-canvas
BATCH_WITH:

================================================================================

PR-PLAN: canvas-supervisors-gates
SOURCE_COMMIT: ef98904
ISSUE_TITLE: Add supervisor agents, decision gates, loop blocks, and workspace tools to Pipeline Canvas
ISSUE_BODY: Add supervisor agents, decision gates, loop blocks, and workspace tools to the visual Pipeline Canvas drag-and-drop interface. Changes span AgentPalette.tsx, DecisionGate.tsx, LoopBlock.tsx, PipelineCanvas.css, PipelineCanvas.tsx, StageZone.tsx, SupervisorNode.tsx, canvas store, types, implementation tracker, and user guide.

This depends on the pipeline canvas and supervisor agents. It populates the canvas with interactive components for pipeline configuration.
ISSUE_LABELS: feature, webui, canvas, drag-and-drop
BRANCH_NAME: pr-canvas-supervisors-gates
BRANCH_BASE: main
PR_TITLE: feat(webui): add supervisors, decision gates, loop blocks, workspace tools to Canvas
PR_BODY: ## Summary
- Add supervisor agent nodes to canvas
- Add decision gate components
- Add loop block components
- Add workspace tools to canvas
- Update canvas store and type definitions

## Components
- AgentPalette.tsx — agent selection palette
- DecisionGate.tsx — conditional execution gates
- LoopBlock.tsx — loop iteration blocks
- SupervisorNode.tsx — supervisor visualization
- Updated canvas CSS and store

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 34)
- Depends on: pipeline-canvas, supervisor-agents
MERGE_ORDER: 57
DEPENDS_ON: pr-visual-pipeline-canvas, pr-supervisor-agents
BATCH_WITH:

================================================================================

PR-PLAN: canvas-typescript-fix
SOURCE_COMMIT: cea803a
ISSUE_TITLE: Resolve canvas TypeScript errors and React setState warning
ISSUE_BODY: Resolve canvas TypeScript errors and React setState warnings in the pipeline canvas components. Changes in AgentPalette.tsx, PipelineCanvas.tsx, and pipelineCanvasStore.ts fix type mismatches and React anti-patterns.

This depends on the pipeline canvas being implemented. It is a build/runtime fix for the canvas UI.
ISSUE_LABELS: bugfix, typescript, webui, canvas, react
BRANCH_NAME: pr-canvas-typescript-fix
BRANCH_BASE: main
PR_TITLE: fix(webui): resolve canvas TypeScript errors and React setState warning
PR_BODY: ## Summary
- Fix TypeScript type errors in canvas components
- Fix React setState warnings in canvas store
- Ensure clean build for canvas UI

## Files
- AgentPalette.tsx — type fixes
- PipelineCanvas.tsx — type fixes
- pipelineCanvasStore.ts — setState pattern fixes

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 35)
- Depends on: pipeline-canvas
MERGE_ORDER: 51
DEPENDS_ON: pr-visual-pipeline-canvas
BATCH_WITH: pipelinerunner-typescript-fix

================================================================================

PR-PLAN: pipeline-canvas-docs
SOURCE_COMMIT: 9106a72
ISSUE_TITLE: Add pipeline canvas user guide and SDK reference
ISSUE_BODY: Add comprehensive pipeline canvas user guide and SDK reference documentation covering drag-and-drop interface, agent palette, and canvas configuration. Updates docs/docs.json, docs/guides/pipeline-canvas.mdx (142 lines), and docs/sdk/sdks/agent-ui.mdx (219 lines).

This depends on the pipeline canvas being implemented. Documentation-only change.
ISSUE_LABELS: documentation, pipeline-canvas, sdk-reference
BRANCH_NAME: pr-pipeline-canvas-docs
BRANCH_BASE: main
PR_TITLE: docs: add pipeline canvas user guide and SDK reference
PR_BODY: ## Summary
- Add 142-line pipeline canvas user guide
- Add 219-line SDK reference for agent-ui canvas integration
- Update docs.json navigation

## Coverage
- Drag-and-drop interface usage
- Agent palette configuration
- Canvas setup and configuration
- SDK integration patterns

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 36)
- Depends on: pipeline-canvas
MERGE_ORDER: 52
DEPENDS_ON: pr-visual-pipeline-canvas
BATCH_WITH:

================================================================================

PR-PLAN: visual-pipeline-canvas
SOURCE_COMMIT: 3838a8a
ISSUE_TITLE: Add visual drag-and-drop pipeline canvas
ISSUE_BODY: Add visual drag-and-drop pipeline canvas with agent nodes, agent palette, stage zones, and canvas store management. Extensive frontend changes across App.tsx, Sidebar.tsx, AgentNode.tsx, AgentPalette.tsx, PipelineCanvas.tsx, PipelineRunner.tsx, StageZone.tsx, api.ts, pipelineCanvas.ts, pipelineCanvasStore.ts, and types.

This depends on the pipeline runner page being implemented. It is the foundation for all subsequent canvas enhancements (supervisors, loops, templates, etc.).
ISSUE_LABELS: feature, webui, canvas, drag-and-drop, foundation
BRANCH_NAME: pr-visual-pipeline-canvas
BRANCH_BASE: main
PR_TITLE: feat(webui): add visual drag-and-drop pipeline canvas
PR_BODY: ## Summary
- Implement drag-and-drop pipeline canvas UI
- Add agent nodes with visual representation
- Add agent palette for component selection
- Add stage zones for pipeline organization
- Implement canvas store for state management

## Components
- AgentNode.tsx — draggable agent representation
- AgentPalette.tsx — component selection panel
- PipelineCanvas.tsx — main canvas view
- PipelineRunner.tsx — canvas execution controls
- StageZone.tsx — pipeline stage organization
- pipelineCanvasStore.ts — canvas state management

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 37)
- Depends on: pipeline-runner-page
MERGE_ORDER: 50
DEPENDS_ON: pr-pipeline-runner-page
BATCH_WITH:

================================================================================

PR-PLAN: recursive-pipeline-sse
SOURCE_COMMIT: d187907
ISSUE_TITLE: Add recursive pipeline SSE streaming and agent registry source editing
ISSUE_BODY: Implement recursive pipeline SSE streaming and agent registry source editing with UI components. Changes span PipelineRunner.tsx, AgentRegistry.tsx, api.ts, pipelineStore.ts, types, backend engine.py, orchestrator.py, pipeline router, and integration tests.

This depends on the pipeline runner page and pipeline SSE wiring. It enables nested pipeline execution with real-time streaming.
ISSUE_LABELS: feature, sse, pipeline, recursive, agent-registry
BRANCH_NAME: pr-recursive-pipeline-sse
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add recursive pipeline SSE streaming and agent registry editing
PR_BODY: ## Summary
- Implement recursive pipeline SSE streaming
- Add agent registry source editing UI
- Update backend engine and orchestrator for recursive execution
- Add integration tests

## Components
- PipelineRunner.tsx — recursive execution display
- AgentRegistry.tsx — agent source editing
- Backend: engine.py, orchestrator.py, pipeline router

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 38)
- Depends on: pipeline-runner-page, pipeline-sse-wiring
MERGE_ORDER: 64
DEPENDS_ON: pr-pipeline-runner-page, pr-pipeline-sse-wiring
BATCH_WITH:

================================================================================

PR-PLAN: docs-debt-cleanup
SOURCE_COMMIT: 76675ea
ISSUE_TITLE: Archive 62 historical documents and clean up documentation debt
ISSUE_BODY: Archive 62 historical documents and clean up documentation debt by moving superseded plans, phase reports, and historical specs to the archive directory. Updates .gitignore, docs/archive/ directory structure, docs/docs.json, and adds ether-repl-spec.md (504 lines).

This is a standalone documentation chore with no code dependencies. It can be batched with other doc cleanup changes.
ISSUE_LABELS: documentation, cleanup, archive
BRANCH_NAME: pr-docs-debt-cleanup
BRANCH_BASE: main
PR_TITLE: docs: archive 62 historical documents and clean up documentation debt
PR_BODY: ## Summary
- Archive 62 historical documents to docs/archive/
- Move superseded plans, phase reports, and historical specs
- Update .gitignore for archived content
- Update docs.json navigation
- Add ether-repl-spec.md (504 lines)

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 39)
- No dependencies
MERGE_ORDER: 1
DEPENDS_ON:
BATCH_WITH: pdf-bundle-generator, runtime-artifact-exclusions

================================================================================

PR-PLAN: etherrepl-security-fix
SOURCE_COMMIT: 0702252
ISSUE_TITLE: Resolve EtherREPL P0/P1 vulnerabilities (SEC-001 through SEC-003)
ISSUE_BODY: Resolve EtherREPL P0/P1 security vulnerabilities (SEC-001 through SEC-003) including code injection, sandbox escape, and path traversal. Changes span ether_repl.py (1161 lines), security module, component loader, and comprehensive security tests (513 lines).

This is a critical security fix that depends on the docs debt cleanup being merged. It addresses the highest priority security vulnerabilities in the code execution environment.
ISSUE_LABELS: security, etherrepl, sec-001, sec-002, sec-003, critical
BRANCH_NAME: pr-etherrepl-security-fix
BRANCH_BASE: main
PR_TITLE: fix(security): resolve EtherREPL P0/P1 vulnerabilities (SEC-001 through SEC-003)
PR_BODY: ## Summary
- Fix SEC-001: Code injection vulnerability
- Fix SEC-002: Sandbox escape vulnerability
- Fix SEC-003: Path traversal vulnerability
- Add 513 lines of security tests

## Security Impact
- Prevents arbitrary code execution via EtherREPL
- Blocks sandbox escape attempts
- Validates all file system operations

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 40)
- Depends on: docs-debt-cleanup
MERGE_ORDER: 13
DEPENDS_ON: pr-docs-debt-cleanup
BATCH_WITH:

================================================================================

PR-PLAN: phase5-milestone3-agents
SOURCE_COMMIT: 54c5499
ISSUE_TITLE: Complete Phase 5 milestone 3 — pipeline agents, UI fixes, ecosystem docs
ISSUE_BODY: Complete Phase 5 milestone 3 with pipeline agents, Agent UI rendering fixes, and ecosystem documentation. Adds 20 agent configurations, migrates YAML to MD format, implements capability model, and updates PipelineRunner and AgentRegistry UI. Extensive changes across agent configs, docs, scripts, source code, and tests.

This depends on agent ecosystem docs and component framework loader. It is a large milestone completion that ties together multiple Phase 5 workstreams.
ISSUE_LABELS: feature, phase5, milestone3, agents, ecosystem
BRANCH_NAME: pr-phase5-milestone3-agents
BRANCH_BASE: main
PR_TITLE: feat(phase5): complete milestone 3 — pipeline agents, UI fixes, ecosystem docs
PR_BODY: ## Summary
- Add 20 agent configurations in MD format
- Migrate agent configs from YAML to MD
- Implement capability model
- Fix Agent UI rendering for pipeline agents
- Update ecosystem documentation

## Components
- 20 agent config files under config/agents/
- Migration script: migrate_agents_yaml_to_md.py
- Core: agents/registry.py, capabilities.py
- UI: PipelineRunner, AgentRegistry updates

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 41)
- Depends on: agent-ecosystem-docs, component-framework-loader
MERGE_ORDER: 73
DEPENDS_ON: pr-agent-ecosystem-design-spec, pr-component-framework-loader
BATCH_WITH:

================================================================================

PR-PLAN: phase5-agent-docs
SOURCE_COMMIT: 8522e0b
ISSUE_TITLE: Update phase 5 docs — agent ecosystem display added to Pipeline Runner
ISSUE_BODY: Update phase 5 documentation with agent ecosystem display additions to Pipeline Runner. Updates future-where-to-resume-left-off.md to reflect the completed agent ecosystem display feature.

This depends on the agent ecosystem display being implemented. Documentation-only change.
ISSUE_LABELS: documentation, phase5, agents
BRANCH_NAME: pr-phase5-agent-docs
BRANCH_BASE: main
PR_TITLE: docs: update phase 5 — agent ecosystem display added to Pipeline Runner
PR_BODY: ## Summary
- Update phase 5 status documentation
- Document agent ecosystem display in Pipeline Runner
- Update roadmap resume point

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 42)
- Depends on: agent-ecosystem-display
MERGE_ORDER: 75
DEPENDS_ON: pr-agent-ecosystem-display
BATCH_WITH:

================================================================================

PR-PLAN: agent-ecosystem-display
SOURCE_COMMIT: f22f48a
ISSUE_TITLE: Display agent ecosystem in Pipeline Runner
ISSUE_BODY: Add agent ecosystem display component to Pipeline Runner UI showing available agents and their capabilities. Changes in PipelineRunner.css and PipelineRunner.tsx add the visual agent ecosystem panel.

This depends on the pipeline runner page being implemented. It provides visibility into the agent ecosystem within the pipeline UI.
ISSUE_LABELS: feature, webui, pipeline, agents
BRANCH_NAME: pr-agent-ecosystem-display
BRANCH_BASE: main
PR_TITLE: feat(webui): display agent ecosystem in Pipeline Runner
PR_BODY: ## Summary
- Add agent ecosystem display to Pipeline Runner
- Show available agents and their capabilities
- Update PipelineRunner.css for new panel styling

## Components
- PipelineRunner.tsx — agent ecosystem panel
- PipelineRunner.css — panel styling

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 43)
- Depends on: pipeline-runner-page
MERGE_ORDER: 71
DEPENDS_ON: pr-pipeline-runner-page
BATCH_WITH:

================================================================================

PR-PLAN: phase5-runtime-verification-docs
SOURCE_COMMIT: cf3469f
ISSUE_TITLE: Update phase 5 status — runtime verified, all endpoints functional
ISSUE_BODY: Update phase 5 status documentation confirming runtime verification and all endpoints functional. Updates future-where-to-resume-left-off.md.

This depends on the double API prefix fix being resolved. Documentation-only status update.
ISSUE_LABELS: documentation, phase5, verification
BRANCH_NAME: pr-phase5-runtime-verification-docs
BRANCH_BASE: main
PR_TITLE: docs: update phase 5 status — runtime verified, all endpoints functional
PR_BODY: ## Summary
- Confirm runtime verification complete
- Document all endpoints as functional
- Update status tracking document

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 44)
- Depends on: webui-double-api-fix
MERGE_ORDER: 76
DEPENDS_ON: pr-webui-double-api-fix
BATCH_WITH:

================================================================================

PR-PLAN: webui-double-api-fix
SOURCE_COMMIT: 4faa22e
ISSUE_TITLE: Resolve double /api prefix in pipeline API calls
ISSUE_BODY: Resolve double /api prefix bug in pipeline API calls that was causing 404 errors in the web UI. Fix is in src/gaia/apps/webui/src/services/api.ts.

This depends on the pipeline runner page. It is a critical bug fix that blocks all pipeline API functionality.
ISSUE_LABELS: bugfix, webui, api, pipeline
BRANCH_NAME: pr-webui-double-api-fix
BRANCH_BASE: main
PR_TITLE: fix(webui): resolve double /api prefix in pipeline API calls
PR_BODY: ## Summary
- Fix double /api prefix bug causing 404 errors
- Update api.ts service to use correct URL paths
- Restores all pipeline API functionality

## Impact
- Critical: all pipeline API calls were failing with 404
- Fix enables all downstream pipeline features

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 45)
- Depends on: pipeline-runner-page
MERGE_ORDER: 70
DEPENDS_ON: pr-pipeline-runner-page
BATCH_WITH:

================================================================================

PR-PLAN: pipelinerunner-typescript-fix
SOURCE_COMMIT: 1761d70
ISSUE_TITLE: Resolve TypeScript errors in PipelineRunner and API service
ISSUE_BODY: Resolve TypeScript errors in PipelineRunner component and API service, including test file type mismatches. Changes in MetricsDashboard.test.tsx, PipelineRunner.tsx, and api.ts.

This depends on the pipeline runner page. It is a build fix for the TypeScript compiler.
ISSUE_LABELS: bugfix, typescript, webui, pipeline
BRANCH_NAME: pr-pipelinerunner-typescript-fix
BRANCH_BASE: main
PR_TITLE: fix(webui): resolve TypeScript errors in PipelineRunner and API service
PR_BODY: ## Summary
- Fix TypeScript errors in PipelineRunner.tsx
- Fix type mismatches in MetricsDashboard.test.tsx
- Fix API service type definitions

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 46)
- Depends on: pipeline-runner-page
MERGE_ORDER: 69
DEPENDS_ON: pr-pipeline-runner-page
BATCH_WITH: canvas-typescript-fix

================================================================================

PR-PLAN: pipelinerunner-accessibility
SOURCE_COMMIT: 859058f
ISSUE_TITLE: Improve PipelineRunner accessibility and state management
ISSUE_BODY: Improve PipelineRunner accessibility and state management with ARIA attributes, better keyboard navigation, and state synchronization fixes. Changes in PipelineRunner.tsx and documentation updates in agent-ui.mdx, pipeline.mdx, and cli.mdx.

This depends on the pipeline runner page. It improves accessibility compliance and user experience.
ISSUE_LABELS: bugfix, accessibility, webui, pipeline, a11y
BRANCH_NAME: pr-pipelinerunner-accessibility
BRANCH_BASE: main
PR_TITLE: fix(webui): improve PipelineRunner accessibility and state management
PR_BODY: ## Summary
- Add ARIA attributes for screen reader support
- Improve keyboard navigation
- Fix state synchronization issues
- Update related documentation

## Standards
- WCAG 2.1 AA compliance
- Keyboard navigation support
- Screen reader compatibility

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 47)
- Depends on: pipeline-runner-page
MERGE_ORDER: 72
DEPENDS_ON: pr-pipeline-runner-page
BATCH_WITH:

================================================================================

PR-PLAN: pipeline-runner-page
SOURCE_COMMIT: 33686dd
ISSUE_TITLE: Add Pipeline Runner page with SSE streaming execution UI
ISSUE_BODY: Add Pipeline Runner page to Agent UI with SSE streaming execution interface for monitoring and controlling pipeline runs. Changes in App.tsx, Sidebar.tsx, PipelineRunner.css, and PipelineRunner.tsx.

This depends on the pipeline engine wiring being complete. It is the primary user-facing interface for pipeline execution.
ISSUE_LABELS: feature, webui, pipeline, sse
BRANCH_NAME: pr-pipeline-runner-page
BRANCH_BASE: main
PR_TITLE: feat(webui): add Pipeline Runner page with SSE streaming execution UI
PR_BODY: ## Summary
- Add Pipeline Runner page to Agent UI
- Implement SSE streaming execution interface
- Add pipeline monitoring and controls
- Update App navigation and sidebar

## Components
- PipelineRunner.tsx — main execution UI
- PipelineRunner.css — execution UI styling
- App.tsx — navigation integration
- Sidebar.tsx — sidebar entry

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 48)
- Depends on: pipeline-engine-wiring
MERGE_ORDER: 44
DEPENDS_ON: pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: phase5-docs-coherence
SOURCE_COMMIT: c9abc59
ISSUE_TITLE: Final documentation coherence fixes for Phase 5 merge
ISSUE_BODY: Final documentation coherence fixes for Phase 5 merge including PR documentation, merge verification report, and update manifest. Updates PR_PIPELINE_ORCHESTRATION.md, phase5-merge-verification.md (133 lines), and phase5-update-manifest.md.

This depends on the auto-spawn pipeline being complete. It ensures all Phase 5 documentation is consistent and accurate for merge.
ISSUE_LABELS: documentation, phase5, coherence, merge
BRANCH_NAME: pr-phase5-docs-coherence
BRANCH_BASE: main
PR_TITLE: docs: final Phase 5 documentation coherence fixes for merge
PR_BODY: ## Summary
- Update PR documentation for Phase 5 merge
- Add merge verification report (133 lines)
- Update Phase 5 manifest
- Ensure documentation consistency

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 49)
- Depends on: auto-spawn-pipeline
MERGE_ORDER: 78
DEPENDS_ON: pr-auto-spawn-pipeline
BATCH_WITH:

================================================================================

PR-PLAN: sse-endpoint-tests
SOURCE_COMMIT: 3b6ebe6
ISSUE_TITLE: Add SSE endpoint lock release and JSON serialization tests
ISSUE_BODY: Add SSE endpoint lock release tests and JSON serialization tests for pipeline router endpoints. Tests in test_pipeline_json_serialization.py (178 lines) and test_pipeline_sse_lock_release.py (216 lines).

This depends on pipeline SSE wiring. It provides critical test coverage for SSE endpoint behavior.
ISSUE_LABELS: testing, sse, pipeline, endpoints
BRANCH_NAME: pr-sse-endpoint-tests
BRANCH_BASE: main
PR_TITLE: test(pipeline): add SSE endpoint lock release and JSON serialization tests
PR_BODY: ## Summary
- Add SSE lock release tests (216 lines)
- Add JSON serialization tests (178 lines)
- Test pipeline router endpoint behavior

## Coverage
- SSE connection lock release under various conditions
- JSON serialization of pipeline responses
- Edge cases in SSE event delivery

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 50)
- Depends on: pipeline-sse-wiring
MERGE_ORDER: 74
DEPENDS_ON: pr-pipeline-sse-wiring
BATCH_WITH:

================================================================================

PR-PLAN: session3-quality-review-fixes
SOURCE_COMMIT: 9b19f90
ISSUE_TITLE: Resolve Session-3 quality review bugs and complete documentation
ISSUE_BODY: Resolve Session-3 quality review bugs across pipeline routing engine, agent registry bridge, capability migration, and complete documentation. Changes span api.ts, pipelineStore.ts, types, routing_engine.py, pipeline router, schemas, integration tests, agent registry tests, capability migration tests, orchestrator tests, documentation quality tests, and migration utilities.

This depends on canvas config quality bridge. It is a comprehensive bug fix pass addressing all Session-3 quality review findings.
ISSUE_LABELS: bugfix, quality-review, pipeline, session3
BRANCH_NAME: pr-session3-quality-review-fixes
BRANCH_BASE: main
PR_TITLE: fix(pipeline): resolve Session-3 quality review bugs and complete documentation
PR_BODY: ## Summary
- Fix pipeline routing engine bugs
- Fix agent registry bridge issues
- Fix capability migration problems
- Complete documentation quality fixes
- Update test suite for all fixes

## Impact
- Resolves all Session-3 quality review findings
- Ensures pipeline routing works correctly
- Fixes agent registry integration

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 51)
- Depends on: canvas-config-quality-bridge
MERGE_ORDER: 77
DEPENDS_ON: pr-canvas-config-quality-bridge
BATCH_WITH:

================================================================================

PR-PLAN: e2e-pipeline-timeout-fix
SOURCE_COMMIT: 0ae23c9
ISSUE_TITLE: Fix E2E pipeline integration timeout after Session-2 changes
ISSUE_BODY: Fix E2E pipeline integration timeout after Session-2 changes to ensure reliable end-to-end test execution. Changes in tests/e2e/test_full_pipeline.py.

This depends on pipeline CLI wiring. It fixes test reliability issues introduced by Session-2 changes.
ISSUE_LABELS: testing, e2e, pipeline, timeout
BRANCH_NAME: pr-e2e-pipeline-timeout-fix
BRANCH_BASE: main
PR_TITLE: test(pipeline): fix E2E pipeline integration timeout after Session-2 changes
PR_BODY: ## Summary
- Fix E2E test timeout issues
- Ensure reliable end-to-end test execution
- Adapt to Session-2 timing changes

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 52)
- Depends on: pipeline-cli-wiring
MERGE_ORDER: 82
DEPENDS_ON: pr-pipeline-cli-wiring
BATCH_WITH:

================================================================================

PR-PLAN: pipeline-cli-wiring
SOURCE_COMMIT: 71d5d48
ISSUE_TITLE: Resolve all hard-stop runtime bugs and wire gaia pipeline CLI
ISSUE_BODY: Resolve all hard-stop runtime bugs and wire gaia pipeline CLI commands for pipeline orchestration execution. Changes in cli.py, pipeline __init__.py, orchestrator.py, all pipeline stage files, conftest.py, and integration tests (122 lines).

This depends on auto-spawn pipeline and execute tool dispatch fix. It is a critical wiring fix that enables the pipeline CLI to function.
ISSUE_LABELS: bugfix, cli, pipeline, runtime
BRANCH_NAME: pr-pipeline-cli-wiring
BRANCH_BASE: main
PR_TITLE: fix(pipeline): resolve runtime bugs and wire gaia pipeline CLI
PR_BODY: ## Summary
- Fix all hard-stop runtime bugs in pipeline
- Wire gaia pipeline CLI commands
- Update all pipeline stage files
- Add integration tests (122 lines)

## Impact
- Critical: enables pipeline CLI execution
- Resolves all blocking runtime errors

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 53)
- Depends on: auto-spawn-pipeline, execute-tool-dispatch-fix
MERGE_ORDER: 80
DEPENDS_ON: pr-auto-spawn-pipeline, pr-execute-tool-dispatch-fix
BATCH_WITH:

================================================================================

PR-PLAN: execute-tool-dispatch-fix
SOURCE_COMMIT: 242e380
ISSUE_TITLE: Fix execute_tool dispatch bugs blocking real pipeline runs
ISSUE_BODY: Fix execute_tool dispatch bugs that were blocking real pipeline runs across all pipeline stages. Changes in orchestrator.py, all pipeline stage files, orchestrator tests, and agent ecosystem design spec.

This depends on auto-spawn pipeline. It fixes a critical dispatch bug that prevents actual pipeline execution.
ISSUE_LABELS: bugfix, pipeline, dispatch, runtime
BRANCH_NAME: pr-execute-tool-dispatch-fix
BRANCH_BASE: main
PR_TITLE: fix(pipeline): fix execute_tool dispatch bugs blocking real pipeline runs
PR_BODY: ## Summary
- Fix execute_tool dispatch bugs across all pipeline stages
- Enable real pipeline execution
- Update orchestrator and stage files
- Fix agent ecosystem design spec

## Impact
- Critical: blocks all real pipeline runs
- Fix enables actual tool execution in pipelines

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 54)
- Depends on: auto-spawn-pipeline
MERGE_ORDER: 79
DEPENDS_ON: pr-auto-spawn-pipeline
BATCH_WITH:

================================================================================

PR-PLAN: phase6-matrix-update-74
SOURCE_COMMIT: 52df806
ISSUE_TITLE: Update matrix for Phase 6 pull (984 files, 74 commits)
ISSUE_BODY: Update branch change matrix for Phase 6 pull tracking (984 files, 74 commits). Changes to docs/reference/branch-change-matrix.md.

This is a standalone documentation-only change tracking Phase 6 pull metrics.
ISSUE_LABELS: documentation, phase6, matrix
BRANCH_NAME: pr-phase6-matrix-update-74
BRANCH_BASE: main
PR_TITLE: docs: update branch change matrix for Phase 6 pull (984 files, 74 commits)
PR_BODY: ## Summary
- Update branch-change-matrix.md for Phase 6
- Track 984 files and 74 commits

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 55)
- No dependencies
MERGE_ORDER: 1
DEPENDS_ON:
BATCH_WITH: phase6-matrix-update-73

================================================================================

PR-PLAN: design-spec-coherence
SOURCE_COMMIT: e28a922
ISSUE_TITLE: Resolve Open Item 5 — Update design spec coherence for Phase 5/6
ISSUE_BODY: Resolve Open Item 5 — update design spec coherence for Phase 5/6 alignment. Changes in branch-change-matrix.md and agent-ecosystem-design-spec.md ensure consistency between Phase 5 and Phase 6 specifications.

This is a standalone documentation fix that can be batched with other doc updates.
ISSUE_LABELS: documentation, design-spec, coherence
BRANCH_NAME: pr-design-spec-coherence
BRANCH_BASE: main
PR_TITLE: docs: resolve Open Item 5 — update design spec coherence for Phase 5/6
PR_BODY: ## Summary
- Update design spec for Phase 5/6 alignment
- Resolve Open Item 5 from review
- Ensure specification consistency

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 56)
- No dependencies
MERGE_ORDER: 1
DEPENDS_ON: phase6-matrix-update-74, phase6-matrix-update-73
BATCH_WITH:

================================================================================

PR-PLAN: phase6-matrix-update-73
SOURCE_COMMIT: 49b6704
ISSUE_TITLE: Update matrix for Phase 6 pull (984 files, 73 commits)
ISSUE_BODY: Update branch change matrix for Phase 6 pull tracking (984 files, 73 commits). Changes to docs/reference/branch-change-matrix.md.

Standalone documentation-only change.
ISSUE_LABELS: documentation, phase6, matrix
BRANCH_NAME: pr-phase6-matrix-update-73
BRANCH_BASE: main
PR_TITLE: docs: update branch change matrix for Phase 6 pull (984 files, 73 commits)
PR_BODY: ## Summary
- Update branch-change-matrix.md for Phase 6
- Track 984 files and 73 commits

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 57)
- No dependencies
MERGE_ORDER: 1
DEPENDS_ON:
BATCH_WITH: phase6-matrix-update-74

================================================================================

PR-PLAN: auto-spawn-pipeline
SOURCE_COMMIT: 41ee396
ISSUE_TITLE: Complete five-stage auto-spawn pipeline implementation
ISSUE_BODY: Complete the five-stage auto-spawn pipeline implementation with DomainAnalyzer, GapDetector, LoomBuilder, PipelineExecutor, and WorkflowModeler stages. Extensive changes across documentation (712-line state flow spec, 554-line code review feedback spec, 434-line capability model spec), stage implementations, E2E tests, and unit tests.

This is a major feature that depends on all five individual stage implementations. It ties together the complete auto-spawn pipeline.
ISSUE_LABELS: feature, pipeline, auto-spawn, five-stage
BRANCH_NAME: pr-auto-spawn-pipeline
BRANCH_BASE: main
PR_TITLE: feat(pipeline): complete five-stage auto-spawn pipeline implementation
PR_BODY: ## Summary
- Complete 5-stage auto-spawn pipeline
- Integrate DomainAnalyzer, GapDetector, LoomBuilder, PipelineExecutor, WorkflowModeler
- Add state flow specification (712 lines)
- Add code review feedback documentation (554 lines)
- Add unified capability model (434 lines)

## Pipeline Stages
1. DomainAnalyzer — domain analysis and component recommendations
2. GapDetector — identify missing pipeline stages
3. WorkflowModeler — workflow pattern selection
4. LoomBuilder — execution graph construction
5. PipelineExecutor — actual pipeline execution

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 58)
- Depends on: all five stage implementations
MERGE_ORDER: 35
DEPENDS_ON: pr-gap-detector, pr-domain-analyzer, pr-workflow-modeler, pr-loom-builder, pr-pipeline-executor
BATCH_WITH:

================================================================================

PR-PLAN: pr606-integration-analysis
SOURCE_COMMIT: 5c52eb8
ISSUE_TITLE: Add PR #606 integration analysis for feature/pipeline-orchestration-v1
ISSUE_BODY: Add PR #606 integration analysis document for feature/pipeline-orchestration-v1 branch. Creates docs/reference/pr606-integration-analysis.md (531 lines) and updates branch-change-matrix.md.

Standalone documentation-only change.
ISSUE_LABELS: documentation, integration-analysis, pr606
BRANCH_NAME: pr-pr606-integration-analysis
BRANCH_BASE: main
PR_TITLE: docs: add PR #606 integration analysis for pipeline-orchestration-v1
PR_BODY: ## Summary
- Add PR #606 integration analysis (531 lines)
- Update branch change matrix

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 59)
- No dependencies
MERGE_ORDER: 2
DEPENDS_ON:
BATCH_WITH: pr720-integration-analysis, pipeline-pr-description

================================================================================

PR-PLAN: phase5-matrix-design-docs
SOURCE_COMMIT: 6f839a6
ISSUE_TITLE: Update matrix and design docs for Phase 5 pull (970 files, 71 commits)
ISSUE_BODY: Update matrix and design docs for Phase 5 pull (970 files, 71 commits). Updates branch-change-matrix.md, agent-ecosystem-design-spec.md, phase5-update-manifest.md (600 lines), and senior-dev-work-order.md.

Standalone documentation update.
ISSUE_LABELS: documentation, phase5, matrix, design
BRANCH_NAME: pr-phase5-matrix-design-docs
BRANCH_BASE: main
PR_TITLE: docs: update matrix and design docs for Phase 5 pull (970 files, 71 commits)
PR_BODY: ## Summary
- Update branch change matrix for Phase 5
- Update agent ecosystem design spec
- Add phase 5 update manifest (600 lines)
- Update senior developer work order

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 60)
- No dependencies
MERGE_ORDER: 2
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: gap-detector
SOURCE_COMMIT: fa3ef98
ISSUE_TITLE: Add autonomous agent spawning with GapDetector
ISSUE_BODY: Add autonomous agent spawning with GapDetector for identifying missing pipeline stages and auto-provisioning required agents. Implementation in orchestrator.py (518 lines), gap_detector.py (419 lines), stages __init__.py, auto-spawn-pipeline.mdx guide (353 lines), and docs.json.

This depends on component framework templates being in place. It provides the gap detection and auto-provisioning capability for the pipeline.
ISSUE_LABELS: feature, pipeline, gap-detector, auto-spawn
BRANCH_NAME: pr-autonomous-agent-spawning
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add autonomous agent spawning with GapDetector
PR_BODY: ## Summary
- Implement GapDetector (419 lines) for stage gap identification
- Add autonomous agent provisioning logic
- Update orchestrator (518 lines) for auto-spawn
- Add user guide (353 lines)

## Components
- `src/gaia/pipeline/stages/gap_detector.py` — gap detection
- `src/gaia/pipeline/orchestrator.py` — auto-spawn logic
- `docs/guides/auto-spawn-pipeline.mdx` — user guide

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 61)
- Depends on: component-framework-templates
MERGE_ORDER: 33
DEPENDS_ON: pr-component-framework-templates
BATCH_WITH:

================================================================================

PR-PLAN: quality-gate7-tests
SOURCE_COMMIT: f57e5ba
ISSUE_TITLE: Add Quality Gate 7 validation tests and report
ISSUE_BODY: Add Quality Gate 7 validation tests and report covering end-to-end pipeline quality verification. Changes in quality-gate-7-plan.md, quality-gate-7-report.md (356 lines), test_quality_gate_7.py (1184 lines), integration __init__.py, and frontmatter parser tests (493 lines).

This depends on auto-spawn pipeline being complete. It provides the final quality gate validation for the pipeline.
ISSUE_LABELS: testing, quality-gate, pipeline, e2e
BRANCH_NAME: pr-quality-gate7-tests
BRANCH_BASE: main
PR_TITLE: test(pipeline): add Quality Gate 7 validation tests and report
PR_BODY: ## Summary
- Add Quality Gate 7 validation tests (1184 lines)
- Add quality gate report (356 lines)
- Add frontmatter parser tests (493 lines)
- Complete quality gate 7 plan documentation

## Coverage
- End-to-end pipeline quality verification
- Frontmatter parser validation
- Quality gate evaluation criteria

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 62)
- Depends on: auto-spawn-pipeline
MERGE_ORDER: 83
DEPENDS_ON: pr-auto-spawn-pipeline
BATCH_WITH:

================================================================================

PR-PLAN: component-framework-templates
SOURCE_COMMIT: e952716
ISSUE_TITLE: Complete component-framework templates and tool calling docs
ISSUE_BODY: Complete component-framework templates and tool calling documentation with 13 template types, 4 persona definitions, 5 workflow patterns, and explicit tool calling guide. Extensive changes across component-framework directories, documentation, quality reports, and test files.

This depends on the component framework loader being implemented. It provides the actual template content and workflow patterns used by the framework.
ISSUE_LABELS: feature, component-framework, templates, tool-calling
BRANCH_NAME: pr-component-framework-templates
BRANCH_BASE: main
PR_TITLE: feat(utils): complete component-framework templates and tool calling docs
PR_BODY: ## Summary
- Add 13 template types under component-framework/templates/
- Add 4 persona definitions under component-framework/personas/
- Add 5 workflow patterns under component-framework/workflows/
- Add explicit tool calling guide (336 lines)
- Add Phase 3 Sprint 2 technical spec (2278 lines)

## Components
- Template files: checklists, commands, documents, knowledge, memory, tasks
- Persona files: 4 agent persona definitions
- Workflow files: 5 workflow patterns

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 63)
- Depends on: component-framework-loader
MERGE_ORDER: 32
DEPENDS_ON: pr-component-framework-loader
BATCH_WITH:

================================================================================

PR-PLAN: pipeline-executor
SOURCE_COMMIT: 0c5f294
ISSUE_TITLE: Add PipelineExecutor stage for agent orchestration execution
ISSUE_BODY: Add PipelineExecutor stage for agent orchestration execution, handling the actual execution of orchestrated agent pipelines. Implementation in src/gaia/pipeline/stages/pipeline_executor.py (488 lines).

This depends on the LoomBuilder stage being implemented. It is the final stage in the auto-spawn pipeline that actually executes the orchestrated agents.
ISSUE_LABELS: feature, pipeline, executor, auto-spawn
BRANCH_NAME: pr-pipeline-executor
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add PipelineExecutor stage for agent orchestration execution
PR_BODY: ## Summary
- Implement PipelineExecutor stage (488 lines)
- Handle actual execution of orchestrated agent pipelines
- Coordinate agent lifecycle and execution results

## Components
- `src/gaia/pipeline/stages/pipeline_executor.py` — executor implementation

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 64)
- Depends on: loom-builder
MERGE_ORDER: 34
DEPENDS_ON: pr-loom-builder
BATCH_WITH:

================================================================================

PR-PLAN: loom-builder
SOURCE_COMMIT: 8dd22c1
ISSUE_TITLE: Add LoomBuilder stage for agent execution graph construction
ISSUE_BODY: Add LoomBuilder stage for agent execution graph construction, creating the execution topology for pipeline agents. Implementation in src/gaia/pipeline/stages/loom_builder.py (426 lines).

This depends on the WorkflowModeler stage being implemented. It builds the execution graph based on the selected workflow pattern.
ISSUE_LABELS: feature, pipeline, loom-builder, auto-spawn
BRANCH_NAME: pr-loom-builder
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add LoomBuilder stage for agent execution graph construction
PR_BODY: ## Summary
- Implement LoomBuilder stage (426 lines)
- Create execution topology for pipeline agents
- Build execution graph from workflow model

## Components
- `src/gaia/pipeline/stages/loom_builder.py` — loom builder implementation

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 65)
- Depends on: workflow-modeler
MERGE_ORDER: 30
DEPENDS_ON: pr-workflow-modeler
BATCH_WITH:

================================================================================

PR-PLAN: workflow-modeler
SOURCE_COMMIT: a32187c
ISSUE_TITLE: Add WorkflowModeler stage for workflow pattern selection
ISSUE_BODY: Add WorkflowModeler stage for workflow pattern selection, analyzing requirements and selecting appropriate workflow patterns for pipeline execution. Implementation in src/gaia/pipeline/stages/workflow_modeler.py (387 lines).

This depends on the DomainAnalyzer stage being implemented. It selects the workflow pattern based on domain analysis results.
ISSUE_LABELS: feature, pipeline, workflow, auto-spawn
BRANCH_NAME: pr-workflow-modeler
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add WorkflowModeler stage for workflow pattern selection
PR_BODY: ## Summary
- Implement WorkflowModeler stage (387 lines)
- Analyze requirements and select workflow patterns
- Integrate with domain analysis results

## Components
- `src/gaia/pipeline/stages/workflow_modeler.py` — workflow modeler

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 66)
- Depends on: domain-analyzer
MERGE_ORDER: 29
DEPENDS_ON: pr-domain-analyzer
BATCH_WITH:

================================================================================

PR-PLAN: domain-analyzer
SOURCE_COMMIT: 8d6ffdd
ISSUE_TITLE: Add DomainAnalyzer stage with component integration analysis
ISSUE_BODY: Add DomainAnalyzer stage with component integration analysis, examining project domain and recommending appropriate components for pipeline orchestration. Extensive changes across domain_analyzer.py (365 lines), engine.py, and numerous Phase 5 documentation files including design specs, implementation plans, risk registers, and quality gate plans.

This depends on the component framework loader and agent base tools. It is the first stage in the auto-spawn pipeline.
ISSUE_LABELS: feature, pipeline, domain-analyzer, auto-spawn
BRANCH_NAME: pr-domain-analyzer
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add DomainAnalyzer stage with component integration analysis
PR_BODY: ## Summary
- Implement DomainAnalyzer stage (365 lines)
- Examine project domain for component recommendations
- Add Phase 5 documentation (design specs, plans, risk register)

## Components
- `src/gaia/pipeline/stages/domain_analyzer.py` — domain analysis
- `src/gaia/pipeline/engine.py` — engine integration
- Phase 5 documentation suite

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 67)
- Depends on: agent-base-tools
MERGE_ORDER: 27
DEPENDS_ON: pr-agent-base-tools
BATCH_WITH:

================================================================================

PR-PLAN: agent-base-tools
SOURCE_COMMIT: 520bea3
ISSUE_TITLE: Add component framework tools to Agent base class
ISSUE_BODY: Add component framework tools to the Agent base class, enabling all agents to use component framework utilities. Changes in src/gaia/agents/base/agent.py (254 lines).

This depends on the component framework loader being implemented. It integrates component framework capabilities into all agents through the base class.
ISSUE_LABELS: feature, agents, component-framework, base-class
BRANCH_NAME: pr-agent-base-tools
BRANCH_BASE: main
PR_TITLE: feat(agents): add component framework tools to Agent base class
PR_BODY: ## Summary
- Add component framework tools to Agent base class (254 lines)
- Enable all agents to use component framework utilities
- Integrate with component framework loader

## Components
- `src/gaia/agents/base/agent.py` — updated base class

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 68)
- Depends on: component-framework-loader
MERGE_ORDER: 11
DEPENDS_ON: pr-component-framework-loader
BATCH_WITH:

================================================================================

PR-PLAN: component-framework-loader
SOURCE_COMMIT: 57ee63d
ISSUE_TITLE: Implement component framework template system with loader utility
ISSUE_BODY: Implement component framework template system with loader utility, frontmatter parser, and comprehensive template directories for checklists, commands, documents, knowledge, memory, and tasks. Implementation in component_loader.py (474 lines), frontmatter_parser.py (410 lines), and 24 template files across 6 categories. Includes 860 lines of unit tests.

This is a foundational utility with no code dependencies. It provides the template loading and parsing infrastructure used by all subsequent component framework features.
ISSUE_LABELS: feature, component-framework, loader, templates
BRANCH_NAME: pr-component-framework-loader
BRANCH_BASE: main
PR_TITLE: feat(utils): implement component framework template system with loader
PR_BODY: ## Summary
- Implement component loader utility (474 lines)
- Implement frontmatter parser (410 lines)
- Add 24 template files across 6 categories
- 860 lines of unit tests

## Template Categories
- checklists/ (4 files)
- commands/ (4 files)
- documents/ (4 files)
- knowledge/ (4 files)
- memory/ (4 files)
- tasks/ (4 files)

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 69)
- No dependencies
MERGE_ORDER: 10
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: agent-ecosystem-design-spec
SOURCE_COMMIT: 08b93eb
ISSUE_TITLE: Add agent ecosystem design spec, action plan, and senior-dev work order
ISSUE_BODY: Add agent ecosystem design specification (1814 lines), action plan (1299 lines), and senior developer work order (856 lines) defining the complete agent architecture for pipeline orchestration.

This is a foundational documentation artifact with no code dependencies. It defines the architecture that all subsequent agent implementations follow.
ISSUE_LABELS: documentation, agent-ecosystem, design-spec, architecture
BRANCH_NAME: pr-agent-ecosystem-design-spec
BRANCH_BASE: main
PR_TITLE: docs: add agent ecosystem design spec, action plan, and senior-dev work order
PR_BODY: ## Summary
- Add agent ecosystem design specification (1814 lines)
- Add agent ecosystem action plan (1299 lines)
- Add senior developer work order (856 lines)

## Coverage
- Complete agent architecture definition
- Implementation action plan
- Developer work assignment

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 70)
- No dependencies
MERGE_ORDER: 3
DEPENDS_ON:
BATCH_WITH: kpi-loom-specs

================================================================================

PR-PLAN: pr720-integration-analysis
SOURCE_COMMIT: 078739b
ISSUE_TITLE: Add PR #720 integration analysis for feature/pipeline-orchestration-v1
ISSUE_BODY: Add PR #720 integration analysis document for feature/pipeline-orchestration-v1 branch. Creates docs/reference/pr720-integration-analysis.md (321 lines).

Standalone documentation-only change.
ISSUE_LABELS: documentation, integration-analysis, pr720
BRANCH_NAME: pr-pr720-integration-analysis
BRANCH_BASE: main
PR_TITLE: docs: add PR #720 integration analysis for pipeline-orchestration-v1
PR_BODY: ## Summary
- Add PR #720 integration analysis (321 lines)
- Document integration points and dependencies

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 71)
- No dependencies
MERGE_ORDER: 2
DEPENDS_ON:
BATCH_WITH: pr606-integration-analysis, pipeline-pr-description

================================================================================

PR-PLAN: baibel-phase-status-fix
SOURCE_COMMIT: d794360
ISSUE_TITLE: Correct BAIBEL phase status and open items in branch-change-matrix
ISSUE_BODY: Correct BAIBEL phase status and open items in branch change matrix documentation. Updates docs/reference/branch-change-matrix.md with accurate phase status and open item tracking.

This depends on the branch change matrix being in place. Documentation-only correction.
ISSUE_LABELS: documentation, baibel, status, matrix
BRANCH_NAME: pr-baibel-phase-status-fix
BRANCH_BASE: main
PR_TITLE: docs: correct BAIBEL phase status and open items in branch-change-matrix
PR_BODY: ## Summary
- Correct BAIBEL phase status in branch change matrix
- Update open item tracking
- Ensure accurate phase reporting

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 72)
- Depends on: branch-change-matrix
MERGE_ORDER: 9
DEPENDS_ON: pr-branch-change-matrix
BATCH_WITH:

================================================================================

PR-PLAN: branch-change-matrix
SOURCE_COMMIT: 79b1861
ISSUE_TITLE: Add branch change matrix for feature/pipeline-orchestration-v1
ISSUE_BODY: Add comprehensive branch change matrix for feature/pipeline-orchestration-v1 tracking all changes, open items, and cross-branch dependencies. Creates docs/reference/branch-change-matrix.md (957 lines).

This is a foundational documentation artifact with no code dependencies. It provides the tracking mechanism for all branch changes.
ISSUE_LABELS: documentation, matrix, tracking
BRANCH_NAME: pr-branch-change-matrix
BRANCH_BASE: main
PR_TITLE: docs: add branch change matrix for feature/pipeline-orchestration-v1
PR_BODY: ## Summary
- Add comprehensive branch change matrix (957 lines)
- Track all changes across feature/pipeline-orchestration-v1
- Document open items and cross-branch dependencies

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 73)
- No dependencies
MERGE_ORDER: 4
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: minor-fixes-updates
SOURCE_COMMIT: 5931d85
ISSUE_TITLE: Minor fixes and updates across pipeline and agent modules
ISSUE_BODY: Apply minor fixes and updates across agent base, tools, configurable agents, perf module, pipeline audit logger, engine, and security module. Changes span agent.py, tools.py, configurable.py, perf __init__.py, audit_logger.py, engine.py, and security __init__.py.

This is a chore-level change with no specific dependencies. It consolidates small fixes across multiple modules.
ISSUE_LABELS: chore, cleanup, miscellaneous
BRANCH_NAME: pr-minor-fixes-updates
BRANCH_BASE: main
PR_TITLE: chore: minor fixes and updates across pipeline and agent modules
PR_BODY: ## Summary
- Minor fixes in agent base class and tools
- Updates to configurable agent
- Perf module updates
- Pipeline audit logger fixes
- Security module updates

## Files
- src/gaia/agents/base/agent.py, tools.py
- src/gaia/agents/configurable.py
- src/gaia/perf/__init__.py
- src/gaia/pipeline/audit_logger.py, engine.py
- src/gaia/security/__init__.py

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 74)
- No dependencies
MERGE_ORDER: 2
DEPENDS_ON:
BATCH_WITH: remove-claude-from-git

================================================================================

PR-PLAN: phase4-closeout-report
SOURCE_COMMIT: 82a6d42
ISSUE_TITLE: Add Phase 4 closeout report and update roadmap
ISSUE_BODY: Add Phase 4 closeout report documenting completion of health monitoring, resilience patterns, and data protection sprints. Creates docs/reference/phase4-closeout-report.md (737 lines) and updates future-where-to-resume-left-off.md.

This depends on data protection, resilience patterns, and health monitoring being complete. It is the Phase 4 closure documentation.
ISSUE_LABELS: documentation, phase4, closeout
BRANCH_NAME: pr-phase4-closeout-report
BRANCH_BASE: main
PR_TITLE: docs: add Phase 4 closeout report — health, resilience, data protection complete
PR_BODY: ## Summary
- Add Phase 4 closeout report (737 lines)
- Document health monitoring sprint completion
- Document resilience patterns sprint completion
- Document data protection sprint completion
- Update roadmap resume point

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 75)
- Depends on: data-protection-perf, resilience-patterns, health-monitoring
MERGE_ORDER: 18
DEPENDS_ON: pr-data-protection-perf, pr-resilience-patterns, pr-health-monitoring
BATCH_WITH:

================================================================================

PR-PLAN: data-protection-perf
SOURCE_COMMIT: 4c02e45
ISSUE_TITLE: Add Phase 4 Week 3 Data Protection and Performance Profiling
ISSUE_BODY: Add Phase 4 Week 3 data protection module (814 lines) and performance profiling (899 lines) with comprehensive test coverage for both components. Tests include profiler tests (873 lines) and data protection tests (766 lines).

This depends on resilience patterns being implemented. It completes the Phase 4 Week 3 deliverables.
ISSUE_LABELS: feature, phase4, data-protection, profiling, security
BRANCH_NAME: pr-data-protection-perf
BRANCH_BASE: main
PR_TITLE: feat(phase4): add Week 3 Data Protection and Performance Profiling
PR_BODY: ## Summary
- Implement data protection module (814 lines)
- Implement performance profiler (899 lines)
- Add profiler tests (873 lines)
- Add data protection tests (766 lines)

## Components
- `src/gaia/security/data_protection.py` — data protection
- `src/gaia/perf/profiler.py` — performance profiling

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 76)
- Depends on: resilience-patterns
MERGE_ORDER: 16
DEPENDS_ON: pr-resilience-patterns
BATCH_WITH:

================================================================================

PR-PLAN: resilience-patterns
SOURCE_COMMIT: 84ed269
ISSUE_TITLE: Add Phase 4 Week 2 Resilience Patterns (bulkhead, circuit breaker, retry)
ISSUE_BODY: Add Phase 4 Week 2 resilience patterns including bulkhead isolation (284 lines), circuit breaker (344 lines), and retry strategies (367 lines) with comprehensive test coverage (1826 lines of tests across 3 files).

This depends on health monitoring being implemented. It provides the resilience patterns that protect pipeline execution from cascading failures.
ISSUE_LABELS: feature, phase4, resilience, bulkhead, circuit-breaker, retry
BRANCH_NAME: pr-resilience-patterns
BRANCH_BASE: main
PR_TITLE: feat(phase4): add Week 2 Resilience Patterns — bulkhead, circuit breaker, retry
PR_BODY: ## Summary
- Implement bulkhead isolation (284 lines)
- Implement circuit breaker (344 lines)
- Implement retry strategies (367 lines)
- 1826 lines of comprehensive tests

## Test Coverage
- Bulkhead: 550 lines of tests
- Circuit breaker: 647 lines of tests
- Retry: 629 lines of tests

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 77)
- Depends on: health-monitoring
MERGE_ORDER: 15
DEPENDS_ON: pr-health-monitoring
BATCH_WITH:

================================================================================

PR-PLAN: health-monitoring
SOURCE_COMMIT: 8b05805
ISSUE_TITLE: Add Phase 4 Week 1 Health Monitoring module
ISSUE_BODY: Add Phase 4 Week 1 health monitoring module with health checker (870 lines), models (706 lines), and probes (1110 lines) for pipeline and agent health assessment. Comprehensive test coverage includes checker tests (652 lines), model tests (478 lines), and probe tests (718 lines).

This depends on the modular architecture core being implemented. It provides the foundational health monitoring capability for Phase 4.
ISSUE_LABELS: feature, phase4, health-monitoring, probes
BRANCH_NAME: pr-health-monitoring
BRANCH_BASE: main
PR_TITLE: feat(phase4): add Week 1 Health Monitoring module
PR_BODY: ## Summary
- Implement health checker (870 lines)
- Implement health models (706 lines)
- Implement health probes (1110 lines)
- 1848 lines of tests

## Components
- `src/gaia/health/checker.py` — health checking logic
- `src/gaia/health/models.py` — health data models
- `src/gaia/health/probes.py` — readiness/liveness probes

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 78)
- Depends on: modular-architecture-core
MERGE_ORDER: 14
DEPENDS_ON: pr-modular-architecture-core
BATCH_WITH:

================================================================================

PR-PLAN: phase3-sprint4-test-fixes
SOURCE_COMMIT: 7781ef9
ISSUE_TITLE: Resolve Phase 3 Sprint 4 integration test failures
ISSUE_BODY: Resolve Phase 3 Sprint 4 integration test failures across API and cache modules. Changes in openapi.py, cache_layer.py, API integration tests, and cache integration tests.

This depends on Phase 3 Sprint 4 observability being implemented. It fixes the failing integration tests.
ISSUE_LABELS: bugfix, integration-tests, phase3, sprint4
BRANCH_NAME: pr-phase3-sprint4-test-fixes
BRANCH_BASE: main
PR_TITLE: fix(phase3): resolve Sprint 4 integration test failures
PR_BODY: ## Summary
- Fix API integration test failures
- Fix cache integration test failures
- Update openapi.py for test compatibility
- Fix cache_layer.py for test compatibility

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 79)
- Depends on: phase3-sprint4-observability
MERGE_ORDER: 39
DEPENDS_ON: pr-phase3-sprint4-observability
BATCH_WITH:

================================================================================

PR-PLAN: phase3-closeout-report
SOURCE_COMMIT: 85b1f55
ISSUE_TITLE: Add Phase 3 Closeout Report — All 4 Sprints Complete
ISSUE_BODY: Add Phase 3 closeout report documenting completion of all 4 sprints: modular architecture, DI/performance, caching/config, and observability/API. Creates docs/reference/phase3-closeout-report.md (552 lines).

This depends on Phase 3 Sprint 4 observability being complete. It is the Phase 3 closure documentation.
ISSUE_LABELS: documentation, phase3, closeout
BRANCH_NAME: pr-phase3-closeout-report
BRANCH_BASE: main
PR_TITLE: docs: add Phase 3 Closeout Report — All 4 Sprints Complete
PR_BODY: ## Summary
- Add Phase 3 closeout report (552 lines)
- Document Sprint 1: Modular Architecture
- Document Sprint 2: DI and Performance
- Document Sprint 3: Caching and Config
- Document Sprint 4: Observability and API

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 80)
- Depends on: phase3-sprint4-observability
MERGE_ORDER: 38
DEPENDS_ON: pr-phase3-sprint4-observability
BATCH_WITH:

================================================================================

PR-PLAN: phase3-sprint4-observability
SOURCE_COMMIT: c25982b
ISSUE_TITLE: Phase 3 Sprint 4 — Observability and API Standardization
ISSUE_BODY: Phase 3 Sprint 4 — Observability and API standardization with metrics, logging, tracing, API versioning, deprecation management, and OpenAPI specification. Extensive implementation across observability module (core, prometheus exporter, logging formatter, metrics, tracing), API module (deprecation, openapi, versioning), and comprehensive integration and unit tests.

This depends on Phase 3 Sprint 3 caching being complete. It provides the observability and API standardization layer.
ISSUE_LABELS: feature, phase3, sprint4, observability, api
BRANCH_NAME: pr-phase3-sprint4-observability
BRANCH_BASE: main
PR_TITLE: feat(phase3): Sprint 4 — Observability and API Standardization
PR_BODY: ## Summary
- Implement observability module (metrics, logging, tracing)
- Implement API versioning and deprecation management
- Add OpenAPI specification
- Add Prometheus exporter
- Comprehensive integration and unit tests

## Components
- `src/gaia/observability/` — observability core
- `src/gaia/api/` — API versioning, deprecation, openapi
- Promethes exporter integration
- Structured logging formatter

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 81)
- Depends on: phase3-sprint3-caching
MERGE_ORDER: 26
DEPENDS_ON: pr-phase3-sprint3-caching
BATCH_WITH:

================================================================================

PR-PLAN: phase3-sprint3-caching
SOURCE_COMMIT: 64db788
ISSUE_TITLE: Phase 3 Sprint 3 — Caching and Enterprise Config
ISSUE_BODY: Phase 3 Sprint 3 — Caching system with disk cache, LRU cache, TTL management, and enterprise configuration management with secrets manager and validators. Extensive implementation across cache module (7 files), config module (6 files), and comprehensive integration, stress, and unit tests.

This depends on Phase 3 Sprint 2 DI being complete. It provides the caching and configuration infrastructure.
ISSUE_LABELS: feature, phase3, sprint3, caching, config
BRANCH_NAME: pr-phase3-sprint3-caching
BRANCH_BASE: main
PR_TITLE: feat(phase3): Sprint 3 — Caching and Enterprise Config
PR_BODY: ## Summary
- Implement caching system (disk cache, LRU, TTL)
- Implement enterprise config management
- Add secrets manager with validators
- Comprehensive integration and stress tests

## Cache Components
- `src/gaia/cache/cache_layer.py` — cache abstraction
- `src/gaia/cache/disk_cache.py` — disk-based cache
- `src/gaia/cache/lru_cache.py` — LRU cache
- `src/gaia/cache/ttl_manager.py` — TTL management
- `src/gaia/cache/stats.py` — cache statistics

## Config Components
- `src/gaia/config/config_manager.py` — config management
- `src/gaia/config/config_schema.py` — schema validation
- `src/gaia/config/secrets_manager.py` — secrets management
- `src/gaia/config/validators/` — config validators

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 82)
- Depends on: phase3-sprint2-di
MERGE_ORDER: 24
DEPENDS_ON: pr-phase3-sprint2-di
BATCH_WITH:

================================================================================

PR-PLAN: kpi-loom-specs
SOURCE_COMMIT: daf21f9
ISSUE_TITLE: Add KPI references, eval metrics, and GAIA Loom architecture specs
ISSUE_BODY: Add KPI references, evaluation metrics specifications, and GAIA Loom architecture specs defining the evaluation framework and metrics tracking. Includes 7 specification documents covering agent UI eval KPIs, eval metrics, GAIA Loom architecture, Nexus-GAIA integration, and pipeline metrics analysis.

This is a standalone documentation artifact with no code dependencies.
ISSUE_LABELS: documentation, kpi, evaluation, metrics, architecture
BRANCH_NAME: pr-kpi-loom-specs
BRANCH_BASE: main
PR_TITLE: docs: add KPI references, eval metrics, and GAIA Loom architecture specs
PR_BODY: ## Summary
- Add agent UI eval KPI reference (86 lines)
- Add eval KPI slides (371 lines)
- Add eval KPIs spec (696 lines)
- Add GAIA Loom architecture spec (851 lines)
- Add Nexus-GAIA integration spec (577 lines)
- Add pipeline metrics competitive analysis (355 lines)
- Add pipeline metrics KPI reference (258 lines)

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 83)
- No dependencies
MERGE_ORDER: 3
DEPENDS_ON:
BATCH_WITH: agent-ecosystem-design-spec

================================================================================

PR-PLAN: phase3-sprint2-di
SOURCE_COMMIT: 505d22f
ISSUE_TITLE: Phase 3 Sprint 2 — Dependency Injection and Performance
ISSUE_BODY: Phase 3 Sprint 2 — Dependency injection container with adapter pattern (545 lines), async utilities (703 lines), and connection pooling (787 lines) for performance optimization. Includes DI container (770 lines) and comprehensive tests for adapter, DI container, async utilities, and connection pool.

This depends on the modular architecture core being implemented. It provides the DI and performance optimization layer.
ISSUE_LABELS: feature, phase3, sprint2, dependency-injection, performance
BRANCH_NAME: pr-phase3-sprint2-di
BRANCH_BASE: main
PR_TITLE: feat(phase3): Sprint 2 — Dependency Injection and Performance
PR_BODY: ## Summary
- Implement DI container (770 lines)
- Implement adapter pattern (545 lines)
- Implement async utilities (703 lines)
- Implement connection pooling (787 lines)
- Comprehensive test coverage

## Components
- `src/gaia/core/di_container.py` — DI container
- `src/gaia/core/adapter.py` — adapter pattern
- `src/gaia/perf/async_utils.py` — async utilities
- `src/gaia/perf/connection_pool.py` — connection pooling

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 84)
- Depends on: modular-architecture-core
MERGE_ORDER: 12
DEPENDS_ON: pr-modular-architecture-core
BATCH_WITH:

================================================================================

PR-PLAN: modular-architecture-core
SOURCE_COMMIT: d8f0269
ISSUE_TITLE: Phase 3 Sprint 1 — Modular Architecture Core Implementation
ISSUE_BODY: Phase 3 Sprint 1 — Modular architecture core with capabilities model (417 lines), executor engine (649 lines), plugin system (790 lines), and profile management (508 lines). Includes implementation plans, technical specs, and integration documentation. Foundation for all subsequent Phase 3 sprints.

This is the Phase 3 foundation with no code dependencies. It provides the modular architecture that all Phase 2+ features build upon.
ISSUE_LABELS: feature, phase3, sprint1, modular-architecture, foundation
BRANCH_NAME: pr-modular-architecture-core
BRANCH_BASE: main
PR_TITLE: feat(phase3): Sprint 1 — Modular Architecture Core Implementation
PR_BODY: ## Summary
- Implement capabilities model (417 lines)
- Implement executor engine (649 lines)
- Implement plugin system (790 lines)
- Implement profile management (508 lines)
- Phase 3 implementation plan and technical specs

## Components
- `src/gaia/core/capabilities.py` — capability model
- `src/gaia/core/executor.py` — executor engine
- `src/gaia/core/plugin.py` — plugin system
- `src/gaia/core/profile.py` — profile management

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 85)
- Phase 3 foundation: no code dependencies
MERGE_ORDER: 6
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: baibel-integration-phases
SOURCE_COMMIT: 32f4cf4
ISSUE_TITLE: Complete BAIBEL Integration Phases 0, 1, 2
ISSUE_BODY: Complete BAIBEL integration Phases 0, 1, and 2 with pipeline isolation, quality supervision, security validation, workspace management, state management (nexus, context lens, relevance, token counter), and review operations. Extensive changes across state module, security module, quality module, pipeline isolation, tools, tests, and configuration.

This depends on the BAIBEL master spec being in place. It is a major integration that implements the first three phases of the BAIBEL-GAIA integration roadmap.
ISSUE_LABELS: feature, baibel, integration, phase0, phase1, phase2
BRANCH_NAME: pr-baibel-integration-phases
BRANCH_BASE: main
PR_TITLE: feat(baibel): complete BAIBEL Integration Phases 0, 1, 2
PR_BODY: ## Summary
- Complete Phase 0: pipeline isolation, quality supervision
- Complete Phase 1: security validation, workspace management
- Complete Phase 2: state management (nexus, context lens, relevance, token counter)
- Add review operations and comprehensive tests

## Components
- `src/gaia/pipeline/isolation.py` — pipeline isolation
- `src/gaia/quality/supervisor.py` — quality supervision
- `src/gaia/security/` — security validation
- `src/gaia/state/` — state management suite
- `src/gaia/tools/review_ops.py` — review operations

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 86)
- Depends on: baibel-master-spec
MERGE_ORDER: 5
DEPENDS_ON: pr-baibel-master-spec
BATCH_WITH:

================================================================================

PR-PLAN: baibel-master-spec
SOURCE_COMMIT: dc4ddda
ISSUE_TITLE: Add BAIBEL-GAIA Integration Master Specification — 4-phase roadmap
ISSUE_BODY: Add BAIBEL-GAIA Integration Master Specification defining 4-phase roadmap for conversation-compaction architecture with Phase 0 tool scoping ready. Creates baibel-gaia-integration-master.md (1191 lines), tool-scoping-test-plan.md (720 lines), and phase0-tool-scoping-integration.md (647 lines).

This is the foundational specification with no dependencies. It defines the complete BAIBEL-GAIA integration roadmap that all subsequent BAIBEL work follows.
ISSUE_LABELS: documentation, baibel, master-spec, foundation
BRANCH_NAME: pr-baibel-master-spec
BRANCH_BASE: main
PR_TITLE: docs: add BAIBEL-GAIA Integration Master Specification — 4-phase roadmap
PR_BODY: ## Summary
- Add BAIBEL-GAIA Integration Master Spec (1191 lines)
- Add tool scoping test plan (720 lines)
- Add Phase 0 tool scoping integration (647 lines)

## Coverage
- 4-phase integration roadmap
- Tool scoping and testing plan
- Phase 0 readiness criteria

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 87)
- Foundation spec: no dependencies
MERGE_ORDER: 2
DEPENDS_ON:
BATCH_WITH: branch-change-matrix

================================================================================

PR-PLAN: artifact-extractor
SOURCE_COMMIT: 1fbffb9
ISSUE_TITLE: Add artifact extractor for code file output and root cause docs
ISSUE_BODY: Add artifact extractor for code file output and root cause documentation for pipeline analysis. Creates src/gaia/pipeline/artifact_extractor.py (123 lines), docs/spec/pipeline-root-causes.md (166 lines), and examples/pipeline_demo.py.

This depends on the pipeline engine wiring being complete. It provides code artifact extraction capability for pipeline analysis.
ISSUE_LABELS: feature, pipeline, artifacts
BRANCH_NAME: pr-artifact-extractor
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add artifact extractor for code file output and root cause docs
PR_BODY: ## Summary
- Add artifact_extractor.py (123 lines) for code file extraction
- Add pipeline root causes documentation (166 lines)
- Add demo pipeline script

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 88)
- Depends on: pipeline-engine-wiring
MERGE_ORDER: 5
DEPENDS_ON: pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: rc2-tool-package
SOURCE_COMMIT: b533669
ISSUE_TITLE: Implement RC#2 tool package and fix RC#6/RC#8 in ConfigurableAgent
ISSUE_BODY: Implement RC#2 tool package with code, file, and shell operations. Fixed RC#6 and RC#8 issues in ConfigurableAgent. Changes span setup.py, src/gaia/agents/configurable.py, src/gaia/tools/__init__.py, src/gaia/tools/code_ops.py (164 lines), src/gaia/tools/file_ops.py (137 lines), src/gaia/tools/shell_ops.py (97 lines).

Standalone feature with no code dependencies. Provides the core tool operations for agent functionality.
ISSUE_LABELS: feature, tools, agents
BRANCH_NAME: pr-rc2-tool-package
BRANCH_BASE: main
PR_TITLE: feat(tools): implement RC#2 tool package and fix RC#6/RC#8 in ConfigurableAgent
PR_BODY: ## Summary
- Add code operations module (164 lines)
- Add file operations module (137 lines)
- Add shell operations module (97 lines)
- Fix RC#6 and RC#8 in ConfigurableAgent

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 89)
- No dependencies
MERGE_ORDER: 1
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: remove-claude-from-git
SOURCE_COMMIT: d14e3fe
ISSUE_TITLE: Remove .claude/ from git tracking and update .gitignore
ISSUE_BODY: Remove .claude/ directory from git tracking and update .gitignore. Cleaned up 24 agent definition files, command, and settings.

Standalone chore with no code dependencies. Can be merged with other cleanup changes.
ISSUE_LABELS: chore, cleanup
BRANCH_NAME: pr-remove-claude-from-git
BRANCH_BASE: main
PR_TITLE: chore: remove .claude/ from git tracking and update .gitignore
PR_BODY: ## Summary
- Remove .claude/ directory from git tracking
- Update .gitignore to exclude .claude/
- Clean up 24 agent definition files

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 90)
- No dependencies
MERGE_ORDER: 1
DEPENDS_ON:
BATCH_WITH: minor-fixes-updates

================================================================================

PR-PLAN: llm-output-propagation
SOURCE_COMMIT: eed48d2
ISSUE_TITLE: Propagate agent LLM outputs to state machine and improve output visibility
ISSUE_BODY: Propagate agent LLM outputs to state machine for improved output visibility in pipeline execution flow. Changes in examples/pipeline_demo.py, src/gaia/hooks/registry.py, src/gaia/pipeline/engine.py, src/gaia/quality/scorer.py.

This depends on the pipeline engine wiring being complete. It improves visibility of LLM outputs in the pipeline state machine.
ISSUE_LABELS: feature, pipeline, llm
BRANCH_NAME: pr-llm-output-propagation
BRANCH_BASE: main
PR_TITLE: feat(pipeline): propagate agent LLM outputs to state machine and improve output visibility
PR_BODY: ## Summary
- Propagate LLM outputs to pipeline state machine
- Improve output visibility in execution flow
- Update hooks registry and quality scorer

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 91)
- Depends on: pipeline-engine-wiring
MERGE_ORDER: 5
DEPENDS_ON: pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: demo-lemonade-integration
SOURCE_COMMIT: 8cce2d9
ISSUE_TITLE: Add demo scripts, Lemonade integration, and fix stub mode
ISSUE_BODY: Add demo scripts, Lemonade LLM backend integration, and fix stub mode for pipeline demonstration and testing. Changes span docs/docs.json, docs/spec/pipeline-demo-guide.md (100 lines), examples/pipeline_demo.py (188 lines), examples/pipeline_with_lemonade.py (358 lines), and pipeline/agent modules.

This depends on RC#2 tool package being implemented. It provides the LLM integration for pipeline demonstration.
ISSUE_LABELS: feature, pipeline, demo, lemonade
BRANCH_NAME: pr-demo-lemonade-integration
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add demo scripts, Lemonade integration, and fix stub mode
PR_BODY: ## Summary
- Add pipeline demo guide (100 lines)
- Add demo scripts (188 + 358 lines)
- Integrate Lemonade LLM backend
- Fix stub mode for testing

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 92)
- Depends on: rc2-tool-package
MERGE_ORDER: 2
DEPENDS_ON: pr-rc2-tool-package
BATCH_WITH:

================================================================================

PR-PLAN: model-id-support
SOURCE_COMMIT: 7832c7e
ISSUE_TITLE: Add model_id support across all pipeline layers
ISSUE_BODY: Add model_id support across all pipeline layers including agent configurations, pipeline templates, engine, loop manager, and recursive templates. Changes in config/agents/* (20 yaml files), config/pipeline_templates/* (3 files), and core pipeline modules.

This depends on the pipeline engine wiring being complete. It enables model selection at all pipeline layers.
ISSUE_LABELS: feature, pipeline, configuration
BRANCH_NAME: pr-model-id-support
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add model_id support across all pipeline layers
PR_BODY: ## Summary
- Add model_id to 20 agent configuration files
- Update 3 pipeline template files
- Add model_id support in engine, loop manager, recursive templates

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 93)
- Depends on: pipeline-engine-wiring
MERGE_ORDER: 5
DEPENDS_ON: pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: npm-oidc-publish
SOURCE_COMMIT: 4fe0441
ISSUE_TITLE: Upgrade npm to 11.5.1+ for OIDC trusted publishing
ISSUE_BODY: Upgrade npm to 11.5.1+ for OIDC trusted publishing in the NPM UI workflow. Changes in .github/workflows/publish-npm-ui.yml.

Standalone CI/CD fix with no code dependencies.
ISSUE_LABELS: bugfix, ci-cd, npm
BRANCH_NAME: pr-npm-oidc-publish
BRANCH_BASE: main
PR_TITLE: fix(ci): upgrade npm to 11.5.1+ for OIDC trusted publishing
PR_BODY: ## Summary
- Upgrade npm version for OIDC support
- Update publish-npm-ui.yml workflow

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 94)
- No dependencies
MERGE_ORDER: 4
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: webui-version-bump
SOURCE_COMMIT: b19d812
ISSUE_TITLE: Bump webui package.json version to 0.17.1
ISSUE_BODY: Bump webui package.json version to 0.17.1. Changes in src/gaia/apps/webui/package.json.

Standalone version bump with no code dependencies.
ISSUE_LABELS: bugfix, version
BRANCH_NAME: pr-webui-version-bump
BRANCH_BASE: main
PR_TITLE: fix: bump webui package.json version to 0.17.1
PR_BODY: ## Summary
- Bump webui package.json to version 0.17.1

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 95)
- No dependencies
MERGE_ORDER: 4
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: pipeline-eval-metrics
SOURCE_COMMIT: 31de02f
ISSUE_TITLE: Integrate pipeline performance metrics with agent eval framework
ISSUE_BODY: Integrate pipeline performance metrics with agent eval framework (Phase 2) including eval metrics module, UI router, and comprehensive tests. Changes span src/gaia/eval/eval_metrics.py (355 lines), src/gaia/ui/routers/eval_metrics.py (407 lines), and tests.

This depends on the metrics dashboard being implemented. It connects pipeline metrics to the evaluation framework.
ISSUE_LABELS: feature, pipeline, eval, metrics
BRANCH_NAME: pr-pipeline-eval-metrics
BRANCH_BASE: main
PR_TITLE: feat(eval): integrate pipeline performance metrics with agent eval framework
PR_BODY: ## Summary
- Add eval metrics module (355 lines)
- Add eval metrics UI router (407 lines)
- Add integration and unit tests

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 96)
- Depends on: metrics-dashboard
MERGE_ORDER: 7
DEPENDS_ON: pr-metrics-dashboard
BATCH_WITH:

================================================================================

PR-PLAN: pipeline-engine-wiring
SOURCE_COMMIT: 969eefe
ISSUE_TITLE: Fix engine wiring, add CLI stub, docs, examples, and smoke tests
ISSUE_BODY: Fix engine wiring, add gaia pipeline CLI stub, comprehensive documentation, example scripts, and smoke tests for the pipeline orchestration engine. Changes in docs/docs.json, docs/guides/pipeline.mdx (531 lines), docs/reference/cli.mdx, docs/sdk/infrastructure/pipeline.mdx (795 lines), docs/spec/pipeline-demo-plan-v2.md (1095 lines), docs/spec/pipeline-engine.mdx (346 lines), examples/*, setup.py, src/gaia/cli.py, src/gaia/pipeline/engine.py, tests/unit/test_pipeline_smoke.py.

This is the foundational pipeline commit with no dependencies. It establishes the pipeline engine wiring, CLI integration, and comprehensive documentation that all subsequent pipeline work depends on.
ISSUE_LABELS: feature, pipeline, foundation, cli
BRANCH_NAME: pr-pipeline-engine-wiring
BRANCH_BASE: main
PR_TITLE: feat(pipeline): fix engine wiring, add CLI stub, docs, examples, and smoke tests
PR_BODY: ## Summary
- Fix pipeline engine wiring
- Add gaia pipeline CLI stub
- Add comprehensive documentation (531 + 795 + 1095 + 346 lines)
- Add example pipeline scripts
- Add smoke tests

## Components
- Pipeline engine wiring and integration
- CLI stub for pipeline commands
- Documentation: guides, SDK reference, specs
- Example scripts for various pipeline patterns

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 99)
- No dependencies — pipeline foundation
MERGE_ORDER: 7
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: metrics-dashboard
SOURCE_COMMIT: 5d167c4
ISSUE_TITLE: Complete metrics dashboard, template management, and comprehensive testing
ISSUE_BODY: Complete metrics dashboard, template management, and comprehensive testing across pipeline engine, hooks, quality, and metrics systems. 133 files changed with 20948 insertions including metrics collector (889 lines), metrics hooks (596 lines), metrics service (524 lines), template service (501 lines).

This depends on the pipeline engine wiring being complete. It is a major feature that provides the metrics and template management infrastructure.
ISSUE_LABELS: feature, pipeline, metrics, templates
BRANCH_NAME: pr-metrics-dashboard
BRANCH_BASE: main
PR_TITLE: feat(pipeline): complete metrics dashboard, template management, and comprehensive testing
PR_BODY: ## Summary
- Add metrics dashboard with collector (889 lines)
- Add metrics hooks (596 lines)
- Add metrics service (524 lines) and template service (501 lines)
- 133 files changed, 20948 insertions
- Comprehensive testing across all subsystems

## Components
- Metrics collector, hooks, and service
- Template management system
- Pipeline metrics UI router

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 97)
- Depends on: pipeline-engine-wiring
MERGE_ORDER: 6
DEPENDS_ON: pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: release-v0171
SOURCE_COMMIT: bc26a31
ISSUE_TITLE: Release v0.17.1
ISSUE_BODY: Release v0.17.1 with version bump and release notes documentation. Changes in docs/docs.json, docs/releases/v0.17.1.mdx (69 lines), src/gaia/version.py.

This depends on the metrics dashboard being complete. It is the release commit for v0.17.1.
ISSUE_LABELS: release, v0.17.1
BRANCH_NAME: pr-release-v0171
BRANCH_BASE: main
PR_TITLE: chore: release v0.17.1
PR_BODY: ## Summary
- Bump version to v0.17.1
- Add release notes (69 lines)
- Update docs navigation

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 98)
- Depends on: metrics-dashboard
MERGE_ORDER: 8
DEPENDS_ON: pr-metrics-dashboard
BATCH_WITH:

================================================================================

PR-PLAN: lemonade-version-warning
SOURCE_COMMIT: 780a711
ISSUE_TITLE: Add Lemonade version mismatch warning, eval perf tracking, MCP stats
ISSUE_BODY: Add Lemonade version mismatch warning, eval performance tracking, and MCP stats monitoring. Changes in eval/prompts/*, src/gaia/eval/runner.py, src/gaia/eval/scorecard.py, src/gaia/llm/lemonade_client.py, src/gaia/mcp/mixin.py, and tests.

Standalone feature with no code dependencies. Improves developer experience with version warnings and performance tracking.
ISSUE_LABELS: feature, lemonade, eval, mcp
BRANCH_NAME: pr-lemonade-version-warning
BRANCH_BASE: main
PR_TITLE: feat: add Lemonade version mismatch warning, eval perf tracking, MCP stats
PR_BODY: ## Summary
- Add Lemonade version mismatch warning
- Add eval performance tracking
- Add MCP stats monitoring
- Add version check tests (119 lines)

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 100)
- No dependencies
MERGE_ORDER: 3
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: cpp-sse-streaming
SOURCE_COMMIT: 7ed2db3
ISSUE_TITLE: Add C++ SSE streaming response support
ISSUE_BODY: Add SSE streaming response support for C++ agent framework with SSE parser, lemonade client integration, and comprehensive tests. Changes in cpp/CMakeLists.txt, cpp/include/gaia/* (headers), cpp/src/* (139 + 92 lines), cpp/tests/* (287 lines).

Standalone C++ framework feature with no dependencies on Python pipeline. Foundation for C++ performance benchmarks and runtime config.
ISSUE_LABELS: feature, cpp, sse
BRANCH_NAME: pr-cpp-sse-streaming
BRANCH_BASE: main
PR_TITLE: feat(cpp): add SSE streaming response support for C++ agent framework
PR_BODY: ## Summary
- Add SSE parser for C++ (92 lines)
- Add Lemonade client integration (139 lines)
- Add comprehensive C++ tests (287 lines)
- Update CMakeLists.txt and README

## Components
- sse_parser.h/cpp — SSE response parsing
- lemonade_client.h/cpp — LLM client for C++
- Test suite for SSE parsing and console

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 101)
- No dependencies
MERGE_ORDER: 5
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: cpp-perf-benchmarks
SOURCE_COMMIT: 9c4101d
ISSUE_TITLE: Add C++ performance benchmarks and binary size tracking
ISSUE_BODY: Add C++ performance benchmarks with benchmark test suite, utilities, mock LLM server, and CI workflow for binary size tracking. Changes in .github/workflows/benchmark_cpp.yml (153 lines), cpp/benchmarks/* (335 + 282 + 154 lines).

This depends on C++ SSE streaming being implemented. It provides performance benchmarking infrastructure for the C++ framework.
ISSUE_LABELS: feature, cpp, benchmarks, ci-cd
BRANCH_NAME: pr-cpp-perf-benchmarks
BRANCH_BASE: main
PR_TITLE: feat(cpp): add performance benchmarks and binary size tracking
PR_BODY: ## Summary
- Add C++ benchmark test suite (335 lines)
- Add benchmark utilities (282 lines)
- Add mock LLM server (154 lines)
- Add CI workflow (153 lines)

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 102)
- Depends on: cpp-sse-streaming
MERGE_ORDER: 6
DEPENDS_ON: pr-cpp-sse-streaming
BATCH_WITH:

================================================================================

PR-PLAN: cpp-runtime-config
SOURCE_COMMIT: 878a976
ISSUE_TITLE: Add C++ runtime configuration and dynamic reconfiguration
ISSUE_BODY: Add runtime configuration and dynamic reconfiguration support for C++ agent framework with agent configuration, tool registry, and type system enhancements. Changes in cpp/include/gaia/* (headers), cpp/src/agent.cpp (228 lines), cpp/tests/*.

This depends on C++ SSE streaming being implemented. It enables dynamic agent configuration in the C++ framework.
ISSUE_LABELS: feature, cpp, configuration
BRANCH_NAME: pr-cpp-runtime-config
BRANCH_BASE: main
PR_TITLE: feat(cpp): add runtime configuration and dynamic reconfiguration
PR_BODY: ## Summary
- Add agent runtime configuration (228 lines)
- Add tool registry configuration
- Update type system (84 lines)
- Add configuration tests

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 103)
- Depends on: cpp-sse-streaming
MERGE_ORDER: 6
DEPENDS_ON: pr-cpp-sse-streaming
BATCH_WITH:

================================================================================

PR-PLAN: mcp-test-isolation
SOURCE_COMMIT: e0e5695
ISSUE_TITLE: Isolate MCP unit tests from real mcp_servers.json
ISSUE_BODY: Isolate MCP unit tests from real ~/.gaia/mcp_servers.json configuration to prevent test environment dependencies. Changes in tests/unit/mcp/client/test_mcp_client_manager.py.

Standalone test fix with no code dependencies.
ISSUE_LABELS: bugfix, testing, mcp
BRANCH_NAME: pr-mcp-test-isolation
BRANCH_BASE: main
PR_TITLE: fix(testing): isolate MCP unit tests from real mcp_servers.json
PR_BODY: ## Summary
- Isolate MCP tests from real configuration
- Prevent test environment dependencies

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 104)
- No dependencies
MERGE_ORDER: 3
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: agent-ui-build-init
SOURCE_COMMIT: bb010a0
ISSUE_TITLE: Build Agent UI frontend in gaia init and fix doc prerequisites
ISSUE_BODY: Add Agent UI frontend build to gaia init command and fix documentation prerequisites. Added UI build system and tests. Changes in docs/guides/agent-ui.mdx, docs/quickstart.mdx, src/gaia/cli.py, src/gaia/installer/init_command.py, src/gaia/ui/build.py (125 lines), tests.

Standalone fix with no code dependencies. Improves developer experience with automatic UI building.
ISSUE_LABELS: bugfix, ui, build, cli
BRANCH_NAME: pr-agent-ui-build-init
BRANCH_BASE: main
PR_TITLE: fix: build Agent UI frontend in gaia init and fix doc prerequisites
PR_BODY: ## Summary
- Add UI build system (125 lines)
- Integrate with gaia init command
- Add build tests (314 lines)
- Update documentation prerequisites

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 105)
- No dependencies
MERGE_ORDER: 4
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: pipeline-pr-description
SOURCE_COMMIT: 4345b92
ISSUE_TITLE: Add PR description for pipeline orchestration feature
ISSUE_BODY: Add PR description document for pipeline orchestration feature covering scope, implementation details, and testing approach. Creates PR_PIPELINE_ORCHESTRATION.md (355 lines).

Standalone documentation-only change with no code dependencies.
ISSUE_LABELS: documentation, pipeline
BRANCH_NAME: pr-pipeline-pr-description
BRANCH_BASE: main
PR_TITLE: docs: add PR description for pipeline orchestration feature
PR_BODY: ## Summary
- Add PR description document (355 lines)
- Document scope, implementation, and testing approach

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 106)
- No dependencies
MERGE_ORDER: 3
DEPENDS_ON:
BATCH_WITH: pr606-integration-analysis, pr720-integration-analysis

================================================================================

PR-PLAN: merge-upstream-main
SOURCE_COMMIT: 7e7ff14
ISSUE_TITLE: Merge upstream/main into feature/pipeline-orchestration-v1
ISSUE_BODY: Merged upstream/main into feature/pipeline-orchestration-v1 branch to incorporate latest changes from main.

Merge commit — no PR needed. Recorded for completeness.
ISSUE_LABELS: chore, merge
BRANCH_NAME: N/A (merge commit)
BRANCH_BASE: main
PR_TITLE: chore: merge upstream/main into feature/pipeline-orchestration-v1
PR_BODY: ## Summary
- Merge commit — recorded for traceability
- No PR needed

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 107)
- No dependencies
MERGE_ORDER: 4
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: version-py-proposal
SOURCE_COMMIT: 375091e
ISSUE_TITLE: Add __version__.py from pipeline proposal
ISSUE_BODY: Add __version__.py from pipeline proposal as part of large documentation and configuration update including Claude Code agents, CI workflows, and eval framework.

Standalone chore with no code dependencies.
ISSUE_LABELS: chore, configuration
BRANCH_NAME: pr-version-py-proposal
BRANCH_BASE: main
PR_TITLE: chore: add __version__.py from pipeline proposal
PR_BODY: ## Summary
- Add __version__.py module
- Update CI workflows and configuration

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 108)
- No dependencies
MERGE_ORDER: 4
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: missing-metrics-modules
SOURCE_COMMIT: c290ed7
ISSUE_TITLE: Add missing metrics, agents/definitions, and test modules
ISSUE_BODY: Add missing metrics, agent definitions, and test modules to complete pipeline orchestration infrastructure.

This depends on the pipeline engine wiring being complete. It fills gaps in the metrics and agent infrastructure.
ISSUE_LABELS: feature, pipeline, metrics, agents
BRANCH_NAME: pr-missing-metrics-modules
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add missing metrics, agents/definitions, and test modules
PR_BODY: ## Summary
- Add missing metrics modules
- Add agent definitions
- Add test modules

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 109)
- Depends on: pipeline-engine-wiring
MERGE_ORDER: 6
DEPENDS_ON: pr-pipeline-engine-wiring
BATCH_WITH:

================================================================================

PR-PLAN: remove-registry-url
SOURCE_COMMIT: 334b011
ISSUE_TITLE: Remove registry-url to enable OIDC trusted publishing
ISSUE_BODY: Remove registry-url configuration to enable OIDC trusted publishing for NPM packages. Changes in .github/workflows/publish-npm-ui.yml.

This depends on npm-oidc-publish being merged. It completes the OIDC publishing setup.
ISSUE_LABELS: bugfix, ci-cd, npm
BRANCH_NAME: pr-remove-registry-url
BRANCH_BASE: main
PR_TITLE: fix(ci): remove registry-url to enable OIDC trusted publishing
PR_BODY: ## Summary
- Remove registry-url from publish workflow
- Complete OIDC publishing setup

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 110)
- Depends on: npm-oidc-publish
MERGE_ORDER: 5
DEPENDS_ON: pr-npm-oidc-publish
BATCH_WITH:

================================================================================

PR-PLAN: merge-queue-notify-fix
SOURCE_COMMIT: 776dc34
ISSUE_TITLE: Resolve merge-queue-notify phantom failures
ISSUE_BODY: Resolve merge-queue-notify phantom failures in GitHub Actions workflow. Changes in .github/workflows/merge-queue-notify.yml.

Standalone CI/CD fix with no code dependencies.
ISSUE_LABELS: bugfix, ci-cd
BRANCH_NAME: pr-merge-queue-notify-fix
BRANCH_BASE: main
PR_TITLE: fix(ci): resolve merge-queue-notify phantom failures
PR_BODY: ## Summary
- Fix phantom failures in merge-queue-notify workflow
- Update GitHub Actions configuration

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 111)
- No dependencies
MERGE_ORDER: 4
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: npm-oidc-switch
SOURCE_COMMIT: 83a4db1
ISSUE_TITLE: Switch npm publish to OIDC trusted publishing
ISSUE_BODY: Switch npm publish workflow to OIDC trusted publishing for secure package distribution. Changes in .github/workflows/publish-npm-ui.yml.

This depends on npm-oidc-publish being merged. It completes the OIDC migration.
ISSUE_LABELS: bugfix, ci-cd, npm
BRANCH_NAME: pr-npm-oidc-switch
BRANCH_BASE: main
PR_TITLE: fix(ci): switch npm publish to OIDC trusted publishing
PR_BODY: ## Summary
- Switch npm publish to OIDC
- Update workflow for trusted publishing

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 112)
- Depends on: npm-oidc-publish
MERGE_ORDER: 4
DEPENDS_ON: pr-npm-oidc-publish
BATCH_WITH:

================================================================================

PR-PLAN: pipeline-engine-p1p6
SOURCE_COMMIT: efb1ca7
ISSUE_TITLE: GAIA pipeline orchestration engine P1-P6
ISSUE_BODY: Initial GAIA pipeline orchestration engine implementation covering Phases 1 through 6 — the foundational pipeline orchestration system.

Standalone initial pipeline commit with no dependencies. Foundation for all subsequent pipeline work.
ISSUE_LABELS: feature, pipeline, foundation
BRANCH_NAME: pr-pipeline-engine-p1p6
BRANCH_BASE: main
PR_TITLE: feat(pipeline): GAIA pipeline orchestration engine P1-P6
PR_BODY: ## Summary
- Initial pipeline orchestration engine
- Covers Phases 1-6
- Foundation for all subsequent pipeline work

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 113)
- No dependencies
MERGE_ORDER: 5
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: v0170-release-notes-fix
SOURCE_COMMIT: 2fd4a80
ISSUE_TITLE: Fix v0.17.0 release notes — npm install, gaia-ui CLI
ISSUE_BODY: Fix v0.17.0 release notes to include npm install instructions and gaia-ui CLI documentation. Changes in docs/releases/v0.17.0.mdx.

Standalone documentation fix with no code dependencies.
ISSUE_LABELS: documentation, release
BRANCH_NAME: pr-v0170-release-notes-fix
BRANCH_BASE: main
PR_TITLE: docs: fix v0.17.0 release notes — npm install, gaia-ui CLI
PR_BODY: ## Summary
- Add npm install instructions to release notes
- Add gaia-ui CLI documentation

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 114)
- No dependencies
MERGE_ORDER: 7
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: release-v0170
SOURCE_COMMIT: f7e688e
ISSUE_TITLE: Release v0.17.0
ISSUE_BODY: Release v0.17.0 with version bump and release notes documentation. Changes in docs/docs.json, docs/releases/v0.17.0.mdx, src/gaia/version.py.

Standalone release commit with no code dependencies.
ISSUE_LABELS: release, v0.17.0
BRANCH_NAME: pr-release-v0170
BRANCH_BASE: main
PR_TITLE: chore: release v0.17.0
PR_BODY: ## Summary
- Bump version to v0.17.0
- Add release notes
- Update docs navigation

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 115)
- No dependencies
MERGE_ORDER: 7
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: system-prompt-reduction
SOURCE_COMMIT: 2d08088
ISSUE_TITLE: Reduce system prompt 78% to fix Qwen3.5 timeouts + MCP runtime status
ISSUE_BODY: Reduce system prompt by 78% to fix Qwen3.5 timeouts and update MCP runtime status handling. Changes in agent prompt configurations and MCP modules.

Standalone fix with no code dependencies. Critical for resolving LLM timeout issues.
ISSUE_LABELS: bugfix, llm, mcp, performance
BRANCH_NAME: pr-system-prompt-reduction
BRANCH_BASE: main
PR_TITLE: fix: reduce system prompt 78% to fix Qwen3.5 timeouts + MCP runtime status
PR_BODY: ## Summary
- Reduce system prompt by 78%
- Fix Qwen3.5 timeout issues
- Update MCP runtime status handling

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 116)
- No dependencies
MERGE_ORDER: 8
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: agent-definition-dataclass-fix
SOURCE_COMMIT: ec86362
ISSUE_TITLE: Resolve AgentDefinition/AgentConstraints dataclass mismatch and remove shadow module
ISSUE_BODY: Resolve AgentDefinition/AgentConstraints dataclass mismatch and remove shadow module that was causing import conflicts. Changes in agent definitions and base modules.

Standalone fix with no code dependencies.
ISSUE_LABELS: bugfix, agents, dataclass
BRANCH_NAME: pr-agent-definition-dataclass-fix
BRANCH_BASE: main
PR_TITLE: fix(agents): resolve AgentDefinition/AgentConstraints dataclass mismatch and remove shadow module
PR_BODY: ## Summary
- Fix dataclass mismatch in agent definitions
- Remove shadow module causing import conflicts

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 117)
- No dependencies
MERGE_ORDER: 8
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: phase-contract-audit-defect
SOURCE_COMMIT: 2630b38
ISSUE_TITLE: Add PhaseContract, AuditLogger, and DefectRemediationTracker
ISSUE_BODY: Add PhaseContract for pipeline phase management, AuditLogger for execution audit trails, and DefectRemediationTracker for defect lifecycle management.

This depends on the pipeline engine P1-P6 being implemented. It adds critical pipeline governance infrastructure.
ISSUE_LABELS: feature, pipeline, audit, governance
BRANCH_NAME: pr-phase-contract-audit-defect
BRANCH_BASE: main
PR_TITLE: feat(pipeline): add PhaseContract, AuditLogger, and DefectRemediationTracker
PR_BODY: ## Summary
- Add PhaseContract for phase management
- Add AuditLogger for execution audit trails
- Add DefectRemediationTracker for defect lifecycle

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 118)
- Depends on: pipeline-engine-p1p6
MERGE_ORDER: 5
DEPENDS_ON: pr-pipeline-engine-p1p6
BATCH_WITH:

================================================================================

PR-PLAN: agent-ui-eval-benchmark
SOURCE_COMMIT: c72e6d9
ISSUE_TITLE: Add Agent UI eval benchmark framework with gaia eval agent command
ISSUE_BODY: Add Agent UI eval benchmark framework with gaia eval agent command for automated evaluation of agent capabilities. Changes in eval/* (comprehensive eval framework), src/gaia/eval/* (eval runner, scorecard), docs/eval.mdx.

Standalone feature with no code dependencies. Provides the evaluation framework for agent benchmarking.
ISSUE_LABELS: feature, eval, benchmark, agents
BRANCH_NAME: pr-agent-ui-eval-benchmark
BRANCH_BASE: main
PR_TITLE: feat: add Agent UI eval benchmark framework with gaia eval agent command
PR_BODY: ## Summary
- Add comprehensive eval benchmark framework
- Add gaia eval agent command
- Add eval runner and scorecard
- Add eval documentation

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 119)
- No dependencies
MERGE_ORDER: 6
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: configurable-agent-tool-isolation
SOURCE_COMMIT: 20beb54
ISSUE_TITLE: Add ConfigurableAgent with tool isolation and DefectRouter
ISSUE_BODY: Add ConfigurableAgent with tool isolation for clean tool separation and DefectRouter for pipeline defect management. Changes in src/gaia/agents/configurable.py, src/gaia/pipeline/defect_router.py.

This depends on the pipeline engine P1-P6 being implemented. It provides configurable agent tool isolation.
ISSUE_LABELS: feature, agents, pipeline
BRANCH_NAME: pr-configurable-agent-tool-isolation
BRANCH_BASE: main
PR_TITLE: feat: add ConfigurableAgent with tool isolation and DefectRouter
PR_BODY: ## Summary
- Add ConfigurableAgent with tool isolation
- Add DefectRouter for pipeline defect management

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 120)
- Depends on: pipeline-engine-p1p6
MERGE_ORDER: 5
DEPENDS_ON: pr-pipeline-engine-p1p6
BATCH_WITH:

================================================================================

PR-PLAN: toctou-security-fix
SOURCE_COMMIT: 8c2d24a
ISSUE_TITLE: Fix TOCTOU race condition in document upload endpoint
ISSUE_BODY: Fixed TOCTOU (Time of Check to Time of Use) race condition vulnerability in document upload endpoint. Security-critical fix.

CRITICAL SECURITY FIX — merge immediately. Standalone with no dependencies.
ISSUE_LABELS: security, toctou, race-condition
BRANCH_NAME: pr-toctou-security-fix
BRANCH_BASE: main
PR_TITLE: security: fix TOCTOU race condition in document upload endpoint
PR_BODY: ## Summary
- Fix TOCTOU race condition vulnerability
- Secure document upload endpoint
- CRITICAL: merge ASAP

## Security Impact
- Prevents race condition exploitation
- Ensures atomic check-and-use operations

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 128)
- No dependencies
MERGE_ORDER: 1
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: tool-guardrails
SOURCE_COMMIT: 3df90ff
ISSUE_TITLE: Add tool execution guardrails with confirmation popup
ISSUE_BODY: Add tool execution guardrails with confirmation popup for safer tool invocation in Agent UI. Changes in Agent UI components.

Standalone feature with no code dependencies. Improves safety of tool execution.
ISSUE_LABELS: feature, ui, safety, tools
BRANCH_NAME: pr-tool-guardrails
BRANCH_BASE: main
PR_TITLE: feat: add tool execution guardrails with confirmation popup
PR_BODY: ## Summary
- Add confirmation popup for tool execution
- Improve safety of tool invocation in Agent UI

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 127)
- No dependencies
MERGE_ORDER: 9
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: agent-ui-round5-fixes
SOURCE_COMMIT: cc90935
ISSUE_TITLE: Fix Agent UI Round 5 — hide post-tool thinking, FileListView, text spacing
ISSUE_BODY: Fix Agent UI Round 5 issues: hiding post-tool thinking, FileListView rendering, and text spacing corrections.

Standalone UI fix with no code dependencies.
ISSUE_LABELS: bugfix, ui, agent
BRANCH_NAME: pr-agent-ui-round5-fixes
BRANCH_BASE: main
PR_TITLE: fix: Agent UI Round 5 — hide post-tool thinking, FileListView, text spacing
PR_BODY: ## Summary
- Hide post-tool thinking in Agent UI
- Fix FileListView rendering
- Fix text spacing issues

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 125)
- No dependencies
MERGE_ORDER: 9
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: lru-eviction-fix
SOURCE_COMMIT: 8a6452f
ISSUE_TITLE: Fix LRU eviction silent failure allowing unbounded memory growth
ISSUE_BODY: Fix LRU eviction silent failure that was allowing unbounded memory growth in Agent UI.

Standalone fix with no code dependencies. Critical for memory stability.
ISSUE_LABELS: bugfix, memory, ui
BRANCH_NAME: pr-lru-eviction-fix
BRANCH_BASE: main
PR_TITLE: fix: LRU eviction silent failure allowing unbounded memory growth
PR_BODY: ## Summary
- Fix LRU eviction silent failure
- Prevent unbounded memory growth

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 126)
- No dependencies
MERGE_ORDER: 9
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: restore-reverted-changes
SOURCE_COMMIT: b7a97e6
ISSUE_TITLE: Restore changes reverted by accidental PR #566 merge
ISSUE_BODY: Restore changes that were accidentally reverted by PR #566 merge, including security fixes, tool guardrails, and Agent UI improvements.

This depends on toctou-security-fix, tool-guardrails, and agent-ui-round5-fixes being merged first. It restores accidentally lost changes.
ISSUE_LABELS: bugfix, restore
BRANCH_NAME: pr-restore-reverted-changes
BRANCH_BASE: main
PR_TITLE: fix: restore changes reverted by accidental PR #566 merge
PR_BODY: ## Summary
- Restore security fix changes
- Restore tool guardrail changes
- Restore Agent UI improvements

## Dependencies
- Must merge after: toctou-security-fix, tool-guardrails, agent-ui-round5-fixes

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 121)
- Depends on: toctou-security-fix, tool-guardrails, agent-ui-round5-fixes
MERGE_ORDER: 10
DEPENDS_ON: pr-toctou-security-fix, pr-tool-guardrails, pr-agent-ui-round5-fixes
BATCH_WITH:

================================================================================

PR-PLAN: rag-indexing-guards
SOURCE_COMMIT: af652d9
ISSUE_TITLE: RAG indexing guards, gaia init pip extras, and docs update
ISSUE_BODY: Add RAG indexing guards, gaia init pip extras, and documentation updates for reliable document indexing.

Standalone fix with no code dependencies.
ISSUE_LABELS: bugfix, rag, documentation
BRANCH_NAME: pr-rag-indexing-guards
BRANCH_BASE: main
PR_TITLE: fix: RAG indexing guards, gaia init pip extras, and docs update
PR_BODY: ## Summary
- Add RAG indexing guards
- Add gaia init pip extras
- Update documentation

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 122)
- No dependencies
MERGE_ORDER: 9
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: agent-ui-guardrails-round6
SOURCE_COMMIT: 95b304f
ISSUE_TITLE: Fix Agent UI guardrails, rendering, LRU eviction, and Windows paths
ISSUE_BODY: Fix Agent UI guardrails, rendering issues, LRU eviction bugs, and Windows path compatibility problems.

This depends on lru-eviction-fix being merged first. It is a comprehensive UI fix round.
ISSUE_LABELS: bugfix, ui, guardrails
BRANCH_NAME: pr-agent-ui-guardrails-round6
BRANCH_BASE: main
PR_TITLE: fix: Agent UI guardrails, rendering, LRU eviction, and Windows paths
PR_BODY: ## Summary
- Fix Agent UI guardrails
- Fix rendering issues
- Fix LRU eviction bugs
- Fix Windows path compatibility

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 123)
- Depends on: lru-eviction-fix
MERGE_ORDER: 9
DEPENDS_ON: pr-lru-eviction-fix
BATCH_WITH:

================================================================================

PR-PLAN: agent-ui-device-guard
SOURCE_COMMIT: 5dd71a2
ISSUE_TITLE: Guard Agent UI against unsupported devices
ISSUE_BODY: Add guard to prevent Agent UI from running on unsupported devices with appropriate error messaging.

Standalone feature with no code dependencies.
ISSUE_LABELS: feature, ui, device-compatibility
BRANCH_NAME: pr-agent-ui-device-guard
BRANCH_BASE: main
PR_TITLE: feat: guard Agent UI against unsupported devices
PR_BODY: ## Summary
- Add device detection for Agent UI
- Show error message on unsupported devices

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 124)
- No dependencies
MERGE_ORDER: 9
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: v0161-release-notes
SOURCE_COMMIT: bae3a62
ISSUE_TITLE: Add missing PRs to v0.16.1 release notes
ISSUE_BODY: Add missing PRs to v0.16.1 release notes documentation. Changes in docs/releases/v0.16.1.mdx.

Standalone documentation fix with no code dependencies.
ISSUE_LABELS: documentation, release
BRANCH_NAME: pr-v0161-release-notes
BRANCH_BASE: main
PR_TITLE: docs: add missing PRs to v0.16.1 release notes
PR_BODY: ## Summary
- Add missing PRs to v0.16.1 release notes

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 129)
- No dependencies
MERGE_ORDER: 9
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: agent-ui-terminal-fixes
SOURCE_COMMIT: 25c6d25
ISSUE_TITLE: Agent UI terminal animations, pixelated cursor, and docs fixes
ISSUE_BODY: Fix Agent UI terminal animations, pixelated cursor, and documentation issues.

Standalone UI fix with no code dependencies.
ISSUE_LABELS: bugfix, ui, terminal
BRANCH_NAME: pr-agent-ui-terminal-fixes
BRANCH_BASE: main
PR_TITLE: fix: Agent UI terminal animations, pixelated cursor, and docs fixes
PR_BODY: ## Summary
- Fix terminal animations
- Fix pixelated cursor issue
- Update documentation

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 130)
- No dependencies
MERGE_ORDER: 9
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: gaia-chat-ui
SOURCE_COMMIT: b2ace80
ISSUE_TITLE: Add GAIA Chat UI — privacy-first desktop chat with document Q&A
ISSUE_BODY: Add GAIA Chat UI — privacy-first desktop chat application with document Q&A capabilities.

Standalone feature with no code dependencies. Foundation for the desktop chat experience.
ISSUE_LABELS: feature, ui, chat, desktop
BRANCH_NAME: pr-gaia-chat-ui
BRANCH_BASE: main
PR_TITLE: feat: add GAIA Chat UI — privacy-first desktop chat with document Q&A
PR_BODY: ## Summary
- Add GAIA Chat UI application
- Privacy-first desktop chat
- Document Q&A capabilities

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 131)
- No dependencies
MERGE_ORDER: 10
DEPENDS_ON:
BATCH_WITH:

================================================================================

PR-PLAN: lemonade-v10-compat-fix
SOURCE_COMMIT: 4015bb2
ISSUE_TITLE: Fix Lemonade v10 system-info device key compatibility
ISSUE_BODY: Fix Lemonade v10 system-info device key compatibility for proper hardware detection. Changes in src/gaia/llm/lemonade_client.py.

Standalone fix with no code dependencies.
ISSUE_LABELS: bugfix, lemonade, compatibility
BRANCH_NAME: pr-lemonade-v10-compat-fix
BRANCH_BASE: main
PR_TITLE: fix: Lemonade v10 system-info device key compatibility
PR_BODY: ## Summary
- Fix device key compatibility for Lemonade v10
- Update lemonade_client.py

## Related
- Spec sheet: `cpp/SPEC-SHEETS-ALL-FINAL.md` (Commit 132)
- No dependencies
MERGE_ORDER: 11
DEPENDS_ON:
BATCH_WITH:

================================================================================
# BATCHING SUMMARY
================================================================================

## Batch 1: Security & Standalone Cleanup (SEQUENTIAL — safety-critical)
### Merge in this order:
1. toctou-security-fix (SECURITY — merge first)
2. remove-claude-from-git
3. rc2-tool-package
4. pdf-bundle-generator, runtime-artifact-exclusions, docs-debt-cleanup (parallel)

## Batch 2: Doc-Only Matrix Updates (SEQUENTIAL — same file conflict)
### Merge in this order:
1. phase6-matrix-update-73 (MERGE_ORDER 1)
2. phase6-matrix-update-74 (depends on 73 — MERGE_ORDER 1)
3. design-spec-coherence (depends on both — MERGE_ORDER 1)

NOTE: All have MERGE_ORDER 1 but MUST be merged sequentially due to
docs/reference/branch-change-matrix.md file conflicts.

## Batch 3: Integration Analysis & Pipeline Docs (SEQUENTIAL — related refs)
### Merge in this order:
1. pr606-integration-analysis (MERGE_ORDER 2)
2. pr720-integration-analysis (MERGE_ORDER 2)
3. pipeline-pr-description (MERGE_ORDER 3)

## Batch 4: CI/CD & Version Updates (can merge in parallel)
- npm-oidc-publish (MERGE_ORDER 4)
- remove-registry-url (depends on npm-oidc-publish)
- npm-oidc-switch (depends on npm-oidc-publish)
- webui-version-bump
- mcp-test-isolation
- agent-ui-build-init
- merge-upstream-main (merge commit — no PR needed)
- version-py-proposal
- merge-queue-notify-fix

## Batch 5: Minor Fixes & Branch Matrix (can merge in parallel after Batch 2-3)
- minor-fixes-updates (BATCH_WITH: remove-claude-from-git)
- branch-change-matrix
- baibel-master-spec (BATCH_WITH: branch-change-matrix)
- kpi-loom-specs
- agent-ecosystem-design-spec
- phase5-matrix-design-docs

## Batch 6: Phase 3 Core (sequential within batch)
- modular-architecture-core (Wave 2, order 6)
- phase3-sprint2-di (Wave 2, order 12)
- phase3-sprint3-caching (Wave 3, order 24)
- phase3-sprint4-observability (Wave 4, order 26)
- phase3-sprint4-test-fixes (Wave 6, order 39)
- phase3-closeout-report (Wave 6, order 38)

## Batch 7: Phase 4 (sequential within batch)
- health-monitoring (Wave 3, order 14)
- resilience-patterns (Wave 3, order 15)
- resilience-error-consolidation (Wave 3, order 17)
- data-protection-perf (Wave 3, order 16)
- phase4-closeout-report (Wave 3, order 18)

## Batch 8: Orchestration Kernel (sequential)
- core-orchestration-kernel (Wave 3, order 20)
- project-supervisor-hierarchy (Wave 3, order 21)
- git-supervisor-hierarchy (Wave 3, order 22)
- automation-hooks (Wave 4, order 23)
- parallel-execution-engine (Wave 4, order 25)
- parallel-exec-edge-tests (Wave 5, order 31)
- orchestrator-ui-visibility (Wave 5, order 28)
- orchestration-user-guide (Wave 7, order 42)

## Batch 9: Pipeline Foundation (sequential)
- pipeline-engine-wiring (Wave 1, order 7)
- pipeline-runner-page (Wave 6, order 44)
- visual-pipeline-canvas (Wave 6, order 50)

## Batch 10: Component Framework (sequential)
- component-framework-loader (Wave 2, order 10)
- agent-base-tools (Wave 2, order 11)
- component-framework-templates (Wave 5, order 32)
- gap-detector (Wave 5, order 33)
- component-registry-ui (Wave 8, order 62)

## Batch 11: Auto-Spawn Pipeline Stages (sequential chain)
- domain-analyzer (Wave 5, order 27)
- workflow-modeler (Wave 5, order 29)
- loom-builder (Wave 5, order 30)
- pipeline-executor (Wave 5, order 34)
- auto-spawn-pipeline (Wave 5, order 35)
- pipeline-cli-wiring (Wave 7, order 80)
- execute-tool-dispatch-fix (Wave 7, order 79)

## Batch 12: Canvas UI Features (can merge in parallel after canvas base)
- canvas-typescript-fix
- pipeline-canvas-docs
- canvas-supervisors-gates
- tier12-tracker-update
- execution-history-replay
- multiple-independent-loops
- tier3-pipeline-canvas
- recursive-pipeline-sse
- agent-ecosystem-display

## Batch 13: Tracker Updates (SEQUENTIAL — same file conflict)
### Merge in this order:
1. tier12-tracker-update (MERGE_ORDER 58)
2. tier3-tracker-update (MERGE_ORDER 67, depends on tier3-canvas)
3. pipeline-canvas-guide-update (MERGE_ORDER 61)

NOTE: All modify docs/PIPELINE-CANVAS-IMPLEMENTATION-TRACKER.md.

## Batch 14: Phase 5 Docs (SEQUENTIAL — same file conflict)
### Merge in this order:
1. phase5-agent-docs (MERGE_ORDER 75)
2. phase5-runtime-verification-docs (MERGE_ORDER 76)

NOTE: Both modify future-where-to-resume-left-off.md.

## Batch 15: Security (standalone — merge ASAP)
- sec-003-path-traversal
- etherrepl-security-fix

## Batch 16: BAIBEL Integration
- baibel-integration-phases (depends on master spec)
- baibel-phase-status-fix (depends on matrix)

## Batch 17: Final Fixes and Tests
- final-quality-review-fixes
- session3-quality-review-fixes
- phase5-docs-coherence
- sse-endpoint-tests
- quality-gate7-tests
- e2e-pipeline-timeout-fix

## Batch 18: Release & Version Management (sequential where dependent)
- v0170-release-notes-fix
- release-v0170
- release-v0171 (depends on metrics-dashboard)

## Batch 19: Pipeline Engine & Metrics (sequential within subgroups)
- pipeline-engine-p1p6 (foundation)
- phase-contract-audit-defect (depends on p1p6)
- configurable-agent-tool-isolation (depends on p1p6)
- pipeline-eval-metrics (depends on metrics-dashboard)
- metrics-dashboard (depends on pipeline-engine-wiring)
- missing-metrics-modules (depends on pipeline-engine-wiring)

## Batch 20: C++ Framework (sequential)
- cpp-sse-streaming (foundation)
- cpp-perf-benchmarks (depends on cpp-sse-streaming)
- cpp-runtime-config (depends on cpp-sse-streaming)

## Batch 21: Agent UI Fixes & Guardrails (can merge in parallel)
- system-prompt-reduction
- agent-definition-dataclass-fix
- agent-ui-eval-benchmark
- rag-indexing-guards
- toctou-security-fix
- tool-guardrails
- agent-ui-round5-fixes
- lru-eviction-fix
- agent-ui-guardrails-round6 (depends on lru-eviction-fix)
- agent-ui-device-guard
- agent-ui-terminal-fixes
- restore-reverted-changes (depends on toctou, guardrails, round5)
- lemonade-v10-compat-fix

## Batch 22: New Feature Additions (can merge in parallel)
- gaia-chat-ui
- demo-lemonade-integration (depends on rc2-tool-package)
- artifact-extractor (depends on pipeline-engine-wiring)
- llm-output-propagation (depends on pipeline-engine-wiring)
- model-id-support (depends on pipeline-engine-wiring)
- lemonade-version-warning
- agent-ui-build-init

================================================================================
# DEPENDENCY GRAPH (Topological Order)
================================================================================

MERGE_ORDER 1:  pdf-bundle-generator, runtime-artifact-exclusions, docs-debt-cleanup, phase6-matrix-update-73, phase6-matrix-update-74, design-spec-coherence, toctou-security-fix, remove-claude-from-git, rc2-tool-package
MERGE_ORDER 2:  pr606-integration-analysis, pr720-integration-analysis, phase5-matrix-design-docs, branch-change-matrix, baibel-master-spec, minor-fixes-updates, demo-lemonade-integration
MERGE_ORDER 3:  kpi-loom-specs, agent-ecosystem-design-spec, pipeline-pr-description, lemonade-version-warning, mcp-test-isolation
MERGE_ORDER 4:  npm-oidc-publish, remove-registry-url, npm-oidc-switch, webui-version-bump, agent-ui-build-init, merge-upstream-main, version-py-proposal, merge-queue-notify-fix
MERGE_ORDER 5:  baibel-integration-phases, pipeline-engine-wiring, artifact-extractor, llm-output-propagation, model-id-support, pipeline-engine-p1p6, phase-contract-audit-defect, configurable-agent-tool-isolation, cpp-sse-streaming, supervisor-agents
MERGE_ORDER 6:  modular-architecture-core, metrics-dashboard, pipeline-eval-metrics, release-v0171, missing-metrics-modules, cpp-perf-benchmarks, cpp-runtime-config, agent-ui-eval-benchmark
MERGE_ORDER 7:  pipeline-engine-p1p6, v0170-release-notes-fix, release-v0170
MERGE_ORDER 8:  supervisor-agents, system-prompt-reduction, agent-definition-dataclass-fix
MERGE_ORDER 9:  baibel-phase-status-fix, restore-reverted-changes, rag-indexing-guards, agent-ui-guardrails-round6, lru-eviction-fix, tool-guardrails, agent-ui-round5-fixes, agent-ui-device-guard, agent-ui-terminal-fixes, v0161-release-notes
MERGE_ORDER 10: component-framework-loader, gaia-chat-ui
MERGE_ORDER 11: agent-base-tools, lemonade-v10-compat-fix
MERGE_ORDER 12: phase3-sprint2-di
MERGE_ORDER 13: etherrepl-security-fix
MERGE_ORDER 14: health-monitoring
MERGE_ORDER 15: resilience-patterns
MERGE_ORDER 16: data-protection-perf
MERGE_ORDER 17: resilience-error-consolidation
MERGE_ORDER 18: phase4-closeout-report
MERGE_ORDER 19: resilience-apis-fix
MERGE_ORDER 20: core-orchestration-kernel
MERGE_ORDER 21: project-supervisor-hierarchy
MERGE_ORDER 22: git-supervisor-hierarchy
MERGE_ORDER 23: automation-hooks
MERGE_ORDER 24: phase3-sprint3-caching
MERGE_ORDER 25: parallel-execution-engine
MERGE_ORDER 26: phase3-sprint4-observability
MERGE_ORDER 27: domain-analyzer
MERGE_ORDER 28: orchestrator-ui-visibility
MERGE_ORDER 29: workflow-modeler
MERGE_ORDER 30: loom-builder
MERGE_ORDER 31: parallel-exec-edge-tests
MERGE_ORDER 32: component-framework-templates
MERGE_ORDER 33: gap-detector
MERGE_ORDER 34: pipeline-executor
MERGE_ORDER 35: auto-spawn-pipeline
MERGE_ORDER 37: supervisor-decision-tests
MERGE_ORDER 38: phase3-closeout-report
MERGE_ORDER 39: phase3-sprint4-test-fixes
MERGE_ORDER 42: orchestration-user-guide
MERGE_ORDER 44: pipeline-runner-page
MERGE_ORDER 45: pipeline-sse-wiring
MERGE_ORDER 46: artifact-provenance
MERGE_ORDER 47: remove-pipeline-isolation
MERGE_ORDER 60: sprint-integration-tests
MERGE_ORDER 49: canvas-wiring-quality
MERGE_ORDER 50: visual-pipeline-canvas
MERGE_ORDER 51: canvas-typescript-fix
MERGE_ORDER 52: pipeline-canvas-docs
MERGE_ORDER 53: event-loop-consolidation
MERGE_ORDER 54: canvas-looppath-fix
MERGE_ORDER 55: final-quality-review-fixes
MERGE_ORDER 56: sec-003-path-traversal
MERGE_ORDER 57: canvas-supervisors-gates
MERGE_ORDER 58: tier12-tracker-update
MERGE_ORDER 59: multiple-independent-loops
MERGE_ORDER 60: execution-history-replay
MERGE_ORDER 61: pipeline-canvas-guide-update
MERGE_ORDER 62: component-registry-ui
MERGE_ORDER 63: tier3-pipeline-canvas
MERGE_ORDER 64: recursive-pipeline-sse
MERGE_ORDER 65: canvas-ui-wiring-fix
MERGE_ORDER 66: canvas-config-quality-bridge
MERGE_ORDER 67: tier3-tracker-update
MERGE_ORDER 68: webui-typescript-fix
MERGE_ORDER 69: pipelinerunner-typescript-fix
MERGE_ORDER 70: webui-double-api-fix
MERGE_ORDER 71: agent-ecosystem-display
MERGE_ORDER 72: pipelinerunner-accessibility
MERGE_ORDER 73: phase5-milestone3-agents
MERGE_ORDER 74: sse-endpoint-tests
MERGE_ORDER 75: phase5-agent-docs
MERGE_ORDER 76: phase5-runtime-verification-docs
MERGE_ORDER 77: session3-quality-review-fixes
MERGE_ORDER 78: phase5-docs-coherence
MERGE_ORDER 79: execute-tool-dispatch-fix
MERGE_ORDER 80: pipeline-cli-wiring
MERGE_ORDER 82: e2e-pipeline-timeout-fix
MERGE_ORDER 83: quality-gate7-tests

================================================================================
# END OF PR PLANS — ALL 132 SPEC SHEETS
================================================================================
