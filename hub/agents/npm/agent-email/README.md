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
npx @amd-gaia/agent-email fetch --out resources --base-url https://<r2-bucket>/email-agent/0.1.0
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
Typed wrapper over the five endpoints. Methods: `triage`, `draft`, `send`,
`health`, `version`. Every non-2xx response throws `HttpError` (carrying
`status`, `url`, `bodyText`) — never a silent empty/null result.

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

## Types

TypeScript types in `src/types.ts` are **hand-written** to mirror two Python
sources of truth:
- `hub/agents/python/email/gaia_agent_email/contract.py` — the frozen #1262
  triage request/response contract (`SCHEMA_VERSION = "1.0"`).
- `hub/agents/python/email/gaia_agent_email/api_routes.py` — the local draft/send
  handshake models (the #1264 send-confirmation gate).

Hand-written (vs. generated from `/openapi.json`) because the contract is small
and frozen, keeping the published package free of a typegen build step. The
runtime `checkVersion` guard catches contract drift loudly. The server exposes
`GET /openapi.json` if you prefer to regenerate.

> Wire note: `EmailMessage.from` is the JSON key on the wire (Python aliases its
> `from_` field to `from`), so the TS interface uses `from` directly.

## End-to-end demo (the MVP proof)

`scripts/demo.mjs` drives the full lifecycle through the package's own helpers
(spawn → health → version → triage → tree-kill shutdown) and prints the triage
result. It runs against the dev `server.py` by default, or a frozen binary via
`AGENT_EMAIL_BINARY`.

```bash
npm run build

# Against the dev server (needs the Python env from the spike):
#   uv venv && uv pip install --system-certs -e ".[api]" \
#     && uv pip install --system-certs -e hub/agents/python/email
node scripts/demo.mjs

# Against the frozen binary:
AGENT_EMAIL_BINARY=../../python/email/packaging/dist/email-agent/email-agent.exe \
  node scripts/demo.mjs
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
