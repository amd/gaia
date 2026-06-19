---
name: "agent-hub-release"
description: "Publish a GAIA sidecar agent (frozen no-Python binary + npm client) to the Agent Hub and npm. Use when cutting or setting up a release for any agent under hub/agents/ — 'release the email agent', 'publish agent-<x> v0.2.0', 'onboard a new hub agent', or wiring its release workflow. The email agent is the reference implementation; this skill generalizes it to every future agent."
---

# Publishing a GAIA Agent to the Agent Hub

How to ship a **sidecar agent** — a frozen, no-Python REST binary plus a thin npm
client — to the GAIA Agent Hub and the npm registry. The **email agent is the
reference**: `hub/agents/python/email/` + `hub/agents/npm/agent-email/` +
`.github/workflows/release_agent_email.yml`. Every future agent mirrors that shape.

This is a **phased process with one hard human gate** (the `agent-publish`
environment). Stop and confirm before anything irreversible — pushing a release
tag, approving the publish gate. Publishes are **immutable per filename**: a bad
release is fixed by a new version, never an overwrite.

## When to use

- Cutting a release of an existing hub agent (the happy path).
- Onboarding a brand-new sidecar agent into the hub/npm pipeline (one-time).
- Authoring or debugging a `release_agent_<id>.yml` workflow.

Not for in-repo Python-only agents (analyst, browser, jira, …) that ship inside
the `amd-gaia` wheel — those follow the core `gaia-release` skill. This skill is
specifically the **frozen-binary + npm-client + hub** distribution path.

## Distribution model (the mental picture)

```
freeze.py (PyInstaller, native per-OS, no cross-compile)
   └─ email-agent-<platform>[.exe]   ← one-file binary, boots a FastAPI REST sidecar
        └─ POST /publish  →  Agent Hub Worker (workers/agent-hub/) ── R2 bucket "gaia-hub"
             • stores each object IMMUTABLY under agents/<id>/<ver>/<file>
             • computes SHA-256 server-side
             • rebuilds index.json (the hub catalog the website reads)
        download = plain public GET at hub.amd-gaia.ai/agents/<id>/<ver>/<file>
   └─ @amd-gaia/agent-<id> (npm)  ← typed client + fetch CLI + sidecar lifecycle
        • binaries.lock.json maps platform → {filename, sha256, size}; the SHA-256
          is the integrity gate the fetch CLI enforces on download
        • published via npm OIDC trusted publishing (provenance, NO npm token)
```

The Worker fronts the bucket; **CI never touches R2 directly** — it POSTs to
`/publish` (Bearer auth) so uploads are server-checksummed and the catalog is
rebuilt atomically. The manifest (`gaia-agent.yaml`) rides along inside each POST.

## What a publishable agent is made of (email = reference)

| Path | Role |
|------|------|
| `hub/agents/python/<id>/gaia_agent_<id>/` | the agent package — `agent.py`, `cli.py`, `contract.py` (frozen wire contract + `SCHEMA_VERSION`), `api_routes.py`, server entry |
| `hub/agents/python/<id>/gaia-agent.yaml` | **manifest** — validated against `workers/agent-hub/schemas/manifest.schema.json` |
| `hub/agents/python/<id>/README.md` | agent README — published to the hub alongside the binaries (`--readme`) |
| `hub/agents/python/<id>/packaging/` | `freeze.py`, `smoke_test.py`, `server.py`, `gen_binaries_lock.py`, `publish_to_r2.py`, `HUB-UPLOAD.md` (manual fallback) |
| `hub/agents/npm/agent-<id>/package.json` | ESM-only; `exports` `.` (Node) + `./client` (browser-safe); `bin`; `files` includes `CHANGELOG.md` |
| `hub/agents/npm/agent-<id>/binaries.lock.json` | platform → artifact + **sha256** + size + `baseUrl` |
| `hub/agents/npm/agent-<id>/README.md` + `CHANGELOG.md` | client docs + SemVer history |
| `hub/agents/npm/agent-<id>/src/` | `client.ts`, `client-entry.ts` (browser), `fetch.ts`, lifecycle, `types.ts`, `errors.ts`, `cli.ts` |
| `.github/workflows/release_agent_<id>.yml` | the tag-triggered release (copy of `release_agent_email.yml`) |

