# `/query` SSE Event Contract (Agent UI v2)

> **Internal design spec** — not published to the Mintlify docs site (`.md`, not
> registered in `docs/docs.json`), matching the sibling `docs/spec/*.md` design
> specs. Integrator-facing rendering lands with the agent's own docs in #2016.
>
> **Status:** Frozen (v2 wire contract). This document is the single source of
> truth for the `POST /v1/<agent>/query` request body and the Server-Sent Events
> (SSE) response stream. Every v2 surface — the sidecar `/query` endpoint, the
> daemon streaming relay, the frontend render map, the `gaia <agent>` CLI, and
> `gaia api` — codes against **this** vocabulary. Freezing it first stops each
> surface from inventing a private dialect.
>
> **Tracks:** [#2015](https://github.com/amd/gaia/issues/2015) (this spec),
> epic [#2014](https://github.com/amd/gaia/issues/2014) (Agent UI v2, Phase 0).
> **Design source:** [`docs/plans/agent-ui-agent-capabilities-plan.md`](../plans/agent-ui-agent-capabilities-plan.md)
> §0.1 (REST contract), §0.2 (SSE schema), §0.15 (contract evolution).
> **First consumer:** [#2016](https://github.com/amd/gaia/issues/2016) implements
> this contract as `POST /v1/email/query` on the email sidecar (contract bump
> 2.3 → 2.4).

---

## 1. Scope and intent

The loop→SSE seam already exists in-process:
[`src/gaia/ui/sse_handler.py`](../../src/gaia/ui/sse_handler.py)
(`SSEOutputHandler(OutputHandler)`) turns every agent-loop `console.print_*`
call into a typed JSON event on a `queue.Queue` that a streaming endpoint drains.
**But the handler emits its own vocabulary**
(`status` / `step` / `thinking` / `plan` / `tool_start` / `tool_args` /
`tool_result` / `tool_end` / `chunk` / `answer` / `permission_request` / …),
which is **not** the v2 contract below.

This document defines two things and freezes the boundary between them:

1. **The target wire contract** (§3–§5) — the seven canonical event types plus
   the `/query` request body. Integrators depend on this and nothing else.
2. **The translation map** (§6) — a total, source-exhaustive mapping from the
   in-process handler's vocabulary onto the seven canonical types, so the v2
   translation layer (built in #2016) can be implemented with **no source event
   left unmapped**.

`/query` is therefore "run the agent with an SSE handler, translate its events to
the canonical vocabulary, and expose the queue as `text/event-stream`" — not
net-new agent instrumentation.

---

## 2. `POST /v1/<agent>/query` — request

**Method / path:** `POST /v1/<agent>/query`
**Request `Content-Type`:** `application/json`
**Response `Content-Type`:** `text/event-stream`

### 2.1 Request body

```json
{
  "query": "Triage my inbox and draft replies to anything urgent.",
  "run_id": "0f9c2b6e-2c4a-4b1e-9d6a-1e2f3a4b5c6d",
  "context": [
    {"role": "user", "content": "earlier turn"},
    {"role": "assistant", "content": "earlier reply"}
  ],
  "model": "Gemma-4-E4B-it-GGUF",
  "provider": "lemonade",
  "max_steps": 20,
  "session_id": "3c1b9e2a-...-uuidv4"
}
```

### 2.2 Field schema

| Field | Type | Required | Notes |
|---|---|---|---|
| `query` | string | **yes** | The natural-language request driving the agent loop. Non-empty. |
| `run_id` | string (UUIDv4) | **yes** | **Host-minted** streaming-run handle. See §2.3. |
| `context` | array of `{role, content}` objects | **yes** | The relevant transcript slice, **pushed in the body**. May be an empty array `[]` for a fresh conversation, but the field must be present. See §2.4. |
| `model` | string | no | Model id override. Omitted ⇒ the sidecar's default (e.g. `Gemma-4-E4B-it-GGUF` for the email agent). |
| `provider` | string | no | LLM provider override (`lemonade` / `claude` / `openai`). Omitted ⇒ sidecar default. |
| `max_steps` | integer ≥ 1 | no | Agent-loop step ceiling. Omitted ⇒ the sidecar's configured default. |
| `can_answer_questions` | boolean | no | Whether **this caller** can render a `needs_input` event and POST the answer (§5.1). **Send only to a peer at contract ≥ 2.6** — an older sidecar 422s the unknown field (see §7). Defaults to **false** — the safe answer. A caller that cannot answer would otherwise park the run until the question times out, which is indistinguishable from a hang. When false, a step that would ask instead fails immediately with what the user should do on that surface. |
| `session_id` | string, `^[A-Za-z0-9_-]{1,128}$` | no | Opaque conversation id, host-minted once per conversation and reused across turns (#2829). **Send only to a peer at contract ≥ 2.12** — an older sidecar 422s the unknown field (see §7). Omitted ⇒ today's stateless behaviour: a throwaway agent per call. Present ⇒ the run resolves the SAME agent every other turn on this id used, so a reference to something an earlier turn surfaced (e.g. "reply to number 1") can resolve. See §2.4. |

```jsonc
// JSON Schema (draft 2020-12) — request body
{
  "$id": "gaia:/query:request",
  "type": "object",
  "additionalProperties": false,
  "required": ["query", "run_id", "context"],
  "properties": {
    "query":     { "type": "string", "minLength": 1 },
    "run_id":    { "type": "string", "format": "uuid" },
    "context": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["role", "content"],
        "properties": {
          "role":    { "enum": ["user", "assistant", "system", "tool"] },
          "content": { "type": "string" }
        }
      }
    },
    "model":     { "type": "string" },
    "provider":  { "type": "string" },
    "max_steps": { "type": "integer", "minimum": 1 },
    "can_answer_questions": { "type": "boolean", "default": false },
    "session_id": { "type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$" }
  }
}
```

### 2.3 The host mints `run_id` (cancellable from the instant the request is sent)

The **host mints `run_id`**, not the sidecar (§0.1). Cancellation
(`POST /v1/<agent>/query/{run_id}/cancel`, §0.13) and the confirmation flow
(§0.4) key off it. If the sidecar minted the id, a "Stop" pressed before the
first SSE event would have no id to cancel — a race window. Because the client
already knows `run_id` at request time, the run is cancellable from the instant
the request is sent, before any event has streamed back.

### 2.4 Context is pushed, never pulled

The host owns the transcript (§0.9) and passes the relevant slice as `context`.
The sidecar stays **stateless** and never reads other sessions back over the
`/host/v1/*` callback (§0.11 scoping forbids it anyway). A pushed slice and a
pulled one therefore can't disagree.

**Unless `session_id` is sent (#2829, schema 2.12).** Then the run resolves a
real, persistent agent keyed on that id instead of a throwaway one, so
whatever the agent itself accumulates outside `conversation_history` (e.g. a
just-surfaced reference an in-process tool cached) survives to the next turn.
`context` is still pushed and still REPLACES the agent's
`conversation_history` every turn — it is never read back from the sidecar —
so the two mechanisms complement rather than duplicate each other: `context`
is the host-owned transcript, the session is the agent's own working state.
A `session_id` the sidecar has never seen (e.g. it restarted mid-conversation
— the session registry is in-memory) is surfaced as a one-time `status`
event, never a silent cold start.

---

## 3. Response stream framing

- **Transport:** `text/event-stream`. One canonical event per SSE `data:` line,
  each line a single JSON object discriminated on `type`.
- **Terminal event:** exactly one `final` **or** one `error` ends a run. After it
  the server closes the stream. (The in-process handler's `None` queue sentinel
  in `signal_done()` maps to *stream close*, not to a wire event.)
- **Ordering:** `status`/`token`/`tool_call`/`tool_result` may interleave freely
  during a run. `needs_confirmation` pauses the logical run pending the §0.4
  handoff. A terminal `final`/`error` is always last.

---

## 4. The seven canonical event types

The contract is exactly these `type` values (design §0.2 — the original seven,
plus `needs_input` added as an additive MINOR by #2469). No others are valid on
the wire; a receiver applies the §7 unknown-type rule to anything else.

| `type` | Payload | UI effect |
|---|---|---|
| `status` | `{message}` | progress line / spinner label |
| `token` | `{delta}` | stream assistant text |
| `tool_call` | `{tool, args}` | "using tool" card |
| `tool_result` | `{tool, render?, data}` | if `render` set (e.g. `email_pre_scan`), draw the typed card from `data`; else a generic result card |
| `needs_confirmation` | `{run_id, action, summary, confirm_url?}` | show approve/deny; on approve continue per §0.4. **Terminal** under the stateless model |
| `needs_input` | `{run_id, request_id, question, options, allow_free_text, sensitive?, respond_url, timeout_seconds?}` | show the question and its options; POST the answer to `respond_url`. **Not terminal** — the run resumes on the same stream (§5.1) |
| `final` | `{answer, usage?}` | finalize the message; terminal |
| `error` | `{detail, status}` | surface the actionable error verbatim; terminal |

A `:`-prefixed SSE comment may appear at any point as a heartbeat. It carries no
payload and every conformant reader skips it, but it MUST reset any read-idle
watchdog — a run parked on a `needs_input` question is otherwise
indistinguishable from a dead one.

### 4.1 JSON Schema per type

```jsonc
// status
{ "type": "object", "additionalProperties": false,
  "required": ["type", "message"],
  "properties": {
    "type":    { "const": "status" },
    "message": { "type": "string" }
  } }

// token
{ "type": "object", "additionalProperties": false,
  "required": ["type", "delta"],
  "properties": {
    "type":  { "const": "token" },
    "delta": { "type": "string" }   // an incremental chunk of assistant text
  } }

// tool_call
{ "type": "object", "additionalProperties": false,
  "required": ["type", "tool", "args"],
  "properties": {
    "type": { "const": "tool_call" },
    "tool": { "type": "string" },
    "args": { "type": "object" }     // {} when the tool takes no arguments
  } }

// tool_result
{ "type": "object", "additionalProperties": false,
  "required": ["type", "tool", "data"],
  "properties": {
    "type":   { "const": "tool_result" },
    "tool":   { "type": "string" },
    "render": { "type": "string" },  // optional card key, e.g. "email_pre_scan"
    "data":   {}                     // structured result; shape is render-specific
  } }

// needs_confirmation
{ "type": "object", "additionalProperties": false,
  "required": ["type", "run_id", "action", "summary"],
  "properties": {
    "type":        { "const": "needs_confirmation" },
    "run_id":      { "type": "string", "format": "uuid" },
    "action":      { "type": "string" },   // e.g. "send", "archive", "input"
    "summary":     { "type": "string" },   // the literal text the user approves
    "confirm_url": { "type": "string" }    // resume-model only (§0.4); omitted under stateless
  } }

// needs_input  (added #2469 — additive MINOR; resolves §9 Q3)
{ "type": "object", "additionalProperties": false,
  "required": ["type", "run_id", "request_id", "question", "options", "allow_free_text"],
  "properties": {
    "type":            { "const": "needs_input" },
    "run_id":          { "type": "string", "format": "uuid" },
    "request_id":      { "type": "string" },   // identifies THIS question within the run
    "question":        { "type": "string" },   // the literal text the user answers
    "options": {                               // 0-4 mutually exclusive answers
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["value", "label"],
        "properties": {
          "value":       { "type": "string" }, // what goes back on the wire
          "label":       { "type": "string" }, // what the user picks
          "description": { "type": "string" }  // what choosing it DOES
        }
      }
    },
    "allow_free_text": { "type": "boolean" },  // typed answers accepted too
    "sensitive":       { "type": "boolean" },  // the answer is a credential: mask it
    "respond_url":     { "type": "string" },   // /v1/<agent>/query/{run_id}/respond
    "timeout_seconds": { "type": "integer" }   // after this the run fails loudly
  } }

// final
{ "type": "object", "additionalProperties": false,
  "required": ["type", "answer"],
  "properties": {
    "type":   { "const": "final" },
    "answer": { "type": "string" },
    "usage":  { "type": "object" }   // optional {steps?, tools_used?, elapsed?, tokens?, ttft?}
  } }

// error
{ "type": "object", "additionalProperties": false,
  "required": ["type", "detail", "status"],
  "properties": {
    "type":   { "const": "error" },
    "detail": { "type": "string" },   // actionable message, surfaced verbatim
    "status": { "type": "integer" }   // HTTP-style status code for the failure class
  } }
```

### 4.2 `render` replaces the in-process fence-injection hack

Before the #2109 cutover, `MessageBubble.tsx` rendered structured cards by
*fence-parsing* the assistant text (`STRUCTURED_PAYLOAD_LANGS`), fed by the
`_capture_render_payload` / `_drain_render_payloads` hack in `sse_handler.py`
(HACK, issue #1000). Both are now deleted: the sidecar declares the card type
explicitly via `tool_result.render`, so the host needs no per-tool knowledge
(pre-cutover session history containing fenced payloads degrades to a plain
JSON code block). Each `render` type still needs a frontend component; per
§0.15 a **custom `render` type is first-party / AMD-verified only in v1**, and an
unknown `render` degrades to the generic result card (see §7).

### 4.3 Generic render primitives (#2108)

Beyond agent-specific keys like `email_pre_scan`, the Agent UI registers five
**generic primitives** any agent can emit on `tool_result.render` without
shipping its own frontend component. `data` must match the schema for the key:

| `render` | `data` schema |
|---|---|
| `table` | `{ title?: string, columns: string[], rows: Array<Array<string\|number\|boolean\|null>> }` |
| `key_value` | `{ title?: string, items: Array<{key: string, value: string\|number\|boolean\|null}> }` |
| `list` | `{ title?: string, ordered?: boolean, items: Array<string\|number> }` |
| `image` | `{ src: string, alt?: string, caption?: string }` |
| `diff` | `{ title?: string, unified: string }` — a unified-diff text; lines are styled by their `+` / `-` / `@@` prefix |

Rules every receiver implements and every producer can rely on:

- **Fallback, never nothing.** An unknown `render` key renders an explicit
  `Unsupported card type: "<render>"` card; a payload that fails its schema
  renders `Invalid <render> payload`. Both include a collapsible dump of the
  raw `data` so the turn stays debuggable. A card is never silently dropped
  and a bad card never blanks the message.
- **`image.src` accepts only inline base64 raster data** matching
  `^data:image/(png|jpe?g|gif|webp);base64,` — SVG is deliberately excluded
  (it can carry script), and remote URLs (`http(s)://`) are rejected. Anything
  else is an invalid payload.
- **500-item render cap.** `table` rows, `list` items, and `key_value` items
  render at most 500 entries; the card shows a visible `+N more (truncated)`
  row when capped. Producers should pre-trim to what the user actually needs.
- **Values are rendered as plain text** — markdown/HTML inside cell, item, or
  key/value strings is NOT interpreted.

**Author guidance:** default to these primitives — they need zero frontend
work and render consistently. A custom `render` key requires a first-party /
AMD-verified frontend component in v1 (§0.15); until yours ships, emitting it
degrades to the unsupported-card fallback.

---

## 5. `needs_confirmation` and the confirmation model

`needs_confirmation` carries `run_id` so the host can correlate the pause with
the run it is cancelling/continuing. The **decision between the two §0.4 models
is pending sign-off** (epic decision D1); the contract is written to accommodate
either:

- **Stateless stop-and-hand-off (recommended v1):** the event *ends the stream*;
  `confirm_url` is **omitted**. The host performs the deterministic call itself
  (e.g. `POST /v1/email/send` with the single-use token) and issues a fresh
  `/query` carrying the approved-step state to continue.
- **Resume model:** the event includes `confirm_url =
  /v1/<agent>/query/{run_id}/confirm`; the run stays paused server-side and
  resumes emitting on the same stream after the host POSTs the token.

Until D1 is signed off, a destructive step may instead end with a `final`
"skipped — use the fixed-function endpoint" answer (per #2016).

### 5.1 `needs_input` and the resume seam (#2469)

A **question** is not an approval, and conflating them was blocking the thing
users actually need: an agent that can set up its own mailbox access instead of
printing `gaia connectors connect google --scopes <scopes>` at someone sitting in
a terminal chat.

`needs_input` is the eighth canonical type. It differs from `needs_confirmation`
on the one axis that matters on the wire:

| | `needs_confirmation` | `needs_input` |
|---|---|---|
| shape | binary approve/deny | a question + 0-4 labelled options + optional free text |
| terminal | **yes** (stateless model: the run ends, deny-by-default) | **no** — the run stays parked on the open stream |
| resumed by | nothing; the host re-issues a fresh `/query` | `POST /v1/<agent>/query/{run_id}/respond` |

Keeping them separate is deliberate: `needs_confirmation`'s terminal,
deny-by-default behaviour is a **security control**, and it must not become
conditional on whether some optional field happens to be present.

**`POST /v1/<agent>/query/{run_id}/respond`**

```json
{ "request_id": "…", "value": "google" }
```

`value` is an option's `value` (its `label` is also accepted, so a client that
echoes the label still resolves to the same branch) or free text when
`allow_free_text` is set. Responses:

| Status | Meaning |
|---|---|
| `200` | `{run_id, request_id, accepted: true, status: "ok"}` — the run continues on its ORIGINAL stream |
| `404` | no run with that `run_id` is in flight (it finished or was cancelled) |
| `409` | the run is live but is not waiting on that `request_id` — a **stale** answer, rejected rather than applied to whatever is pending now |

**A caller that cannot answer must never be asked.** `needs_input` is only
emitted when the request set `can_answer_questions: true`. Every non-interactive
front-door (one-shot CLI, batch, eval) leaves it false and gets an immediate,
actionable refusal instead of a run parked on a question no one can see.

**An unanswered question must fail loudly, not hang.** The agent side blocks with
a bounded timeout (`timeout_seconds`); when it expires the step raises and the
run terminates with an `error`. A paused run that never resumes and never ends is
not an acceptable state.

**Approvals should eventually ride this primitive** — an approval is a question
whose options are Approve and Deny — so receivers should implement one question
UI, not two. That migration is a separate change: it alters the security
behaviour above and must be made deliberately.

---

## 6. Translation map — in-process handler → canonical contract

This is the total mapping the v2 translation layer implements. **Source of
truth:** [`src/gaia/ui/sse_handler.py`](../../src/gaia/ui/sse_handler.py) on
`main`. Every top-level `type` the handler emits appears below with an explicit
**map / fold / drop** decision — nothing falls through.

> **Vocabulary-count correction.** A naive
> `grep '"type":"..."'` over `sse_handler.py` returns 18 strings, but two of
> them — `file_list` and `search_results` — are **nested** `result_data.type`
> values *inside* a `tool_result` event, not top-level event types. The handler
> emits **16 distinct top-level event types** (listed below), plus the `None`
> queue sentinel (stream close, not a wire event).

### 6.1 The four clean maps (named in the issue)

| Source event | → Canonical | Transform |
|---|---|---|
| `tool_start` | `tool_call` | Rename; carry `tool`. `args` filled from the paired `tool_args` (§6.3). `detail`/`mcp_server` dropped (host derives its own label). |
| `chunk` | `token` | `content` → `delta`. |
| `answer` | `final` | `content` → `answer`; `elapsed`/`steps`/`tools_used`/`tokens`/`ttft` → `usage`. |
| `permission_request` | `needs_confirmation` | `tool` → `action`; render `args` as `summary`; carry `run_id`; `confirm_url` per §5. |

### 6.2 Every remaining top-level source event

| Source event | Emitters (`sse_handler.py`) | Decision | → Canonical | Rationale |
|---|---|---|---|---|
| `status` | `print_processing_start`, `print_goal`, `print_warning`, `print_info`, `start_progress`, `print_repeated_tool_warning`, `print_completion`, `print_agent_selected`, confirm-timeout | **map** | `status` | Already the canonical shape; keep `message`, drop the `status`/`steps`/`elapsed` sub-fields (progress-only). |
| `step` | `print_step_header` | **fold** | `status` | Step counter is progress narration; render as a `status` line (e.g. `"Step 3/20"`). No dedicated canonical type. |
| `thinking` | `print_thought`, `print_streaming_text` (`<think>…</think>`) | **fold** | `status` | Reasoning narration, not final assistant text — folds to `status`, **not** `token` (which is answer text the UI commits to the message). See open question Q1. |
| `plan` | `print_plan` | **fold** | `status` | Plan preview is progress narration; join `steps` into one `status` message. |
| `tool_args` | `pretty_print_json` (`title == "Arguments"`) | **fold** | `tool_call` | Merged into the preceding `tool_call` as `args` (§6.3), not a standalone event. |
| `tool_result` | `pretty_print_json` (result branch) | **map** | `tool_result` | Direct. `result_data` (incl. nested `file_list` / `search_results`) → canonical `data`; `render` set from the sidecar's declared card type (replaces the #1000 fence hack). |
| `tool_end` | `print_tool_complete` | **fold** | `tool_result` | Redundant terminator. The `tool_result` is the completion signal; if `tool_end` fires with no preceding result (skipped), synthesize a minimal `tool_result{tool, data:{}}` so completion is never lost. |
| `agent_error` | `print_error` | **map** | `error` | `content` → `detail`; `status` set to the failure class (500 for an agent-loop error). Non-terminal loop warnings that today ride `agent_error` should use `status`. |
| `policy_alert` | `print_policy_alert` | **map** | `error` | A governance **block** is an actionable, must-surface refusal. `reason` → `detail`; `decision`/`rule_ids`/`policy_version`/`receipt_id` → a structured tail on `detail` or dropped. See open question Q2 (per-tool vs terminal). |
| `user_input_request` | `request_user_input_blocking` | **map** | `needs_input` | `request_id`/`message`/`options`/`choices`/`allow_free_text`/`sensitive`/`timeout_seconds` map straight across; `choices` strings become `{value, label}` options so a bare choice list is still pickable. `respond_url` is derived from `run_id`. (Was folded into `needs_confirmation` before #2469 — see §9 Q3, now resolved.) |
| `tool_confirm_denied` | `confirm_tool_execution` (background mode) | **fold** | `status` | Unattended auto-deny is informational — the run continues and the agent retries. Surface the actionable `message` as a `status` line, not an `error` (the run did not fail). |
| `agent_created` | `print_agent_created` | **drop** | — | Registry-refresh signal with no chat-stream meaning. Must move to a **host control channel**, not the `/query` event stream. Dropping it here is a contract decision, not a silent loss — call it out in #2016. |
| `None` (queue sentinel) | `signal_done` | **n/a** | *stream close* | Not a wire event. The canonical stream ends after the terminal `final`/`error`; the sentinel maps to closing the `text/event-stream` response. |

### 6.3 `tool_start` + `tool_args` → one `tool_call`

The in-process handler emits `tool_start` (name only) and then, separately,
`tool_args` (arguments) once they're known. The canonical `tool_call` carries
`{tool, args}` **together**. The translation layer buffers the `tool_start`,
attaches `args` from the following `tool_args`, and emits a single `tool_call`.
If no `tool_args` arrives (argument-less tool), it emits `tool_call{tool,
args:{}}`.

---

## 7. Contract evolution (§0.15)

The seven types are the frozen v1 vocabulary. Evolution rules:

- **Unknown top-level event `type` → visible "unsupported event", never silently
  dropped.** A newer agent emitting a new top-level `type` to an older host must
  surface a visible *"unsupported event"* placeholder (the frontend render map's
  default branch; the CLI prints a one-line notice). This is the CLAUDE.md
  no-silent-fallback rule applied to the wire, and it is symmetric with the
  unknown-`render` fallback (§4.2).
- **Unknown `render` type → generic result card.** An unrecognized
  `tool_result.render` degrades to the generic result card, never a blank.
- **A client MUST NOT send an optional request field the peer has not agreed
  to.** Sidecar request models are strict (`extra="forbid"`), so an unknown
  field is a hard `422` on *every* request — the correct fail-loudly behaviour on
  the receiving side, and the reason the sending side has to ask first. The two
  halves of a feature reach users on different clocks: a host/TUI change ships
  when the host builds, a sidecar change only when a new agent is **published**
  and the user installs it, so a current host routinely talks to an older
  sidecar. Read `GET /v1/<agent>/version` (`{apiVersion, agentVersion}`),
  compare against the version that introduced the field, and omit it below that
  floor — `omitempty` alone is not enough, because the interesting value is
  often `true`. Not claiming an absent capability is **negotiation**, not a
  silent fallback; when the missing capability changes what the user can do, say
  so where they can act on it.
- **Additive is MINOR; removal needs a sunset window.** Adding an event type,
  a `render` type, or a request field is a backward-compatible **MINOR**.
  Removing one requires a stated **deprecation/sunset window**, not a silent
  break. The install-time contract-**MAJOR** gate (§0.15) rejects out-of-range
  agents loudly; it cannot express additive-vs-deprecation on its own, so both
  are governed by this rule.
- **Version the callback API too.** The mirror case (an old installed sidecar
  calling a changed `/host/v1/*`) is covered by §0.15, out of scope for this
  document but noted so the SSE contract isn't versioned in isolation.

---

## 8. Email agent regeneration plan (implemented by #2016)

Freezing this contract is the dependency for [#2016](https://github.com/amd/gaia/issues/2016),
which adds `POST /v1/email/query` (the SSE agent loop) to the email sidecar and
bumps the contract **2.3 → 2.4** (additive MINOR — 2.2 was consumed by
attachments #1542, 2.3 by the triage scaffold #1984). Per the #1841 "update every
doc that describes it" rule, #2016 must regenerate/update **together**:

| Surface | File | Change |
|---|---|---|
| OpenAPI | [`hub/agents/email/python/openapi.email.json`](../../hub/agents/email/python/openapi.email.json) | Regenerate via `export_openapi.py` with the new `/query` route + this SSE contract referenced as the streaming response. |
| OpenAPI generator | `hub/agents/email/python/gaia_agent_email/export_openapi.py` | Emit the `/query` path + `/query/{run_id}/cancel`. |
| Human spec (HTML) | [`hub/agents/email/python/specification.html`](../../hub/agents/email/python/specification.html) via `gaia_agent_email/spec_html.py` | Document `/query`, the seven event types, and the request body. |
| Contract version | `gaia_agent_email/contract.py` (`SCHEMA_VERSION`) + `gaia_agent_email/version.py` (`API_VERSION`) | `2.3` → `2.4`. |
| Route | `gaia_agent_email/api_routes.py` | New `/query` route wiring the agent loop through the translation layer (§6). |
| Integrator docs | `hub/agents/email/npm/{SPEC.md, SKILL.md, README.md, CHANGELOG.md}` | Describe `/query`, the event vocabulary, and the 2.4 CHANGELOG entry. |

The same rule applied again for the #2469 `needs_input` MINOR (contract 2.5 →
2.6): this spec, `openapi.email.json`, `specification.html` / `spec_html.py`,
`contract.py`, `CONTRACT.md`, `CAPABILITY_MATRIX.md`, and the npm
`SPEC.md` / `README.md` / `SKILL.md` / `CHANGELOG.md` all moved together.

The translation layer (§6) is the reusable piece: #2016 builds it once against
this contract; later surfaces (#2014 relay, CLI, `gaia api`) consume the same
canonical stream and never re-derive the mapping.

---

## 9. Open questions

- **Q1 — dedicated reasoning stream?** `thinking` currently folds into `status`.
  If the v2 UI wants a distinct "reasoning" affordance separate from progress
  narration and from committed answer text, that is an **additive MINOR** (a new
  event type or a `status` subtype) — not a change to the frozen seven.
- **Q2 — `policy_alert` terminal vs per-tool.** Mapped to `error`, but a
  governance block is per-*tool* (the run may continue), whereas the canonical
  `error` is documented as terminal. Either the translation layer distinguishes
  via `status` code, or governance blocks warrant a dedicated additive type.
  Needs a call before #2016 wires governance into `/query`.
- **Q3 — free-text `user_input_request` vs approve/deny. RESOLVED (#2469).**
  Interactive input became load-bearing (agent-led mailbox onboarding), so the
  additive-MINOR path was taken: `needs_input` is now its own type with its own
  resume route (§5.1). `needs_confirmation` keeps its terminal, deny-by-default
  approval semantics unchanged.
- **Q4 — confirmation model (D1).** `confirm_url` presence in
  `needs_confirmation` depends on the stateless-vs-resume decision (§5), still
  pending epic sign-off.
