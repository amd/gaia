---
type: plan
source-issue: 1268
repo: amd/gaia
title: "feat(email): full-thread reading & comprehension"
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
branch: tmi/issue-1268-full-thread
reflection_iterations: 1
agents_used: [planning, execution, validation]
---

# Issue #1268 — Full-thread reading & comprehension

## Problem
`get_thread` (read_tools.py) fetches every message in a thread, but nothing
summarizes/comprehends over the FULL thread. The agent can only summarize the
latest message (per-email `summarize_message`, #1267). A decision made in an
early message that the latest reply doesn't repeat is invisible to the user.

## Acceptance criteria
- Agent summarizes/acts on the FULL thread, not only the latest message.
- Test asserts the produced thread summary reflects content from a NON-latest
  message in a multi-message (3+) thread fixture.

## Stacked on #1267
Reuses `summarize_tools.summarize_email_llm` (the bounded, fail-loud LLM
summarization primitive) to summarize the whole-thread transcript rather than
reinventing summarization.

## Design
Add to `read_tools.py` (my lane):
- `_format_thread_for_summary(messages)` — pure helper. Sorts messages
  oldest→newest by `internalDate` (Gmail `threads.get` is chronological, but
  don't trust ordering; sort defensively), then renders a compact transcript:
  each message numbered with From/Date/body, every body wrapped in the existing
  untrusted-input delimiters. This is the FULL-thread payload — not just the
  latest message.
- `summarize_thread_impl(gmail, chat, *, thread_id, max_chars, debug)` —
  reads all messages via `get_thread`, builds the transcript, calls
  `summarize_email_llm` with `body=<full transcript>` and a thread-aware
  subject. Returns `{thread_id, subject, message_count, summary}`.
  Fail-loud: empty thread raises; LLM failure propagates as
  `EmailSummarizeError` (no silent truncation to latest-only).
- `summarize_thread(thread_id)` `@tool` on `ReadToolsMixin`, mirroring the
  envelope + error handling of the other read tools. Reads `agent.chat` live.

Register `summarize_thread` in `agent.py`? No — it's added to `ReadToolsMixin`
which is already wired via `_register_read_tools()`, so NO `agent.py` edit is
required. (Collision-free.)

## Tests (TDD) — `tests/unit/agents/email/test_thread_comprehension.py`
1. Build a 3-message thread in-test via `FakeGmailBackend.add_message` (shared
   `threadId`, ascending `internalDate`). Message #1 announces a decision
   ("we chose Postgres"); the latest message does NOT repeat it.
2. Stub chat to echo back the transcript it received (so the test can assert
   the early decision text reached the model) AND a fixed summary referencing
   the early decision.
3. Assertions:
   - The full transcript fed to the LLM contains message #1's decision text
     (proves non-latest content is included, not just the latest).
   - All 3 messages present in the prompt, oldest-first.
   - Bodies wrapped in untrusted delimiters.
   - Summary envelope `ok=True`, `message_count == 3`.
   - LLM failure → error envelope (fail-loud).
   - Empty/unknown thread → error envelope.

Mock the LLM — no Lemonade, no `gaia eval`.

## Lane
OWN: `read_tools.py` thread-comprehension addition + new test file.
Consume `summarize_tools.py` read-only. No `agent.py` edit.

## Eval trigger
New LLM-facing prompt surface (a thread transcript + a new `@tool` docstring).
Flag for an eval run before merge per repo policy, though the unit test mocks
the LLM. Recorded in handoff.
