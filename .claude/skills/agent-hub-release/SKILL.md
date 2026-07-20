---
name: "agent-hub-release"
description: "Cut or wire a frozen-binary + npm sidecar release for a GAIA agent (the email-agent CI pipeline: freeze -> Agent Hub Worker /publish -> npm OIDC). Use when releasing or onboarding a sidecar agent under hub/agents/, or authoring a release_agent_<id>.yml. For the standard wheel/PyPI + Hub publish, defer to the author guide docs/guides/hub-publishing.mdx — this skill is the sidecar extension on top of it."
---

# Releasing a GAIA Sidecar Agent (frozen binary + npm)

How to ship a **sidecar agent** — a frozen, no-Python REST binary plus a thin npm
client — to the GAIA Agent Hub and npm, via the tag-triggered CI release. The
**email agent is the reference**: `hub/agents/email/python/` +
`hub/agents/email/npm/` + `.github/workflows/release_agent_email.yml`.

This is a **phased process with one hard human gate** (the `agent-publish`
environment). Stop and confirm before anything irreversible — pushing a release
tag, approving the publish gate. Publishes are **immutable per filename**: a bad
release is fixed by a new version, never an overwrite.

> **Status — the email pipeline has not yet cut a real release.**
> `hub/agents/email/npm/binaries.lock.json` still carries placeholder
> `PENDING-1648` hashes and **#1648 is open**, so the freeze→publish→fetch-verify
> path has never run end-to-end. Treat this skill as the design of record, not a
> paved road, until the first real release lands and fills the lock. Likewise the
> `agent-publish` environment + `GAIA_HUB_TOKEN` + hub vars are **maintainer
> setup** (workflow header) — verify they exist before relying on them.

## Relationship to the author guide (read first)

[`docs/guides/hub-publishing.mdx`](../../../docs/guides/hub-publishing.mdx) is the
**author-facing** guide for the standard distribution: a Python **wheel** published
to the Hub + PyPI via `gaia agent publish` (or the no-token PR route). It owns the
authoritative rules for the **manifest, versioning, immutability, and the two
publish routes** — don't restate or contradict them here.

This skill covers the **additional** channel an agent like email layers on top: a
**frozen native binary + an `@amd-gaia/agent-<id>` npm client**, released through
`release_agent_<id>.yml`. An agent can ship one or both channels. Where the two
overlap (manifest, version rules), the guide is the source of truth.

**Releasing an agent that has never shipped before?** Cutting the release is the
*last* phase. An agent is not release-ready because its code runs — it needs a
capability-truth audit, a real scorecard, the doc bundle, and a day-one usability
check first. Use [`porting-agent-to-hub`](../porting-agent-to-hub/SKILL.md) for
that flow, then come back here for the lane itself.

## Distribution model (the mental picture)

```
freeze.py (PyInstaller, native per-OS, no cross-compile)
   └─ email-agent-<platform>[.exe]   ← one-file binary, boots a FastAPI REST sidecar
        └─ POST /publish  →  Agent Hub Worker (workers/agent-hub/) ── R2 bucket "gaia-hub"
             • stores each object IMMUTABLY under agents/<id>/<ver>/<file>
             • computes SHA-256 server-side
             • rebuilds index.json (the catalog the website reads)
        download = plain public GET at hub.amd-gaia.ai/agents/<id>/<ver>/<file>
   └─ @amd-gaia/agent-<id> (npm)  ← typed client + fetch CLI + sidecar lifecycle
        • binaries.lock.json maps platform → {filename, sha256, size}; the SHA-256
          is the integrity gate the fetch CLI enforces on download
        • published via npm OIDC trusted publishing (provenance, NO npm token)
```

The Worker fronts the bucket; **CI never touches R2 directly** — it POSTs to
`/publish` so uploads are server-checksummed and the catalog is rebuilt atomically.
The manifest + README ride along inside the publish.

## What a sidecar agent adds (email = reference)

On top of the normal agent package (manifest + code + README + `pyproject.toml`,
per the guide), a sidecar agent adds:

