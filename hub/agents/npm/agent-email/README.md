# @amd-gaia/agent-email

[![npm version](https://img.shields.io/npm/v/@amd-gaia/agent-email?label=version)](https://www.npmjs.com/package/@amd-gaia/agent-email) · contract `SCHEMA_VERSION` **2.2** · last updated **2026-07-01**

**Eval scorecard (v0.3.0): aggregate 84.67 / 100** — within-one-bucket **acceptance** accuracy (3-run mean, 95% CI [83.4, 86.0]) over 100 of 220 labeled emails ([`./SCORECARD.md`](./SCORECARD.md)). Triage priority is ordinal, so the bar (#1437) credits exact-or-adjacent buckets — what users feel — not exact 4-way match (reported as a secondary, 0.46). The linked scorecard carries the full recipe, metrics + reported secondaries, run-to-run variance/CI, a per-category breakdown, the run environment, a worked recomputation, and reproduction steps.

Embed the **GAIA email agent** in your JS/TS app. It triages, organizes, replies
to, and schedules from Gmail and Outlook — with every email body analyzed
**locally on AMD Ryzen AI** via Lemonade. No message content is sent to a cloud
LLM; local-only inference is enforced at startup.

This package is the **client**: a typed `EmailClient`, a build-time fetcher that
downloads and SHA-256-verifies the right native binary for your platform, and
helpers to spawn, health-check, and shut down the sidecar. It ships **no Python** —
the agent runs as a frozen, self-contained REST sidecar your app launches and owns.

> Using an AI coding assistant? This package ships a [`SKILL.md`](https://github.com/amd/gaia/blob/main/hub/agents/npm/agent-email/SKILL.md) — load
> it into Claude Code (or similar) for a focused, copy-paste integration playbook.

## What the agent does

- **Triage** — classify each message (urgent / needs-response / FYI / promotional /
  personal), summarize a thread, and extract action items and phishing signals.
- **Organize** — archive, label, move, mark read/unread — one message or in batches.
- **Reply & send** — draft context-aware replies and forwards, then send —
  attachments included (schema 2.2): triage exposes attachment metadata, and
  draft/send accept base64 file payloads.
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

Once it's running, any Node process can drive it over local HTTP. A browser or
Electron **renderer** reaches it through your app's main process — the sidecar is
same-origin only (no CORS), so a renderer can't call it cross-origin directly (see
[Browser / Electron](#browser--electron-renderer)).

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

### Triage many at once

`triageBatch` sends up to 100 emails or threads in a single request and returns a
parallel `results` array (one entry per item, order-preserved). It's additive — the
single `triage()` above is unchanged. Per-item failures are isolated, so an HTTP 200
can still carry errored items: inspect each `results[].error`, never just the status.

```ts
const batch = await sidecar.client.triageBatch({
  items: [
    {
      kind: "single",
      principal: { email: "me@example.com" },
      message: { message_id: "m1", from: { email: "sarah@example.com" },
        subject: "Prod incident", body: "Reply by Friday." },
    },
    {
      kind: "single",
      principal: { email: "me@example.com" },
      message: { message_id: "m2", from: { email: "promo@shop.example" },
        subject: "Sale", body: "Shop now." },
    },
  ],
});
for (const item of batch.results) {
  if (item.error) console.warn(`item ${item.index} failed: ${item.error.message}`);
  else console.log(`item ${item.index}:`, item.result!.category);
}
```

### Browser / Electron renderer

The `.` entry uses Node built-ins, so it can't be bundled for a browser or an
Electron renderer. The sidecar also serves **same-origin only — it sends no CORS
headers** — so a renderer on a different origin (`file://`, a dev server, an
`app://` scheme) **cannot call `http://127.0.0.1:8131` directly**; the browser
blocks it. Don't design around a direct renderer→sidecar fetch.

**Recommended (Electron):** spawn and own the sidecar in your **main** process via
the `.` entry, and expose `triage`/`draft` to the renderer over your own IPC. The
renderer never touches the sidecar.

The browser-safe `./client` entry (zero Node built-ins, so it bundles for a
renderer) is for the case where the renderer *does* have a same-origin or
app-proxied path to the sidecar:

```ts
import { EmailClient } from "@amd-gaia/agent-email/client";

// Only from a same-origin page, or behind a proxy you control — not a
// cross-origin fetch straight at 127.0.0.1:8131.
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
| `triageBatch(req)` | Local LLM only | Same as `triage`, but for an `items` array (1–100). Returns a parallel `results` array; per-item failures isolate (HTTP 200 can carry errored items — inspect `results[].error`). |
| `search(req)` | A connected Gmail/Outlook mailbox | Searches the connected inbox (**read-only**) by Gmail-style `query`/`labels` and returns message metadata — id, subject, sender, snippet, labels. No message is read in full or modified; no confirmation token needed. |
| `prescan(req?)` | A connected Gmail/Outlook mailbox | Reads recent inbox messages and returns the triage-card envelope (urgent / needs-response / suggested-archive rows + an informational count). Read-only — nothing is archived, marked, or sent. |
| `draft(req)` | Nothing external | Proposes a reply (`to` is a list of `{ email }` objects, not strings) and returns a single-use confirmation token. Optional `attachments` (schema 2.2): `{ filename, mime_type, content_base64 }` each, ≤ 25 MB decoded — the token binds to their content digests. |
| `send(req)` | A connected Gmail/Outlook mailbox + the token | Actually transmits the mail, attachments included. Must carry the exact attachment set the token was minted for — a swapped file or a smuggled extra is rejected with **403**. |
| `confirmAction(req)` | Nothing external | Mints a single-use token for a destructive action (`"archive"` / `"quarantine"`), bound to that exact `(action, message_id)`. |
| `archive(req)` | A connected mailbox + the token | Removes the message from the inbox. Returns a `batch_id` undo handle. |
| `unarchive(req)` | A connected mailbox | Restores an archived message within the 30s window (ungated — pass the `batch_id`). |
| `quarantine(req)` | A connected **Gmail** mailbox + the token | Applies the `GAIA_PHISHING_QUARANTINE` label + archives a phishing message. Refuses `is_phishing: false`; Gmail-only (an Outlook mailbox → 400, since label-undo can't reverse a folder move). |
| `unquarantine(req)` | A connected mailbox | Restores a quarantined message's prior labels within the 30s window (ungated — pass the `action_id`). |
| `listCalendarEvents(opts?)` | A connected mailbox + calendar scope | Views events on the primary calendar (read-only). Optional `timeMin`/`timeMax` RFC 3339 bounds; `provider` only when >1 account is connected. |
| `previewCalendarEvent(req)` | Nothing external | Mints a single-use confirmation token bound to a proposed event — the calendar analogue of `draft`. |
| `createCalendarEvent(req)` | A connected mailbox + calendar scope + the token | Creates the event. Requires the token from `previewCalendarEvent`; a missing/invalid token is rejected with **403** before any backend call. |
| `respondToCalendarEvent(req)` | A connected mailbox + calendar scope | RSVPs `accepted`/`declined`/`tentative` to an existing invite. |

**`triage`, `draft`, `confirmAction`, and `previewCalendarEvent` are standalone** — you
can build and verify those flows with zero connector setup. The read-only `search` and
`prescan` need a connected mailbox (they read the live inbox) but **no** confirmation
token (`503` with none connected, `400` with two). The mutating calls — `send`,
`archive`, `quarantine`, and `createCalendarEvent` — are different on two counts: each
requires a single-use confirmation token (call `draft` for `send`, `confirmAction` for
`archive`/`quarantine`, or `previewCalendarEvent` for `createCalendarEvent`; a
missing/invalid token is rejected with **403** before anything else), and each acts on
the mailbox **connected in GAIA on the host** (under *Settings → Connectors*) — so even
with a valid token, a headless server returns **HTTP 503** until a mailbox is connected.
`archive`/`quarantine` are reversible within a 30-second window via
`unarchive`/`unquarantine` (which are *not* gated — they restore, never destroy); the
calendar actions additionally need the account's calendar scope (a missing scope fails
loud with **403** and the reconnect CTA). For a server integration, treat the standalone
calls as your surface and handle the mailbox/calendar-mutating calls where a connector is
available.

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

As of `SCHEMA_VERSION` 2.2 this package exposes inbox **search** (read-only),
the **archive** / phishing-**quarantine** mailbox actions (+ their undo),
calendar **view / create / respond**, and **attachments** (#1542: triage
exposes metadata, draft/send accept files). The remaining mailbox **actions**
(label, move, mark read/unread) are part of the full agent but not yet exposed
through this package — see
[`SPEC.md`](https://github.com/amd/gaia/blob/main/hub/agents/npm/agent-email/SPEC.md) for the complete surface.

## Running in production

This is a long-lived local resource, not a per-request one. Wire it like this:

- **Fetch at build time, not per request.** `fetchBinary` does network I/O and
  SHA-256 verification — run it once in your build / postinstall step, ship the
  verified binary, then `resolveBinaryPath` at runtime.
- **Spawn once at boot.** Call `startSidecar` during app startup and hold the
  `Sidecar` handle for the process lifetime. Never spawn per request.
- **Keep concurrency low.** The sidecar accepts concurrent HTTP requests, but
  inference runs on a **single local Lemonade model slot** — parallel `triage`
  calls queue behind one another. Cap inflight calls (a small queue / concurrency
  limit) rather than fanning out.
- **Pick a port you own.** `port` is yours to choose; if it's already taken,
  `startSidecar` fails its health wait (`HealthTimeoutError`) — run with
  `DEBUG=agent-email` to see the bind error.
- **Supervise it yourself.** The package does not restart a crashed sidecar. If you
  need resilience, watch `sidecar.child` for `exit` and re-`startSidecar`.
- **Cleanup is automatic.** By default the sidecar is reaped when your process
  goes away — normal exit, `process.exit()`, an uncaught exception, or
  `SIGINT`/`SIGTERM`/`SIGHUP` (Ctrl+C) — so the frozen binary's detached child
  never leaks and never keeps holding its port. No signal wiring required. For a
  graceful, *awaited* shutdown that lets in-flight work finish, call
  `shutdown(sidecar)` (the automatic reaper is a `SIGKILL` backstop on top of it):

```ts
const sidecar = await startSidecar({ binaryPath, port: 8131 });
// … use sidecar.client …
await shutdown(sidecar); // optional & graceful; auto-cleanup also runs on exit
```

  To own the lifecycle yourself, pass `autoCleanup: false` and wire the signals.
  (A hard `SIGKILL` of your process can't be intercepted by anyone, so the safest
  guarantee is the default in-process reaper.)

```ts
const sidecar = await startSidecar({ binaryPath, port: 8131, autoCleanup: false });
const reap = (code = 0) => shutdown(sidecar).finally(() => process.exit(code));
for (const sig of ["SIGTERM", "SIGINT"]) process.once(sig, () => reap(0));
```

### Errors & retries

Every non-2xx throws `HttpError` (`status`, `url`, `bodyText`); a network failure or
timeout surfaces as `HttpError` with `status === 0` (not an HTTP code).

| Failure | Meaning | Retry? |
|---------|---------|--------|
| `HttpError` 502 (from `triage`) | Lemonade is down or the model is cold/missing | **Yes** — transient; back off and retry |
| `HttpError` 0 (network/timeout) | Sidecar not reachable / crashed | **Yes** — after re-spawning the sidecar |
| `HttpError` 403 (from `send` / `archive` / `quarantine`) | Missing/invalid confirmation token | **No** — call `draft` (for `send`) or `confirmAction` (for `archive`/`quarantine`) first and pass its token |
| `HttpError` 503 (from `send` / `archive` / `quarantine`) | No mailbox connected on the host | **No** — configuration, not transient |
| `HttpError` 409 (from `unarchive` / `unquarantine`) | Undo window expired or handle unknown | **No** — past the 30s window; restore manually in the mail client |
| `HttpError` 400 | Bad request, 2+ mailboxes connected without a provider, or `quarantine` with `is_phishing: false` | **No** — fix the call/config |
| `HealthTimeoutError` / `VersionMismatchError` / `IntegrityError` | Startup faults (port taken, contract mismatch, bad binary) | **No** — fail fast at boot |

## Playground

One command fetches the binary, starts the sidecar, and opens the playground:

```bash
npx @amd-gaia/agent-email playground
```

It serves a zero-setup, **localhost-only** page at
[http://127.0.0.1:8131/v1/email/playground](http://127.0.0.1:8131/v1/email/playground)
— a stack-health check, live triage and draft, and a Connectors panel to connect
Gmail/Outlook and try a live send. It's served same-origin under a strict CSP, so
the page can only ever reach your local sidecar: triage and draft stay on-device,
while a `send` transmits to your mail provider by definition. Press Ctrl+C to stop
(`--port <n>` to bind elsewhere, `--no-open` to skip auto-opening the browser,
`--out <dir>` to choose where the binary is cached).

## Requirements

- **Platforms:** Windows x64, Linux x64, macOS Apple Silicon (`darwin-arm64`).
- **Memory:** 8 GB RAM — sized for the default local model (`Gemma-4-E4B-it-GGUF`)
  running in Lemonade, not the sidecar itself.
- **Lemonade:** the sidecar calls a local Lemonade endpoint (default
  `http://localhost:13305`); cloud hosts are rejected at startup.
- **Footprint:** one self-contained native binary (~30–45 MB depending on
  platform), no Python runtime. (Model weights are downloaded and managed
  separately by Lemonade.)

## Troubleshooting

| Symptom | Cause & fix |
|---------|-------------|
| `triage()` returns **HTTP 502** | Lemonade isn't running or the model isn't pulled. Start it (`lemonade-server serve`) and provision the model (`gaia init`). Not a bug in this package. |
| `/health` is green but `triage` fails | `health()` is **liveness-only** — it doesn't check Lemonade or the model. The real readiness signal is a `triage` returning 200. |
| `npm install` fails with `UNABLE_TO_GET_ISSUER_CERT` | Corporate TLS proxy. Reinstall with Node's system CA store: `NODE_OPTIONS=--use-system-ca npm install` (Node ≥ 22). |
| `require(...)` throws `ERR_REQUIRE_ESM` | The package is ESM-only. Use `import`, or `await import("@amd-gaia/agent-email")` from CommonJS. |
| Sidecar process lingers after exit | Auto-cleanup reaps it on exit/crash/signal by default; a lingering sidecar means `autoCleanup: false` (call `shutdown(sidecar)` yourself) or a hard `SIGKILL` of the host. |
| `IntegrityError` / `VersionMismatchError` on start | The downloaded binary failed its SHA-256 check, or its contract MAJOR differs from the client. Clear `resources/` and re-`fetchBinary`, and make sure the package version matches the binary. |

Set `DEBUG=agent-email` for verbose spawn/fetch/health logs (on stderr).

## Reference

- [`SPEC.md`](https://github.com/amd/gaia/blob/main/hub/agents/npm/agent-email/SPEC.md) — full API, lifecycle helpers, connectors, module format, and platforms.
- [`SKILL.md`](https://github.com/amd/gaia/blob/main/hub/agents/npm/agent-email/SKILL.md) — load into Claude Code (or similar) for a step-by-step integration playbook.
- [`CHANGELOG.md`](https://github.com/amd/gaia/blob/main/hub/agents/npm/agent-email/CHANGELOG.md) — version history.

## License

Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: MIT
