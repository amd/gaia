# GAIA Pipeline Orchestration Screenshot Execution Plan

**Branch:** `feature/pipeline-orchestration-v1`
**Target:** `docs/guides/screenshots/`
**Total Screenshots:** 24 (ORCH-03 through ORCH-26)
**Server:** GAIA UI running at http://localhost:4200
**Test Suite:** 304 tests across 7 files in `tests/unit/orchestration/`

---

## Dependency Map

```
Phase 1 (pytest-only, no server needed)
  └── Phase 2 (curl to live server)
        └── Phase 3 (combined pytest + curl demo)
              └── Phase 4 (validation + handoff)
```

## Phase Groupings

### Phase 1: Parallel Execution Engine (4 screenshots) -- ORCH-03 to ORCH-06
**Dependency:** None (pytest-only)
**Estimated Effort:** 8 minutes

| ID | File | Command | What It Proves |
|----|------|---------|----------------|
| ORCH-03 | `orch-03-level-partitioning.txt` | `python -c` script | Kahn's algorithm partitions DAG into executable levels |
| ORCH-04 | `orch-04-parallel-config.txt` | pytest: `TestOrchestratorConfigParallel` | Config flags for parallel/rollback/serialize |
| ORCH-05 | `orch-05-parallel-integration.txt` | pytest: `TestParallelIntegration::test_three_level_dag` | Full 3-level DAG parallel execution |
| ORCH-06 | `orch-06-semaphore-bounds.txt` | pytest: `TestSemaphoreBounds::test_semaphore_bounds_concurrent_executions` | max_parallel_objectives semaphore enforcement |

### Phase 2: Conflict Detection (2 screenshots) -- ORCH-07 to ORCH-08
**Dependency:** None (pytest-only)
**Estimated Effort:** 4 minutes

| ID | File | Command | What It Proves |
|----|------|---------|----------------|
| ORCH-07 | `orch-07-conflict-no-overlap.txt` | pytest: `TestConflictDetection::test_detect_conflicts_no_overlap` | No false positives on disjoint files |
| ORCH-08 | `orch-08-conflict-file-overlap.txt` | pytest: `TestConflictDetection::test_detect_conflicts_file_overlap` | Correctly detects shared file conflicts |

### Phase 3: Rollback Mechanism (3 screenshots) -- ORCH-09 to ORCH-11
**Dependency:** None (pytest-only)
**Estimated Effort:** 6 minutes

| ID | File | Command | What It Proves |
|----|------|---------|----------------|
| ORCH-09 | `orch-09-rollback-disabled.txt` | pytest: `TestRollback::test_rollback_disabled` | No rollback when feature disabled |
| ORCH-10 | `orch-10-rollback-success.txt` | pytest: `TestRollback::test_rollback_failed_objectives` | Git rollback on failed objective |
| ORCH-11 | `orch-11-rollback-abort-verdict.txt` | pytest: `TestRollback::test_rollback_supervisor_abort` | Rollback on supervisor ABORT |

### Phase 4: Worktree Lifecycle (4 screenshots) -- ORCH-12 to ORCH-15
**Dependency:** None (pytest-only, requires git)
**Estimated Effort:** 8 minutes

| ID | File | Command | What It Proves |
|----|------|---------|----------------|
| ORCH-12 | `orch-12-worktree-create.txt` | pytest: `TestWorktreeLifecycle::test_create_worktree_for_objective` | Branch + worktree directory creation |
| ORCH-13 | `orch-13-worktree-cleanup.txt` | pytest: `TestWorktreeLifecycle::test_cleanup_worktree_success` | Worktree directory removal |
| ORCH-14 | `orch-14-worktree-stale-cleanup.txt` | pytest: `TestWorktreeLifecycle::test_cleanup_stale_worktrees` | Bulk stale worktree removal at run start |
| ORCH-15 | `orch-15-worktree-concurrent.txt` | pytest: `TestWorktreeLifecycle::test_concurrent_git_operations_serialized` | Lock prevents race conditions |

### Phase 5: REST API Layer (4 screenshots) -- ORCH-16 to ORCH-19
**Dependency:** GAIA UI server running at :4200
**Estimated Effort:** 4 minutes

| ID | File | Command | What It Proves |
|----|------|---------|----------------|
| ORCH-16 | `orch-16-api-state.txt` | curl `/api/v1/orchestrator/state` | State + project summary (already captured, verify) |
| ORCH-17 | `orch-17-api-objectives-list.txt` | curl `/api/v1/orchestrator/objectives` | 5 objectives with phases/status (already captured, verify) |
| ORCH-18 | `orch-18-api-objective-detail.txt` | curl `/api/v1/orchestrator/objectives/obj-001` | Single objective detail with branch (already captured, verify) |
| ORCH-19 | `orch-19-api-history.txt` | curl `/api/v1/orchestrator/history` | Execution history pagination (already captured, verify) |

### Phase 6: SSE Streaming (2 screenshots) -- ORCH-20 to ORCH-21
**Dependency:** GAIA UI server running at :4200
**Estimated Effort:** 4 minutes

