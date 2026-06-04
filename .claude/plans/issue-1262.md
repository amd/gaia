---
type: plan
source-issue: 1262
repo: amd/gaia
title: "feat(email): request/response contract schema (single email + thread)"
created: 2026-06-01
status: in-progress
work_type: feature
complexity: medium
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 6
test_command: "python -m pytest tests/unit/agents/email/test_contract_schema.py -x"
build_command: "uv pip install -e \".[dev]\""
lint_command: "python util/lint.py --all"
branch: feat/issue-1262-email-contract-schema
reflection_iterations: 1
agents_used:
  - general-purpose (code-explorer)
  - general-purpose (code-reviewer)
---

# Issue #1262 — Email request/response contract schema

## Goal
A documented, STABLE request/response schema shared by the REST surface (#1229)
and the MCP stdio interface (#1104). GAIA owns the contract (#1261 Q2 RESOLVED).
Freeze it before dependent endpoints are built.

## Acceptance criteria
1. Documented, stable INPUT schema: principal recipient, other participants, body.
2. Documented OUTPUT schema: category, summary, draft, action items.
3. Schema handles BOTH a single email AND a full thread.

### Test ACs
- Sample request/response payloads validate (single email AND thread).
- Invalid payloads rejected loudly.

## Design decisions (grounded in the existing email agent)

- **Validation idiom: pydantic v2.** Already a hard dependency (`setup.py`:
  `pydantic>=2.9.2`) and used in `src/gaia/api/schemas.py`, `src/gaia/ui/models.py`.
  No new dependency. Matches the REST surface's idiom so #1229 can reuse the models
  directly as FastAPI request/response bodies.
- **Placement: `src/gaia/agents/email/contract.py`** — dependency-light (pydantic
  only, no Gmail/connector imports) so BOTH the REST server and the MCP stdio
  interface can import it without dragging backends in.
- **Category taxonomy** reuses the frozen four-bucket scheme from
  `triage_heuristics.py` (`urgent | actionable | informational | low priority`)
  via a `Literal`/enum — drift would break AC4 of the agent. Plus `is_spam` /
  `is_phishing` booleans surfaced independently (matches triage output).
- **Single-email vs thread** is modeled with a discriminated union on a `kind`
  field (`"single"` | `"thread"`) so a consumer can branch deterministically and
  validation rejects a thread payload missing its message list.
- **Participants model** mirrors RFC-822 roles: `principal` (the recipient the
  agent acts on behalf of), `from`, `to`, `cc`, `bcc`. Address = display name +
  email, email validated non-empty + contains `@`.
- **Output draft / action items** mirror what the agent already produces
  (`reply_tools` draft = to/subject/body; action items = free-text imperative with
  optional due hint). `summary` is plain text.
- **Stability surface:** `SCHEMA_VERSION` constant pinned in the module and echoed
  in both request and response so a consumer can detect a breaking change. Models
  use `extra="forbid"` so unknown fields are rejected loudly (no silent drift).

## Module shape (`contract.py`)
- `SCHEMA_VERSION: str`
- `EmailCategory` (str Enum: urgent/actionable/informational/low priority)
- `EmailAddress` (name, email)
- `EmailMessage` (message_id, from_, to, cc, date, subject, body, ...)
- `SingleEmailInput` (kind="single", principal, message)
- `ThreadInput` (kind="thread", principal, thread_id, messages: non-empty list)
- `EmailTriageRequest` (schema_version, payload: discriminated union)
- `ActionItem` (description, due_hint?)
- `DraftReply` (to, subject, body)
- `EmailTriageResult` (category, is_spam, is_phishing, summary, action_items, draft?)
- `EmailTriageResponse` (schema_version, request_kind, result)
- helper `parse_request(dict) -> EmailTriageRequest` that raises loudly.

## TDD steps
1. Write `tests/unit/agents/email/test_contract_schema.py` with failing tests:
   valid single-email request, valid thread request, valid responses (both kinds),
   invalid payloads rejected (missing principal, empty thread, bad category,
   unknown field, malformed address, wrong schema_version type).
2. Implement `contract.py` until green.
3. Re-export from `email/__init__.py` (dependency-light import safety).
4. Docs: `docs/spec/email-contract.mdx` + register in `docs/docs.json`.
5. Lint + review.

## Test / build / lint
- test: `python -m pytest tests/unit/agents/email/test_contract_schema.py -x`
  plus `python -m pytest tests/ -k "email and (schema or contract)" -x`
- build: `uv pip install -e ".[dev]"`
- lint: `python util/lint.py --all`

## Real-world tier
LOCAL-ONLY — pure schema + docs. Nothing to exercise on hardware.
