---
type: plan
source-issue: 1272
repo: amd/gaia
title: "feat(email): detect meeting requests in email body"
created: 2026-06-02
status: in-progress
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 2
test_command: ".venv/bin/python -m pytest tests/unit/agents/email/ -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1272-meeting-detect
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Detect meeting requests in an email body

## Goal
Identify meeting requests embedded in an email body. Net-new capability —
only a passing mention of "meeting invitation" exists in the llm_triage
prompt today; no detector tool/function exists.

## Acceptance criteria
- Identifies meeting requests within an email body.
- Unit tests on labelled fixtures: a clear meeting request (true positive),
  a non-meeting email (true negative), and an ambiguous body
  (e.g. "let's sync sometime") — assert sensible handling.

## Design

Mirror the established triage pattern in this package:
`triage_heuristics.py` (deterministic) + `llm_triage.py` (LLM, fail-loud).

Add to `src/gaia/agents/email/tools/calendar_tools.py`:

1. `detect_meeting_request_heuristic(subject, body) -> MeetingDetection`
   — deterministic keyword + time/date signal scan. Returns a frozen
   dataclass with `is_meeting_request: bool`, `confidence: str`
   (`"high" | "low"`), `signals: tuple[str, ...]`, `reason: str`.
   - High confidence: an explicit invite phrase ("are you free",
     "schedule a call", "let's meet", "set up a meeting", "invite you to",
     "calendar invite") OR a meeting noun co-occurring with a concrete
     time/date signal (weekday, clock time, "tomorrow", "next week").
   - Low confidence (ambiguous): a soft/vague signal alone
     ("let's sync sometime", "we should catch up", "touch base") with no
     concrete time — flagged is_meeting_request=False but confidence="low"
     so the caller knows to escalate to the LLM rather than trusting the
     negative.
   - Clear non-meeting: no signals at all → is_meeting_request=False,
     confidence="high".

2. `detect_meeting_request_llm(chat, *, subject, body, message_id="")
   -> dict` — LLM follow-up for the ambiguous (low-confidence) case.
   Reuses `wrap_untrusted_body` for the prompt-injection fence and the
   fail-loud contract from llm_triage: raises `MeetingDetectionError` on
   transport failure / unparseable output / out-of-schema value. NEVER
   silently defaults to "not a meeting".

3. `detect_meeting_request_impl(*, subject, body, classifier=None,
   message_id="")` — orchestrator. Runs the heuristic; if it is confident,
   returns immediately (no LLM). If low-confidence AND a classifier is
   wired, escalates to the LLM and returns its decision. If low-confidence
   and no classifier, returns the heuristic result with
   `confident=False` so the caller can decide. Raises loudly if the
   classifier raises (no swallow).

4. `@tool detect_meeting_request(...)` registered in
   `CalendarToolsMixin._register_calendar_tools` — read-only (NOT in
   `TOOLS_REQUIRING_CONFIRMATION`; it only inspects text). Wires
   `agent.chat` into the LLM classifier at call time like `triage_inbox`.

## Lane boundary
- OWN: `src/gaia/agents/email/tools/calendar_tools.py` + new test file.
- DO NOT touch: read_tools, reply_tools, organize_tools, summarize_tools,
  agent.py, api/, connectors/.

## Tests (TDD — written first, must fail before impl)
`tests/unit/agents/email/test_meeting_detection.py` — all LLM mocked, no
Lemonade:
- TP: clear meeting request → is_meeting_request True, high confidence.
- TN: ordinary email → is_meeting_request False, high confidence.
- Ambiguous "let's sync sometime" → low confidence; heuristic does not
  assert a hard positive; with a mock LLM the orchestrator returns the
  LLM's decision; without a classifier it surfaces confident=False.
- Fail-loud: LLM transport error / unparseable / out-of-schema → raises
  MeetingDetectionError, never defaults.
- Body wrapped in untrusted-input delimiters before reaching the model.
