# GAIA Agent Hub — R2 distribution Worker

A Cloudflare Worker fronting an R2 bucket. It is the cloud distribution layer for
the [Agent Hub](https://amd-gaia.ai/docs/spec/agent-hub-restructure): publishers
upload an agent's `gaia-agent.yaml` + artifact (wheel or native binary), the
Worker validates and stores them immutably, generates a server-side checksum, and
rebuilds a lightweight catalog that the `gaia agent` CLI and the Hub UI read.

Implements [#1095](https://github.com/amd/gaia/issues/1095) (Phase 3A–3C of the
Agent Hub plan). This directory is **isolated infra** — it does not import or
depend on any `src/gaia` code.

## What it does

| Route | Auth | Purpose |
|-------|------|---------|
| `POST /publish` | Bearer | Publish a new agent version (validate → scope-check → immutability-check → checksum → store → rebuild index). Form parts: `manifest` (gaia-agent.yaml text), `artifact` (wheel/binary/zip file), optional `readme` + `changelog` + `spec` + `skill` + `evaluation` + `capability_matrix` + `eval_scorecard` (markdown, rendered as the Hub page's doc tabs), and optional `package_files` (JSON `{files:[{name,size_bytes}]}` listing the contents of a whole-package `.zip` artifact — surfaced as the catalog's `package`) |
| `GET /index.json` | none | Catalog of every agent (latest version only), including the latest README + CHANGELOG + SPEC + SKILL + EVALUATION + CAPABILITY_MATRIX + scorecard markdown |
| `GET /agents/<id>/manifest.json` | none | Per-agent aggregate manifest (all versions) |
| `GET /agents/<id>/<version>/<file>` | none | Download an artifact, the raw `gaia-agent.yaml`, `README.md`, `CHANGELOG.md`, `SPEC.md`, `SKILL.md`, `EVALUATION.md`, `CAPABILITY_MATRIX.md`, or `SCORECARD.md` |
| `GET /health` | none | Liveness probe |

### Publish guarantees

- **Per-publisher auth.** Bearer token resolved against the `PUBLISH_TOKENS`
  secret. Unknown/missing token → `401`. Missing secret → `500` (fails loudly;
  it never falls back to allow-all).
- **Publisher scope.** A token may only publish under the `author` values it is
  granted (`"*"` grants any). Once an agent id exists, only a publish whose
  `author` matches the recorded owner can add versions — you cannot hijack
  someone else's agent id.
- **Version immutability (per filename).** A published artifact is never
  overwritten: re-uploading the same `agents/<id>/<version>/<filename>` is
  rejected with `409`, enforced by an object-level `head()` check. A version's
  artifact set is **append-only per distinct filename** — see *Multi-platform
  releases* below. A `409` on an artifact that already matches is the idempotent
  re-run signal a release job treats as "already published".
- **Multi-platform releases.** A single `<id>@<version>` may hold more than one
  artifact — one per platform for a native binary (e.g. the frozen email agent
  ships four binaries under `email@0.1.0`). The first publish of a version
  creates it (and stores the immutable `gaia-agent.yaml`); each later publish
  under the same version with a *new* filename appends another artifact. The
  per-agent manifest's `versions[v]` records every artifact in `artifacts[]`,
  with `artifact` kept as the primary (first-published) entry for single-artifact
  (wheel) agents and catalog display.
- **Server-side SHA-256.** The checksum is computed by the Worker from the bytes
  it received — never trusted from the request. It is also handed to R2's `put`
  integrity check.
- **Automatic index rebuild.** After every successful publish, `index.json` is
  regenerated from all per-agent manifests.

## R2 bucket layout

```
index.json                                     # lightweight catalog (all agents)
agents/<id>/manifest.json                       # per-agent aggregate (all versions)
agents/<id>/<version>/gaia-agent.yaml           # exact manifest uploaded for this version
agents/<id>/<version>/README.md                 # README markdown for this version (if published)
agents/<id>/<version>/CHANGELOG.md              # CHANGELOG markdown for this version (if published)
agents/<id>/<version>/SPEC.md                   # SPEC markdown for this version (if published)
agents/<id>/<version>/SKILL.md                  # SKILL markdown for this version (if published)
agents/<id>/<version>/EVALUATION.md             # EVALUATION markdown for this version (if published)
agents/<id>/<version>/CAPABILITY_MATRIX.md      # capability matrix markdown for this version (if published)
agents/<id>/<version>/SCORECARD.md              # eval scorecard markdown for this version (if published)
agents/<id>/<version>/<filename>                # the artifact (wheel or binary)
```

Example:

```
index.json
agents/chat/manifest.json
agents/chat/0.1.0/gaia-agent.yaml
agents/chat/0.1.0/gaia_agent_chat-0.1.0-py3-none-any.whl
agents/chat/0.2.0/gaia-agent.yaml
agents/chat/0.2.0/gaia_agent_chat-0.2.0-py3-none-any.whl
agents/email/manifest.json
agents/email/0.1.0/gaia-agent.yaml
agents/email/0.1.0/email-agent-win32-x64.exe        # multi-platform: 4 binaries,
agents/email/0.1.0/email-agent-darwin-arm64         # one version
agents/email/0.1.0/email-agent-darwin-x64
agents/email/0.1.0/email-agent-linux-x64
```

## JSON shapes

Field names mirror `gaia-agent.yaml` (parsed by `src/gaia/hub/manifest.py`) so the
catalog is consumable by the same code that reads source manifests. Formal
schemas live in [`schemas/`](./schemas):

- [`schemas/index.schema.json`](./schemas/index.schema.json) — `GET /index.json`.
  Each entry: `id`, `name`, `description`, `category`, `latest_version`, `icon`,
  `language`, `author`, `security_tier`, `download_size_bytes`, `tags`,
  `tools_count`, `models`, `min_gaia_version`, `permissions`, `deprecated`,
  `deprecation_message` (only when set), full `requirements` (`min_memory_gb`,
  `min_disk_gb`, `min_context_size`, `platforms`, `npu` as
  `"required"`/`"optional"`, `gpu_vram_gb`), `readme` (latest version's README
  markdown, `""` if none was published), `changelog` (latest version's CHANGELOG
  markdown, `""` if none was published), `spec` + `skill` + `evaluation` +
  `capability_matrix` (latest version's SPEC.md / SKILL.md / EVALUATION.md /
  CAPABILITY_MATRIX.md markdown, `""` if none was published), `scorecard` (latest
  version's SCORECARD.md body with the YAML front matter stripped, `""` if none
  was published), the optional `eval_scorecard_url` + `eval_score` (the raw
  scorecard's public URL and its parsed 0–100 aggregate, absent when no scorecard
  was published), the optional `npm_package` /
  `playground_url` (present only when the manifest declares them — they drive the
  hub page's npm install method and playground launcher), and the optional
  `package` (`{ filename, size_bytes, files: [{name, size_bytes}] }` — the
  whole-package `.zip` download + its file listing, present only when a
  `package_files` manifest was published). This shape is the build-time contract
  for the website Hub pages (`website/src/data/catalog.ts`).
- [`schemas/manifest.schema.json`](./schemas/manifest.schema.json) —
  `GET /agents/<id>/manifest.json`. Full display metadata plus a `versions` map;
  each version carries `published_at`, `publisher`, `deprecated`, an `artifact`
  block (the primary — `filename`, `path`, `size_bytes`, `sha256`,
  `content_type`), and an `artifacts[]` array of every per-platform artifact in
  that version.

## Local development & testing

No real Cloudflare account or R2 bucket is needed for development.

```bash
npm install
npm test            # vitest — runs the full handlers against an in-memory R2 fake
npm run typecheck   # tsc --noEmit
npm run dev         # wrangler dev — Miniflare's simulated R2, no real bucket
npm run deploy:dry-run   # validate wrangler.toml + bundle without deploying
```

`npm test` exercises the request handlers end-to-end (auth rejection, scope
enforcement, version immutability, checksum generation, index rebuild, download
routes) using `test/fake-r2.ts`, an in-memory R2 that implements the subset of
the `R2Bucket` API the Worker uses. The handlers rely only on Web-standard
globals (`Request`, `Response`, `FormData`, `crypto.subtle`), so the suite runs
in plain Node without Miniflare.

### Try a publish against `wrangler dev`

```bash
# Terminal 1
PUBLISH_TOKENS='{"dev-token":{"publisher":"AMD","authors":["AMD"]}}' npm run dev

# Terminal 2
curl -X POST http://localhost:8787/publish \
  -H "Authorization: Bearer dev-token" \
  -F "manifest=@hub/agents/python/chat/gaia-agent.yaml" \
  -F "artifact=@dist/gaia_agent_chat-0.1.0-py3-none-any.whl" \
  -F "readme=@hub/agents/python/chat/README.md;type=text/markdown" \
  -F "changelog=@hub/agents/python/chat/CHANGELOG.md;type=text/markdown"

curl http://localhost:8787/index.json
```

## Deploying (maintainer)

Deploy requires Cloudflare resources the maintainer provisions — they are **not**
checked into the repo:

1. **Create the R2 bucket** named to match `bucket_name` in
   [`wrangler.toml`](./wrangler.toml) (default `gaia-agent-hub`):

   ```bash
   npx wrangler r2 bucket create gaia-agent-hub
   ```

2. **Set the publisher token map** as a secret (JSON of token → publisher):

   ```bash
   npx wrangler secret put PUBLISH_TOKENS
   # paste, e.g.:
   # {
   #   "<amd-token>":   { "publisher": "AMD",        "authors": ["AMD"] },
   #   "<indie-token>": { "publisher": "Jane Dev",   "authors": ["Jane Dev"] },
   #   "<admin-token>": { "publisher": "Hub Admin",  "authors": ["*"] }
   # }
   ```

   Tokens are tied to the AMD Developer Program. The `authors` list bounds which
   `author` values a token may publish under; `"*"` is reserved for hub admins.

3. **Deploy:**

   ```bash
   npx wrangler deploy
   ```

4. **(Optional) Bind the route** by uncommenting the `routes` line in
   `wrangler.toml` to serve the API under `hub.amd-gaia.ai/*`.

`MAX_ARTIFACT_BYTES` (a plain var, default 250 MiB) caps artifact size and can be
overridden per environment without a secret.

## Deploying on Railway (demo)

For demo/staging only: [`Dockerfile`](./Dockerfile) runs `wrangler dev`
(Miniflare) with simulated R2 persisted to a Railway volume — no Cloudflare
account needed. Railway service settings:

| Setting | Value |
|---------|-------|
| Root directory | `workers/agent-hub` |
| Env var | `PUBLISH_TOKENS` — JSON token map (same shape as the production secret above). The container fails at startup if unset. |
| Volume | mount at `/data` (simulated R2 state lives in `/data/wrangler-state`) |
| Healthcheck | `/health` (set via [`railway.json`](./railway.json)) |

Railway injects `PORT` automatically; the container listens on `0.0.0.0:$PORT`.

## Layout

```
workers/agent-hub/
├── src/
│   ├── index.ts      # entry point + router
│   ├── publish.ts    # POST /publish handler
│   ├── auth.ts       # bearer auth + publisher scope
│   ├── manifest.ts   # gaia-agent.yaml validation + semver
│   ├── catalog.ts    # per-agent manifest + index.json rebuild
│   ├── storage.ts    # R2 key layout + read/write helpers
│   ├── http.ts       # HttpError + JSON response helpers
│   └── types.ts      # shared types (mirror gaia-agent.yaml)
├── schemas/          # index.schema.json, manifest.schema.json
├── test/             # vitest suite + in-memory R2 fake
├── wrangler.toml
├── package.json
└── tsconfig.json
```
