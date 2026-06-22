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

> **Frozen REST sidecar (JS/TS Path B below):** the standalone binary exposes
> only **triage and draft** — read/organize/send/calendar are full agent-loop
> features that require a connected mailbox (Path A).

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

JavaScript / TypeScript — two paths, depending on whether you want the full
agent or a lightweight local binary:

```bash
npm install @amd-gaia/agent-email
```

**Path A — full GAIA (live inbox, real send, all capabilities)**

Point `EmailClient` at a running GAIA server that mounts `/v1/email/*` (started
with `gaia api start` or `gaia chat --ui`). This gives you the complete feature
set — triage, draft, send, organize, calendar — backed by a connected Gmail or
Outlook.com mailbox.

```ts
// Browser apps: import from the browser-safe subpath (no Node built-ins).
// Node apps: "@amd-gaia/agent-email" works too.
import { EmailClient } from "@amd-gaia/agent-email/client";

// baseUrl is the GAIA API server that exposes /v1/email/*:
//   gaia api start   → http://localhost:8080  (default)
//   gaia chat --ui   → http://localhost:4200
const client = new EmailClient({ baseUrl: "http://localhost:8080" });

const { result } = await client.triage({
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
console.log(result.category, result.summary);

// Send goes through a draft → confirmation-token → send handshake.
// A send() without a valid confirmation token is rejected with HTTP 403.
```

**Path B — frozen sidecar (no Python required; triage + draft only)**

The sidecar is a self-contained native binary that runs inference locally via
Lemonade. It exposes only `POST /v1/email/triage` and `POST /v1/email/draft` —
there is no `/v1/connections`, and `send` cannot complete (no connected mailbox):
a direct `send()` returns **HTTP 403** (missing confirmation token), and even a
`/draft`-confirmed send returns **HTTP 503** (no connected mailbox). Use Path A
for anything that sends mail.

```ts
import { startSidecar, shutdown } from "@amd-gaia/agent-email";

// `fetchBinary()` is intentionally blocked until #1648 — the binaries.lock.json
// contains placeholder hashes and fetchBinary() throws a PlatformError.
// Build the sidecar locally instead:
//   python hub/agents/python/email/packaging/freeze.py
// Note: one-dir build (default) — binaryPath points at the executable INSIDE
// the dist directory, not the directory itself.
const binaryPath =
  "hub/agents/python/email/packaging/dist/email-agent/email-agent"; // .exe on Windows
const sidecar = await startSidecar({ binaryPath, port: 8131 });

const { result } = await sidecar.client.triage({
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
  // Full payload schema: see openapi.email.json (served live at GET /openapi.json)
});
console.log(result.category, result.summary);
// result.action_items[0].description  ← field is "description", not "text"

await shutdown(sidecar); // free function — not sidecar.shutdown()
```

**Consumer gotchas:**
1. **Absolute `file:` path** — when your app lives outside the package tree, use
   an absolute path in `package.json` (`file:/abs/path/to/agent-email`); a
   relative `file:` becomes a broken symlink in npm workspaces.
2. **One-dir `binaryPath`** — the default PyInstaller build produces a directory
   (`dist/email-agent/`); `binaryPath` must point at the executable *inside* it
   (`dist/email-agent/email-agent`), not at the parent directory.
3. **`description`, not `text`** — action items in `EmailTriageResult` use the
   field name `description` (e.g. `result.action_items[0].description`).
4. **Running the built binary** — the `freeze.py` binary is dynamically linked
   against the build host's glibc, so run it on the same machine or a newer OS.
   A binary built on glibc 2.39 fails to start on an older runtime (e.g.
   Debian 12 / `node:18` images ship glibc 2.36 → `GLIBC_2.38 not found`); a
   glibc ≥ 2.38 base (e.g. `node:22-trixie` / Ubuntu 24.04) works. When the
   consumer app runs in a container, the sidecar reaches a Lemonade on the host
   via `host.docker.internal` — set
   `LEMONADE_BASE_URL=http://host.docker.internal:13305` (this also satisfies
   the local-only allowlist).

The `/v1/email/*` routes are a versioned contract shared by the Python agent and
the frozen binary — `openapi.email.json` is the source of truth.

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
