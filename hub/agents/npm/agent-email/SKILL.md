---
name: integrate-agent-email
description: Use when integrating the @amd-gaia/agent-email npm package — embedding the GAIA email agent (a local triage/draft/send sidecar) into a Node, TypeScript, or Electron app. Covers install, spawning the sidecar, calling the typed client, prerequisites, and the common gotchas.
---

# Integrating @amd-gaia/agent-email

`@amd-gaia/agent-email` embeds the GAIA email agent in a JS/TS app. It triages,
drafts, and sends email **locally on AMD Ryzen AI** — no cloud LLM. This package is
the **client**: it downloads a frozen native **sidecar** binary, spawns it, and
talks to it over local HTTP. There is **no Python** and no separate GAIA install.

Follow these steps to wire it into an app.

## 1. Install

```bash
npm install @amd-gaia/agent-email
```

The package is **ESM-only** (`"type": "module"`). Use `import`, not `require`. From
a CommonJS file, use `await import("@amd-gaia/agent-email")`.

## 2. Pick the right entry point

- **Node / main process** → the default entry `@amd-gaia/agent-email`. It can
  fetch the binary and spawn/own the sidecar (uses `node:fs`, `node:child_process`).
- **Browser / Electron renderer** → the `@amd-gaia/agent-email/client` subpath. It
  has zero Node built-ins and only talks to an already-running sidecar over HTTP.

The desktop pattern: spawn the sidecar **once** from the Node/main process, then
drive it from the renderer via `./client`.

## 3. Fetch the binary and start the sidecar (Node)

```ts
import { fetchBinary, startSidecar, shutdown } from "@amd-gaia/agent-email";

// Build time (or first run): download + SHA-256-verify the platform binary.
const { binaryPath } = await fetchBinary({ outDir: "resources" });

// Runtime: spawn -> wait for /health -> version-check, in one call.
const sidecar = await startSidecar({ binaryPath, port: 8131 });

// ... use sidecar.client ...

await shutdown(sidecar); // graceful stop — auto-cleanup also reaps on exit
```

- `fetchBinary` writes a verified binary into `outDir`. SHA-256 is mandatory; a
  bad download is rejected and not left on disk. Run it at build time or guard it
  to run once.
- `startSidecar` throws if the binary can't start, never becomes healthy, or the
  contract MAJOR version mismatches — and cleans up so a failed start leaks nothing.
- The sidecar is auto-reaped when your process exits, crashes, or is signalled
  (default `autoCleanup`), so a missed `shutdown` won't orphan the frozen binary's
  child. `shutdown(sidecar)` is the graceful, awaited stop; `autoCleanup: false` opts out.

## 4. Call the typed client

```ts
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
```

The interface:

| Call | Needs | Notes |
|------|-------|-------|
| `triage(req)` | Local LLM only | Classify / summarize / extract action items + phishing signals on the message you pass. No mailbox read. |
| `search(req)` | A connected mailbox | Read-only inbox search by `query`/`labels`; returns message metadata (id, subject, sender, snippet, labels), no body. No token. No mailbox → 503, two+ → 400. |
| `draft(req)` | Nothing external | Returns a single-use confirmation token. |
| `send(req)` | Draft token + a connected mailbox | Gate fires first: no/invalid `draft` token → 403; valid token but no mailbox connected on the host → 503. |
| `confirmAction(req)` | Nothing external | Mints a single-use token for `"archive"`/`"quarantine"`, bound to the `(action, message_id)`. |
| `archive(req)` | `confirm` token + a connected mailbox | Removes from inbox. Gate fires first (no/invalid token → 403). Returns a `batch_id` undo handle (+ `post_archive_id` for the Outlook id change). |
| `unarchive(req)` | A connected mailbox | Restores within the 30s window (ungated — pass `batch_id`); expired/unknown → 409. |
| `quarantine(req)` | `confirm` token + a connected **Gmail** mailbox | Applies `GAIA_PHISHING_QUARANTINE` + archives a phishing message. Refuses `is_phishing:false` → 400; Gmail-only (Outlook → 400). |
| `unquarantine(req)` | A connected mailbox | Restores prior labels within the 30s window (ungated — pass `action_id`); expired/unknown → 409. |

