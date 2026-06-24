# @amd-gaia/agent-email

[![npm version](https://img.shields.io/npm/v/@amd-gaia/agent-email?label=version)](https://www.npmjs.com/package/@amd-gaia/agent-email) · contract `SCHEMA_VERSION` **2.0** · last updated **2026-06-24**

Embed the **GAIA email agent** in your JS/TS app. It triages, organizes, replies
to, and schedules from Gmail and Outlook — with every email body analyzed
**locally on AMD Ryzen AI** via Lemonade. No message content is sent to a cloud
LLM; local-only inference is enforced at startup.

This package is the **client**: a typed `EmailClient`, a build-time fetcher that
downloads and SHA-256-verifies the right native binary for your platform, and
helpers to spawn, health-check, and shut down the sidecar. It ships **no Python** —
the agent runs as a frozen, self-contained REST sidecar your app launches and owns.

> Using an AI coding assistant? This package ships a [`SKILL.md`](./SKILL.md) — load
> it into Claude Code (or similar) for a focused, copy-paste integration playbook.

## What the agent does

- **Triage** — classify each message (urgent / needs-response / FYI / promotional /
  personal), summarize a thread, and extract action items and phishing signals.
- **Organize** — archive, label, move, mark read/unread — one message or in batches.
- **Reply & send** — draft context-aware replies and forwards, then send.
- **Calendar** — detect meeting requests, flag conflicts, RSVP, and create events
  from an email.
- **Safe by construction** — email bodies are treated as untrusted **data, never
  instructions**, and anything that leaves the mailbox (send, delete, RSVP) asks for
  confirmation.

Triage and draft analyze the content you pass and need only the local LLM. Reading
or acting on a live mailbox (send, archive, label, calendar) goes through the
**Google or Microsoft connector** (OAuth, configured in GAIA).

## How it works

![@amd-gaia/agent-email architecture — your Node code spawns and drives a frozen email-agent sidecar (127.0.0.1:8131) over HTTP, which runs triage inference on a local Lemonade Server; a browser/Electron renderer drives the same sidecar over HTTP via the ./client entry.](https://raw.githubusercontent.com/amd/gaia/main/hub/agents/npm/agent-email/assets/architecture.webp)

Three tiers, all on the user's machine — no cloud, no separate GAIA install:

- **Your app** (Node) fetches and spawns the sidecar via the `.` entry and owns its
  lifecycle (`shutdown()` tears it down).
- **The sidecar** is a frozen `email-agent` binary serving the email REST API — no
  Python on the host.
- **Lemonade Server** is the one runtime dependency: the sidecar calls a **local**
  Lemonade for inference. With none reachable, `triage` returns HTTP 502.

Once it's running, anything that speaks HTTP can drive it — including a browser or
Electron renderer via the [`./client`](#browser--electron-renderer) entry. You only
need Node to *launch* the binary, not to *talk to* it.

## Install

```bash
npm install @amd-gaia/agent-email
```

> **Corporate TLS:** if install fails with `UNABLE_TO_GET_ISSUER_CERT` behind a
> proxy, use Node's system CA store: `NODE_OPTIONS=--use-system-ca npm install`
> (Node ≥ 22).

## Quick start

```ts
import { fetchBinary, startSidecar, shutdown } from "@amd-gaia/agent-email";

// Build-time: download + SHA-256-verify the binary for this platform.
const { binaryPath } = await fetchBinary({ outDir: "resources" });

// Runtime: spawn -> wait for /health -> version-check.
const sidecar = await startSidecar({ binaryPath, port: 8131 });

// Drive it with the typed client.
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

await shutdown(sidecar); // kills the whole process tree
```

### Browser / Electron renderer

The `.` entry uses Node built-ins to fetch and spawn the binary, so it can't be
bundled for a browser or an Electron renderer. Spawn the sidecar once from your
**main/Node** process, then drive it from the renderer over HTTP via the
zero-Node-dependency `./client` entry:

```ts
import { EmailClient } from "@amd-gaia/agent-email/client";

const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131" });
const res = await client.triage({ payload: { /* … */ } });
```

## Prerequisites

The package fetches and spawns the binary, but it does **not** provision the model.
Before any LLM call succeeds, the host needs:

1. **A running Lemonade Server** — `lemonade-server serve`.
2. **The model pulled** — via `gaia init` (installs Lemonade and downloads the
   default model, `Gemma-4-E4B-it-GGUF`).

On a fresh machine the binary boots fine, but the first `triage` returns **HTTP
502** until Lemonade and the model are in place. `health()` is **liveness-only** — a
green `/health` means the REST surface is up, not that triage will work.

## Interface

You drive everything through the typed `EmailClient` — `sidecar.client` after
`startSidecar`, or constructed directly against a running sidecar (see the renderer
example above). Every non-2xx response throws `HttpError` (with `status`, `url`,
`bodyText`) — never a silent null.

| Call | Needs | Does |
|------|-------|------|
| `triage(req)` | Local LLM only | Classifies the message you pass, summarizes it, and extracts action items + spam/phishing signals. No mailbox is read. |
| `draft(req)` | Nothing external | Wraps your `(to, subject, body)` and returns a single-use confirmation token. |
| `send(req)` | A connected Gmail/Outlook mailbox + the token | Actually transmits the mail. |

**Everything except `send` is standalone** — you can build and verify the whole
flow with zero connector setup. `send` uses the mailbox connected in GAIA on the
host (configured under *Settings → Connectors*); there is no way to pass a token
through the API, so it only works where the mailbox is already connected.

### Lifecycle

`startSidecar({ binaryPath, port })` runs spawn → health-check → version-check in
one call and cleans up if any step fails (so a failed start never leaks a process).
When you need finer control, the steps are exported individually:

| Helper | Purpose |
|--------|---------|
| `fetchBinary(opts)` | Download + SHA-256-verify the platform binary (build time). |
| `spawnSidecar(opts)` | Launch the binary on `127.0.0.1:<port>` (default `8131`). |
| `waitForHealth(baseUrl)` | Poll `/health` until live; throws on timeout. |
| `checkVersion(client)` | Throw if the sidecar's contract MAJOR differs from the client's. |
| `shutdown(sidecar)` | Kill the whole process tree. |

Mailbox and calendar **actions** beyond send (read, archive, label, RSVP, create
events) are part of the full agent but not yet exposed through this package — see
[`SPEC.md`](./SPEC.md) for the complete surface.

## Playground

Once the sidecar is running, open
[http://127.0.0.1:8131/v1/email/playground](http://127.0.0.1:8131/v1/email/playground)
— a zero-setup, **localhost-only** page with a stack-health check, live triage and
draft, and a Connectors panel to connect Gmail/Outlook and try a live send. It's
served same-origin under a strict CSP, so email content never leaves the machine.

## Requirements

- **Platforms:** Windows x64, Linux x64, macOS Apple Silicon (`darwin-arm64`).
- **Memory:** 8 GB RAM.
- **Lemonade:** the sidecar calls a local Lemonade endpoint (default
  `http://localhost:13305`); cloud hosts are rejected at startup.
- **Footprint:** one ~31 MB native binary, no Python runtime. (Model weights are
  managed separately by Lemonade.)

## Troubleshooting

| Symptom | Cause & fix |
|---------|-------------|
| `triage()` returns **HTTP 502** | Lemonade isn't running or the model isn't pulled. Start it (`lemonade-server serve`) and provision the model (`gaia init`). Not a bug in this package. |
| `/health` is green but `triage` fails | `health()` is **liveness-only** — it doesn't check Lemonade or the model. The real readiness signal is a `triage` returning 200. |
| `npm install` fails with `UNABLE_TO_GET_ISSUER_CERT` | Corporate TLS proxy. Reinstall with Node's system CA store: `NODE_OPTIONS=--use-system-ca npm install` (Node ≥ 22). |
| `require(...)` throws `ERR_REQUIRE_ESM` | The package is ESM-only. Use `import`, or `await import("@amd-gaia/agent-email")` from CommonJS. |
| Sidecar process lingers after exit | Always call `shutdown(sidecar)` — it kills the whole process tree (the frozen binary spawns a child). |
| `IntegrityError` / `VersionMismatchError` on start | The downloaded binary failed its SHA-256 check, or its contract MAJOR differs from the client. Clear `resources/` and re-`fetchBinary`, and make sure the package version matches the binary. |

Set `DEBUG=agent-email` for verbose spawn/fetch/health logs (on stderr).

## Reference

- [`SPEC.md`](./SPEC.md) — full API, lifecycle helpers, connectors, module format, and platforms.
- [`SKILL.md`](./SKILL.md) — load into Claude Code (or similar) for a step-by-step integration playbook.
- [`CHANGELOG.md`](./CHANGELOG.md) — version history.

## License

Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: MIT
