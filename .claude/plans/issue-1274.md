---
type: plan
source-issue: 1274
repo: amd/gaia
title: "feat(email): create calendar events from email context"
created: 2026-06-02
status: complete
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 2
test_command: ".venv/bin/python -m pytest tests/unit/agents/email/ -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1274-event-from-email
reflection_iterations: 1
agents_used: [planning, execution, validation]
---

# Create calendar events from email context

## Goal
Create a calendar event using details extracted from an email
(title/time/attendees), and — critically — refuse to create a bogus event
when the email carries no parseable date/time. Stacked on #1273; the
`create_event_from_email` tool already exists in `calendar_tools.py`. Scope
is hardening + the missing test, including the no-datetime negative case.

## Acceptance criteria (from issue #1274)
- Creates events using details extracted from an email.
- Test asserts the created event's title/time/attendees match what was
  extracted from a fixture email.
- No-datetime negative case: an email with no parseable date/time must NOT
  silently create a bogus event — it surfaces that no time was found
  (fail-loud).

## Defect being fixed
`create_event_from_email_impl` builds `{"dateTime": start_iso}` /
`{"dateTime": end_iso}` and posts to `cal.create_event` with NO validation.
When the caller (the LLM, which parses the email) has no time to supply and
passes empty strings, the backend would create an event with an empty
start/end — a silent bogus event. That is the exact fail-loud violation the
issue calls out. The fix: reject a missing/blank start or end (and an
inverted/equal window) before any `create_event` call, raising a clear,
actionable error.

## Design — deterministic extraction + fail-loud creation, no LLM

Two additions to `calendar_tools.py` (lane-clean):

1. `extract_event_details(subject, body, sender)` — deterministic. Title
   from the subject (trimmed; falls back to a generic when blank);
   attendees parsed from the `From`/sender header (reusing
   `read_tools.extract_sender_email` — no parallel parser); `has_datetime`
   reusing the existing `_TIME_RE` already in this module for meeting
   detection. It DETECTS whether a concrete time exists; it does not do
   natural-language → ISO conversion (no date-parse lib in the project, and
   that resolution is the LLM's job for the timed-arg path). The
   `has_datetime=False` result is what powers the no-datetime negative case.

2. `create_event_from_email_impl` gains a guard: blank/missing `start`/`end`
   `dateTime` (or a `date`) raises `NoEventDateTimeError` (a `ValueError`
   subclass) BEFORE calling `cal.create_event`. The registered tool turns
   that into an `ok=False` envelope so the agent surfaces "no time found"
   rather than creating a bogus event.

## Tests (TDD — failing first)
`tests/unit/agents/email/test_create_event_from_email.py`:

- Extraction (fixture email, clear time): title == subject, attendees ==
  sender address, `has_datetime is True`.
- Creation happy path: `create_event_from_email_impl` against
  `FakeCalendarBackend` creates an event whose summary/start/end/attendees
  match the extracted details; the `create_event` call is recorded with
  those fields.
- No-datetime negative case (impl): blank start/end raises
  `NoEventDateTimeError` and NO `create_event` call is recorded on the fake.
- No-datetime negative case (tool): the registered `create_event_from_email`
  tool returns `ok=False` with an actionable error and creates no event.
- Inverted/equal window raises (already-bad input surfaced loudly).

All LLM access mocked; no Lemonade, no `gaia eval`. Extraction is heuristic,
so the real parser is exercised directly.

## Lane boundary
OWN: `src/gaia/agents/email/tools/calendar_tools.py` + the new test. Do not
touch read/reply/organize/summarize tools, the agent, api/, connectors/.
`calendar_backend.py` untouched (the guard lives in the tool layer).
