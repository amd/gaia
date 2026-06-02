---
type: plan
source-issue: 1267
repo: amd/gaia
title: "feat(email): per-email summarization"
created: 2026-06-02
status: in-progress
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 3
test_command: "python -m pytest tests/unit/agents/email/test_summarize_tools.py -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1267-per-email-summarize
reflection_iterations: 0
agents_used:
  - planning
  - execution
  - validation
---

# Issue #1267 — Per-email summarization

## Goal
Produce a concise, length-bounded summary for a single email that captures the
message's key ask/decision. Today summaries only arise implicitly inside triage;
there is no standalone per-email summarize capability and no length-bounded
"key ask" test.

## Acceptance criteria
1. Produces a concise summary for an individual email.

### Test ACs
- Test asserts the summary captures the key ask/decision of a fixture email
  within a length bound.

## Design decisions (grounded in the existing email agent)
- New module `src/gaia/agents/email/tools/summarize_tools.py`, mirroring the
  structure of the sibling LLM-facing module `tools/llm_triage.py`:
  - A custom `EmailSummarizeError(RuntimeError)` carrying `message_id` — fail
    loudly on LLM transport failure or an empty/whitespace summary; NEVER return
    a silent empty summary (repo "No Silent Fallbacks" rule).
  - A pure `summarize_email_llm(chat, *, subject, sender, body, message_id,
    max_chars)` that wraps the body in the agent's untrusted-input delimiters
    (`wrap_untrusted_body`) so a crafted body cannot steer the summarizer, calls
    `chat.send_messages(messages, system_prompt=..., temperature=0.0)`, and
    reads `.text` exactly like `classify_email_llm`.
  - **Length bound enforced explicitly**: the system prompt asks for 1-2
    sentences; the impl then hard-caps the returned text to `max_chars`
    (default 300) at a word boundary with an ellipsis. This is the *contract*
    of a length-bounded summary, not a silent degradation path.
- A `SummarizeToolsMixin` exposing `_register_summarize_tools()` and a
  `summarize_message(message_id)` tool that reads the message via the existing
  `get_message_impl` (so HTML→text decoding + body limits are reused) and calls
  `summarize_email_llm`. Returns the standard `{"ok": true|false, ...}` envelope
  used by every other email tool. The LLM `chat` is captured live from
  `agent.chat` at call time (same pattern as `triage_inbox`).
- Registration: add `SummarizeToolsMixin` to `EmailTriageAgent`'s bases and call
  `self._register_summarize_tools()` inside `_register_tools()` — the ONLY edit
  to `agent.py`, restricted to the mixin list + the registration line. No change
  to `process_query` or `_get_system_prompt` (lane boundary with #1106).

## Lane boundary
- OWN: `tools/summarize_tools.py`, its registration in `agent.py` (bases + one
  call), and `tests/unit/agents/email/test_summarize_tools.py`.
- AVOID: `read_tools.py`, `reply_tools.py`, `calendar_tools.py`, `api/`,
  `connectors/`, and any `agent.py` logic beyond tool registration.

## TDD
1. FAILING test in `tests/unit/agents/email/test_summarize_tools.py`: a fixture
   email + a deterministic `_FakeChat` (mirrors `test_email_llm_triage.py`).
   Assert (a) summary ≤ length bound, (b) it surfaces the key ask, (c) empty LLM
   output raises, (d) transport failure raises, (e) body is fenced in untrusted
   delimiters.
2. Implement the module + mixin + registration.
3. Green + lint.

## Eval trigger
This adds a NEW LLM system prompt + a NEW tool docstring/schema → flag for the
orchestrator's serial eval. Do NOT run `gaia eval` here (single-Lemonade
contention with other leads).
