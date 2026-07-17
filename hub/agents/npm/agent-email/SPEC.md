# @amd-gaia/agent-email — Technical reference

Detailed reference for `@amd-gaia/agent-email`. For a quick start, see
[`README.md`](./README.md); for an AI-assisted integration walkthrough, see
[`SKILL.md`](./SKILL.md). The contract version is `SCHEMA_VERSION` **2.4**.

## Architecture

Three tiers, all on the user's machine:

- **Your app** (a Node process) depends on this package, fetches the sidecar
  binary, and spawns it via the `.` entry. It does **not** attach to an
  already-running GAIA instance — the package **launches and owns its own
  sidecar** and tears it down on `shutdown()`.
- **The sidecar** is a self-contained, PyInstaller-frozen `email-agent` binary
  serving the email REST endpoints. **No Python is required on the host.**
- **Lemonade Server** is the one external runtime dependency: the sidecar calls a
  **local** Lemonade for the actual LLM inference. With none reachable,
  `POST /v1/email/triage` returns HTTP 502.

Once a sidecar is running, any Node process can drive it over local HTTP. The
sidecar serves **same-origin only and sends no CORS headers**, so a browser or
Electron renderer reaches it through the app's main process, not a direct
cross-origin fetch — see [Browser / Electron renderer](#browser--electron-renderer-client).

## Concurrency & deployment

Run **one sidecar per host**, spawned once at process start — not one per request.
It accepts concurrent HTTP requests, but inference runs on a **single local
Lemonade model slot**, so parallel `triage` calls serialize behind one another;
cap inflight calls on your side rather than fanning out. The package does not
supervise or restart a crashed sidecar — watch `sidecar.child` `exit` and
re-`startSidecar` if you need resilience. It **does** auto-reap the sidecar when
your process exits, crashes, or is interrupted (default `autoCleanup`); call
`shutdown` for a graceful, awaited stop, or pass `autoCleanup: false` to manage
signals yourself.

## Authentication

The sidecar binds `127.0.0.1` and can send mail as the user, so it authenticates
its **caller** (#1706) — distinct from the draft→send `confirmation_token`, which
binds a send to one exact message but does not identify the caller.

- **Per-session bearer token.** `spawnSidecar` / `startSidecar` mint a
  cryptographically-random token, pass it to the sidecar over the private
  `GAIA_EMAIL_SIDECAR_TOKEN` env channel, and bind it to `sidecar.client`. Every
  `/v1/email/*` request must carry `Authorization: Bearer <token>` → otherwise
  **401**. Construct-your-own clients pass `authToken` (from `sidecar.authToken`);
  `generateSessionToken()` is exported for advanced flows. Exempt: `/health`,
  `/version`, `/v1/email/health`, `/v1/email/version`, `/v1/email/spec`,
  `/v1/email/playground`.
- **Host allowlist** — non-loopback `Host` → **400** (DNS-rebinding).
- **Origin rejection** — non-loopback browser `Origin` → **403** (drive-by page).
  Non-browser clients send no `Origin` and are unaffected. No CORS is ever sent.

Running the sidecar by hand without `GAIA_EMAIL_SIDECAR_TOKEN` disables the token
check (local development only, logged loudly); the Host/Origin controls still
apply. The shipped product always spawns with a token.

## REST API

The code-derived, CI-guarded inventory of every capability surface (internal
agent-loop tools, REST, MCP, eval coverage) is
[`CAPABILITY_MATRIX.md`](https://github.com/amd/gaia/blob/main/hub/agents/python/email/CAPABILITY_MATRIX.md) —
the canonical cross-surface reference.

Every `/v1/email/*` request also requires the per-session bearer token (see
[Authentication](#authentication)); the "Auth" column below covers the additional
per-endpoint connector/token requirements. `EmailClient` is a typed wrapper over
the sidecar's HTTP surface. Methods:
`triage`, `triageBatch`, `search`, `prescan`, `draft`, `send`, `confirmAction`,
`archive`, `unarchive`, `quarantine`, `unquarantine`, `listCalendarEvents`,
`previewCalendarEvent`, `createCalendarEvent`, `respondToCalendarEvent`, `health`,
`version`, `emailHealth`, `emailVersion`, `spec`, `openapi`. `health`/`version` hit the **root** routes (the standalone
sidecar); `emailHealth`/`emailVersion` hit the **`/v1/email`-scoped** mirrors (for
when the router is mounted on a product app). Every non-2xx response throws
`HttpError` (carrying `status`, `url`, `bodyText`) — never a silent empty/null
result.

| Endpoint | Client method | Auth | What it needs |
|----------|---------------|------|---------------|
| `POST /v1/email/triage` | `triage()` | **Standalone** | Local Lemonade LLM only. Categorizes / summarizes / extracts action items + spam/phishing **signals** on the message you send in. *No mailbox is read.* Extracted action items also persist to the sidecar's local task list (see "Action-item task persistence" below); the response shape is unchanged. |
| `POST /v1/email/triage/batch` | `triageBatch()` | **Standalone** | Same as `triage` for an `items` array (1–100). Returns a parallel `results` array, order-preserved; per-item failures isolate (HTTP 200 can carry errored items — inspect `results[].error`). A `502` fails the whole batch (Lemonade unreachable). |
| `POST /v1/email/search` | `search()` | **Connector** | Read-only inbox search. A connected Google/Microsoft mailbox (`503` if none, `400` if 2+); **no** confirmation token. Lists messages matching `query`/`labels` and returns metadata only (no body). |
| `POST /v1/email/prescan` | `prescan()` | **Connector** | Reads recent inbox messages from the connected Google/Microsoft mailbox and returns the read-only triage-card envelope (`kind: "email_pre_scan"`). `503` if no mailbox is connected, `400` if 2+ are. Heuristic-only — no Lemonade call. |
| `GET /v1/email/briefing` | — (plain `fetch`; no wrapper yet) | **Standalone** | The latest **scheduled** daily briefing (#1608) — the same `email_pre_scan` envelope as `prescan`, generated by the sidecar's daily timer without a prompt, plus a `generated_at` stamp. **Off by default**: start the sidecar with `GAIA_EMAIL_BRIEFING_ENABLED=true` (fire time `GAIA_EMAIL_BRIEFING_TIME`, 24h local `HH:MM`, default `08:00`; scan size `GAIA_EMAIL_BRIEFING_MAX_MESSAGES`, default 25) — e.g. via `startSidecar({ env: {...} })`. `404` until a scheduled run has happened. |
| `POST /v1/email/draft` | `draft()` | **Standalone** | Nothing external — wraps your `(to, subject, body, attachments)` and returns a single-use confirmation token. |
| `POST /v1/email/send` | `send()` | **Connector** | A valid `draft` confirmation token **and** a connected Google/Microsoft mailbox. The token gate fires first: no/invalid token → `403`; then `503` if no mailbox is connected, `400` if 2+ are. |
| `POST /v1/email/confirm` | `confirmAction()` | **Standalone** | Nothing external — mints a single-use token for `"archive"`/`"quarantine"`, bound to that exact `(action, message_id)`. |
| `POST /v1/email/archive` | `archive()` | **Connector** | A valid `confirm` token (`action="archive"`) **and** a connected mailbox. Gate fires first (no/invalid token → `403`); returns a `batch_id` undo handle + `post_archive_id`. |
| `POST /v1/email/unarchive` | `unarchive()` | **Connector** | A connected mailbox + the `batch_id`. **Ungated** (it restores). Window expired / unknown handle → `409`. |
| `POST /v1/email/quarantine` | `quarantine()` | **Connector (Gmail)** | A valid `confirm` token (`action="quarantine"`) **and** a connected **Gmail** mailbox. Applies `GAIA_PHISHING_QUARANTINE` + archives; refuses `is_phishing: false` → `400`; refuses an Outlook mailbox → `400` (label-undo can't reverse a folder move, #1738). |
| `POST /v1/email/unquarantine` | `unquarantine()` | **Connector** | A connected mailbox + the `action_id`. **Ungated** (it restores prior labels). Window expired / unknown → `409`. |
| `GET /v1/email/calendar/events` | `listCalendarEvents()` | **Connector** | A connected mailbox whose **calendar scope** was granted. Read-only view of the primary calendar; `403` (reconnect CTA) if the scope is missing. Optional `time_min`/`time_max` — omitting both defaults to a forward window (now → +30 days); `provider` only when 2+ accounts. |
| `POST /v1/email/calendar/events/preview` | `previewCalendarEvent()` | **Standalone** | Nothing external — mints a single-use confirmation token bound to the event (calendar analogue of `draft`). |
| `POST /v1/email/calendar/events` | `createCalendarEvent()` | **Connector** | A valid `preview` token **and** a connected calendar. Token gate fires first: no/invalid token → `403`; then the calendar-scope / account checks. |
| `POST /v1/email/calendar/events/respond` | `respondToCalendarEvent()` | **Connector** | A connected calendar. RSVPs `accepted`/`declined`/`tentative` to an existing invite. |
| `POST /v1/email/query` | `query()` | **Connector** | Canonical agent-loop query (schema 2.4, #2016). NL request in, seven canonical SSE event types out (`status`/`token`/`tool_call`/`tool_result`/`needs_confirmation`/`final`/`error`), terminated by one `final`/`error`. `query()` returns an async iterator of typed `QueryEvent`s. Host mints `run_id`; context is pushed. See "Canonical agent-loop query" below. |
| `POST /v1/email/query/{run_id}/cancel` | `cancelQuery()` | **Standalone** | Cancel an in-flight `/query` run — stops tool execution between steps. `404` if no run with that id is in flight. |
| `GET /v1/email/init` | `init()` | **Standalone** | **Readiness preflight** (#1795): probes the whole triage stack — Lemonade reachable **and** version-compatible **and** the triage model downloaded. Returns `200` when ready, `503` when not, with an actionable `hint` either way (same `InitResponse` envelope). Read-only — no model pull. Unlike `/health` (liveness only), this verifies "ready to triage," not just "process up." |
| `POST /v1/email/init` | — (streaming; no wrapper yet) | **Standalone** | **Provisioning** (#1795): tells the *running* local Lemonade to download the configured triage model, streaming `text/plain` progress line-by-line. Lemonade unreachable → real `503` (pulls nothing); once a pull starts the `200` is committed, so the trailing `✓`/`✗` line carries the true outcome. Not in the OpenAPI JSON — a streaming operational verb (like `GET /spec`), so `include_in_schema=False`. |
| `GET /health` | `health()` | **Standalone** | Liveness only — does **not** check Lemonade/model. |
| `GET /version` | `version()` | **Standalone** | Version negotiation. |
| `GET /v1/email/health` | `emailHealth()` | **Standalone** | Router-scoped liveness (mounted-on-app case). |
| `GET /v1/email/version` | `emailVersion()` | **Standalone** | Router-scoped version. |
| `GET /v1/email/spec` | `spec()` | **Standalone** | Human-readable HTML endpoint page. |
| `GET /openapi.json` | `openapi()` | **Standalone** | Machine-readable OpenAPI document. |

`GET /docs` (Swagger UI) and `GET /redoc` are also served but are browser UIs, not
wrapped by the client. **The standalone surface is `triage`, `draft`, `confirmAction`,
and `previewCalendarEvent`** (plus the probes) — integrate and verify those flows with
zero connector setup. The read-only `search` and `prescan` read the live inbox (a
connected mailbox, but no token); the mutating calls (`send`, `archive`, `quarantine`,
`createCalendarEvent`) and the reversals/calendar views need a connected mailbox whose
relevant scope was granted.

### Canonical agent-loop query (`POST /v1/email/query`, schema 2.4)

The v2 keystone (#2016): a natural-language request in, the agent reasons and chains its
tools into a multi-step workflow, and the **seven canonical Server-Sent Event types** out
(the frozen `/query` wire contract). Every v2 front-door (the Agent UI relay, the
`gaia email` CLI, `gaia api`) relays to **this one loop**. Request body:

```jsonc
{
  "query": "Triage my inbox and draft replies to anything urgent.",
  "run_id": "0f9c2b6e-2c4a-4b1e-9d6a-1e2f3a4b5c6d", // host-minted UUIDv4
  "context": [ { "role": "user", "content": "earlier turn" } ], // pushed slice
  "model": "Gemma-4-E4B-it-GGUF",   // optional
  "provider": "lemonade",           // optional; only 'lemonade' (local-only agent)
  "max_steps": 20                    // optional
}
```

The **host mints `run_id`**, so the run is cancellable from the instant the request is
sent (`POST /v1/email/query/{run_id}/cancel`, which stops tool execution between steps).
Context is **pushed** in the body — the sidecar stays stateless. The response is
`text/event-stream`; each `data:` line is one canonical event discriminated on `type`:

| `type` | Payload | Meaning |
|---|---|---|
| `status` | `{ message }` | progress narration (also folds `step`/`thinking`/`plan`) |
| `token` | `{ delta }` | an incremental chunk of assistant text |
| `tool_call` | `{ tool, args }` | the agent is invoking a tool |
| `tool_result` | `{ tool, render?, data }` | a tool returned; `render` names a typed card |
| `needs_confirmation` | `{ run_id, action, summary }` | a gated step is awaiting approval |
| `final` | `{ answer, usage? }` | terminal — the assistant's answer |
| `error` | `{ detail, status }` | terminal — an actionable failure, surfaced verbatim |

The stream ends with **exactly one `final` or `error`**.

**Confirmation (stateless stub, epic decision D1):** a step that needs approval (a
destructive/external tool such as `send_now`) emits `needs_confirmation` and then the run
ends with a `final` refusal pointing at the deterministic fixed-function route — mint a
token via `draft()`/`POST /v1/email/draft`, then `send()`/`POST /v1/email/send`.
Server-side resume is not wired yet, so `confirm_url` is omitted.

**Typed client:** `query()` wraps the stream as an async iterator of typed `QueryEvent`s
(discriminated on `type`); `cancelQuery(runId)` wraps the cancel route.

```ts
const runId = crypto.randomUUID(); // host-minted (spec §2.3); also the cancel handle
for await (const ev of sidecar.client.query({
  query: "Triage my inbox and draft replies to anything urgent.",
  run_id: runId,
  context: [], // pushed transcript slice; [] for a fresh conversation
})) {
  switch (ev.type) {
    case "status":       spinner.text = ev.message; break;
    case "token":        answer += ev.delta; break;
    case "tool_call":    console.log(`→ ${ev.tool}`, ev.args); break;
    case "tool_result":  renderCard(ev.render, ev.data); break;
    case "needs_confirmation": /* run then ends with a final refusal (D1) */ break;
    case "final":        console.log(ev.answer); break;        // terminal
    case "error":        console.error(ev.detail); break;      // terminal, verbatim
    default:             console.warn("unsupported event", ev); // additive future type
  }
}
// Mid-run, from anywhere that knows runId:
// await sidecar.client.cancelQuery(runId);
```

Semantics: exactly one terminal `final`/`error` ends the iterator — a terminal `error`
event is **yielded** (its `detail` is the actionable message), while transport/contract
failures **throw** (`HttpError` on a non-2xx; `QueryStreamError` on a non-SSE response,
a malformed event, or a stream that closes with no terminal event). An event `type`
outside the frozen seven is yielded as `{ type: "unknown", eventType, raw }` — surfaced,
never silently dropped (contract §7). The client's `timeoutMs` bounds time-to-first-response
only; a healthy run streams as long as the agent works (pass an `AbortSignal` via
`query(req, { signal })` to abort the transport — and also call `cancelQuery` so the
sidecar stops the loop, not just the socket).

### Stateful agent surface (`/v1/email/agent/*`, 0.4.0)

Everything above is **stateless** — each call analyzes the payload you send, with no
memory and no agent loop. The sidecar also hosts a **session-scoped, conversational
agent** so a host can drive the full `EmailTriageAgent` (memory, personalization, and
every agent tool) over HTTP instead of importing it in-process. This is the surface the
Agent UI uses to back its email experience with the packaged sidecar. It is **not wrapped
by the typed npm client yet** — call it directly (e.g. `fetch`) or via the Agent UI.
Distinct from `/v1/email/query` above: `/agent/*` is session-scoped (server-held memory +
history), while `/query` is stateless with a host-minted `run_id` and pushed `context` and
emits the canonical seven-event vocabulary.

| Endpoint | Notes |
|---|---|
| `POST /v1/email/agent/session` | Create/reset a session (`{ session_id, reset? }`) → `{ created, memory }`. Builds the agent (surfaces failures early). |
| `POST /v1/email/agent/query` | Run one turn; **SSE** stream (`text/event-stream`) of the loop — `thinking`/`step`/tool/`permission_request`/`error`/terminal `run_complete`. Body `{ session_id, message, memory_enabled? }`. Every agent tool is reachable via natural language. Overlapping turn → **409**. |
| `POST /v1/email/agent/confirm-tool` | Approve/deny a gated tool the run is blocking on (`{ session_id, approved }`). |
| `POST /v1/email/agent/cancel` | Cooperatively cancel the in-flight run. |
| `DELETE /v1/email/agent/session/{id}` | Evict a session + tear down its agent. |
| `GET /v1/email/agent/session/{id}/history` | Conversation so far (`turns[]`, oldest first). |
| `POST /v1/email/agent/memory` | Runtime memory toggle (#1666), `{ session_id, enabled }` → `{ enabled, available, message }`. Enabling memory that was never initialized (started with `GAIA_MEMORY_DISABLED` / Lemonade unreachable) → **409**, never a silent no-op. |
| `GET /v1/email/agent/memory/{id}` | Memory status without changing it. |

Sessions are in-process and single-tenant (the sidecar hosts one user's agent); one turn
runs at a time per session. Memory uses FAISS locally; embeddings still go over Lemonade
HTTP, so the frozen binary stays free of torch/transformers.

### Mailbox actions (archive / quarantine, schema 2.1)

`archive` and `quarantine` mutate the live mailbox, so each is gated on a single-use
token exactly like `send` — but minted by `confirmAction` (not `draft`), bound to the
`(action, message_id)`. A token for one action/message cannot authorize a different
one. Both are reversible inside the 30s undo window:

```ts
// Archive (gated) → undo within the window (ungated):
const { confirmation_token } = await client.confirmAction({
  action: "archive",
  message_id: "msg-123",
});
const { batch_id, post_archive_id } = await client.archive({
  message_id: "msg-123",
  confirmation_token,
});
// post_archive_id is the id valid NOW — Outlook mints a new one on the folder move.
await client.unarchive({ batch_id }); // restores to inbox; 409 if the window lapsed

// Quarantine a phishing message (Gmail-only; refuses is_phishing:false and Outlook), then undo by action_id:
const t = await client.confirmAction({ action: "quarantine", message_id: "msg-9" });
const q = await client.quarantine({
  message_id: "msg-9",
  is_phishing: true,
  confirmation_token: t.confirmation_token,
});
await client.unquarantine({ action_id: q.action_id });
```

### Calendar (view / create / respond, schema 2.1)

> **Confirmation gating — deliberate asymmetry.** `send` and calendar **create**
> are token-gated (a payload-bound `confirmation_token` from `draft` /
> `previewCalendarEvent`; no/invalid token → `403`). Calendar **respond** (RSVP) is
> intentionally **not** token-gated, even though the in-process agent treats
> `accept_invite` / `decline_invite` as confirmation-tier tools. The contract draws
> the line at irreversibility: `send` and `create` are externally visible and not
> cleanly undoable, whereas an RSVP only sets your own response status on an existing
> invite and can be changed by responding again. The REST caller (the Agent UI's
> accept/decline controls) is the human-in-the-loop for that reversible action.

### Agent-loop capabilities not on the contract

Some agent capabilities run **only in the agent tool loop** (chat / Agent UI /
`gaia email`) and have **no REST endpoint**, so this package's `EmailClient`
can't drive them — they reach hosts through the agent chat surface until routes
land in a future schema bump:

- **Scheduled send + snooze (#1609):** `schedule_send`, `snooze_message`,
  `cancel_scheduled_job`, `list_scheduled_jobs`. A send is user-confirmed at
  creation, persisted as a mailbox draft plus a one-shot job in the agent's
  SQLite, and fired by the agent's scheduler at/after its time.
- **Voice / style-matched drafting (#1607):** `build_voice_profile` samples the
  user's Sent mail into a **local** style profile (top greetings / sign-offs,
  typical length, contraction & exclamation rate — derived features only, never
  raw content, stored on-device), and the agent's system prompt injects that
  guidance every turn so drafted reply bodies come out in the user's own voice
  instead of a neutral scaffold; `clear_voice_profile` forgets it. Read-only
  over Sent mail — nothing remote is mutated.
- **Follow-up tracking (#1606):** `check_followups` scans every connected
  mailbox's Sent folder and flags threads whose latest message is still the
  user's own outbound mail past a configurable window (default 3 days), most
  overdue first. **Detection only** — it never sends a nudge (any send stays
  confirmation-gated).

None of these are on the REST/MCP contract, so none of them moves `SCHEMA_VERSION`.

### Readiness vs liveness

`health()` is **liveness-only** — a green `/health` means "the REST surface is up,"
**not** "triage will work." On a fresh machine the binary boots fine, but the first
`triage` returns **HTTP 502** until a local Lemonade Server is running and the
configured model is pulled.

The authoritative readiness signal is **`GET /v1/email/init`** (#1795): it probes the
whole triage stack — Lemonade reachable **and** version-compatible **and** the triage
model downloaded — and returns `200` when ready, `503` when not, with an actionable
`hint`. The **`init()`** client method wraps it — returning the `InitResponse` on
both the ready (`200`) and not-ready (`503`) paths (branch on `.ready`), and, like
every `EmailClient` method, attaching the per-session bearer token (#1706) for you.
A raw `fetch` works too (the `InitResponse` type is exported) but must attach it:

```ts
const r = await fetch("http://127.0.0.1:8131/v1/email/init", {
  headers: { Authorization: `Bearer ${sidecar.authToken}` },
});
const init = (await r.json()) as import("@amd-gaia/agent-email").InitResponse;
if (!init.ready) throw new Error(init.hint ?? "email agent not ready to triage");
```

`POST /v1/email/init` is the companion provisioning verb: it asks the running Lemonade
to pull the model and **streams** `text/plain` progress. It cannot install Lemonade
itself (a host prerequisite) — if Lemonade is unreachable it returns `503` and pulls
nothing.

### Request shapes

Recipients and senders are **address objects**, not bare strings:
`{ email: string, name?: string }`. This applies to `triage`'s `message.from` and
`principal`, and to `draft`/`send`'s `to` (a non-empty array of them). Passing a
plain string for `to` is a `422` validation error.

`draft` proposes a reply and mints a single-use `confirmation_token` bound to that
exact message; `send` echoes it back. A full round-trip:

```ts
const { draft, confirmation_token } = await client.draft({
  to: [{ email: "you@example.com" }],
  subject: "Re: Prod incident",
  body: "On it — fix lands today.",
});
// `draft` is { to, subject, body, attachments }; the token authorizes exactly
// this payload.
const sent = await client.send({ ...draft, confirmation_token });
console.log(sent.sent_id);
```

#### Attachments (schema 2.2, #1542)

`draft` and `send` accept an optional `attachments` array of
`{ filename, mime_type, content_base64 }` (standard base64, ≤ 25 MB decoded
each). Validation is fail-loud (`422` for bad base64, a malformed MIME type, an
empty file, or oversize — never a silent drop), and the confirmation token
binds to each attachment's filename, MIME type, **and content digest**: a
`send` whose attachment set differs in any way from the confirmed draft is
rejected with `403`. Note the send payload carries the full `content_base64` —
spread the *request* you drafted with, not the metadata-only `draft` echo, when
attaching files:

```ts
const req = {
  to: [{ email: "you@example.com" }],
  subject: "Re: Prod incident",
  body: "Report attached.",
  attachments: [{
    filename: "incident-report.pdf",
    mime_type: "application/pdf",
    content_base64: reportB64,
  }],
};
const { confirmation_token } = await client.draft(req);
const sent = await client.send({ ...req, confirmation_token });
// sent.attachments echoes [{ filename, mime_type, size_bytes }] — metadata only.
```

Outlook mailboxes cap each attachment at **3 MB** (the Graph simple-attach
limit) — a larger file fails the send loudly rather than being truncated.

### Triage response shape

`triage` returns `{ schema_version, request_kind, result }`. The `result`
(`EmailTriageResult`) is what you route on:

| Field | Type | Notes |
|-------|------|-------|
| `category` | `"URGENT" \| "NEEDS_RESPONSE" \| "FYI" \| "PROMOTIONAL" \| "PERSONAL"` | The five buckets — **uppercase wire strings** (`res.result.category === "URGENT"`). |
| `is_spam`, `is_phishing` | `boolean` | Independent signals (a message can be neither, either, or both). |
| `summary` | `string` | Plain-text summary of the message/thread. |
| `action_items` | `ActionItem[]` | Each `{ description, due_hint?, type?: "text" \| "link", url? }`; may be empty. |
| `suggested_action` | `"reply" \| "none" \| "archive"` | `"reply"` for URGENT/NEEDS_RESPONSE, `"archive"` for PROMOTIONAL, else `"none"`. |
| `draft` | `DraftScaffold \| null` | A proposed reply **scaffold** (`{ to, subject }` — no body) when one is suggested (schema 2.3). Triage never composes reply prose; compose the body yourself and call `draft()` for a full `DraftReply` + confirmation token. |
| `usage` | `TriageUsage \| null` | LLM token/latency metrics; `null` on the heuristic-only path. |
| `attachments` | `AttachmentMeta[]` | Metadata (`{ filename, mime_type, size_bytes, attachment_id? }`) of the analyzed message's attachments, echoed from the request for downstream processing (schema 2.2; empty when none). |

The full request/response types are exported from the package (`src/types.ts`) for
exact field-level reference.

### Action-item task persistence (additive, #1605)

Beyond returning `action_items` inline, `triage` / `triageBatch` persist each
extracted item as a task row in the sidecar's local SQLite
(`~/.gaia/email/state.db`), linked back to the source via the request's
`message_id` (or `thread_id` for a thread). Persistence is de-duplicated per
message on the normalized description, so re-triaging the same message never
creates duplicate tasks. Results with no `message_id` are not persisted (no
source to link back to). This is a **side-effect only** — the wire response is
byte-for-byte what it was before; there is no read/complete task endpoint on
this contract yet (that surface arrives with GAIA's cross-agent task store,
amd/gaia#1521).

### Batch triage shape (additive, #1887)

`triageBatch` takes `{ schema_version?, items, context? }` where `items` is 1–100
`EmailInput` objects (the same `SingleEmailInput` / `ThreadInput` shapes `triage`
accepts, discriminated on `kind`), and `context` — when present — applies to **all**
items. It returns `{ schema_version, results }` with one `BatchItemResult` per item,
order-preserved (1:1 with `items`):

| Field | Type | Notes |
|-------|------|-------|
| `index` | `number` | 0-based position in the request `items` array. |
| `result` | `EmailTriageResult \| null` | Set when the item succeeded (same shape as `triage`'s `result`). |
| `error` | `BatchItemError \| null` | Set (with a `message`) when the item failed. Exactly one of `result` / `error` is set. |

**HTTP 200 with every item errored is a valid response** — a per-item failure does
not fail the request, so always inspect each `results[].error`, never just the HTTP
status. A `502` means Lemonade was unreachable before any item ran (the whole batch
fails). The single `triage()` endpoint and its types are unchanged; `MAX_BATCH_SIZE`
is exported for the 100-item cap (over-cap → `422`).

### Inbox search shape

`search({ query?, labels?, max_results? })` lists messages from the connected
mailbox and returns `{ schema_version, query, count, messages, next_page_token }`.
It is **read-only** — no body is read in full, nothing is modified, no confirmation
token is involved. Both `query` and `labels` are optional: a `query` searches **all
mail** (Gmail search semantics), `labels` filter to those labels, and with **neither**
it lists the INBOX. `max_results` is `1–100` (default `25`); each match is hydrated
with a per-message fetch, so the cap bounds that fan-out. To page, pass the
response's `next_page_token` back as the request's `page_token`. Each `messages[]`
item:

| Field | Type | Notes |
|-------|------|-------|
| `id` | `string` | Provider message id (opaque) — pass to the agent/triage path to read in full. |
| `thread_id` | `string \| null` | Provider thread id. |
| `subject` | `string` | Subject line. |
| `from` | `string` | Raw `From` header (e.g. `"Sarah Chen <sarah@example.com>"`) — a **string**, not an address object, unlike triage's `from`. |
| `to` | `string` | Raw `To` header. |
| `date` | `string` | Raw `Date` header. |
| `snippet` | `string` | Provider-supplied short preview. |
| `label_ids` | `string[]` | Label ids on the message. |

```ts
const { messages } = await client.search({ query: "is:unread", max_results: 20 });
for (const m of messages) console.log(m.subject, "—", m.from);
```

## Lifecycle helpers

`startSidecar(opts)` does spawn → `waitForHealth` → `checkVersion` in one call and
shuts down on any failure so a failed start never leaks a process. For finer
control, the steps are exported individually:

- `fetchBinary(opts)` → download + verify + install; returns `{ binaryPath, sha256, cached, ... }`.
- `resolveBinaryPath({ resourcesDir })` → locate a fetched binary (throws `BinaryNotFoundError` if absent).
- `spawnSidecar({ binaryPath, host?, port?, extraArgs? })` → spawn with `--host 127.0.0.1 --port <p>` (default port **8131**).
- `waitForHealth(baseUrl, { timeoutMs })` → poll `/health`; throws `HealthTimeoutError` on timeout (never assumes ready).
- `checkVersion(client, { expectedApiVersion })` → throws `VersionMismatchError` if the sidecar's apiVersion **MAJOR** differs (a higher MINOR is accepted).
- `verifySha256(buf, expected, label)` → throws `IntegrityError` on mismatch.
- `shutdown(sidecar)` → kill the **whole process tree** (`taskkill /F /T` on Windows; detached process-group kill on POSIX). The default auto-reaper does the same on process exit/crash/signal, so only a hard `SIGKILL` of the host can still orphan the child.
- `connectSidecar({ baseUrl, authToken?, timeoutMs?, healthTimeoutMs?, verifyVersion?, expectedApiVersion?, signal? })` → **attach mode**: `waitForHealth` + (default) `checkVersion` against a server this package did **not** spawn, returning an `AttachedSidecar` (`{ host, port, baseUrl, client, authToken? }` — no `child`). Spawns nothing and owns no lifecycle, so there is nothing to `shutdown()`. Pass an `AbortSignal` as `signal` to cancel the health wait early (e.g. the server process you're waiting on died). This is the client half of the fast dev loop — pair it with the Python source server (`gaia-agent-email serve --reload`), which serves an identical contract to the frozen binary. See [Fast local iteration](#fast-local-iteration-dev-mode).

### Fast local iteration (dev mode)

The published flow fetches and spawns a **frozen** binary — there is no source to
edit when you hit a bug. To iterate on the agent, run its **Python source** and
attach this client instead. The frozen binary is that source frozen (PyInstaller
freezes `packaging/server.py`, a thin re-export of `gaia_agent_email.server`), so
the `/v1/email/*` contract is byte-for-byte identical — **only the base URL
differs from production.**

```bash
pip install -e hub/agents/python/email     # editable: your edits take effect live
gaia-agent-email serve --reload            # source server, auto-reload, token off for dev
```

```ts
import { connectSidecar } from "@amd-gaia/agent-email";
const dev = await connectSidecar({ baseUrl: "http://127.0.0.1:8131" });
await dev.client.triage({ payload: { /* … */ } });
// edit Python → auto-reload → re-run. `npx @amd-gaia/agent-email dev` launches the
// serve process for you (`--python <path>` to use a specific venv).
```

The `serve` CLI (`gaia_agent_email.server:main`) accepts `--host`, `--port`
(rejects the reserved 4001), `--reload` (import-string app + watches the package
dir; add `--reload-dir` for your core checkout), `--dev` (implies `--reload`), and
`--print-openapi`. Running without `GAIA_EMAIL_SIDECAR_TOKEN` disables the caller
token (local dev only, logged loudly); Host/Origin protection still applies.
Auto-reload resets in-process `/v1/email/agent/*` sessions — irrelevant to the
stateless `triage`/`draft`/`send` surface.

## CLI

```bash
npx @amd-gaia/agent-email playground          # fetch + run the sidecar, open the playground
npx @amd-gaia/agent-email fetch --out resources
npx @amd-gaia/agent-email version             # show manifest + current platform
npx @amd-gaia/agent-email help
```

`playground` is the zero-to-running shortcut: it `fetchBinary`s into a temp cache
(`--out` to override), `startSidecar`s on `--port` (default 8131), opens the default
browser to `/v1/email/playground` (`--no-open` to skip), and runs until Ctrl+C.
The command owns the sidecar lifecycle itself (`autoCleanup: false`) and shuts it
down on `SIGINT`/`SIGTERM`/`SIGHUP` or on any startup error. Lemonade still has to
be running for live triage — the page itself reports if it isn't.

`fetch` is the supported, build-time path. It resolves
`${process.platform}-${process.arch}`, downloads that platform's artifact from the
base URL in `binaries.lock.json`, **verifies its SHA-256 against the lock and fails
loudly on any mismatch**, writes it to `--out`, and `chmod +x`'s it on POSIX.

| Flag | Meaning |
|------|---------|
| `--out <dir>` | Resources dir to write the verified binary into (**required**) |
| `--base-url <url>` | Override the download base URL (defaults to the lock's `baseUrl`) |
| `--platform <key>` | Override platform key (e.g. `linux-x64`); default is the host |
| `--force` | Re-download even if a verified binary already exists |

**SHA-256 is mandatory.** There is no "use it anyway" path — a corrupt, truncated,
or tampered download is rejected before it can ever be spawned, and the bad file is
not left on disk.

## Connectors & auth

An endpoint that works on the **content you pass in the request** is **standalone**
— it needs nothing but the local Lemonade LLM. An action that **reads from or acts
on the live Gmail/Outlook mailbox or calendar** requires the **Google or Microsoft
connector** (OAuth), configured in GAIA under *Settings → Connectors*.

`send` resolves its OAuth token from the **local GAIA connector store**
(`gaia.connectors`) on the host — `EmailSendRequest` has **no `access_token`
field** (`provider` is only a routing hint). There is **no way to pass or forward a
connection through this package's API**, so connector-backed calls only work on a
machine where the mailbox is already connected in GAIA. Triage and draft, which
need no connector, work anywhere.

As of `SCHEMA_VERSION` 2.2 this package's REST API exposes the read-only inbox
**search** and **pre-scan** (`search` / `prescan`), the **archive** and
phishing-**quarantine** mailbox actions plus their undo (`confirmAction` / `archive` /
`unarchive` / `quarantine` / `unquarantine`), calendar **view / create / respond**
(`listCalendarEvents` / `previewCalendarEvent` / `createCalendarEvent` /
`respondToCalendarEvent`), and **attachments** on triage/draft/send (#1542).
The full GAIA email agent does more on the live mailbox
(label, move, mark spam) and calendar (detect / conflicts); those remaining actions are
connector-gated by definition and are **not exposed through this package's REST API
yet**.

## Browser / Electron renderer (`./client`)

The default entry (`.`) pulls in Node built-ins (`node:fs`, `node:child_process`,
`node:crypto`) to fetch and spawn the binary, so it can't be bundled for a browser
or an Electron renderer. The browser-safe `./client` subpath re-exports only
zero-Node-dependency symbols — `EmailClient`, every error class, `SCHEMA_VERSION`,
and all request/response types — so it *bundles* for a renderer.

But the sidecar serves **same-origin only and sends no CORS headers**, so a
renderer on a different origin cannot `fetch` `http://127.0.0.1:8131` directly. Two
working patterns:

- **Electron (recommended):** spawn and own the sidecar in your **main** process
  (the `.` entry), and expose `triage`/`draft` to the renderer over your own IPC.
- **Same-origin / proxied:** use `./client` from a page that already shares the
  sidecar's origin, or behind a proxy you control.

```ts
import { EmailClient } from "@amd-gaia/agent-email/client";

// Same-origin or proxied path only — not a cross-origin fetch at 127.0.0.1:8131.
const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131" });
const res = await client.triage({ payload: { /* … */ } });
```

## Module format

The package is **ESM-only** (`"type": "module"`; no CommonJS build). Import it with
`import …`. From a CommonJS module, use a dynamic import instead of `require`:

```js
const { startSidecar } = await import("@amd-gaia/agent-email");
```

Plain JavaScript works — the package ships compiled JS in `dist/`; TypeScript is
the authoring language, not a consumer requirement. The bundled `.d.ts` files give
editors autocomplete but your code never imports them.

## Types

TypeScript types in `src/types.ts` mirror two Python sources of truth:

- `contract.py` — the triage request/response contract plus the schema-2.1
  additions (inbox search, mailbox actions, calendar, pre-scan), the schema-2.2
  attachment models (`AttachmentMeta` / `OutgoingAttachment`), and the schema-2.3
  triage draft scaffold (`DraftScaffold`; `SCHEMA_VERSION = "2.4"`).
- `api_routes.py` — the local draft/send confirmation handshake models, the
  readiness-preflight envelope (`InitResponse` / `InitLemonadeStatus` /
  `InitModelStatus`, #1795), and the scheduled-briefing response
  (`EmailBriefingResponse`, #1608).
- `query_routes.py` + the frozen `/query` SSE contract
  (`docs/spec/agent-ui-query-sse-contract.md`) — the schema-2.4 agent-loop query:
  `EmailQueryRequest` / `QueryContextItem`, the seven `QueryEvent` shapes (plus
  the `unknown` placeholder for additive future types), and `QueryCancelResponse`.

They are hand-written (vs. generated from `/openapi.json`) because the contract is
small and version-gated, keeping the published package free of a typegen build
step. The runtime `checkVersion` guard catches contract drift loudly; the server
exposes `GET /openapi.json` if you prefer to regenerate.

> Wire note: `EmailMessage.from` is the JSON key on the wire (Python aliases its
> `from_` field to `from`), so the TS interface uses `from` directly.

## Platforms

Fully supported: `win32-x64`, `linux-x64`, `darwin-arm64` (Apple Silicon). Intel
macOS (`darwin-x64`) is a **best-effort** target — built when the release can, and
omitted with a clear "no binary for darwin-x64" install error otherwise. Each
binary is built natively (PyInstaller does not cross-compile); `binaries.lock.json`
maps every available platform to its artifact filename, SHA-256, and size.

## License

Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: MIT
