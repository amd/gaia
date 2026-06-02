---
type: plan
source-issue: 1265
repo: amd/gaia
created: 2026-06-02
status: in-progress
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 1
test_command: ".venv/bin/python -m pytest tests/unit/agents/email/ -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1265-prescan-counts
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Issue #1265 — inbox pre-scan with per-category counts (cheap-scan regression test)

## Problem
`pre_scan_inbox` already exists in `src/gaia/agents/email/tools/read_tools.py`
(`pre_scan_inbox_impl` ~line 529, tool ~line 854). The acceptance criteria require
a test that asserts it returns per-category counts that match a fixture inbox
**without** triggering full per-email processing (the cheap-scan guarantee — the
whole point of a pre-scan).

## Finding from code read (decisive)
- `triage_inbox` (the *expensive* tool, line 815) builds
  `classifier = make_llm_classifier(chat)` and passes it to `triage_inbox_impl`,
  so heuristic-uncertain messages get an LLM round-trip.
- `pre_scan_inbox` (the *cheap* tool, line 854) → `pre_scan_inbox_impl` → calls
  `triage_inbox_impl` **without** a classifier (line 569). In `triage_inbox_impl`
  the LLM branch is gated on `classifier is not None` (line 484), and
  `decode_message_body` is read only inside that branch (line 485). So pre-scan is
  **already heuristic-only / cheap.** Deliverable = regression test locking it.
- Latent footgun: `pre_scan_inbox_impl` forwards `force_llm`. Even with
  `force_llm=True`, no classifier exists to call, so it stays cheap — lock this too.

## Heuristic taxonomy (drives the fixture distribution)
`classify_category_heuristic` only ever commits *confidently* to `low priority`
(SPAM / CATEGORY_PROMOTIONS / CATEGORY_SOCIAL / promo-keyword) and `informational`
(CATEGORY_UPDATES / automated-sender). It NEVER confidently emits `urgent` or
`actionable`: IMPORTANT/STARRED → `actionable` but `confident=False`; no-match →
`informational` but `confident=False`. The `confident=False` messages are exactly
the ones the expensive path would send to the LLM — so including them in the
fixture makes the no-LLM assertion meaningful.

## TDD plan
Add `tests/unit/agents/email/test_pre_scan_counts.py`:
1. Build a self-contained `FakeGmailBackend()` (empty) and `add_message()` a fixture
   inbox with a precisely-known distribution: low-priority (promotions), informational
   (updates + automated-sender), and `confident=False` cases (IMPORTANT label, plain
   no-match). Known counts per category.
2. `test_counts_match_fixture` — call `pre_scan_inbox_impl`; assert `totals` +
   `informational_count` + section lengths match the known distribution.
3. `test_no_llm_invoked_impl_layer` — spy `triage_inbox_impl`'s `classifier` kwarg
   (assert never passed non-None) and `decode_message_body` (assert never called).
4. `test_force_llm_stays_cheap` — `force_llm=True`; same no-LLM spies hold.
5. `test_pre_scan_tool_never_calls_llm` — production wiring via the tool registry
   (`_make_email_agent` mocks `AgentSDK`). Spy `make_llm_classifier` (in read_tools'
   namespace) and `agent.chat.send_messages`; assert neither is called while
   `pre_scan_inbox` runs, yet counts are still returned.

All LLM mocked — no Lemonade, no `gaia eval`.

## Lane boundary
OWN: `read_tools.py` (no change expected — impl is already cheap) + new test file.
AVOID `agent.py` (registration already present). Do NOT touch summarize/reply/
calendar/organize tools, `api/`, `connectors/`. REST exposure (issue mentions
"exposed via API") depends on #1229 — noted as follow-up, not in this PR.

## Validation
- `.venv/bin/python -m pytest tests/unit/agents/email/ -q`
- `python util/lint.py --black --isort`
- Self-review (code-reviewer proxy); fix Critical only; ≤3 iterations.
