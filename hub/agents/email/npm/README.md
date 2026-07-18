# @amd-gaia/agent-email

[![npm version](https://img.shields.io/npm/v/@amd-gaia/agent-email?label=version)](https://www.npmjs.com/package/@amd-gaia/agent-email)

Sorts your Gmail or Outlook inbox into urgent / needs-reply / FYI, pulls out
action items, and drafts replies — all running **locally on your machine**, so no
email content ever leaves it.

You embed it in a JavaScript or TypeScript app. Every email is analyzed on-device
by a local AI model (via AMD's Lemonade runtime); message content is never sent to
a cloud service, and that's enforced when the agent starts up.

> Using an AI coding assistant? This package ships a
> [`SKILL.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/email/npm/SKILL.md)
> — load it into Claude Code (or similar) for a copy-paste integration playbook.

## What it can do

- **Triage** — sort each message into urgent, needs-reply, FYI, promotional, or
  personal; summarize a thread; and extract the action items and any phishing or
  spam signals.
- **Organize** — archive, label, and move messages, one at a time or in batches.
- **Reply & send** — draft context-aware replies (optionally in your own writing
  style, learned locally from your Sent mail) and send them — with attachments.
  Anything that leaves your mailbox asks for confirmation first.
- **Calendar** — spot meeting requests, flag conflicts, RSVP, and create events
  from an email.
- **Track follow-ups** — flag replies you're still waiting on past a window you
  choose (it points them out; it never nudges anyone for you).
- **Daily briefing** — generate a morning inbox summary on a schedule, no prompt
  needed.
- **Plain-language requests** — describe what you want done ("find today's
  urgent mail and archive the promotions") and the agent chains the steps
  itself, streaming progress; runs are cancellable mid-way.

## Prerequisites

A local AI model has to be running before triage or drafting works:

1. Install and start it with **`gaia init`** (downloads the default model) and
   **`lemonade-server serve`**.
2. On a fresh machine the agent still starts, but triage won't return results
   until that local model is up. Call `client.init()` to check readiness.

You'll need about 8 GB of RAM for the default model, and one of: Windows x64,
Linux x64, or macOS Apple Silicon.

## Install

```bash
npm install @amd-gaia/agent-email
```

> **Behind a corporate proxy?** If install fails with `UNABLE_TO_GET_ISSUER_CERT`,
> reinstall with `NODE_OPTIONS=--use-system-ca npm install` (Node ≥ 22).

## Quick start

Triage one email — get back a category and a summary:

```ts
import { fetchBinary, startSidecar, shutdown } from "@amd-gaia/agent-email";

// Once, at build time: download and verify the agent for your platform.
const { binaryPath } = await fetchBinary({ outDir: "resources" });

// At startup: launch the local agent and hold onto the handle.
const sidecar = await startSidecar({ binaryPath, port: 8131 });

const res = await sidecar.client.triage({
  payload: {
    kind: "single",
    principal: { email: "me@example.com" },
    message: {
      message_id: "m1",
      from: { name: "Sarah Chen", email: "sarah@example.com" },
      subject: "Prod incident follow-up",
      body: "Please review the report and reply by Friday.",
    },
  },
});

console.log(res.result.category, res.result.summary);
// e.g. "NEEDS_RESPONSE  Sarah asks you to review the report and reply by Friday."

await shutdown(sidecar);
```

Or hand the agent a plain-language request and watch it work — `query()` streams
typed progress events (`status`, `tool_call`, `tool_result`, …) and ends with the
answer; `cancelQuery()` stops a run mid-way:

```ts
const runId = crypto.randomUUID(); // yours to mint — it's also the cancel handle
for await (const ev of sidecar.client.query({
  query: "Find today's urgent mail and archive the promotions.",
  run_id: runId,
  context: [],
})) {
  if (ev.type === "status") console.log(ev.message);
  if (ev.type === "final") console.log(ev.answer); // last event
}
```

Triage classifies and drafts using only the local model — no mailbox connection
needed. Reading or acting on a live inbox (search, send, archive, calendar) uses
the **Google or Microsoft connector** you set up in GAIA under
*Settings → Connectors*. (Inside the full GAIA Agent UI daemon the connector token
is forwarded to the agent by the daemon — sidecar contract 2.5, #2154; a standalone
integrator using this package is unaffected and resolves the mailbox from the local
GAIA connector store.)

Want to try it without writing code? Run `npx @amd-gaia/agent-email playground`
for a local page to test triage, drafting, and a live send.

## How it works

Three pieces, all on your own machine — no cloud, no separate GAIA install:

- **Your app** launches the agent and owns its lifetime.
- **The agent** is a single self-contained program (~30–45 MB, no Python) that
  serves a small local API.
- **The local model** does the actual thinking; the agent talks to it over your
  machine's local network only.

Full architecture, the complete API, authentication, and every endpoint are in
[`SPEC.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/email/npm/SPEC.md).

## How good is the triage?

Scores **83.4 / 100** on a labeled benchmark inbox — see the **Scorecard** tab (or
[`SCORECARD.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/email/npm/SCORECARD.md))
for the full breakdown, and the **Evaluation** tab for how it's measured.

## Reference

- [`SPEC.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/email/npm/SPEC.md) — full API, authentication, lifecycle, connectors, and platforms.
- [`SKILL.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/email/npm/SKILL.md) — integration playbook for AI coding assistants.
- [`SCORECARD.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/email/npm/SCORECARD.md) / [`EVALUATION.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/email/npm/EVALUATION.md) — eval results and how they're measured.
- [`CHANGELOG.md`](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/email/npm/CHANGELOG.md) — what's new in each version.

## License

Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: MIT