| Path | Role |
|------|------|
| `hub/agents/<id>/python/packaging/` | `freeze.py`, `smoke_test.py`, `server.py`, `gen_binaries_lock.py`, `publish_to_r2.py`, `HUB-UPLOAD.md` (manual fallback) |
| `hub/agents/<id>/npm/package.json` | ESM-only client; `exports` `.` (Node) and `./client` (browser-safe, client-only — landed in **#1773**) |
| `hub/agents/<id>/npm/binaries.lock.json` | platform → artifact + **sha256** + size + `baseUrl` (placeholders until the first release) |
| `hub/agents/<id>/npm/README.md` (+ `CHANGELOG.md`) | client docs; CHANGELOG is recommended (Keep a Changelog), **not** required by publish |
| `hub/agents/<id>/npm/src/` | `client.ts`, `client-entry.ts` (browser entry, landed in #1773), `fetch.ts`, lifecycle, `types.ts`, `errors.ts`, `cli.ts` |
| `.github/workflows/release_agent_<id>.yml` | the tag-triggered release (copy of `release_agent_email.yml`) |

Notes:
- **The npm client is ESM-only** (`"type": "module"`). Node consumers use the `.`
  entry (fetch + spawn); browser/Electron renderers use `./client` (client-only,
  zero Node built-ins) — landed in **#1773**, documented by **#1776**.
- **`binaries.lock.json` ships placeholder hashes** until the first real release.
  While a hash is a placeholder the fetch CLI is **fail-loud** — a bad binary can
  never be fetched. For local dev, point the lifecycle helpers at a locally-frozen
  binary instead of `fetchBinary`.
- **Manifest validation:** validate the author manifest with `gaia agent test
  --lint`. Do **not** validate `gaia-agent.yaml` against
  `workers/agent-hub/schemas/manifest.schema.json` — that is the Hub's *server-side
  aggregate* schema and requires fields you never hand-write (`versions`,
  `latest_version`, `deprecated`, `security_tier`, `permissions`).
- **Platform-name skew (easy bug):** the manifest's `requirements.platforms` uses
  `win-x64`; `binaries.lock.json` + the CI matrix use `win32-x64`. Same agent, two
  spellings — don't copy one into the other.

## The three version numbers the release checks

The `Resolve + validate release version` step fails loudly unless all three are
identical:

1. the **tag** (or `workflow_dispatch` input) — `agent-pkg-<id>-v<version>` → `<version>`
2. `hub/agents/<id>/npm/package.json` → `.version`
3. `hub/agents/<id>/python/gaia-agent.yaml` → `version`

Bump the Python side with **`gaia agent version patch|minor|major`** (it rewrites
`gaia-agent.yaml` + `pyproject.toml` + `__init__.py` together — bumping **from the
`gaia-agent.yaml` value**, ignoring whatever the npm `package.json` currently says),
then **manually set the npm `package.json`** to that same version — the release
tooling checks all three agree. (Heads up: they can already be out of sync — e.g. a
package.json ahead of `gaia-agent.yaml` — so a blind `patch` bumps the yaml base, not
the npm value; the manual npm sync is what actually reconciles them.) Add the
matching `CHANGELOG.md` entry too if the agent keeps one (recommended, not required by publish). `binaries.lock.json`'s `agentVersion`
+ `baseUrl` are **regenerated by CI** — don't hand-edit them for a release.

## Cutting a release (existing agent)

1. **Version-bump PR → main.** `gaia agent version <bump>`, sync the npm
   `package.json`, add the CHANGELOG entry, merge to `main` (the workflow asserts the
   release commit is a `main` ancestor — publishing is allowed only from main).
2. **Pre-flight** in `hub/agents/<id>/npm/`: `npm ci && npm run build && npm
   test`, and `npm pack --dry-run` to confirm `README.md` ships (and `CHANGELOG.md`, if you keep one).
3. **Tag from main** (or `workflow_dispatch` with the version):
   ```bash
   git tag agent-pkg-<id>-v<version> && git push origin --tags
   ```
   Namespace is `agent-pkg-<id>-*` (NOT `v*`) — it deliberately does not fire the
   core `publish.yml`.
4. **Build stage** freezes on 4 platforms — `win32-x64`, `darwin-arm64`, `linux-x64`
   **required**; `darwin-x64` (Intel) **best-effort** — smoke-tests each, hashes it.
   (The smoke test proves the binary boots and the REST route answers; with no
   Lemonade in CI it does **not** exercise real LLM triage — a 502/timeout passes.)
5. **Approve the gate.** The `publish` job pauses on the `agent-publish` environment
   until a maintainer approves; the publish token isn't readable until then.
6. **Publish stage** (atomic): POST every binary to `/publish` → regenerate
   `binaries.lock.json` with the **real** hashes → **fetch-verify every published
   object** against the lock → `npm publish` via OIDC (provenance) → trigger
   `deploy_website.yml` so the new catalog entry appears.

Monitor with `gh run watch`. `npm publish` skips if that exact version already
exists; `/publish` is a verified 409 no-op for identical bytes.

## Onboarding a NEW sidecar agent (one-time)

1. **Scaffold** the normal agent first (`gaia agent init`, per the guide), then add
   `packaging/` and the `hub/agents/<id>/npm/` client. Mirror email.
2. **Adapt the packaging scripts.** `freeze.py` (its `NAME = "email-agent"` constant)
   and `publish_to_r2.py` (the executable name + the `email-agent-` filename prefix it
   parses) **hardcode `email-agent`** — copy + parameterize them for `<id>`.
   `gen_binaries_lock.py` is already generic (driven by `published.json` + manifest
   `id`); the `agents/email` hub prefix lives in the workflow's `HUB_PREFIX` env, not a
   script.
3. **Copy the workflow** `release_agent_email.yml` → `release_agent_<id>.yml` and
   change: `PKG_DIR`, `MANIFEST`, `README`, `FREEZE_DIST`, `HUB_PREFIX`
   (`agents/<id>`), the tag trigger (`agent-pkg-<id>-*`), the artifact/frozen names,
   and the npm package name in the verify/publish steps.
4. **Register the npm trusted publisher** for `@amd-gaia/agent-<id>` against the
   exact filename `release_agent_<id>.yml`. ⚠️ The OIDC subject is tied to the
   filename — **renaming the workflow later breaks publish.**
5. *(Maintainability)* per-agent copies of a ~550-line workflow + three hardcoded
   scripts **will drift**. The durable fix is a reusable `workflow_call` release
   workflow + scripts parameterized by `<id>`; the per-agent copy is interim.

## One-time infrastructure (verify it exists)

Per the workflow header (`release_agent_email.yml`), these are **maintainer setup**
— confirm each before the first release:

- **GitHub environment `agent-publish`** with **required reviewers**; restrict its
  deployment branches/tags to `main` **and** the `agent-pkg-*` tag pattern (a
  main-only rule blocks the tag-triggered gate).
- **Secret `GAIA_HUB_TOKEN`** — Agent Hub Bearer token matching an entry in the
  Worker's `PUBLISH_TOKENS`, scoped to the agent's `author`. Define it as an
  **environment** secret on `agent-publish` (not a repo secret) so it's unreadable
  until the gate is approved. (The workflow maps it into the publish script's
  `AGENT_HUB_PUBLISH_TOKEN` env var — same token, different name inside the script.)
- **Var `GAIA_HUB_BASE_URL`** — public Worker origin for downloads + the lock
  `baseUrl` (default `https://hub.amd-gaia.ai`).
- **Var `GAIA_HUB_PUBLISH_URL`** — the Worker's **workers.dev** URL for uploads. The
  free-plan WAF on the proxied `hub.amd-gaia.ai` custom domain blocks large binary
  uploads (but not GETs). Unset → uploads fall back to the custom domain and **403**.
- **Railway `HUB_CATALOG_URL=https://hub.amd-gaia.ai`** so the website rebuild
  reflects the new entry.

## Invariants & gotchas

- **Immutable per filename.** The Worker `409`s on any re-POST of an existing
  filename (it keys on the filename via `head()`, not a byte-compare). The publish
  script then re-fetches and hashes the stored object: **identical bytes → idempotent
  no-op**, a **hash mismatch → fail loudly**. Fix a bad release with a new version —
  never an overwrite. (Same immutability the guide describes for `id@version`.)
- **Publish only from main.** The job asserts the release commit is on `main`.
- **`SCHEMA_VERSION` MAJOR is the compat gate.** Client and binary must agree on the
  wire-contract MAJOR or `startSidecar` throws `VersionMismatchError`. Bump the npm
  package and re-publish the binary together.
- **Best-effort Intel.** `darwin-x64` builds on `macos-26-intel`, then is verified on
  `macos-15-intel`. If it fails/absent it's **dropped** (3-platform release) with a
  loud `::warning::`; Intel users get a clear "no binary for darwin-x64" install
  error, never a placeholder one.
- **SHA-256 provenance.** `publish_to_r2.py` hashes each binary locally and the Worker
  hashes it server-side; the script **asserts they match** on the `201` before that
  (local, server-verified) hash is written to `binaries.lock.json`. The lock hash is
  then the gate the npm `fetch` CLI enforces on download (`PlatformError` on a
  placeholder, `IntegrityError` on a mismatch).
- **Fetch-verify is the real gate.** After `/publish`, CI re-fetches every object via
  the npm `fetch` CLI and checks bytes-hash-to-lock (bounded retry for Cloudflare
  edge propagation) before `npm publish`.
- **Website is rebuilt, not patched.** Hub pages build from live `index.json`; the
  publish job triggers `deploy_website.yml` on `main`. (A generic per-agent
  auto-redeploy is still being wired — see the guide's Verify step.)
- **Manual fallback:** `hub/agents/<id>/python/packaging/HUB-UPLOAD.md` documents the
  by-hand rclone path to the `gaia-hub` bucket — identical objects + lock as CI.

## Reference files

- `.github/workflows/release_agent_email.yml` — the canonical release workflow (its
  header comments document the whole contract).
- `docs/guides/hub-publishing.mdx` — the author guide (manifest, versioning, wheel +
  PR publish routes). The overlap's source of truth.
- `hub/agents/email/python/gaia-agent.yaml` — manifest reference.
- `hub/agents/email/npm/{package.json,binaries.lock.json,README.md}` —
  client package reference.
- `hub/agents/email/python/packaging/{freeze,smoke_test,publish_to_r2,gen_binaries_lock}.py`,
  `HUB-UPLOAD.md` — packaging + publish tooling.
- `workers/agent-hub/{README.md,schemas/manifest.schema.json,src/}` — the Worker, the
  `/publish` contract, and the server-side aggregate schema.