### The manifest (`gaia-agent.yaml`)
Reference: `hub/agents/python/email/gaia-agent.yaml`. Required-ish fields:
`id`, `name`, `version`, `description`, `author`, `license`, `category`, `tags`,
`icon`, `language`, `min_gaia_version`, `models`, `python.{entry_module,entry_class,
dependencies}`, `requirements.{min_memory_gb,platforms}`, `interfaces.{cli,pipe,
api_server,mcp_server,tui}`. Validate against
`workers/agent-hub/schemas/manifest.schema.json` before publishing.

### The npm client
- **ESM-only** (`"type": "module"`). Two entry points: `.` (Node: fetch + spawn) and
  `./client` (browser/Electron renderer: `EmailClient` only, zero Node built-ins).
- **`binaries.lock.json` ships placeholder hashes** (`PENDING-…-replace-with-real-sha256`,
  `size: 0`) until the first real release fills them. While a hash is a placeholder,
  the fetch CLI is **intentionally fail-loud** — a bad/untrusted binary can never be
  fetched. (Build-from-source can't `fetchBinary` until a release; point lifecycle
  helpers at a locally-frozen binary for dev.)
- `CHANGELOG.md` follows SemVer; the **MAJOR of the wire `SCHEMA_VERSION`** is what the
  client's `checkVersion` enforces at startup, so a contract MAJOR bump is at least a
  package MINOR bump with a migration note.

## The three version numbers that MUST match

The release fails loudly at the `Resolve + validate release version` step unless all
three are identical:

1. the **tag** (or `workflow_dispatch` input) — `agent-pkg-<id>-v<version>` → `<version>`
2. `hub/agents/npm/agent-<id>/package.json` → `.version`
3. `hub/agents/python/<id>/gaia-agent.yaml` → `version`

Add the matching `CHANGELOG.md` entry in the same version-bump PR.
`binaries.lock.json`'s `agentVersion` + `baseUrl` are **regenerated by CI** — don't
hand-edit them for a release.

## Cutting a release (existing agent)

1. **Version-bump PR → main.** Bump all three versions + add the CHANGELOG entry.
   Merge to `main` (publishing is allowed **only from main** — the workflow asserts
   the release commit is a main ancestor).
2. **Pre-flight locally** (in `hub/agents/npm/agent-<id>/`): `npm ci && npm run build
   && npm test`, and `npm pack --dry-run` to confirm `CHANGELOG.md`/`README.md` ship.
3. **Tag from main** (or use `workflow_dispatch` with the version):
   ```bash
   git tag agent-pkg-<id>-v<version> && git push origin --tags
   ```
   Namespace is `agent-pkg-<id>-*` (NOT `v*`) — it deliberately does not fire the core
   `publish.yml`.
4. **Build stage** freezes the binary on each of 4 platforms (`win32-x64`,
   `darwin-arm64`, `linux-x64` **required**; `darwin-x64` Intel **best-effort**),
   smoke-tests each, computes SHA-256.
5. **Approve the gate.** The `publish` job pauses on the `agent-publish` environment
   until a maintainer approves — the human backstop against an accidental/tampered
   tag. The publish token isn't even readable until approval.
6. **Publish stage** (atomic): POST every binary to the Worker `/publish` → regenerate
   `binaries.lock.json` with the **real** hashes → **fetch-verify every published
   object** against the lock (the real integrity gate) → `npm publish` via OIDC
   (provenance) → trigger `deploy_website.yml` so the new catalog entry appears.

Monitor with `gh run watch`. `npm publish` is idempotent (skipped if that exact
version already exists); `/publish` is a verified 409 no-op for identical bytes.

## Onboarding a NEW agent (one-time)

1. **Scaffold** `hub/agents/python/<id>/` (agent + manifest + packaging) and
   `hub/agents/npm/agent-<id>/` (client + lock with placeholders). Mirror email.
2. **Adapt the packaging scripts.** `freeze.py`, `publish_to_r2.py`, and
   `gen_binaries_lock.py` currently **hardcode the `email-agent` artifact prefix** and
   email paths — copy + parameterize them (or generalize the prefix) for `<id>`.