**Build the `triage` / `draft` / `confirmAction` flow with zero connector setup**;
`search` / `send` / `archive` / `quarantine` (and their reversals) need a connected
mailbox (`search` reads the live inbox but takes no token). Mint the gate token with
`draft` (for `send`) or `confirmAction` (for `archive` / `quarantine`); `archive` and
`quarantine` are reversible inside a 30s window via the ungated `unarchive` /
`unquarantine`. Every non-2xx response throws `HttpError` (`status`, `url`, `bodyText`)
— handle it; there is no silent null.

## 5. From a renderer (Electron / browser)

The sidecar serves **same-origin only — no CORS**. A renderer on a different origin
**cannot** fetch `http://127.0.0.1:8131` directly; the browser blocks it. So:

- **Recommended:** spawn the sidecar in the Electron **main** process (step 3) and
  expose `triage`/`draft` to the renderer over your own IPC. Don't call the sidecar
  from the renderer directly.
- The `./client` entry (zero Node built-ins) is only usable from a **same-origin or
  proxied** page:

```ts
import { EmailClient } from "@amd-gaia/agent-email/client";
const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131" });
```

## Running in a server / long-lived app

- **`fetchBinary` is a build step**, not per request (network + SHA verify). Run it
  once; `resolveBinaryPath` at runtime.
- **Spawn once at boot**, hold the `Sidecar` handle for the process lifetime — never
  per request.
- **Low concurrency.** One local Lemonade model slot, so parallel `triage` calls
  serialize. Cap inflight calls.
- **Cleanup is automatic** (default `autoCleanup`): the sidecar's child is reaped on
  exit/crash/signal. Call `shutdown` for a graceful stop, or `autoCleanup: false` to
  wire signals yourself. The package does not restart a crashed sidecar.

## Prerequisites — the agent needs a local model

The sidecar runs the LLM via **Lemonade Server**, which this package does **not**
install. Before `triage`/`draft`/`send` succeed, the host must have:

1. A running Lemonade Server (`lemonade-server serve`).
2. The model pulled (`gaia init` installs Lemonade and downloads the default model).

Until then the binary boots, but the first `triage` returns **HTTP 502**.

## Gotchas (read before debugging)

- **`health()` is liveness-only.** A green `/health` means the REST surface is up,
  NOT that triage will work. The real readiness signal is a `triage` returning 200.
- **HTTP 502 from `triage`** → Lemonade isn't running/reachable, or the model isn't
  pulled. It is not a bug in this package.
- **Addresses are objects, not strings.** `to` (and `triage`'s `from` / `principal`)
  are `{ email, name? }`; `to` is a non-empty array of them. A plain string → 422.
- **`send` needs the draft `confirmation_token`** (missing/invalid → 403), but it
  takes **no OAuth token** — the mailbox is resolved from the host's GAIA connector
  store (no mailbox connected → 503). Triage and draft need no connector.
- **`archive` / `quarantine` are gated like `send`**, but their token comes from
  `confirmAction` (not `draft`) and is bound to the `(action, message_id)` — a token
  for one can't authorize the other. Undo with `unarchive` (pass the returned
  `batch_id`) / `unquarantine` (pass the `action_id`) **within 30s**; past the window
  the reversal returns **409** (restore manually in the mail client). For Outlook,
  use the `post_archive_id` from the archive response — the folder move changes the id.
- **Cleanup is automatic by default** — the sidecar is reaped on exit/crash/signal;
  only `autoCleanup: false` (or a hard `SIGKILL` of your process) can orphan the
  child. `shutdown` stays the graceful stop.
- **ESM-only.** `require("@amd-gaia/agent-email")` fails; use `import` / dynamic
  `import()`.

## Verify the integration

A green path looks like: `fetchBinary` succeeds → `startSidecar` resolves →
`client.triage(...)` returns a `result` with a `category` and `summary`. If
`triage` 502s, start Lemonade and pull the model, then retry — the rest of your
integration is fine.

To eyeball the agent by hand without writing any code, run
`npx @amd-gaia/agent-email playground` — it fetches the binary, starts the sidecar,
and opens an interactive page where you can fire triage/draft and see a stack-health
check.

For the full endpoint list, lifecycle internals, and connector details, see
`SPEC.md` next to this file.
