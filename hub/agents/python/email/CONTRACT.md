# Email Triage Contract Schema

> **Source code:** [`gaia_agent_email/contract.py`](gaia_agent_email/contract.py)
>
> **Component:** Email request/response contract (issue #1262)
> **Module:** `gaia_agent_email.contract`
> **Validation:** pydantic v2
> **Schema version:** `2.3`

---

## Overview

A **frozen, stable** request/response schema for the Email Triage Agent, shared
by the REST surface ([#1229](https://github.com/amd/gaia/issues/1229)) and the
MCP stdio interface ([#1104](https://github.com/amd/gaia/issues/1104)). GAIA owns
this contract — the consuming application conforms to it, not the other way
around. It is frozen here so the dependent endpoints can be built against a
stable shape.

**Key properties:**

- **One schema, two surfaces.** REST and MCP stdio import the same pydantic
  models, guaranteeing identical structured output for a fixed input.
- **Single email *and* full thread.** The triage input is a discriminated union
  on a `kind` field (`"single"` / `"thread"`); a consumer branches
  deterministically.
- **Dependency-light.** `gaia_agent_email.contract` imports only pydantic — no
  Gmail or connector backends — so either surface can import it without pulling
  live-mail machinery into the process. (A regression test enforces this.)
- **Fail loudly.** Every model forbids unknown fields (`extra="forbid"`). An
  off-contract payload raises a `ValidationError` naming the offending field,
  never a silently coerced result.
- **Versioned.** `SCHEMA_VERSION` (`"2.3"`) is pinned in the module and echoed in
  every request and response so a consumer can detect a breaking change.

### Version history

`SCHEMA_VERSION` bumps only on a breaking change; additive surfaces keep older
consumers working.

| Version | Change |
|---|---|
| `1.0` | First frozen revision — single-email + thread triage. |
| `2.0` | Five-bucket taxonomy (#1615); batch triage (#1887). |
| `2.1` | Additive REST surfaces: inbox search (#1781), archive + phishing-quarantine and their undo (#1779), calendar view/create/respond (#1780), inbox pre-scan (#1778). |
| `2.2` | Additive attachment handling (#1542): `EmailMessage` / `EmailTriageResult` / `DraftReply` gain an `attachments` metadata list; draft/send accept `OutgoingAttachment` payloads. |
| `2.3` | **Breaking:** `EmailTriageResult.draft` is now a `DraftScaffold` (recipient + subject only) instead of a `DraftReply` — triage never composed a body, so the always-empty `draft.body` is dropped. `DraftReply` (with `body`) is unchanged and remains the `POST /v1/email/draft` + MCP `draft_reply` response. |

---

## Category taxonomy

`EmailCategory` is the **five-bucket** triage taxonomy (schema 2.0, #1615). The
string values mirror the agent's `triage_heuristics.ALL_CATEGORIES`; a contract
test asserts byte-for-byte equality, so drift in either place fails CI.

| Value | Meaning |
|---|---|
| `URGENT` | Time-critical; needs attention now. |
| `NEEDS_RESPONSE` | Actionable; a reply/action is expected. |
| `FYI` | Informational; no action required. |
| `PROMOTIONAL` | Marketing / bulk mail. |
| `PERSONAL` | Personal correspondence. |

> **Transport authentication is separate from this schema.** The frozen sidecar
> requires a **per-session bearer token** (`Authorization: Bearer <token>`) on
> every `/v1/email/*` request and enforces a loopback Host/Origin allowlist
> ([#1706](https://github.com/amd/gaia/issues/1706)) — `401`/`400`/`403` on
> failure. That is a deployment/transport control, not part of the request/response
> contract, so it is **not** encoded in the frozen OpenAPI document. See
> [Email Integration → Authentication](https://amd-gaia.ai/docs/guides/email-integration#authentication).

---

## Request schema (input)

`EmailTriageRequest` — top-level triage envelope.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | Contract version. Defaults to `"2.3"`. |
| `payload` | `SingleEmailInput` \| `ThreadInput` | Discriminated on `kind`. |
| `context` | `TriageContext` \| null | Optional; biases categorization/summary. |

### Shared value objects

`EmailAddress`:

| Field | Type | Notes |
|---|---|---|
| `name` | string \| null | Display name. Optional. |
| `email` | string | Required. Rejected loudly if it lacks `@` or a dotted domain. |

`AttachmentMeta` (schema 2.2 — metadata only, no content):

| Field | Type | Notes |
|---|---|---|
| `filename` | string | Required, non-empty. |
| `mime_type` | string | MIME type as reported by the provider. |
| `size_bytes` | int | Decoded size, `>= 0`. |
| `attachment_id` | string \| null | Provider handle to fetch bytes (Gmail `body.attachmentId`); null when none. |

`OutgoingAttachment` (schema 2.2 — content travels inline on draft/send):

| Field | Type | Notes |
|---|---|---|
| `filename` | string | Required; rejects CRLF/null/quote (header-injection safe). |
| `mime_type` | string | Must match `type/subtype`. |
| `content_base64` | string | Standard (RFC 4648) base64. Must decode, be non-empty, and be ≤ `MAX_ATTACHMENT_BYTES` (25 MB). |

`EmailMessage`:

| Field | Type | Notes |
|---|---|---|
| `message_id` | string | Provider message id. Required. |
| `thread_id` | string \| null | Owning thread id. |
| `from` | `EmailAddress` | Sender. On the wire the key is `from`; in Python the field is `from_` (keyword clash). Required. |
| `to` | `EmailAddress[]` | Primary recipients. |
| `cc` | `EmailAddress[]` | Carbon copies. |
| `bcc` | `EmailAddress[]` | Blind carbon copies. |
| `date` | string \| null | ISO-8601 timestamp. |
| `subject` | string | Subject line. |
| `body` | string | Plain-text body to analyze. Required. |
| `attachments` | `AttachmentMeta[]` | Attachment metadata (schema 2.2). Content never travels here. |

`TriageContext` (optional caller-supplied bias, #1541):

| Field | Type | Notes |
|---|---|---|
| `people` | string[] | Important people whose mail weighs higher. |
| `projects` | string[] | Active projects the principal cares about. |
| `tone` | string \| null | Preferred summary tone, e.g. `"concise"`. |
| `self_email` | string \| null | The principal's own address, so the model knows who "I" is. |

### `SingleEmailInput` (`kind: "single"`)

| Field | Type | Notes |
|---|---|---|
| `kind` | `"single"` | Discriminator. |
| `principal` | `EmailAddress` | Inbox owner the agent acts on behalf of. Required. |
| `message` | `EmailMessage` | The one message to analyze. Required. |

### `ThreadInput` (`kind: "thread"`)

| Field | Type | Notes |
|---|---|---|
| `kind` | `"thread"` | Discriminator. |
| `principal` | `EmailAddress` | Inbox owner. Required. |
| `thread_id` | string | Conversation id. Required. |
| `messages` | `EmailMessage[]` | **Non-empty**, oldest-first. An empty thread is rejected loudly. |

The principal is the account owner — distinct from a message's `to`: in a thread
the principal is not necessarily a recipient of every message.

---

## Response schema (output)

`EmailTriageResponse` — top-level triage response envelope.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | Echoes the contract version. |
| `request_kind` | `"single"` \| `"thread"` | Which input shape produced the result. |
| `result` | `EmailTriageResult` | The structured analysis. |

`EmailTriageResult`:

| Field | Type | Notes |
|---|---|---|
| `category` | `EmailCategory` | One of the five taxonomy buckets. |
| `is_spam` | bool | Spam signal, scored independently of `category`. |
| `is_phishing` | bool | Phishing signal, independent of `is_spam`. |
| `summary` | string | Plain-text summary of the email / thread. Required. |
| `action_items` | `ActionItem[]` | Extracted actions. May be empty. |
| `draft` | `DraftScaffold` \| null | Proposed reply **scaffold** (recipient + subject only, no body), or `null` when none is suggested (schema 2.3). Triage never composes reply prose — compose the body yourself and `POST /v1/email/draft` to get a full `DraftReply` + confirmation token. |
| `suggested_action` | `"reply"` \| `"none"` \| `"archive"` | Derived next action (reply for URGENT/NEEDS_RESPONSE, archive for PROMOTIONAL, none otherwise). Defaults to `"none"`. |
| `message_id` | string \| null | Echoes the request message-id when available; null for raw Gmail-API-sourced results. |
| `usage` | `TriageUsage` \| null | LLM token/throughput metrics. Null on the heuristic-only path (no LLM call). |
| `attachments` | `AttachmentMeta[]` | Attachment metadata of the analyzed message(s), echoed for downstream processing (schema 2.2). |

`ActionItem`:

| Field | Type | Notes |
|---|---|---|
| `description` | string | Imperative action. Required, non-empty. |
| `due_hint` | string \| null | Free-text due hint as written (`"Friday"`); not parsed into a date. |
| `type` | `"text"` \| `"link"` | Discriminator; defaults to `"text"`. |
| `url` | string \| null | Required and non-empty when `type="link"`; must be `null` when `type="text"`. |

`DraftScaffold` (the triage-response draft, schema 2.3):

| Field | Type | Notes |
|---|---|---|
| `to` | `EmailAddress[]` | **Non-empty** proposed recipients. |
| `subject` | string | Proposed subject (`Re:`-prefixed). |

There is deliberately **no `body`** here — triage does not write replies. The full
`DraftReply` returned by `POST /v1/email/draft` adds `body` (the reply prose you
compose) and `attachments` (`AttachmentMeta[]`, schema 2.2), and mints a
single-use send-confirmation token.

`TriageUsage`:

| Field | Type | Notes |
|---|---|---|
| `prompt_tokens` | int | Sum of input tokens across the LLM calls. |
| `completion_tokens` | int | Sum of output tokens. |
| `total_tokens` | int | Sum of input + output. |
| `tokens_per_second` | float | Aggregate decode throughput. |

---

## Example — single email

### Request

```json
{
  "schema_version": "2.3",
  "payload": {
    "kind": "single",
    "principal": { "name": "Alice Example", "email": "alice@example.com" },
    "message": {
      "message_id": "msg-1",
      "thread_id": "thread-1",
      "from": { "name": "Bob Sender", "email": "bob@vendor.com" },
      "to": [{ "name": "Alice Example", "email": "alice@example.com" }],
      "cc": [],
      "date": "2026-05-30T09:00:00Z",
      "subject": "Q2 invoice attached",
      "body": "Hi Alice, please review the attached invoice by Friday."
    }
  }
}
```

### Response

```json
{
  "schema_version": "2.3",
  "request_kind": "single",
  "result": {
    "category": "NEEDS_RESPONSE",
    "is_spam": false,
    "is_phishing": false,
    "summary": "Vendor invoice needs review by Friday.",
    "action_items": [
      { "description": "Review the Q2 invoice", "due_hint": "Friday" }
    ],
    "draft": {
      "to": [{ "name": "Bob Sender", "email": "bob@vendor.com" }],
      "subject": "Re: Q2 invoice attached"
    },
    "suggested_action": "reply"
  }
}
```

---

## Example — full thread

### Request

```json
{
  "schema_version": "2.3",
  "payload": {
    "kind": "thread",
    "principal": { "name": "Alice Example", "email": "alice@example.com" },
    "thread_id": "thread-42",
    "messages": [
      {
        "message_id": "msg-1",
        "thread_id": "thread-42",
        "from": { "name": "Bob", "email": "bob@vendor.com" },
        "to": [{ "name": "Alice", "email": "alice@example.com" }],
        "date": "2026-05-30T09:00:00Z",
        "subject": "Contract renewal",
        "body": "Can we hop on a call about the renewal?"
      },
      {
        "message_id": "msg-2",
        "thread_id": "thread-42",
        "from": { "name": "Alice", "email": "alice@example.com" },
        "to": [{ "name": "Bob", "email": "bob@vendor.com" }],
        "date": "2026-05-30T10:00:00Z",
        "subject": "Re: Contract renewal",
        "body": "Sure, does Thursday 2pm work?"
      }
    ]
  }
}
```

### Response

```json
{
  "schema_version": "2.3",
  "request_kind": "thread",
  "result": {
    "category": "NEEDS_RESPONSE",
    "is_spam": false,
    "is_phishing": false,
    "summary": "Bob wants a renewal call; Alice proposed Thursday 2pm.",
    "action_items": [{ "description": "Confirm Thursday 2pm call" }],
    "draft": null,
    "suggested_action": "reply"
  }
}
```

---

## Batch endpoint (`POST /v1/email/triage/batch`)

Added in agent `0.3.0` (#1887) **beside** the single-email endpoint — the single
`POST /v1/email/triage` and its schema above are unchanged. The batch endpoint
triages up to `MAX_BATCH_SIZE` (**100**) emails or threads in one request: an
`items` array in, a parallel `results` array out, order-preserved.

`BatchTriageRequest` — top-level envelope:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | Contract version. Defaults to `"2.3"`. |
| `items` | `(SingleEmailInput \| ThreadInput)[]` | 1–100 inputs, discriminated on `kind` — the same item shapes the single endpoint's `payload` accepts. Over 100 → `422`. |
| `context` | `TriageContext` \| null | Optional; applied to **all** items. |

`BatchTriageResponse` — top-level envelope:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | Echoes the contract version. |
| `results` | `BatchItemResult[]` | One entry per request item, order-preserved, 1:1 with `items`. |

`BatchItemResult` — exactly one of `result` / `error` is set:

| Field | Type | Notes |
|---|---|---|
| `index` | int | 0-based position in the request `items` array. |
| `result` | `EmailTriageResult` \| null | Set when the item succeeded (same shape as the single response's `result`). |
| `error` | `BatchItemError` \| null | Set when the item failed; `BatchItemError` carries a `message`. |

**Per-item isolation — read the results, not the status.** A failure on one item
sets that entry's `error` and the rest still run, so **HTTP 200 with every item
errored is a valid response**. Consumers MUST inspect each `results[].error`, never
just the HTTP status. A `502` means the local LLM was unreachable or the triage
model is unavailable there, detected before any item was processed — the whole
batch fails.

The MCP surface mirrors this with a `triage_email_batch` tool (the single
`triage_email` tool is unchanged).

### Example — batch request

```json
{
  "schema_version": "2.3",
  "items": [
    {
      "kind": "single",
      "principal": { "email": "alice@example.com" },
      "message": {
        "message_id": "msg-1",
        "from": { "email": "bob@vendor.com" },
        "subject": "Q2 invoice",
        "body": "Please review the attached invoice by Friday."
      }
    },
    {
      "kind": "single",
      "principal": { "email": "alice@example.com" },
      "message": {
        "message_id": "msg-2",
        "from": { "email": "promo@shop.example" },
        "subject": "50% off this weekend",
        "body": "Limited-time offer — shop now."
      }
    }
  ]
}
```

### Example — batch response (one item errored)

```json
{
  "schema_version": "2.3",
  "results": [
    {
      "index": 0,
      "result": {
        "category": "NEEDS_RESPONSE",
        "is_spam": false,
        "is_phishing": false,
        "summary": "Vendor invoice needs review by Friday.",
        "action_items": [{ "description": "Review the Q2 invoice", "due_hint": "Friday" }],
        "suggested_action": "reply"
      }
    },
    {
      "index": 1,
      "error": { "message": "local LLM triage failed for this item" }
    }
  ]
}
```

---

## Additional REST surfaces

Schema 2.1 (#1778–#1781) restored several agent capabilities on the REST
contract, all under the `/v1/email` prefix. They are **additive**: triage
consumers are unaffected. Every model still echoes `schema_version` and forbids
unknown fields. Only the triage and batch-triage shapes are mirrored on MCP; the
surfaces below are REST-only.

### Inbox search — `POST /v1/email/search` (#1781)

Read-only mailbox search by Gmail-style query and/or labels. `EmailSearchRequest`
carries `query`, `labels`, `max_results` (1–100, default 25), and a `page_token`
cursor. `EmailSearchResponse` returns `count`, a list of `EmailSearchResultItem`
(inbox-list metadata — raw header strings, `snippet`, `label_ids`, not parsed
`EmailAddress` objects), and `next_page_token`. Fetch the full body via the triage
path.

### Mailbox actions — archive & phishing-quarantine (#1779)

Mutating actions are gated by a single-use confirmation-token handshake, mirroring
draft→send (#1264):

1. `POST /v1/email/confirm` (`EmailActionConfirmRequest`) mints a
   `confirmation_token` bound to one `(action, message_id)` — `action` is
   `"archive"` or `"quarantine"`.
2. `POST /v1/email/archive` / `POST /v1/email/quarantine` echoes the token; a
   call without a valid matching token is rejected (`403`).

Both are reversible inside an undo window via **ungated** reversal endpoints
(`/v1/email/unarchive`, `/v1/email/unquarantine`) — reversal restores, never
destroys. Archive returns the `batch_id` undo handle **and** `post_archive_id`
(folder-based backends like Outlook mint a new id on the move). Quarantine applies
the `GAIA_PHISHING_QUARANTINE` label + archives, records `prior_labels` for undo,
and is **Gmail-only** (a request resolving to an Outlook mailbox is rejected
`400`); it also refuses a message not flagged `is_phishing`.

### Calendar — view / create / respond (#1780)

- `GET /v1/email/calendar/events` → `CalendarEventsResponse` (read-only view;
  `CalendarEvent` flattens provider start/end strings).
- `POST /v1/email/calendar/events/preview` → `CalendarEventPreviewResponse` mints
  a confirmation token bound to the event.
- `POST /v1/email/calendar/events` (`CalendarCreateEventRequest`) creates the
  event — confirmation-gated (`403` without a valid token) — returning
  `CalendarEventResponse`.
- `POST /v1/email/calendar/events/respond` (`CalendarRespondRequest`) RSVPs
  (`accepted` / `declined` / `tentative`); not token-gated (explicit user action).

`CalendarEventDateTime` requires **exactly one** of `date_time` (RFC 3339 timed)
or `date` (`YYYY-MM-DD` all-day). The Outlook backend defaults a missing
`time_zone` to `UTC` on timed events.

### Inbox pre-scan — `POST /v1/email/prescan` (#1778)

A read-only, lightweight triage over recent inbox messages, reshaped into the
scannable card the Agent UI renders. `EmailPreScanRequest` carries `max_messages`
(1–100, default 25). `EmailPreScanResponse.result` is an `EmailPreScanResult`
(`kind == "email_pre_scan"`) with capped `urgent` / `actionable` /
`suggested_archives` lists of `PreScanItem`, an `informational_count`,
`preferences_applied`, and pre-cap `totals`.

---

## Usage

Validate a payload at a boundary (REST endpoint, MCP tool handler). Both helpers
raise loudly on a contract violation — never return a partial object:

```python
from gaia_agent_email.contract import parse_request, parse_response

request = parse_request(raw_request_dict)   # -> EmailTriageRequest
if request.payload.kind == "thread":
    for message in request.payload.messages:
        ...

response = parse_response(raw_response_dict)  # -> EmailTriageResponse
```

---

## Stability contract

- **Versioned additively.** Additive, backward-compatible changes (new optional
  fields, new endpoints) keep older consumers working; `SCHEMA_VERSION` bumps only
  on a breaking change (renamed/removed field, new required field, taxonomy
  change) so consumers detect it. See the [version history](#version-history).
- **Categories never drift.** The five-bucket taxonomy is mirrored from the
  agent's `triage_heuristics.ALL_CATEGORIES`; a unit test asserts byte-for-byte
  equality, so a taxonomy change in either place fails CI.
- **Unknown fields are errors**, not warnings — there is no silent forward-compat
  drift in either direction.

---

## Context-window envelope

The email agent is designed, measured, and released against a pinned
context-window envelope
([#1892](https://github.com/amd/gaia/issues/1892), constants in
[`gaia_agent_email/context_budget.py`](gaia_agent_email/context_budget.py)):

| Bound | Tokens | Meaning |
|---|---|---|
| **Target** | **16,384** | The window every published accuracy/throughput number is measured at. Everyday triage/draft prompts — system prompt, tool schema, and a full thread — fit here on the KV-cache budget of the consumer NPU/GPU hardware GAIA targets. |
| **Acceptable max** | **32,768** | The ceiling for a deliberately larger run (e.g. a long-thread stress sweep). Above it, KV-cache memory pressure makes the measurement unrepresentative of a real device. |

64K — the model's registry floor that the eval path historically ran at — is
**not** part of the envelope: it is unrealistic for the machines this agent
ships to, and numbers measured there do not transfer.

**What a consumer may assume:**

- Published scorecards and baselines are designed to state the window they
  were measured under (`recipe.environment.ctx_size` on the scorecard;
  `ctx_size` in `baseline_accuracy.json` and the benchmark's `quality.json` /
  `scorecard.json`). None of the email agent's committed artifacts carry
  that stamp yet — it lands when the baseline is next re-recorded (the
  consolidated eval pass, [#1319](https://github.com/amd/gaia/issues/1319) /
  [#1892](https://github.com/amd/gaia/issues/1892)); the repo's `gaia eval
  agent` baseline `meta.json` files already record their historic 64K
  window. Until then, treat every existing email-agent number as measured
  at the unpinned 64K window and do not compare it against a future pinned
  run.
- Payloads that fit the 16K target are the supported case. Prompt
  construction bounds body content with documented character limits (marked
  `...[truncated]`, never silent), and a genuine context overflow on the
  LLM call **raises** per the agent's fail-loud contract — a result is
  never fabricated from an over-budget prompt.
- Future budget work is meant to derive from the same constants: the
  long-thread transcript budget
  ([#1889](https://github.com/amd/gaia/issues/1889)) and the per-email body
  limit ([#1318](https://github.com/amd/gaia/issues/1318)) are both designed
  to import `context_budget.py` rather than invent their own numbers — as of
  this writing neither one consumes it yet.

**How to verify what a live triage actually used:** the triage response's
`usage` block reports `prompt_tokens` / `completion_tokens` /
`total_tokens` for the LLM calls behind the result — compare
`prompt_tokens` against the envelope to see how much of the window a
payload consumed. The agent-loop bulk triage (the `triage_inbox` tool
behind natural-language requests like "triage my inbox", including
`POST /v1/email/query`) reports the same accounting at the result level
([#1891](https://github.com/amd/gaia/issues/1891)): the tool's result
data carries a `usage` object (same four fields as `TriageUsage`,
aggregated across every LLM classify call in the run, all mailboxes)
plus `llm_classified_count` — the number of classify calls whose usage
was measurable (on the shipped Lemonade path this equals the number of
emails classified by the LLM rather than the heuristic fast path; a
provider exposing no per-call usage/stats undercounts). Both keys are **absent**
(never zeroed) on a heuristic-only run where no LLM call was made; a
present-but-zero `usage` means classify calls happened but their
per-call measurements were unavailable. `GET /v1/email/init` additionally reports the *currently
loaded* `ctx_size` on `model` when the triage model is loaded and the
server exposes it — null otherwise (no config echo, no guessing).
`ctx_size` reflects `/health`'s loaded state specifically, so it can be set
even when the model-catalog probe fails and `present` reports `false` —
the two fields answer different questions from different probes.

> **Note:** the **interactive `gaia email` CLI path currently loads the model
> at 32K** (the `agent_context_sizes` registry in `src/gaia/cli.py`) — the
> envelope's acceptable max, not the 16K target. Pinning the interactive path
> to the target is deliberately out of #1892's scope; the envelope above
> governs the **eval/benchmark/release** path today.

**Shared-server constraint:** Lemonade Server is single-tenant per model
slot. An agent instance with an exact ctx pin (`EmailAgentConfig.ctx_size` /
`LemonadeClient(ctx_size_override=...)`) and any other client sharing the
same model will fight over the loaded ctx — visible as the reported ctx
flapping between values across successive `GET /v1/email/init` calls. Do not
enable `ctx_size` against a Lemonade instance shared with other traffic.

---

## Default model selection

When `EmailAgentConfig.model_id` is unset, the agent no longer defaults
unconditionally to the GGUF model — it resolves against the Lemonade Server
it will actually talk to
([#1439](https://github.com/amd/gaia/issues/1439),
[`gaia_agent_email/model_select.py`](gaia_agent_email/model_select.py)):

1. Probe that server's `/system-info` for `devices.amd_npu.available`
   (a short-timeout raw probe — never the SDK's `get_system_info()`, which
   has no timeout knob and would hang the whole resolution on an
   unreachable server).
2. If an AMD NPU is available **and** the NPU-native triage model
   (`gemma4-it-e2b-FLM`) is already downloaded on that server, resolve to
   it.
3. Otherwise — no NPU, NPU present but the model not downloaded, or either
   probe failing/timing out — resolve to the GGUF default
   (`Gemma-4-E4B-it-GGUF`).

The resolved id is always exactly one of those two literals; nothing from
the server response is ever interpolated into it. A successful resolution
is cached per Lemonade base URL for the life of the process (so a hot REST
path doesn't re-probe on every request); a failed/timeout probe is never
cached, so a server that comes up later is picked up on the next call
rather than being stuck on a cold-start failure.

An explicit `model_id` (`EmailAgentConfig.model_id`, or a caller-supplied
value) always wins — auto-select only fills in when no preference was
given. `GET /v1/email/init`'s `model.id` and the model actually used by
`POST /v1/email/triage` are guaranteed to be the resolved model for the
same request's `base_url` (both read through the same resolver).

**Auto-selecting the NPU model also switches the agent's memory embedder**
(`gaia.agents.registry.get_embedding_model_for_device`) to the FLM-native
embedder when the resolver picks `gemma4-it-e2b-FLM`, so the chat model and
the embedder stay co-resident on the NPU backend — mixing an NPU chat model
with the default GGUF/Vulkan embedder would otherwise evict and reload the
chat model on every turn on shared-memory hardware. Any other resolved
model keeps the unchanged GGUF embedder default.

**Merge-gate note:** this auto-select is not yet backed by an FLM-variant
triage-accuracy baseline (`baseline_accuracy_e2b.json` was recorded on the
GGUF build) — the measurement lands with the consolidated eval pass
([#1319](https://github.com/amd/gaia/issues/1319)).
