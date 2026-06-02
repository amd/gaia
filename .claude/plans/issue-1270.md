---
type: plan
source-issue: 1270
repo: amd/gaia
title: "feat(email): batch archive (20+ emails in one action)"
created: 2026-06-02
status: in-progress
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 3
test_command: ".venv/bin/python -m pytest tests/unit/agents/email/ tests/unit/agents/test_email_agent_tools.py tests/unit/agents/test_email_agent_soft_delete.py -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1270-batch-archive
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Issue 1270 — Batch archive (20+ emails in one action)

## Acceptance criteria
1. Archives a large selection (20+ emails) in a single action.
2. Test asserts 20+ archived in one action AND the operation is reversible
   within the undo window.

## Verified current state
- `archive_message_batch` (organize_tools.py ~L593) already archives N messages
  in one tool call, recording one `email_actions` row per message with a shared
  `batch_id` and a `prior_labels` payload. AC #1 is met by the existing tool.
  - Batch threshold (`_check_threshold`) is evaluated ONCE at the top of the
    batch (counter starts at 0 on a fresh agent → passes), then the per-message
    counter is bumped AFTER. So a single 20+ batch on a fresh agent does not trip
    the threshold. Confirmed.
- GAP (AC #2): there is NO undo path for `archive`. `restore_message_impl`
  (delete_tools.py — OUT OF LANE) explicitly raises for any `action_type` other
  than `trash`. The `prior_labels` recorded by archive are never consumed. So a
  batch archive is currently NOT reversible.

## Plan
TDD. In lane: `organize_tools.py`, `action_store.py`, tests only.

1. FAILING test under `tests/unit/agents/email/test_batch_archive.py`:
   - Build a ≥20-message fixture inbox (inject Gmail-API-shape messages into a
     `FakeGmailBackend`, all labelled `INBOX`).
   - Archive all in ONE `archive_message_batch` call; assert `total >= 20`,
     all succeeded, all out of INBOX, all rows share one `batch_id`.
   - Undo via a new `undo_archive_batch(batch_id)` tool within the window;
     assert every message is back in INBOX and every row is marked undone.
   - Assert undo OUTSIDE the window fails loudly (rows forced stale).
2. Minimal fix to close the gap (in lane):
   - `action_store.fetch_batch_undoable(db, *, batch_id, window_seconds)` →
     list of in-window, not-yet-undone rows for the batch (mirrors
     `fetch_undoable`).
   - `organize_tools.undo_archive_batch_impl` + `undo_archive_batch` tool:
     for each undoable `archive` row in the batch, re-add INBOX (restore
     `prior_labels`), mark the row undone. Fail loudly if the batch has no
     undoable rows (expired/unknown).
3. Green + lint.

## Risks
- Must not touch `delete_tools.py` / `restore_message` (out of lane). New
  archive-undo lives in organize_tools.py — its rightful owner.
- Undo must restore the FULL prior label set, not just re-add INBOX, so a
  message that also carried e.g. STARRED keeps it. Use `prior_labels`.
