---
type: plan
source-issue: 1273
repo: amd/gaia
title: "feat(email): calendar conflict detection"
created: 2026-06-02
status: complete
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 3
test_command: ".venv/bin/python -m pytest tests/unit/agents/email/ -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1273-calendar-conflict
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Calendar conflict detection

## Goal
Given a proposed meeting time, flag conflicts against the user's existing
calendar. Natural follow-on to #1272 (meeting detection): detect a meeting
→ check it against the calendar for conflicts. Net-new — no
conflict/overlap/free-busy logic exists under `agents/email/` today.

## Acceptance criteria (from issue #1273)
- Flags conflicts against the user's calendar.
- Unit test: a conflict IS flagged when a candidate event overlaps a busy
  block, and is NOT flagged when it merely abuts (end == start) or does not
  overlap (boundary cases).
- Integration test runs conflict detection against a fixture calendar.

## Design — deterministic time-math, no LLM

Conflict detection is pure interval arithmetic; it must be deterministic
(no Lemonade, no `gaia eval`).

### Half-open interval overlap
Treat every event as the half-open interval `[start, end)`. Two intervals
A and B overlap iff `A.start < B.end and B.start < A.end`. This is the
canonical half-open-overlap test and gives the required boundary behaviour
for free: `2:00–3:00` vs `3:00–4:00` → `3:00 < 3:00` is False → NOT a
conflict (abutting intervals do not overlap).

### Timestamp parsing (Python 3.10+ floor, no new deps)
RFC 3339 timestamps arrive with a `Z` or `+00:00` offset. `datetime.fromisoformat`
on the 3.10 floor does not accept `Z`, so a tiny normaliser converts a
trailing `Z` → `+00:00` before parsing. To compare aware and naive
timestamps without raising, all parsed datetimes are normalised to UTC and
made naive for comparison (a missing offset is assumed already-UTC — the
calendar API always returns offset-qualified `dateTime`, and naive input is
only plausible in tests). All-day events use a `date` (no time) field; an
all-day event is treated as spanning that whole day `[00:00, next-day 00:00)`.

### Fail loudly
If the calendar backend raises while listing events for the candidate
window, the error propagates (re-raised with context at the tool boundary
via the existing `ConnectorsError`/`Exception` envelope handlers). No silent
"no conflicts" on a backend error — that would hide a regression.

## Lane / files
- `src/gaia/agents/email/tools/calendar_tools.py` — add:
  - `_parse_event_dt(value)` — RFC 3339 / date string → comparable datetime.
  - `_event_window(event)` — pull `(start, end)` out of a Google-shaped event.
  - `intervals_overlap(a_start, a_end, b_start, b_end)` — half-open test.
  - `detect_calendar_conflicts_impl(cal, *, start_iso, end_iso, ...)` — query
    existing events in the window and return overlaps.
  - `detect_calendar_conflicts` tool inside `CalendarToolsMixin` (read-only,
    NOT confirmation-gated — it only reads the calendar).
- `src/gaia/agents/email/calendar_backend.py` — **no change expected.** The
  existing `list_events(time_min, time_max)` already supports a windowed
  query. Only touch this file (additively) if a gap appears. Keeping it
  untouched eases the #1276 Outlook-calendar merge.
- `tests/unit/agents/email/test_calendar_conflicts.py` — new test module.

## TDD steps
1. RED: write `test_calendar_conflicts.py`:
   - overlap → conflict flagged
   - abut boundary (`end == start`, both directions) → NOT flagged
   - clear non-overlap → NOT flagged
   - containment / partial overlap → flagged
   - integration: seed `FakeCalendarBackend` with events, call
     `detect_calendar_conflicts_impl`, assert overlaps and the windowed
     `list_events` query
   - backend error → propagates (fail-loud)
2. GREEN: implement the functions above.
3. Lint + full email unit suite green.

## Out of scope / do NOT touch
read_tools, reply_tools, organize_tools, summarize_tools, agent.py, api/,
connectors/. No PR, no live validation, no LLM eval.
