# Phase 4 Edge-Case Test Implementation Specification

## Overview

5 edge-case tests to close gaps identified in quality review. All tests follow existing patterns in `tests/unit/orchestration/test_parallel_execution.py`.

---

## Test 1: Hook `halt_pipeline=True` path in parallel mode

**Priority:** Medium
**Gap:** Lines 1023-1034 -- When an OBJECTIVE_START hook returns `halt_pipeline=True`, the objective is marked BLOCKED and skipped from parallel execution. This path is not covered.

### Test Method
```
test_hook_halt_pipeline_blocks_objective
```

### Test Class
`TestHookHaltPipeline` (new class after `TestHookSerialization`)

### Imports Required
Already present in file: `BaseHook`, `HookContext`, `HookPriority`, `HookResult`, `OBJECTIVE_START`

### Mock Setup
- `OrchestratorConfig` with `enable_parallel_execution=True`, `serialize_hooks=True`
- Two objectives: `obj-001` (will be halted by hook), `obj-002` (normal)
- Custom hook class registered on `OBJECTIVE_START` that checks `context.objective.objective_id`:
  - If `obj-001`: return `HookResult(success=True, halt_pipeline=True)`
  - Otherwise: return `HookResult.success_result()`
- `mock_adapter.execute_without_status_update` = `AsyncMock` returning `{"success": True, "artifacts": [], "error": None}`

### Assertions
1. `state.objectives_processed == 1` (only obj-002 processed)
2. `obj-001.status == ObjectiveStatus.BLOCKED`
3. `obj-001.error_message == "Halted by hook"`
4. `mock_adapter.execute_without_status_update` called exactly once (only for obj-002)
5. obj-002 status == `ObjectiveStatus.COMPLETED`

### Rationale
This verifies the critical guard rail: a hook can halt execution of a specific objective during the pre-execution phase, and that objective must NOT proceed to parallel execution.

---

## Test 2: Semaphore bounding verification (`max_parallel_objectives`)

**Priority:** Low
**Gap:** Lines 1044-1051 -- The semaphore bounds parallel concurrency to `max_parallel_objectives`. Need to verify the bound is actually respected.

### Test Method
```
test_semaphore_bounds_concurrent_executions
```

### Test Class
`TestConcurrencyLocks` (existing class, extend)

### Mock Setup
- `OrchestratorConfig` with `enable_parallel_execution=True`, `max_parallel_objectives=2`
- Three objectives: `obj-001`, `obj-002`, `obj-003` (all independent, same level)
- `mock_adapter.execute_without_status_update` = `AsyncMock` with a side-effect function that:
  - Increments a counter on entry, decrements on exit
  - Tracks the peak value of the counter (`peak_concurrent`)
  - Uses `asyncio.sleep(0.01)` to simulate work duration

### Side-Effect Function Pattern
```python
concurrent_count = 0
peak_concurrent = 0

async def mock_execute_tracked(obj):
    nonlocal concurrent_count, peak_concurrent
    concurrent_count += 1
    peak_concurrent = max(peak_concurrent, concurrent_count)
    await asyncio.sleep(0.01)
    concurrent_count -= 1
    return {"success": True, "artifacts": [], "error": None}
```

### Assertions
1. `state.objectives_processed == 3` (all three processed)
2. `peak_concurrent <= 2` (semaphore bound respected)
3. `mock_adapter.execute_without_status_update.call_count == 3`

### Rationale
With `max_parallel_objectives=2` and 3 objectives, the semaphore must ensure no more than 2 tasks run simultaneously. The `peak_concurrent` tracking proves the bound.

---

## Test 3: Exception in `asyncio.gather` with concurrent tasks

**Priority:** Low
**Gap:** Lines 1051-1069 -- When `execute_without_status_update` raises an exception (rather than returning an error dict), the result is caught via `return_exceptions=True` and handled. This path is not covered.

### Test Method
```
test_parallel_execution_raises_exception
```

### Test Class
`TestParallelExecutionMode` (existing class)

### Mock Setup
- `OrchestratorConfig` with `enable_parallel_execution=True`
- Two objectives: `obj-001` (will succeed), `obj-002` (will raise exception)
- `mock_adapter.execute_without_status_update` side effect:
  - If `obj-001`: return `{"success": True, "artifacts": [], "error": None}`
  - If `obj-002`: raise `RuntimeError("Connection lost during execution")`

### Assertions
1. `state.objectives_processed == 1` (only obj-001 succeeds)
2. `state.objectives_failed == 1` (obj-002 failed)
3. `obj-001.status == ObjectiveStatus.COMPLETED`
4. `obj-002.status == ObjectiveStatus.BLOCKED`
5. `obj-002.error_message == "Connection lost during execution"`
6. No exception propagates to the caller (the `run()` method handles it)

### Rationale
Verifies that exceptions from individual tasks don't crash the pipeline -- they are captured by `return_exceptions=True` and converted to failure outcomes.

---

## Test 4: `serialize_hooks=False` path (no lock acquisition)

**Priority:** Medium
**Gap:** Lines 1017-1021 (OBJECTIVE_START) and 1117-1120 (completion hooks) -- When `serialize_hooks=False`, hooks fire without acquiring `_hook_lock`. Need to verify hooks still execute correctly.

