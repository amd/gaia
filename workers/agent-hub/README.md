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
| `POST /publish` | Bearer | Publish a new agent version (validate → scope-check → immutability-check → checksum → store → rebuild index) |
| `GET /index.json` | none | Lightweight catalog of every agent (latest version only) |
| `GET /agents/<id>/manifest.json` | none | Per-agent aggregate manifest (all versions) |
| `GET /agents/<id>/<version>/<file>` | none | Download an artifact or the raw `gaia-agent.yaml` |
| `GET /health` | none | Liveness probe |

### Publish guarantees

- **Per-publisher auth.** Bearer token resolved against the `PUBLISH_TOKENS`
  secret. Unknown/missing token → `401`. Missing secret → `500` (fails loudly;
  it never falls back to allow-all).
- **Publisher scope.** A token may only publish under the `author` values it is
  granted (`"*"` grants any). Once an agent id exists, only a publish whose
  `author` matches the recorded owner can add versions — you cannot hijack
  someone else's agent id.
- **Version immutability.** Republishing an existing `<id>@<version>` is rejected
  with `409`. The version list in `agents/<id>/manifest.json` is the source of
  truth, with an object-level `head()` check as defense in depth.
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
```

## JSON shapes

Field names mirror `gaia-agent.yaml` (parsed by `src/gaia/hub/manifest.py`) so the
catalog is consumable by the same code that reads source manifests. Formal
schemas live in [`schemas/`](./schemas):

- [`schemas/index.schema.json`](./schemas/index.schema.json) — `GET /index.json`.
  Each entry: `id`, `name`, `description`, `category`, `latest_version`, `icon`,
  `language`, `author`, `security_tier`, `download_size_bytes`,
  `requirements.platforms`, `deprecated`.
- [`schemas/manifest.schema.json`](./schemas/manifest.schema.json) —
  `GET /agents/<id>/manifest.json`. Full display metadata plus a `versions` map;
  each version carries `published_at`, `publisher`, `deprecated`, and an
  `artifact` block (`filename`, `path`, `size_bytes`, `sha256`, `content_type`).

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
  -F "artifact=@dist/gaia_agent_chat-0.1.0-py3-none-any.whl"

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
