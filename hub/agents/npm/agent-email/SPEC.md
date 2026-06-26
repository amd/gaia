# @amd-gaia/agent-email — Technical reference

Detailed reference for `@amd-gaia/agent-email`. For a quick start, see
[`README.md`](./README.md); for an AI-assisted integration walkthrough, see
[`SKILL.md`](./SKILL.md). The contract version is `SCHEMA_VERSION` **2.1**.

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

## REST API

`EmailClient` is a typed wrapper over the sidecar's HTTP surface. Methods:
`triage`, `draft`, `send`, `health`, `version`, `emailHealth`, `emailVersion`,
`spec`, `openapi`. `health`/`version` hit the **root** routes (the standalone
sidecar); `emailHealth`/`emailVersion` hit the **`/v1/email`-scoped** mirrors (for
when the router is mounted on a product app). Every non-2xx response throws
`HttpError` (carrying `status`, `url`, `bodyText`) — never a silent empty/null
result.

| Endpoint | Client method | Auth | What it needs |
|----------|---------------|------|---------------|
| `POST /v1/email/triage` | `triage()` | **Standalone** | Local Lemonade LLM only. Categorizes / summarizes / extracts action items + spam/phishing **signals** on the message you send in. *No mailbox is read.* |
| `POST /v1/email/search` | `search()` | **Connector** | Read-only inbox search. A connected Google/Microsoft mailbox (`503` if none, `400` if 2+); **no** confirmation token. Lists messages matching `query`/`labels` and returns metadata only (no body). |
| `POST /v1/email/draft` | `draft()` | **Standalone** | Nothing external — wraps your `(to, subject, body)` and returns a single-use confirmation token. |
| `POST /v1/email/send` | `send()` | **Connector** | A valid `draft` confirmation token **and** a connected Google/Microsoft mailbox. The token gate fires first: no/invalid token → `403`; then `503` if no mailbox is connected, `400` if 2+ are. |
| `POST /v1/email/confirm` | `confirmAction()` | **Standalone** | Nothing external — mints a single-use token for `"archive"`/`"quarantine"`, bound to that exact `(action, message_id)`. |
| `POST /v1/email/archive` | `archive()` | **Connector** | A valid `confirm` token (`action="archive"`) **and** a connected mailbox. Gate fires first (no/invalid token → `403`); returns a `batch_id` undo handle + `post_archive_id`. |
| `POST /v1/email/unarchive` | `unarchive()` | **Connector** | A connected mailbox + the `batch_id`. **Ungated** (it restores). Window expired / unknown handle → `409`. |
| `POST /v1/email/quarantine` | `quarantine()` | **Connector (Gmail)** | A valid `confirm` token (`action="quarantine"`) **and** a connected **Gmail** mailbox. Applies `GAIA_PHISHING_QUARANTINE` + archives; refuses `is_phishing: false` → `400`; refuses an Outlook mailbox → `400` (label-undo can't reverse a folder move, #1738). |
| `POST /v1/email/unquarantine` | `unquarantine()` | **Connector** | A connected mailbox + the `action_id`. **Ungated** (it restores prior labels). Window expired / unknown → `409`. |
| `GET /health` | `health()` | **Standalone** | Liveness only — does **not** check Lemonade/model. |
| `GET /version` | `version()` | **Standalone** | Version negotiation. |
| `GET /v1/email/health` | `emailHealth()` | **Standalone** | Router-scoped liveness (mounted-on-app case). |
| `GET /v1/email/version` | `emailVersion()` | **Standalone** | Router-scoped version. |
| `GET /v1/email/spec` | `spec()` | **Standalone** | Human-readable HTML endpoint page. |
| `GET /openapi.json` | `openapi()` | **Standalone** | Machine-readable OpenAPI document. |

`GET /docs` (Swagger UI) and `GET /redoc` are also served but are browser UIs, not
wrapped by the client. **The standalone surface is `triage`, `draft`, and
`confirmAction`** — integrate and verify that whole flow with zero connector setup.
`search` reads the live inbox (a connected mailbox, but no token); the mailbox-mutating
calls (`send`, `archive`, `quarantine`) and their reversals also need a connected
mailbox on the host.

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

### Readiness vs liveness

`health()` is **liveness-only** — a green `/health` means "the REST surface is up,"
**not** "triage will work." On a fresh machine the binary boots fine, but the first
`triage` returns **HTTP 502** until a local Lemonade Server is running and the
configured model is pulled. The only real readiness signal today is a `triage` (or
any LLM call) returning `200`.

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
// `draft` is { to, subject, body }; the token authorizes exactly this payload.
const sent = await client.send({ ...draft, confirmation_token });
console.log(sent.sent_id);
```

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
| `draft` | `DraftReply \| null` | A proposed reply (`{ to, subject, body }`) when one is suggested. |
| `usage` | `TriageUsage \| null` | LLM token/latency metrics; `null` on the heuristic-only path. |

The full request/response types are exported from the package (`src/types.ts`) for
exact field-level reference.

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

The full GAIA email agent also reads and acts on the live mailbox and calendar
(list/search messages, archive, label, mark spam, RSVP, create events). Those
actions are connector-gated by definition and are **not exposed through this
package's REST API yet** — only triage / draft / send are.

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

- `contract.py` — the triage request/response contract plus the schema-2.1 additions:
  inbox search, mailbox actions (archive / quarantine + reversal), calendar, and
  pre-scan (`SCHEMA_VERSION = "2.1"`).
- `api_routes.py` — the local draft/send confirmation handshake models.

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