### Test Method
```
test_hooks_fire_without_serialization
```

### Test Class
`TestHookSerialization` (existing class)

### Mock Setup
- `OrchestratorConfig` with `enable_parallel_execution=True`, `serialize_hooks=False`
- Two objectives: `obj-001`, `obj-002`
- Custom hook registered on `OBJECTIVE_START` that appends to a shared list
- Custom hook registered on `OBJECTIVE_COMPLETE` that appends to a shared list
- `mock_adapter.execute_without_status_update` = `AsyncMock` returning success dict

### Assertions
1. Both `OBJECTIVE_START` and `OBJECTIVE_COMPLETE` events are in the fired events list
2. Hooks fired for both objectives (4 total OBJECTIVE_START calls, 2 total OBJECTIVE_COMPLETE calls)
3. `_hook_lock` was NOT acquired: verify by wrapping `__aenter__` of the lock with a mock/counter
   - OR simpler: verify the test completes without error (no race conditions at this scale)
4. `state.objectives_processed == 2`

### Lock Verification Strategy
Wrap `_hook_lock` to track acquisitions:
```python
acquire_count = 0
original_aenter = orchestrator._hook_lock.__aenter__

async def tracked_aenter():
    nonlocal acquire_count
    acquire_count += 1
    return await original_aenter()

# This is harder to mock cleanly. Alternative: use a spy pattern
# by monkeypatching the lock and checking that the `else` branch
# was taken by verifying hook execution order.
```

Given complexity of lock tracking, the simpler approach: verify hooks fire correctly with `serialize_hooks=False` and that outcomes are recorded. The structural difference (lock vs no lock) is verified by code inspection; the functional test ensures no regression.

---

## Test 5: Worktree removal ordering during rollback

**Priority:** Medium
**Gap:** Lines 831-834 then 869-876 -- During ABORT: worktree cleanup runs first (lines 831-834), then supervisor verdict triggers rollback (lines 869-876). Need to verify both happen in correct order.

### Test Method
```
test_rollback_worktree_cleanup_before_git_rollback
```

### Test Class
`TestRollback` (existing class)

### Mock Setup
- Real git repo via `_init_git_repo(tmp_path)`
- `OrchestratorConfig` with `enable_parallel_execution=True`, `enable_git_supervisor=True`, `enable_supervisor=True`
- Two objectives: `obj-001`, `obj-002` (both fail)
- `mock_adapter.execute_without_status_update` returns failure for both
- `mock_git_supervisor.rollback.return_value = True`
- `mock_git_supervisor.detect_changed_files.return_value = []`
- `mock_git_supervisor.evaluate_level` returns `Verdict.ABORT`

### Tracking Ordering
```python
call_order = []

def track_rollback(branch):
    call_order.append(("rollback", branch))
    return True

mock_git_supervisor.rollback.side_effect = track_rollback
```

### Assertions
1. Both objectives failed: `state.objectives_failed == 2`
2. Worktrees were cleaned up: `tmp_path / ".gaia" / "worktrees" / "obj-001"` does NOT exist
3. Git rollback was called for both: `mock_git_supervisor.rollback.call_count == 2`
4. Supervisor verdict was ABORT: `orchestrator.supervisor.state.current_verdict == Verdict.ABORT`

### Rationale
Verifies that during ABORT:
1. Worktrees for completed level objectives ARE cleaned up (lines 831-834, runs before verdict check)
2. Git rollback for failed objectives IS called (lines 869-876, triggered by ABORT verdict)
3. Both operations complete without errors

---

## File Structure After Changes

```
test_parallel_execution.py
├── TestConflictReport          (existing)
├── TestLevelResult             (existing)
├── TestPartitionIntoLevels     (existing)
├── TestOrchestratorConfigParallel (existing)
├── TestExecuteWithoutStatusUpdate (existing)
├── TestParallelExecutionMode   (existing + 1 new test)
│   └── test_parallel_execution_raises_exception
├── TestPropagateFailures       (existing)
├── TestEvaluateLevel           (existing)
├── TestHookSerialization       (existing + 1 new test)
│   └── test_hooks_fire_without_serialization
├── TestConcurrencyLocks        (existing + 1 new test)
│   └── test_semaphore_bounds_concurrent_executions
├── TestHookHaltPipeline        (NEW class + 1 test)
│   └── test_hook_halt_pipeline_blocks_objective
├── TestParallelIntegration     (existing)
├── TestConflictDetection       (existing)
├── TestRollback                (existing + 1 new test)
│   └── test_rollback_worktree_cleanup_before_git_rollback
└── TestWorktreeLifecycle       (existing)
```

---

## Implementation Notes

1. **No new imports needed** -- all required classes are already imported at the top of the test file.
2. **All 5 tests use `tmp_path` fixture** -- consistent with existing patterns.
3. **Tests 1, 4 require custom hook classes** -- follow the same pattern as `TestHookSerialization.test_hooks_fire_in_parallel_mode`.
4. **Tests 2, 3 only need mock adapter configuration** -- no git repo needed.
5. **Test 5 needs real git repo** -- use `_init_git_repo()` pattern from `TestWorktreeLifecycle`.
6. **Test naming**: snake_case, descriptive, follows pytest conventions.
7. **Docstrings**: Each test should have a one-line docstring describing what it verifies.
