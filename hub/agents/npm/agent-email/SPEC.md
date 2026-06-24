# @amd-gaia/agent-email — Technical reference

Detailed reference for `@amd-gaia/agent-email`. For a quick start, see
[`README.md`](./README.md); for an AI-assisted integration walkthrough, see
[`SKILL.md`](./SKILL.md). The contract version is `SCHEMA_VERSION` **2.0**.

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

Once a sidecar is running, anything that speaks HTTP can drive it — including a
browser or Electron renderer via the [`./client`](#browser--electron-renderer)
entry, which carries zero Node built-ins. You only need Node to *launch and
supervise* the binary, not to *talk to* it.

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
| `POST /v1/email/draft` | `draft()` | **Standalone** | Nothing external — wraps your `(to, subject, body)` and returns a single-use confirmation token. |
| `POST /v1/email/send` | `send()` | **Connector** | A connected Google/Microsoft mailbox **and** a valid confirmation token. Transmits mail (`503` if no mailbox connected, `400` if 2+ are). |
| `GET /health` | `health()` | **Standalone** | Liveness only — does **not** check Lemonade/model. |
| `GET /version` | `version()` | **Standalone** | Version negotiation. |
| `GET /v1/email/health` | `emailHealth()` | **Standalone** | Router-scoped liveness (mounted-on-app case). |
| `GET /v1/email/version` | `emailVersion()` | **Standalone** | Router-scoped version. |
| `GET /v1/email/spec` | `spec()` | **Standalone** | Human-readable HTML endpoint page. |
| `GET /openapi.json` | `openapi()` | **Standalone** | Machine-readable OpenAPI document. |

`GET /docs` (Swagger UI) and `GET /redoc` are also served but are browser UIs, not
wrapped by the client. **Everything except `send` is standalone** — integrate and
verify the whole surface with zero connector setup.

### Readiness vs liveness

`health()` is **liveness-only** — a green `/health` means "the REST surface is up,"
**not** "triage will work." On a fresh machine the binary boots fine, but the first
`triage` returns **HTTP 502** until a local Lemonade Server is running and the
configured model is pulled. The only real readiness signal today is a `triage` (or
any LLM call) returning `200`.

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
- `shutdown(sidecar)` → kill the **whole process tree** (`taskkill /F /T` on Windows; detached process-group kill on POSIX — the frozen one-file binary orphans a child otherwise).

## The `fetch` CLI

`fetch` is the supported, build-time path. It resolves
`${process.platform}-${process.arch}`, downloads that platform's artifact from the
base URL in `binaries.lock.json`, **verifies its SHA-256 against the lock and fails
loudly on any mismatch**, writes it to `--out`, and `chmod +x`'s it on POSIX.

```bash
npx @amd-gaia/agent-email fetch --out resources
npx @amd-gaia/agent-email version     # show manifest + current platform
npx @amd-gaia/agent-email help
```

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
or an Electron renderer. For those, import the browser-safe, client-only subpath
and talk to an already-running sidecar over HTTP:

```ts
import { EmailClient } from "@amd-gaia/agent-email/client";

const client = new EmailClient({ baseUrl: "http://127.0.0.1:8131" });
const res = await client.triage({ payload: { /* … */ } });
```

`./client` re-exports only zero-Node-dependency symbols — `EmailClient`, every
error class, `SCHEMA_VERSION`, and all request/response types. The desktop-app
pattern: spawn the sidecar once from your **main/Node** process (the `.` entry),
then drive it from the renderer via `./client`.

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

- `contract.py` — the triage request/response contract (`SCHEMA_VERSION = "2.0"`).
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
