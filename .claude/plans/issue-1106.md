---
type: plan
source-issue: 1106
repo: amd/gaia
title: "Reset batch-organize counter across process_query calls"
created: 2026-06-02
status: complete
work_type: code-refactor
complexity: trivial
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 2
test_command: "python -m pytest tests/unit/agents/test_email_agent.py tests/unit/agents/test_email_agent_confirmation.py tests/unit/agents/test_email_agent_prompt_injection.py -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1106-batch-counter-reset
reflection_iterations: 1
agents_used:
  - planning
  - execution
  - validation
---

# Issue #1106 — Reset batch-organize counter across `process_query` calls

## Problem

`EmailTriageAgent`'s I3 batch-organize counter (`_organize_op_count`,
`_organize_distinct_senders`) is only zeroed by `_reset_organize_counter()`, and
that method was only ever called from `_register_tools()` — which runs ONCE at
construction. The second `process_query()` on the same agent instance therefore
inherited the prior turn's counts, so the single-batch-confirm-after-threshold
logic (`_organize_batch_threshold_exceeded`) misfired on the 2nd+ run.

The in-code comment in `__init__` already *claimed* the counter was "Reset per
process_query() call" — the intended design — but nothing wired it to
`process_query`. The reset *method* had a unit test (`test_reset_zeroes_counters`)
but nothing asserted reset *across* `process_query` calls.

## Fix (minimal, surgical)

Override `process_query` on `EmailTriageAgent` to call
`self._reset_organize_counter()` at the start of every turn, then delegate to
`super().process_query(...)`. `EmailTriageAgent` does not inherit `MemoryMixin`,
so this override sits directly above the base `Agent.process_query`.

Only the two batch-organize fields are reset; `_session_preferences` (which must
persist across queries within one instance) is untouched — `_reset_organize_counter`
never references it.

The base `Agent.process_query` / `_process_query_impl` were NOT modified (out of
lane; would alter every agent). The construction-time reset in `_register_tools`
is left as-is (harmless; `__init__` already zeroes the fields).

## TDD

1. RED — added `test_counter_resets_across_process_query_calls` to
   `TestI3BatchThreshold`. Stubs `_process_query_impl` (no live LLM) to capture
   the counter value seen on entry and to simulate a turn that trips the batch
   threshold (6 ops / 4 senders). Asserted both turns START from a zeroed
   counter. Failed `[0, 6] == [0, 0]` before the fix.
2. GREEN — added the `process_query` override; test passes.
3. Full email suite (146 tests) green; black + isort clean.

## Acceptance criteria

- [x] Batch-organize counter resets at the start of every `process_query` call.
- [x] Test asserts the counter starts at zero each `process_query`, the
      single confirmation fires only after the threshold within one run, and the
      next `process_query` starts fresh.

## Lane boundary

Owned: `src/gaia/agents/email/agent.py` + its unit tests
(`tests/unit/agents/test_email_agent_prompt_injection.py`). Did NOT touch
`src/gaia/api/`, `src/gaia/connectors/`, `read_tools.py`, `reply_tools.py`,
`calendar_tools.py`, or the base `Agent`.
