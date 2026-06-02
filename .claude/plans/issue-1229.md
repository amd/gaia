---
type: plan
source-issue: 1229
repo: amd/gaia
title: "REST API surface for the email agent (single email in, structured results out)"
created: 2026-06-02
status: in-progress
work_type: code-feature
complexity: complex
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 4
test_command: ".venv/bin/python -m pytest tests/test_api.py tests/unit/agents/email/ tests/integration/test_email_rest_api_e2e.py -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1229-email-rest-api
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Issue #1229 — REST API surface for the email agent

## Goal
Expose the email agent over REST so a consuming app can:
1. POST a single email (or thread) per the FROZEN #1262 contract and get a
   structured triage result back (category, summary, draft, action items).
2. Request a send, which is REJECTED with a 4xx unless an explicit confirmation
   token is present — the send-confirmation gate (#1264) translated to the API
   boundary. No silent auto-confirm.

## Foundations (reuse, do NOT reinvent)
- `src/gaia/agents/email/contract.py` — FROZEN pydantic v2 contract. Triage-only:
  `EmailTriageRequest` (discriminated `single`/`thread` union) → `EmailTriageResponse`
  (`EmailTriageResult`: category, is_spam, is_phishing, summary, action_items, draft).
  There is NO send envelope in the contract — send is a separate concern.
- `src/gaia/agents/email/tools/triage_heuristics.py::classify_category_heuristic` —
  the agent's REAL deterministic categorizer. Imports cleanly without pulling Gmail/
  connector backends. Used to derive category/is_spam/is_phishing deterministically.
- `src/gaia/agents/email/agent.py::EmailTriageAgent` — conversational LLM agent.
  Consume READ-ONLY (do not modify — #1106 owns it in parallel). Drives the e2e
  pipeline via its read/triage/draft tools + `TOOLS_REQUIRING_CONFIRMATION` gate.
- `src/gaia/api/openai_server.py` — module-level FastAPI `app`. Wire an APIRouter.
- `tests/fixtures/email/fake_gmail.py::FakeGmailBackend` + `synthetic_inbox` fixture
  — the deterministic backend for the e2e (Gmail-API-shaped, records `transport.calls`).

## Architecture
New module `src/gaia/api/email_routes.py`:
- `EmailTriageService` — converts a contract `EmailInput` → a structured
  `EmailTriageResult` deterministically:
  - category/is_spam/is_phishing via `classify_category_heuristic` over the
    message subject/from/(synthetic Gmail labels derived from is_spam? no —
    we have no labelIds in the contract, so classify on subject+sender only;
    that is exactly the heuristic's keyword path).
  - summary: deterministic (subject + first sentence/snippet of body).
  - action_items: deterministic extraction (imperative/“please …” lines).
  - draft: a proposal ONLY (never sent here). The contract's `DraftReply`.
  This is NOT an LLM call — it is the heuristic fast path, which is exactly
  what the agent runs pre-LLM. Unit tier mocks any LLM; this path is deterministic.
- `POST /v1/email/triage` — body=`EmailTriageRequest`, returns `EmailTriageResponse`.
  FastAPI validates the request against the frozen model; an unknown field /
  malformed payload → 422 (pydantic `extra="forbid"`). Response validated by the
  declared `response_model`.
- Send envelope models (LOCAL to email_routes.py — contract.py is frozen):
  `EmailSendRequest { to, subject, body, confirmation_token: Optional[str] }`,
  `EmailSendResponse { sent_id, to, subject, sent: True }`.
- `POST /v1/email/send` — if `confirmation_token` is absent/empty/invalid →
  HTTP 403 (or 400) with an actionable error naming the missing token. The
  guard is the FIRST thing checked. Only a valid token reaches the backend
  `send_message`. This mirrors `console.confirm_tool_execution() == False`
  → boundary translation to 4xx.
- Confirmation token: server issues a token from the triage/draft step
  (`POST /v1/email/draft` returns a `confirmation_token` bound to the draft
  payload hash). The send endpoint validates the token matches the payload.
  Tokens are single-use, in-memory. A consuming app cannot send without first
  obtaining a token AND echoing it — proving explicit confirmation.

  Keep it MINIMAL: a token store seeded by the draft endpoint; send checks it.

Wiring: `openai_server.py` imports and `app.include_router(email_router)`.
Lane: only `src/gaia/api/` + tests. No edits to `agent.py`/`contract.py`/`connectors/`.

## TDD order
1. `tests/test_api.py` (TestClient, ImportError-guarded + skip like existing):
   - triage single-email-in → 200 + contract-valid `EmailTriageResponse`
     (parse_response round-trips; request_kind=="single"; category in taxonomy).
   - triage thread-in → 200 + request_kind=="thread".
   - malformed/unknown-field request → 422 (extra="forbid" fires).
   - send WITHOUT confirmation_token → 4xx (assert no backend send happened).
   - send WITH a valid token (issued by draft endpoint) → 200, backend send called.
2. `tests/integration/test_email_rest_api_e2e.py` — FakeGmailBackend driven through
   the agent's REAL tool impls: pre-scan → categorize → summarize → draft →
   send-gate. Asserts each stage's contract; send blocked w/o confirm, allowed w/.
   Deterministic stubs only; no live mail, no live Lemonade.
3. Implement `email_routes.py`; wire into `openai_server.py`; green.

## Acceptance criteria mapping
- AC: consuming app invokes over REST → `/v1/email/triage` + TestClient tests.
- AC: single email in → structured out → triage endpoint + contract round-trip.
- AC: never sends without confirmation → `/v1/email/send` 4xx-without-token test.
- AC test: TestClient single-in→struct-out, #1262 shape, 4xx on send w/o confirm.
- AC test: e2e FakeGmail pre-scan→categorize→summarize→draft→send-gate, mocked LLM.

## Risks
- The contract is triage-only; the send/draft/confirmation-token envelope is NEW
  and local to email_routes.py (NOT added to frozen contract.py). Documented.
- fastapi is in `[api]`/`[ui]` extras, NOT `[dev]`. Tests MUST guard imports and
  skip when fastapi is absent (matches existing test_api.py) so `[dev]`-only CI
  stays green. Local verification installs `[api]`.
- e2e must not modify agent.py. It constructs `EmailTriageAgent(EmailAgentConfig(
  gmail_backend=FakeGmailBackend(...), base_url=local))` and calls the tool impls
  directly (deterministic), not `process_query` (which needs an LLM).
