# @amd-gaia/agent-email

Thin JS/TS client, build-time binary fetcher, and sidecar lifecycle helpers for
the **GAIA email agent** — a frozen, no-Python REST sidecar that triages email
100% locally on AMD Ryzen AI.

This package ships **no binary**. It ships a typed client, a `fetch` CLI that
downloads + SHA-256-verifies the right binary for your platform at build time,
and helpers to spawn / health-check / version-check / shut down the sidecar.

> Milestone #49 (issues #1646 + #1652). The packaging spike already proved the
> frozen `email-agent` binary boots a FastAPI server and answers a contract-valid
> triage round-trip with **no Python present** — see
> `hub/agents/python/email/packaging/README.md`. This package is the npm side.

## How it fits together

![@amd-gaia/agent-email architecture — your Node code spawns and drives a frozen email-agent sidecar (127.0.0.1:8131) over HTTP, which runs triage inference on a local Lemonade Server; a browser/Electron renderer drives the same sidecar over HTTP via the ./client entry.](https://raw.githubusercontent.com/amd/gaia/main/hub/agents/npm/agent-email/assets/architecture.webp)

Three tiers, all on the user's machine — no cloud, and **no separate GAIA
install**:

- **Your app** depends on this package and, from a **Node** process, fetches +
  spawns the sidecar via the `.` entry. It does **not** attach to an
  already-running GAIA instance — the package **launches and owns its own
  sidecar** and tears it down on `shutdown()`.
- **The sidecar** is a self-contained, PyInstaller-frozen `email-agent` binary
  serving the email REST endpoints. **No Python is required on the host.**
- **Lemonade Server** is the one external runtime dependency: the sidecar calls a
  **local** Lemonade for the actual LLM inference. With none reachable,
  `POST /v1/email/triage` returns HTTP 502. (Lemonade is GAIA's model server, not
  GAIA the framework.)

Once a sidecar is running, **anything that speaks HTTP can drive it** — including a
browser or Electron **renderer** via the [`./client`](#browser--electron-renderer-client)
entry, which carries zero Node built-ins. You only need Node to *launch and
supervise* the binary, not to *talk to* it.

## Why this location

It lives under **`hub/agents/npm/agent-email/`**, mirroring the existing
`hub/agents/python/email/` (the Python agent + freeze tooling) and
`hub/agents/cpp/`. The hub is where shippable, per-language agent artifacts live;
`npm/` is the natural sibling to `python/` and `cpp/`. It is intentionally
**one single public package** — no per-platform sub-packages. One manifest
(`binaries.lock.json`) maps every supported platform to its artifact + hash.

## Install

```bash
npm install @amd-gaia/agent-email
```

> **Corporate TLS:** if `npm install` fails with `UNABLE_TO_GET_ISSUER_CERT`
> behind a proxy, run with Node's system CA store:
> `NODE_OPTIONS=--use-system-ca npm install` (Node ≥ 22). Same class of issue the
> Python spike hit with `uv --system-certs`.

## Prerequisites (runtime)

This package fetches and spawns the sidecar **binary**, but it does **not**
provision the model stack. Before `triage` (or any LLM call) will succeed, the
host must already have:

1. **A running Lemonade Server** — `lemonade-server serve`.
2. **The configured model pulled and loadable** — provisioned out-of-band via
   `gaia init` (installs Lemonade + downloads/tests the model).

The package does none of this for you: no model download, no version check, no
warmup. On a fresh machine, the binary boots fine but the first `triage` returns
**HTTP 502** until Lemonade + the model are in place.

> ⚠️ **`health()` is liveness-only — it does NOT check Lemonade or the model.** A
> green `/health` means "the REST surface is up," **not** "triage will work." The
> only real readiness signal today is a `triage` call returning `200` (vs `502`).
> A dedicated `/v1/init` readiness/provisioning endpoint is planned — see #1795.

## Quick start

```ts
import { fetchBinary, startSidecar, shutdown } from "@amd-gaia/agent-email";

// 1. Build-time: download + verify the binary for this platform.
const { binaryPath } = await fetchBinary({
  outDir: "resources",
  baseUrl: process.env.AGENT_EMAIL_BASE_URL, // real R2 URL pending #1648
});

// 2. Runtime: spawn -> wait for /health -> version-check.
const sidecar = await startSidecar({ binaryPath, port: 8131 });

// 3. Use the typed client.
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

// 4. Clean shutdown (kills the whole process tree).
await shutdown(sidecar);
```

## The `fetch` CLI

`fetch` is the **supported, build-time** path. It resolves
`${process.platform}-${process.arch}`, downloads that platform's artifact from a
configurable base URL, **verifies its SHA-256 against `binaries.lock.json` and
fails loudly on any mismatch**, writes it to `--out`, and `chmod +x`'s it on
POSIX.

```bash
npx @amd-gaia/agent-email fetch --out resources --base-url https://<r2-bucket>/email-agent/0.2.0
npx @amd-gaia/agent-email version     # show manifest + current platform
npx @amd-gaia/agent-email help
```

| Flag | Meaning |
|------|---------|
| `--out <dir>` | Resources dir to write the verified binary into (**required**) |
| `--base-url <url>` | Override the download base URL (real R2 URL pending **#1648**) |
| `--platform <key>` | Override platform key (e.g. `linux-x64`); default is the host |
| `--force` | Re-download even if a verified binary already exists |
| `--runtime` | Opt-in marker; build-time fetch remains the supported flow |

**SHA-256 is mandatory.** There is no "use it anyway" path — a corrupt,
truncated, or tampered download is rejected before it can ever be spawned, and
the bad file is not left on disk.

## API

### `EmailClient`
Typed wrapper over the sidecar's full HTTP surface. Methods: `triage`, `draft`,
`send`, `health`, `version`, `emailHealth`, `emailVersion`, `spec`, `openapi`.
`health`/`version` hit the **root** routes (the standalone sidecar);
`emailHealth`/`emailVersion` hit the **`/v1/email`-scoped** mirrors (for when the
router is mounted on a product app). `spec` returns the raw HTML endpoint page;
`openapi` returns the OpenAPI document. Every non-2xx response throws `HttpError`
(carrying `status`, `url`, `bodyText`) — never a silent empty/null result. Only
`send` needs a connected mailbox — see [Auth & connectors](#auth--connectors).

### Fetcher
- `fetchBinary(opts)` → download + verify + install; returns `{ binaryPath, sha256, cached, ... }`.
- `verifySha256(buf, expected, label)` → throws `IntegrityError` on mismatch.

### Lifecycle
- `resolveBinaryPath({ resourcesDir })` → locate a fetched binary (throws `BinaryNotFoundError` if absent).
- `spawnSidecar({ binaryPath, host?, port?, extraArgs? })` → spawn (`--host 127.0.0.1 --port <p>`).
- `waitForHealth(baseUrl, { timeoutMs })` → poll `/health`; throws `HealthTimeoutError` on timeout (never assumes ready).
- `checkVersion(client, { expectedApiVersion })` → throws `VersionMismatchError` if the sidecar's apiVersion **MAJOR** differs (a higher MINOR is accepted).
- `shutdown(sidecar)` → kill the **whole process tree** (`taskkill /F /T` on Windows; detached process-group kill on POSIX). The frozen one-file binary orphans a child otherwise (packaging/README.md, gotcha 6).
- `startSidecar(opts)` → `spawn` → `waitForHealth` → `checkVersion` in one call; shuts down on any failure so a failed start never leaks a process.

## Auth & connectors

**The rule:** an endpoint that works on the **content you pass in the request** is
**standalone** — it needs nothing but the local Lemonade LLM. An action that
**reads from or acts on the live Gmail/Outlook mailbox or calendar** (send,
archive, mark spam, label, create a calendar invite, …) requires the **Google or
Microsoft connector** (OAuth), configured in GAIA under *Settings → Connectors*.

### What this package's REST API exposes today

| Endpoint | Client method | Auth | What it needs |
|----------|---------------|------|---------------|
| `POST /v1/email/triage` | `triage()` | **Standalone** | Local Lemonade LLM only. Categorizes / summarizes / extracts action items + spam/phishing **signals** on the message you send in. *No mailbox is read.* |
| `POST /v1/email/draft` | `draft()` | **Standalone** | Nothing external — wraps your `(to, subject, body)` and returns a single-use confirmation token. |
| `POST /v1/email/send` | `send()` | **Connector** | A connected Google/Microsoft mailbox **and** a valid confirmation token. Actually transmits mail (`503` if no mailbox connected, `400` if 2+ are). |
| `GET /health` | `health()` | **Standalone** | Liveness only — does **not** check Lemonade/model (see [Prerequisites](#prerequisites-runtime)). |
| `GET /version` | `version()` | **Standalone** | Nothing — version negotiation. |
| `GET /v1/email/health` | `emailHealth()` | **Standalone** | Router-scoped liveness (for the mounted-on-app case). |
| `GET /v1/email/version` | `emailVersion()` | **Standalone** | Router-scoped version. |
| `GET /v1/email/spec` | `spec()` | **Standalone** | Human-readable HTML endpoint page. |
| `GET /openapi.json` | `openapi()` | **Standalone** | Machine-readable OpenAPI document. |

The interactive `GET /docs` (Swagger UI) and `GET /redoc` pages are also served but
are browser UIs, not wrapped by the client. So **everything except `send` is
standalone** — you can integrate and verify the whole surface with zero connector
setup; only `send` needs a connected mailbox.

### Mailbox/calendar actions always need a connector

The full GAIA email agent also *acts on* the live mailbox and calendar. These are
connector-gated **by definition** — they change state in Gmail/Outlook — and are
**not exposed through this package's REST API yet** (only triage/draft/send are):

| Action | Requires |
|--------|----------|
| Read inbox, get/search messages, list labels | Google / Microsoft (read) |
| Archive, mark read/unread, star, label, move | Google / Microsoft |
| Mark spam / quarantine phishing, trash, delete | Google / Microsoft |
| Send / forward a reply | Google / Microsoft |
| Calendar: list events, accept/decline invite, create event from an email | Google / Microsoft Calendar |

> Detecting spam/phishing or a meeting request is **standalone** (it analyzes the
> content you pass). *Acting* on it — moving to spam, creating the invite — is what
> needs the connector.

### No token injection (yet)

`send` resolves its OAuth token from the **local GAIA connector store**
(`gaia.connectors`) on the host — `EmailSendRequest` has **no `access_token`
field** (`provider` is only a routing hint). There is **no way to pass or forward
a connection through this package's API**, so connector-backed calls only work on
a machine where the mailbox is already connected in GAIA. Triage and draft, which
need no connector, work anywhere.

## Module format

**Plain JavaScript works too** — the package ships compiled JS in `dist/`;
TypeScript is the authoring language, not a consumer requirement. The bundled
`.d.ts` files give editors autocomplete but your code never imports them.

This package is **ESM-only** (`"type": "module"`; no CommonJS build). Import it
with `import …`. From a CommonJS module, use a dynamic import instead of `require`:

```js
const { startSidecar } = await import("@amd-gaia/agent-email");
```

## Browser / Electron renderer (`./client`)

The default entry (`.`) pulls in Node built-ins (`node:fs`, `node:child_process`,
`node:crypto`) to fetch and spawn the binary, so it can't be bundled for a browser
or an Electron **renderer** process. For those, import the browser-safe, client-only
subpath and talk to an already-running sidecar over HTTP:

```ts
import { EmailClient } from "@amd-gaia/agent-email/client";

const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131" });
const res = await client.triage({ payload: { /* … */ } });
```

`./client` re-exports only zero-Node-dependency symbols — `EmailClient`, every
error class, `SCHEMA_VERSION`, and all request/response types. The pattern for a
desktop app: spawn the sidecar once from your **main/Node** process (the `.` entry),
then drive it from the renderer via `./client`.

## Types

TypeScript types in `src/types.ts` are **hand-written** to mirror two Python
sources of truth:
- `hub/agents/python/email/gaia_agent_email/contract.py` — the #1262
  triage request/response contract, evolved to `SCHEMA_VERSION = "2.0"` (#1766).
- `hub/agents/python/email/gaia_agent_email/api_routes.py` — the local draft/send
  handshake models (the #1264 send-confirmation gate).

Hand-written (vs. generated from `/openapi.json`) because the contract is small
and version-gated, keeping the published package free of a typegen build step. The
runtime `checkVersion` guard catches contract drift loudly. The server exposes
`GET /openapi.json` if you prefer to regenerate.

> Wire note: `EmailMessage.from` is the JSON key on the wire (Python aliases its
> `from_` field to `from`), so the TS interface uses `from` directly.

## Example — integration smoke test + health check

[`examples/demo.mjs`](examples/demo.mjs) is the **one-command "did my integration
work?" check**. It spawns the sidecar, runs a **health check** that says plainly
what (if anything) is wrong — *sidecar down*, *Lemonade not found*, *model not
downloaded* — then exercises **every standalone endpoint** (`health`, `version`,
`emailHealth`, `emailVersion`, `openapi`, `spec`, `draft`, `triage`) and prints a
PASS/SKIP tally. `send` is skipped (connector-gated — see
[Auth & connectors](#auth--connectors)).

It runs against the dev `server.py` by default, or a frozen binary via
`AGENT_EMAIL_BINARY`. The example is **not** shipped in the npm tarball (kept
lean) — it lives here in the repo and is linked from the agent hub.

```bash
npm run build && npm run demo          # or: node examples/demo.mjs

# Against the frozen binary instead of the dev server:
AGENT_EMAIL_BINARY=../../python/email/packaging/dist/email-agent/email-agent.exe \
  npm run demo
```

Example output on a host with no Lemonade running:

```
[demo] Health check:
[demo]   ✓ sidecar          up — service=gaia-agent-email apiVersion=2.0 …
[demo]   ✗ Lemonade not responding (timed out)
[demo]        → Is `lemonade-server serve` running and reachable on the expected port?
[demo] STACK HEALTH: ✗ NOT READY — Lemonade not responding …
[demo] ENDPOINTS: 7 standalone endpoint(s) PASS; send() is connector-gated (skipped).
```

Set `DEBUG=agent-email` (or `DEBUG=*`) for verbose spawn/fetch/health logs (all
on stderr, so machine-readable stdout stays clean).

## Tests

```bash
npm test     # vitest: client typing, fetch SHA verify (pass + tampered-fail), version-check
```

## Pending real R2 (#1648)

`binaries.lock.json` ships **placeholder** `baseUrl` and `sha256` values. While a
hash is a placeholder, `fetch` is **intentionally blocked** (fails loudly) so a
bad binary can never be trusted. To wire #1648:
1. Build each platform's binary (`hub/agents/python/email/packaging/freeze.py`).
2. Upload to the R2 bucket; set `baseUrl` to the real URL.
3. Replace each `sha256` with the real artifact hash (and `size`).

Triage uses the **real local Lemonade model.** With no Lemonade reachable,
`POST /v1/email/triage` returns HTTP 502; start a Lemonade Server for live triage.

## Platforms

`win32-x64`, `darwin-arm64`, `darwin-x64`, `linux-x64`. Per the spike, no blocker
is foreseen for the non-Windows targets; each must be built natively (PyInstaller
does not cross-compile).
