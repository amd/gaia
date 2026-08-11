# Email Triage Contract Schema

> **Source code:** [`gaia_agent_email/contract.py`](gaia_agent_email/contract.py)
>
> **Component:** Email request/response contract (issue #1262)
> **Module:** `gaia_agent_email.contract`
> **Validation:** pydantic v2
> **Schema version:** `2.13`

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
- **Versioned.** `SCHEMA_VERSION` (`"2.13"`) is pinned in the module and echoed in
  every request and response so a consumer can detect a breaking change.

### Version history

`SCHEMA_VERSION` bumps the MINOR on every change, additive or breaking, and
the MAJOR has never moved. Clients gate on the MAJOR only (`checkVersion` in
the npm package accepts any higher MINOR), so the one breaking change below —
`2.3` — was **not** signalled to pinned consumers. Treat a MINOR bump as
"read the row before upgrading", not as "safe by construction".

| Version | Change |
|---|---|
| `1.0` | First frozen revision — single-email + thread triage. |
| `2.0` | Five-bucket taxonomy (#1615); batch triage (#1887). |
| `2.1` | Additive REST surfaces: inbox search (#1781), archive + phishing-quarantine and their undo (#1779), calendar view/create/respond (#1780), inbox pre-scan (#1778). |
| `2.2` | Additive attachment handling (#1542): `EmailMessage` / `EmailTriageResult` / `DraftReply` gain an `attachments` metadata list; draft/send accept `OutgoingAttachment` payloads. |
| `2.3` | **Breaking:** `EmailTriageResult.draft` is now a `DraftScaffold` (recipient + subject only) instead of a `DraftReply` — triage never composed a body, so the always-empty `draft.body` is dropped. `DraftReply` (with `body`) is unchanged and remains the `POST /v1/email/draft` + MCP `draft_reply` response. |
| `2.4` | Additive (#2016): new streaming agent-loop surface — `POST /v1/email/query` (NL request in, the seven canonical SSE event types out) and `POST /v1/email/query/{run_id}/cancel`. No existing shape changed, so `2.3` consumers keep working. |
| `2.5` | Additive (#2154): OAuth forward-OUT intake — `POST /v1/connections/{provider}` (the daemon forwards a short-lived access token, never a refresh token), `GET /v1/connections` (metadata only), `DELETE /v1/connections/{provider}`. No existing shape changed, so `2.4` consumers keep working. |
| `2.6` | Additive (#2469): the agent can ask the user a question MID-RUN and carry on from the answer. New non-terminal SSE event `needs_input` {run_id, request_id, question, options[{value,label,description}], allow_free_text, sensitive?, respond_url, timeout_seconds?} and `POST /v1/email/query/{run_id}/respond` to answer it (404 unknown run, 409 stale request_id). `needs_confirmation` and its terminal, deny-by-default approval behaviour are unchanged. No existing shape changed, so `2.5` consumers keep working. |
| `2.7` | Additive (#2583): `PreScanItem` gains `is_meeting_request` (bool, default `false`) — the deterministic meeting-request heuristic now runs during the inbox scan, not only on a single message a caller points at directly. No existing shape changed, so `2.6` consumers keep working. |
| `2.8` | Additive (#2582): new read-only attention-view surface — `GET /v1/email/attention` merges inbound waiting-on-you items (#2581), meeting proposals found during the scan (#2583), unreviewed messages (#2584), and open action items (#2110/#2525) into one "what needs you" read-model, computed on open and cached. No existing shape changed, so `2.7` consumers keep working. |
| `2.9` | Additive (#2638/#2643): `EmailPreScanResult` gains `total_inbox` (exact whole-INBOX message count, nullable). Pre-scan now scans read + unread INBOX mail, not unread-only (#2638) — `total_unread` alone stopped being an honest scan-coverage denominator once read mail counts too, so `total_inbox` is the new one. No existing field changed, so `2.8` consumers keep working. |
| `2.10` | Additive (#2716): `AttentionCoverage` gains `message_errors` (list of `{message_id, error}`, nullable) and `degraded` can now be `true` for a message-level gap, not only a mailbox-level one. A Gmail rate-limit that survives retry now drops the one affected message instead of failing the whole attention scan — every other message in the same mailbox is still present in `items`. No existing field changed, so `2.9` consumers keep working. |
| `2.11` | Additive (#2743): `EmailPreScanResult` gains `needs_you` (list of `NeedsYouItem`), `needs_you_total` (int), and `bulk` (`BulkSummary`, nullable) — a single worklist view built on top of the already-classified urgent/actionable/needs_review buckets, never a second independent classification pass. `NeedsYouItem.kind` reuses the published `AttentionItemKind` enum. No existing field changed, so `2.10` consumers keep working. |
| `2.12` | Additive (#2829): `POST /v1/email/query` gains an optional `session_id`. When the host sends it, the run resolves the SAME agent every other turn on that id used, instead of a throwaway per-turn agent — so a reference to something an earlier turn surfaced (e.g. "reply to number 1") can resolve. Omitted -> byte-for-byte the old per-turn behaviour. No existing field changed, so `2.11` consumers keep working. |
| `2.13` | Additive (#2629): a third mailbox provider value, `microsoft_work` (work Microsoft 365 / Entra, distinct from the personal `microsoft` Outlook.com connector), is now valid wherever a provider string is accepted or returned. No existing field or value changed, so `2.12` consumers keep working — they simply never see the new value until a work mailbox is connected. |

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
| `schema_version` | string | Contract version. Defaults to `"2.13"`. |
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

#### Long-thread handling ([#1889](https://github.com/amd/gaia/issues/1889))

`ThreadInput.messages` has no declared size limit, and the request shape is
unchanged — but what reaches the local model is bounded by the
[context-window envelope](#context-window-envelope). The service estimates the
combined thread body against the thread token budget derived from
`context_budget.py`:

- **Fits** — the thread is analyzed exactly as before (newest-first, every
  message verbatim). No behavior change.
- **Over budget** — the LATEST message is kept verbatim and every older
  message is condensed into one digest via a **single extra LLM call** before
  triage (never a multi-pass loop). Expect one additional model call's worth
  of latency on such requests; the fold call's tokens are included in the
  response's `usage` block.
- **Fold failure** — a failed condense call is a loud error (HTTP 502 /
  `BatchItemResult.error` on the batch endpoint), never a silent fallback to
  the over-budget raw prompt.

Independently of the token gate, threads beyond a **500-message ceiling**
are bounded first: only the most recent 500 messages are analyzed, and the
dropped remainder always surfaces as an explicit
`[omitted N older messages]` marker in what reaches the model — bounded and
visible, never a silent clip of recent context.

The agent-loop `summarize_thread` tool applies the same token gate and
ceiling: the gate replaced the tool's previous fixed 24,000-character
transcript cap, so mid-size threads that used to be proportionally clipped
now go through unclipped whenever they fit the token budget, and only
genuinely over-budget threads get the latest-verbatim + condensed-older
treatment. When the condense call ran, the tool's result `data` carries a
`usage` block with that call's tokens (same fields as `TriageUsage`).

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
  "schema_version": "2.13",
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
  "schema_version": "2.13",
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
  "schema_version": "2.13",
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
  "schema_version": "2.13",
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
| `schema_version` | string | Contract version. Defaults to `"2.13"`. |
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
  "schema_version": "2.13",
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
  "schema_version": "2.13",
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
  `CalendarEvent` flattens provider start/end strings). `time_min`/`time_max`
  are optional; omitting both defaults to a forward window (now → +30 days) so
  recurring series don't surface their oldest instances (#2162).
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

A read-only, lightweight triage over recent INBOX messages — read AND unread
alike (#2638) — reshaped into the scannable card the Agent UI renders.
`EmailPreScanRequest` carries `max_messages` (1–100, default 50).
`EmailPreScanResponse.result` is an `EmailPreScanResult` (`kind ==
"email_pre_scan"`) with capped `urgent` / `actionable` / `suggested_archives` /
`needs_review` lists of `PreScanItem`, an `informational_count`,
`preferences_applied`, and pre-cap `totals`.

`needs_review` (#2584) holds messages the heuristic classifier was NOT
confident about — a placeholder category guess, not a real classification.
It only overrides routing into the two LOW-SIGNAL buckets: `informational`
and `suggested_archives`, which assert "you can ignore this" from what is
often a placeholder guess. It never pulls a message out of `urgent` or
`actionable` — an unconfident guess toward a high-signal category already
errs toward surfacing, which is the direction to err in. Capped like the
other three buckets and ordered newest-first (human senders before
automated ones on a timestamp tie); `totals.needs_review` reports the full
uncapped count.

Every `PreScanItem` also carries `is_meeting_request` (#2583) — `true` when the
deterministic heuristic (`detect_meeting_request_heuristic`) confidently detected
a meeting/scheduling request in the message's subject/snippet. It is read-only
(detection makes no calendar changes) and never escalates to the LLM classifier
during pre-scan — it is gated on `is_meeting_request AND confidence == "high"`,
never on confidence alone, since the heuristic's no-signal branch also reports
`confidence == "high"` for a confident negative.

Coverage-honesty fields (#2584, extended #2638), all on `EmailPreScanResult`
directly: `scanned` (how many messages this call actually classified, across
every bucket), `total_inbox` (the mailbox's EXACT total INBOX message count —
read + unread — the scan-coverage denominator since #2638 widened the scan
past unread-only), `total_unread` (the mailbox's EXACT unread-message count —
a secondary figure, no longer the coverage denominator), both sourced from
Gmail's `labels().get(id="INBOX")` (`messagesTotal` / `messagesUnread`) in
ONE call, not `list_messages`'s `resultSizeEstimate`, which Google documents
as approximate and measured 2.6x off on a real mailbox; both `null` for
Outlook, which has no equivalent honest source — `null` propagates through a
multi-mailbox merge if ANY connected mailbox's count is unknown), `degraded`
(true when at least one connected mailbox could not be scanned), and
`mailbox_errors` (the list of `{mailbox, error}` failures behind `degraded`,
surfaced to the caller rather than only logged). A failed mailbox's share
of `max_messages` is reclaimed by the mailboxes
still to be tried, so a single dead connection never halves a healthy
mailbox's scan budget.

### Attention view — `GET /v1/email/attention` (#2582)

The read-only "what needs you" view, rendered without a user prompt when the
email agent opens. `EmailAttentionResponse.result` is an
`EmailAttentionResult` (`kind == "email_attention"`) with an `items` list of
`AttentionItem`, a `coverage` block, `generated_at`, `cache_age_seconds`, and
`stale`.

Each `AttentionItem.kind` names the signal it came from: `meeting_request`,
`waiting_on_you`, `needs_review`, or `action_item`. Built by calling the
underlying tools directly — `triage_inbox_impl` and
`detect_waiting_on_you_impl` — rather than the `/prescan` envelope, because
`EmailPreScanResult.informational_count` is a bare count with no per-message
rows: a meeting proposal in a confidently-classified informational message
(e.g. one carrying Gmail's `CATEGORY_UPDATES` label) would otherwise be
silently invisible.

**`items == []` is NOT itself a "nothing needs you" claim.** It only means
nothing surfaced from what `coverage` says was actually scanned — always read
`coverage` before asserting the mailbox is clear, and qualify the claim when
`coverage.scan_truncated` or `coverage.degraded` is set (e.g. "of the 200
most recent" / "one mailbox couldn't be scanned"). Rendering an empty
`items` list as an unqualified whole-mailbox claim is the exact defect #2584
fixed one layer down for the pre-scan envelope.

`coverage` mirrors the pre-scan coverage-honesty fields: `scanned`,
`total_unread` (null when a connected backend can't report it honestly),
`scan_truncated` (the scan hit its message ceiling), `degraded`,
`mailbox_errors`, and `message_errors` (#2716) — the list of
`{message_id, error}` messages that could not be fetched (e.g. a Gmail
rate-limit that survived retry). A message-level failure is NOT a mailbox
failure: every other message in that mailbox's scan is still present in
`items`, and `degraded` is `true` for either kind of gap.

**Computed on open, then cached — no scheduler dependency.** There is no
background job populating this (the daemon clock, #2379, deliberately
carries no jobs — that's #2585, unbuilt). A call within the freshness
window (`ATTENTION_CACHE_TTL_SECONDS`, 120s) returns the cached result
verbatim with its real `cache_age_seconds`; past that window a fresh scan is
attempted. A failed refresh (every connected mailbox erroring) falls back to
the last known-good result marked `stale=true` rather than hard-failing a
view the user has already seen once — with no prior cache at all, the
failure propagates as a normal connector error (403/502/503).

Read-only throughout: this endpoint never archives, marks, replies, or
sends — proven by an id-set diff in the issue's real-world test evidence.

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
  fields, new endpoints) keep older consumers working; `SCHEMA_VERSION` bumps the
  MINOR on **every** change, additive or breaking (see [Version history](#version-history)
  above), so a consumer that gates on the MINOR always sees a bump, not only
  when something breaks.
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
  never fabricated from an over-budget prompt. Over-budget *threads* no
  longer overflow at all: they are condensed to fit first (see
  [Long-thread handling](#long-thread-handling-1889)).
- Budget work derives from the same constants: the long-thread budget
  ([#1889](https://github.com/amd/gaia/issues/1889)) is the first consumer —
  `thread_budget_tokens()` and `estimate_tokens()` in `context_budget.py`
  gate both thread-triage surfaces. The per-email body limit
  ([#1318](https://github.com/amd/gaia/issues/1318)) is designed to derive
  from the same file and does not consume it yet.

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

> **Note:** no *interactive* front-door loads the model. Since #2191 `gaia email`
> relays `POST /v1/email/query` to this sidecar, so the sidecar's configuration
> decides `ctx_size`; the `agent_context_sizes` registry this note used to cite
> no longer exists. `gaia eval benchmark` is the exception — it constructs
> `EmailAgentConfig(ctx_size=...)` in-process, which is the path the envelope
> above governs.

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
