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

await shutdown(sidecar); // kills the whole process tree — always call this
```

- `fetchBinary` writes a verified binary into `outDir`. SHA-256 is mandatory; a
  bad download is rejected and not left on disk. Run it at build time or guard it
  to run once.
- `startSidecar` throws if the binary can't start, never becomes healthy, or the
  contract MAJOR version mismatches — and cleans up so a failed start leaks nothing.
- Always `shutdown(sidecar)` on exit. The frozen binary spawns a child; only the
  process-tree kill in `shutdown` reaps it.

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
| `draft(req)` | Nothing external | Returns a single-use confirmation token. |
| `send(req)` | A connected mailbox + the token | Needs a Gmail/Outlook mailbox connected in GAIA on the host. |

**Build everything except `send` with zero connector setup.** Every non-2xx
response throws `HttpError` (`status`, `url`, `bodyText`) — handle it; there is no
silent null.

## 5. From a renderer (Electron / browser)

```ts
import { EmailClient } from "@amd-gaia/agent-email/client";

const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131" });
const res = await client.triage({ payload: { /* … */ } });
```

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
- **`send` has no token parameter.** It uses the mailbox connected in GAIA on the
  host; you cannot pass OAuth through the API. Triage and draft work anywhere.
- **Always `shutdown`** — otherwise the sidecar's child process is orphaned.
- **ESM-only.** `require("@amd-gaia/agent-email")` fails; use `import` / dynamic
  `import()`.

## Verify the integration

A green path looks like: `fetchBinary` succeeds → `startSidecar` resolves →
`client.triage(...)` returns a `result` with a `category` and `summary`. If
`triage` 502s, start Lemonade and pull the model, then retry — the rest of your
integration is fine.

For the full endpoint list, lifecycle internals, and connector details, see
`SPEC.md` next to this file.