3. **Copy the workflow** `release_agent_email.yml` → `release_agent_<id>.yml` and change:
   `PKG_DIR`, `MANIFEST`, `README`, `FREEZE_DIST`, `HUB_PREFIX` (`agents/<id>`), the
   tag trigger (`agent-pkg-<id>-*`), the artifact/frozen names, and the npm package
   name in the verify/publish steps.
4. **Register the npm trusted publisher** for `@amd-gaia/agent-<id>` against the exact
   filename `release_agent_<id>.yml`. ⚠️ The OIDC subject is tied to the filename —
   **renaming the workflow later breaks publish.**
5. First release fills the placeholder hashes (step 6 above) — until then the lock's
   `PENDING` entries keep fetch fail-loud.

## One-time infrastructure (per repo / per package)

- **GitHub environment `agent-publish`** with **required reviewers**; restrict its
  deployment branches/tags to `main` **and** the `agent-pkg-*` tag pattern (a
  main-only rule blocks the tag-triggered gate).
- **Secret `GAIA_HUB_TOKEN`** — Agent Hub Bearer publish token; must match an entry in
  the Worker's `PUBLISH_TOKENS`, scoped to the `AMD` author. Define it as an
  **environment** secret on `agent-publish` (not a repo secret) so it's unreadable
  until the gate is approved.
- **Variable `GAIA_HUB_BASE_URL`** — public Worker origin for downloads + the lock
  `baseUrl` (default `https://hub.amd-gaia.ai`).
- **Variable `GAIA_HUB_PUBLISH_URL`** — the Worker's **workers.dev** URL for uploads.
  The free-plan WAF on the proxied `hub.amd-gaia.ai` custom domain blocks large binary
  multipart uploads (but not GETs). Unset → uploads fall back to the custom domain and
  **403**.
- **Railway `HUB_CATALOG_URL=https://hub.amd-gaia.ai`** so the website rebuild reflects
  the new entry.

## Invariants & gotchas

- **Immutable per filename.** Re-publishing identical bytes = idempotent 409 no-op;
  different bytes under a published name **fail loudly**. Fix a bad release with a new
  version — never an overwrite.
- **Publish only from main.** The job asserts the release commit is on `main` (blocks
  releasing from a feature branch and keeps npm OIDC on main).
- **`SCHEMA_VERSION` MAJOR is the compat gate.** Client and binary must agree on the
  wire-contract MAJOR or `startSidecar` throws `VersionMismatchError`. Bump the npm
  package and re-publish the binary together.
- **Best-effort Intel.** `darwin-x64` builds on `macos-26-intel`, then is verified on
  `macos-15-intel`. If it fails or is missing, it's **dropped** and the other 3 ship —
  a loud `::warning::` + job summary, and Intel users get a clear "no binary for
  darwin-x64" install error (never a placeholder/silent one).
- **Fetch-verify is the real gate.** Even after `/publish`, CI re-fetches every object
  through the npm `fetch` CLI and checks bytes-hash-to-lock (with bounded retry for
  Cloudflare edge propagation) before `npm publish`.
- **Website is rebuilt, not patched.** The hub pages build from the live `index.json`;
  the publish job triggers `deploy_website.yml` on `main`.
- **Manual fallback:** `hub/agents/python/<id>/packaging/HUB-UPLOAD.md` documents the
  by-hand rclone path to the `gaia-hub` bucket — it produces the identical objects +
  lock as CI, so a hand-upload and a CI release are interchangeable.

## Reference files

- `.github/workflows/release_agent_email.yml` — the canonical release workflow (read
  its header comments — they document the whole contract).
- `hub/agents/python/email/gaia-agent.yaml` — manifest reference.
- `hub/agents/npm/agent-email/{package.json,binaries.lock.json,README.md,CHANGELOG.md}` —
  client package reference.
- `hub/agents/python/email/packaging/{freeze,smoke_test,publish_to_r2,gen_binaries_lock}.py`,
  `HUB-UPLOAD.md` — packaging + publish tooling.
- `workers/agent-hub/{README.md,schemas/manifest.schema.json,src/}` — the Worker, the
  `/publish` contract, and the manifest schema.