| ID | File | Command | What It Proves |
|----|------|---------|----------------|
| ORCH-20 | `orch-20-sse-bridge-broadcast.txt` | pytest: `TestSSEStream::test_sse_bridge_broadcast` | SSE bridge fan-out to subscribers |
| ORCH-21 | `orch-21-sse-endpoint-connects.txt` | pytest: `TestSSEStream::test_sse_endpoint_connects` | SSE endpoint returns text/event-stream |

### Phase 7: Hook Serialization (2 screenshots) -- ORCH-22 to ORCH-23
**Dependency:** None (pytest-only)
**Estimated Effort:** 4 minutes

| ID | File | Command | What It Proves |
|----|------|---------|----------------|
| ORCH-22 | `orch-22-hooks-serial-parallel.txt` | pytest: `TestHookSerialization::test_hooks_fire_in_parallel_mode` | Hooks fire with lock in serialize_hooks=True |
| ORCH-23 | `orch-23-hooks-no-serial.txt` | pytest: `TestHookSerialization::test_hooks_fire_without_serialization_mixed` | No lock when serialize_hooks=False |

### Phase 8: Status Transition System (1 screenshot) -- ORCH-24
**Dependency:** None (pytest-only)
**Estimated Effort:** 2 minutes

| ID | File | Command | What It Proves |
|----|------|---------|----------------|
| ORCH-24 | `orch-24-status-transitions.txt` | pytest: `test_objectives.py` status transition tests | Valid/invalid status transitions enforced |

### Phase 9: State Serialization (1 screenshot) -- ORCH-25
**Dependency:** None (pytest-only)
**Estimated Effort:** 2 minutes

| ID | File | Command | What It Proves |
|----|------|---------|----------------|
| ORCH-25 | `orch-25-state-to-dict.txt` | pytest: `TestOrchestratorStateToDict` | JSON-serializable state output |

### Phase 10: Health Score (1 screenshot) -- ORCH-26
**Dependency:** GAIA UI server running at :4200
**Estimated Effort:** 2 minutes

| ID | File | Command | What It Proves |
|----|------|---------|----------------|
| ORCH-26 | `orch-26-health-composite.txt` | curl `/api/v1/orchestrator/health` | Composite health score (already captured, verify) |

---

## Execution Order (optimized for minimal context switching)

```
Step 1: Run all pytest-only screenshot captures (Phases 1,2,3,4,7,8,9)
  ├── 1a. ORCH-03: Level partitioning (python -c script)
  ├── 1b. ORCH-04: Parallel config (pytest, 2 tests)
  ├── 1c. ORCH-05: Parallel integration (pytest, 1 test)
  ├── 1d. ORCH-06: Semaphore bounds (pytest, 2 tests)
  ├── 1e. ORCH-07: Conflict no overlap (pytest, 1 test)
  ├── 1f. ORCH-08: Conflict file overlap (pytest, 1 test)
  ├── 1g. ORCH-09: Rollback disabled (pytest, 1 test)
  ├── 1h. ORCH-10: Rollback success (pytest, 1 test)
  ├── 1i. ORCH-11: Rollback abort verdict (pytest, 1 test)
  ├── 1j. ORCH-12: Worktree create (pytest, 1 test)
  ├── 1k. ORCH-13: Worktree cleanup (pytest, 1 test)
  ├── 1l. ORCH-14: Stale worktree cleanup (pytest, 1 test)
  ├── 1m. ORCH-15: Concurrent git ops (pytest, 1 test)
  ├── 1n. ORCH-22: Hooks serialized (pytest, 1 test)
  ├── 1o. ORCH-23: Hooks non-serialized (pytest, 1 test)
  ├── 1p. ORCH-24: Status transitions (pytest, targeted tests)
  └── 1q. ORCH-25: State to_dict (pytest, 3 tests)

Step 2: Capture live API responses (Phases 5, 6, 10)
  ├── 2a. ORCH-16: API state (curl)
  ├── 2b. ORCH-17: API objectives list (curl)
  ├── 2c. ORCH-18: API objective detail (curl)
  ├── 2d. ORCH-19: API history (curl)
  ├── 2e. ORCH-26: API health (curl)
  ├── 2f. ORCH-20: SSE bridge broadcast (pytest)
  └── 2g. ORCH-21: SSE endpoint connects (pytest)
```

## Total Estimated Effort
- Phase 1 pytest captures: ~25 minutes
- Phase 2 curl captures: ~5 minutes
- Quality review: ~10 minutes
- **Total: ~40 minutes**

## Quality Gates

Each screenshot must pass these checks:
1. **File exists** at `docs/guides/screenshots/orch-NN-<name>.txt`
2. **Non-empty content** (> 50 bytes for curl, > 100 bytes for pytest)
3. **Contains expected keywords** (e.g., "Levels:" for partitioning, "passed" for tests)
4. **No error indicators** (no "FAILED", "ERROR", "Traceback", "Connection refused")
5. **Clear caption** at top of file explaining what it proves

## Handoff
After all 24 screenshots are captured, pass to quality-reviewer agent for:
- Content accuracy verification
- Caption completeness check
- Cross-reference with feature requirements
- Documentation consistency check
