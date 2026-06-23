# GAIA Email Agent

A full email-management agent that **reads, triages, organizes, replies, and
schedules** across your Gmail and Outlook.com mailboxes — with every email body
analyzed **locally on AMD Ryzen AI** via Lemonade. No message content is sent to
a cloud LLM; local-only inference is enforced at startup.

## What it does

- **Triage & prioritize** — scans your inbox and classifies each message
  (`urgent`, `needs-response`, `FYI`, `promotional`, `personal`), then builds a
  morning-brief pre-scan of what to act on, what to read, and what to clear.
  Teach it priority and low-priority senders, or a default action per category,
  and it remembers them for the session.
- **Read & search** — list, open, and search threads; summarize a single
  message or an entire thread down to the essentials.
- **Organize at scale** — archive, label, move, and mark read/unread —
  one message or in batches, with one-step **undo** for batch actions.
- **Reply, forward & send** — draft context-aware replies and forwards, then
  send. Anything that leaves your mailbox — **send, forward, permanent delete,
  and calendar RSVPs** — asks for confirmation first.
- **Calendar** — detect meeting requests, flag scheduling conflicts, RSVP to
  invites, and create events straight from an email.
- **Stay safe** — flag and quarantine likely phishing. Email bodies are always
  treated as untrusted **data, never instructions**, so a malicious message
  can't hijack the agent.

Works with **Gmail** (Google connector) and **Outlook.com** (Microsoft
connector); connect one or both and the agent triages them together.

## Use it

### In GAIA (no setup)

Install from the [Agent Hub](https://amd-gaia.ai/hub/email) — or open it
directly in the GAIA desktop app — then ask in plain language:

```bash
gaia email --query "What needs my attention this morning?"
gaia email --interactive
```

First run prompts you to connect a mailbox; do it once with `gaia connectors`
and the agent is grant-checked for access from then on.

### For developers

Python — install the agent into your GAIA environment:

```bash
pip install gaia-agent-email
```

It registers via the `gaia.agent` entry-point group, so the GAIA registry
discovers it automatically and exposes its REST (`/v1/email/*`) and MCP (stdio)
surfaces.

JavaScript / TypeScript — embed the agent as a local REST sidecar (no Python
required) with the companion npm client:

```bash
npm install @amd-gaia/agent-email
```

```ts
import { fetchBinary, startSidecar } from "@amd-gaia/agent-email";

const { binaryPath } = await fetchBinary({ outDir: "resources" });
const sidecar = await startSidecar({ binaryPath, port: 8131 });
const brief = await sidecar.client.triage({ /* … */ });
```

The package downloads and SHA-256-verifies the right native binary for your
platform, then spawns and health-checks the sidecar. The `/v1/email/*` routes
are a versioned contract shared by the Python agent and the frozen binary —
`openapi.email.json` is the source of truth.

## Playground

A zero-setup, **localhost-only** page the sidecar serves at
`http://127.0.0.1:8131/v1/email/playground` — a stack-health check (is Lemonade
up? is the model downloaded?), live **triage** and **draft** against the running
sidecar, a button that runs the `/v1/email/init` readiness check, and copy-paste
install shortcuts.

**Connect a mailbox + live send (opt-in).** Start the sidecar with
`--playground` (or `GAIA_EMAIL_PLAYGROUND=1`) to add a **Connectors** panel:
paste your own Google/Microsoft OAuth client credentials, connect Gmail/Outlook
(the same flow `gaia connectors` uses), and the **Send** panel goes live. These
connector routes are mounted **only** in playground mode — a default/production
sidecar stays connector-free, since the consuming application owns the mailbox
connection. Without the flag the panel explains how to enable it.

![GAIA Email Agent playground — stack health, live triage/draft, and a readiness check, all running against the local sidecar](https://hub.amd-gaia.ai/agents/email/0.2.0/playground.webp)

It's served same-origin with a `Content-Security-Policy: connect-src 'self'`
header, so the page can only ever reach your local sidecar — email content never
leaves the machine. Start the sidecar and open the URL in a browser.

## Requirements

- **Platforms:** Windows x64, Linux x64, and macOS on Apple Silicon
  (`darwin-arm64`).
- **Memory:** 8 GB RAM minimum.
- **GAIA:** 0.20.0 or newer (depends on `amd-gaia>=0.20.0`).
- **Inference backend:** [Lemonade](https://amd-gaia.ai/docs) — GAIA's local
  LLM server — running on your AMD Ryzen AI machine. The agent only ever calls a
  local Lemonade endpoint (default `http://localhost:13305`); cloud hosts are
  rejected at startup.
- **Model:** Gemma 4 E4B (`Gemma-4-E4B-it-GGUF`), GAIA's default local model.
  It runs on CPU / integrated GPU and is downloaded once by Lemonade. Point the
  agent at any other local Lemonade model via `--model` / `LEMONADE_BASE_URL`.
- **Footprint:** the standalone build ships as a single native binary — about
  31 MB, with no Python runtime required. The Python package is a thin wheel on
  top of the `amd-gaia` framework. (The Gemma model weights are managed
  separately by Lemonade.)
- **Mailbox connector:** Google (Gmail) and/or Microsoft (Outlook.com),
  connected through `gaia connectors`.

## Develop & test

```bash
pip install -e "hub/agents/python/email[test]"
pytest hub/agents/python/email/tests/ -x
```

## License

Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: MIT
