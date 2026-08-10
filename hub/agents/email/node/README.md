# @amd-gaia/node-agent-email

Email triage SDK that classifies, summarises, and extracts actionable items from emails and threads using a hybrid heuristic + LLM pipeline. Works with any OpenAI-compatible endpoint.

Requires Node 18+ and an OpenAI-compatible LLM server like [Lemonade Server](https://lemonade-server.ai/).

## Quick Start

```js
const { EmailTriageAgent } = require("@amd-gaia/node-agent-email");

const agent = new EmailTriageAgent({
  baseUrl: "http://127.0.0.1:1234/v1", // the base URL for the LLM server
  model: "gemma-4-e4b", // the model to use
  contextWindowTokens: 32768, // the context window size for the model in tokens
  userContext: "I'm a software founder at Acme Corp.", // context to append to the classify/action prompts or what you like to see in the summary
  aliases: ["me@acme.com", "ceo@acme.com"], // other email addresses for the principal that should be treated as the same person
});

const result = await agent.triage({
  payload: {
    kind: "single",
    principal: { email: "me@acme.com", name: "Alice" },
    message: {
      message_id: "msg-001",
      from: { email: "bob@partner.io", name: "Bob" },
      to: [{ email: "me@acme.com" }],
      subject: "Contract review by Friday",
      body: "Hi Alice, please review the attached contract by end of day Friday...",
      date: "2026-07-25T10:00:00Z",
    },
  },
});

console.log(result.result.category);       // "URGENT"
console.log(result.result.summary);        // concise 1-2 sentence summary
console.log(result.result.primary_action); // highest-priority action item
console.log(result.result.action_items);   // all LLM suggested actions, ranked (will include primary_action again as the first item)
```

## Pipeline

Every email goes through **up to 4 steps**. The pipeline is designed to minimise LLM calls — most promotional and transactional emails resolve in zero calls since those categories are detected via heuristics (english only).

```
┌─────────────────────┐
│  1. Heuristic       │  Zero LLM calls. String matching on sender,
│     Classify        │  subject, body. Detects spam, phishing, promo,
│                     │  transactional, and FYI patterns.
│                     │
│  Short-circuit:     │  Definite spam/phishing → return delete action, done.
│  Skip LLM classify: │  High-confidence heuristic → jump to step 3.
└────────┬────────────┘
         │ (only if heuristic is uncertain)
         ▼
┌─────────────────────┐
│  2. LLM Classify    │  Forced tool call with enum constraint.
│                     │  Categories: URGENT, NEEDS_RESPONSE, FYI,
│                     │  PROMOTIONAL, PERSONAL
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  3. LLM Summarise   │  Plain text completion, 1-2 sentences, ≤300 chars.
│                     │  Context-window-aware: content is truncated to fit
│                     │  within the model's budget before sending.
│                     │  Threads: newest message gets 60% of the budget,
│                     │  older messages share the rest newest-first.
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  4. Extract Actions │  Two-phase forced tool calls:
│                     │    Phase 1: select which action types apply
│                     │    Phase 2: fill action bodies in parallel
│                     │  Produces: send_reply, link, calendar_event,
│                     │  unsubscribe, delete, etc
│                     │  *This uses summary and context from email to generate the actions for better accuracy*
└─────────────────────┘
```

**Max 3 LLM calls per email.** Each prompt is focused on a single task — no tool-schema bloat across steps.

## Use Cases

### Single Email Triage

Classify and extract actions from a standalone email. The most common use case — feed in one email, get back a category, summary, and ranked action items.

### Thread Triage

Pass an array of messages (oldest to newest). The SDK summarises the full thread with context-window-aware budgeting, but classifies and extracts actions based on the newest message only — because that's what the recipient needs to act on.

### Batch Processing

Process up to 100 emails/threads in one call with configurable concurrency. Each item is triaged independently and tagged with `AsyncLocalStorage` so log lines identify which batch item produced them.

```js
const result = await agent.triageBatch({
  items: [emailInput1, emailInput2, ...],
  context: { people: ["Bob<bob@partner.io>", "Carol<carol@partner.io>"] },
});

// result.results is an array — each item is either:
//   { index, result }   — successful triage
//   { index, skipped }  — intentionally skipped (e.g. already replied)
//   { index, error }    — failed with error message
```

### Draft Reply Composition

For `URGENT`, `NEEDS_RESPONSE`, and `PERSONAL` emails, the SDK can compose a ready-to-send reply. The draft addresses every question raised, derives to/cc/bcc from the original headers, and matches the tone of the original.

The draft prompt explicitly prohibits inventing facts — it won't claim "this is a known issue" or promise an ETA unless the email says so. If you want to update how drafts sound, you can pass in writing samples to the agent via the `userContext` parameter for now. Ideally we can break this out to another config so it does not bloat the main context and only used when needed for reply drafting.

### Calendar Event Extraction

When an email contains meeting details, the SDK extracts a structured calendar event with title, start/end times, location, and RSVP status. Relative dates ("next Tuesday", "this Friday") are resolved against the triage date.

### Link Extraction

Actionable URLs are extracted and labelled with a description and CTA verb. The SDK validates every returned URL against the original email body — if the LLM hallucinates a URL that wasn't in the email, it's silently dropped.

### Document Signing Detection

Emails containing DocuSign, HelloSign, Adobe Sign, PandaDoc, SignNow, or eversign URLs are automatically escalated to URGENT with a direct "Sign" action. No LLM call needed — pure regex matching.

### Unsubscribe Extraction

For promotional emails, the SDK extracts unsubscribe URLs by matching against common opt-out path patterns (`/unsubscribe`, `/optout`, `/email-preferences`, etc.) before falling back to an LLM call.

## Edge Cases Handled in Code

These are behaviours the pipeline compensates for based on real-world email patterns we've encountered:

### Thread Skip: Principal Is Last Sender

When triaging a thread, if the principal (or any of their aliases) sent the most recent message, the thread is skipped with `{ kind: "skipped", reason: "already replied" }`. There's nothing to triage — the ball is in someone else's court.

### Transactional Email Recognition

Emails from 60+ known transactional domains (Stripe, PayPal, GitHub, Slack, AWS, DocuSign, Okta, etc.) are confidently classified as FYI without an LLM call. For threads from these domains, the heuristic still fires but defers to the LLM for confirmation — a human may have replied in the thread turning it into a real conversation now.

### Automated Sender Patterns

Senders matching `noreply@`, `no-reply@`, `notifications@`, `alerts@`, `system@`, etc. are classified as FYI. Same thread-deferral rule as transactional domains.

### Weak vs. Strong Promotional Signals

The heuristic distinguishes between strong promotional senders (`newsletter@`, `marketing@`, `deals@`) which are always PROMOTIONAL, and weak ones (`hello@`, `info@`, `updates@`) which only trigger PROMOTIONAL when combined with a promotional subject token or unsubscribe link in the body.

### Reply Category Gating

The action extraction step gates `send_reply` to only URGENT, NEEDS_RESPONSE, and PERSONAL categories. Even if the LLM selects "send_reply" for a PROMOTIONAL email, the gate strips it. Same for `calendar_event` — gated to URGENT, NEEDS_RESPONSE, PERSONAL, and FYI.

### Auto-Delete for Low-Value Categories

FYI and PROMOTIONAL emails that don't already have an explicit `delete` action get one appended automatically. URGENT, NEEDS_RESPONSE, and PERSONAL emails never get auto-delete — those categories represent mail the user likely wants to keep.

### Deterministic Action Ranking

Actions are ranked by category-specific priority order, not by the order the LLM returned them. For URGENT emails, `send_reply` always ranks first. For PROMOTIONAL, `unsubscribe` ranks first. The `primary_action` field always points to index 0 — the single best CTA for a compact UI.

### Context-Window-Aware Thread Summarisation

For threads that exceed the content budget, the newest message gets 60% of the token budget and older messages share the remainder in reverse chronological order. Messages that don't fit are either truncated with a `[... earlier message truncated ...]` marker or dropped entirely. This prevents the model from receiving more content than it can handle.

### Hallucinated URL Filtering

When extracting links, the SDK first extracts all URLs from the raw email body, then asks the LLM to label them. Any URL the LLM returns that wasn't in the original candidate set is dropped with a warning. This prevents the model from inventing plausible-looking URLs.

### Calendar Date Sanity

Calendar events with start times in the past are reset to "TBD". If only an end time is available, start is inferred as 1 hour before. If only a start is available, end is inferred as 1 hour after.

### Due Date Anchoring

Due dates are resolved against the message's sent date, not the triage date. This matters when processing emails days after they were sent — "by Friday" means the Friday after the email was written, not the Friday after you run triage. Due dates already in the past (relative to triage time) are discarded.

### Untrusted Input Sandboxing

All email body content is wrapped in `<<<UNTRUSTED_EMAIL_BODY_START>>>` / `<<<UNTRUSTED_EMAIL_BODY_END>>>` delimiters with explicit prompt instructions to treat everything inside as data only. This mitigates prompt injection from email content.

### Phishing Detection

Known phishing patterns ("verify your credentials", "confirm your password", "your account will be closed", etc.) trigger immediate high-confidence classification without an LLM call. The email is flagged `is_phishing: true` and returns a delete action.

### Recipient Role Awareness

The SDK detects whether the principal is a TO, CC, or BCC recipient and passes this context to the summariser and action extractor. CC'd recipients get summaries noting they're "being kept in the loop" rather than being the primary actor.

## Configuration

```js
const agent = new EmailTriageAgent({
  // Required
  baseUrl: "http://127.0.0.1:1234/v1",   // OpenAI-compatible endpoint
  model: "gemma-4-e4b",                   // Model name

  // LLM tuning
  apiKey: "sk-...",              // API key (default: "not-required")
  timeoutMs: 120_000,            // Per-request timeout (default: 120s)
  temperature: 0,                // Temperature (default: 0)
  maxTokens: 2048,               // Max completion tokens per request
  stop: ["\n\n"],                // Stop sequences

  // Pipeline behaviour
  contextWindowTokens: 32768,    // Model context window (default: 16384)
  forceLlmClassify: false,       // Always run LLM classify even when heuristic is confident
  now: "2026-07-27T00:00:00Z",   // Override "today" for date resolution
  userContext: "I'm a ...",      // Persona context appended to classify/action prompts
  maxActionItems: 5,             // Cap on returned actions (default: 5)
  aliases: ["alt@me.com"],       // Additional email addresses for the principal
  advancedUsage: false,          // Include per-step token breakdowns in usage
  batchConcurrency: 4,           // Max parallel emails in triageBatch (default: 1)
  debug: false,                  // Log LLM calls to stderr
});
```

## Project Structure

```
├── package.json
├── index.js              Public API — re-exports everything
├── core/
│   ├── agent.js          EmailTriageAgent class (pipeline orchestration)
│   ├── llm.js            LlmClient (OpenAI-compatible, 3 call shapes)
│   └── steps/
│       ├── classify.js   Step 1 — LLM classification (forced tool call)
│       ├── summarize.js  Step 2 — summarisation (text completion)
│       ├── actions.js    Step 3 — two-phase action extraction
│       └── due.js        Step 4 — due date extraction
└── utils/
    ├── types.js          JSDoc typedefs + constants (schema 2.2)
    ├── prompts.js        System prompts for each pipeline step
    ├── tools.js          OpenAI ChatCompletionTool schema definitions
    ├── heuristics.js     Rule-based pre-classifier + URL extractors
    └── rank.js           Deterministic action ranker
```