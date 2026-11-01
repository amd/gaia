# website-router (Cloudflare Worker)

Fronts the **amd-gaia.ai apex** and splits traffic between two origins:

| Path            | Origin                                             | Why |
|-----------------|----------------------------------------------------|-----|
| `/docs`, `/docs/*` | Mintlify (the zone origin)                       | Keeps the existing docs live, unchanged. |
| everything else | Railway Astro site (`website/`)                    | Landing (`/`), Agent Hub (`/hub`, `/hub/<id>`), assets (`/_astro/*`). |

## How the `/docs` carve-out stays loop-free

`amd-gaia.ai` is a proxied Cloudflare zone whose origin is Mintlify. This Worker is
bound to the route `amd-gaia.ai/*`, so it intercepts every apex request. For a
`/docs` request it re-`fetch()`es the **same zone** (`https://amd-gaia.ai/docs…`).
Cloudflare does not allow a Worker route to be the target of a same-zone `fetch()`,
so that subrequest **bypasses this Worker and goes straight to the origin (Mintlify)**
with the correct `Host: amd-gaia.ai` — no loop, no Host-override tricks (Workers
ignore a manually set `Host`, so proxying to `cname.mintlify.app` does not work).

`hub.amd-gaia.ai` is a **separate** Worker (`workers/agent-hub`) on its own custom
domain; the `amd-gaia.ai/*` route does not match it.

## Deploy

```bash
cd workers/website-router
npx wrangler deploy          # creates/updates the amd-gaia.ai/* route
```

## Rollback (revert to docs-only on the apex)

Delete the `amd-gaia.ai/*` route — traffic falls back to the zone origin (Mintlify).
Do it in the Cloudflare dashboard (Workers &rarr; website-router &rarr; Settings &rarr;
Domains & Routes) or via the API. To take the Worker down entirely:

```bash
npx wrangler delete   # removes the Worker and its routes
```

(`wrangler triggers` only has a `deploy` subcommand — there is no route-delete
command, so route removal is a dashboard/API action.)

## Configuration

`WEBSITE_ORIGIN` (the Railway public hostname) lives in the `[vars]` block of
`wrangler.toml`. If the Railway service URL changes, update it there and redeploy —
no code change.

It must be a **bare hostname** (`website-production-82ab.up.railway.app`) — no
scheme, port or path. `url.hostname = …` silently ignores anything else, so a
pasted `https://…` would leave the target on `amd-gaia.ai` and quietly serve the
whole apex from Mintlify. The Worker reads the value back after assigning it and,
if the var is missing or malformed, serves a 502 naming the var and the expected
shape rather than falling back to a guess. It does **not** throw — a thrown Worker
renders Cloudflare's generic "Error 1101" page, which names neither this Worker nor
the cause.

## When an origin is down

Both origins are proxied through a wrapper that turns a thrown `fetch()` or an
upstream `>= 502` into a **502 naming the failing origin and where to look** —
e.g. `website-router: upstream Railway origin … is unreachable (…). Check the
Railway 'website' service in project gaia-agent-hub.` Without it, Cloudflare
serves its generic "Error 1101 Worker threw exception" page, which names neither
Railway nor Mintlify and sends on-call to the wrong system. There is deliberately
no fallback to stale or substitute content — the error surfaces.

## Caching

Railway's `serve` emits `ETag` but no `Cache-Control`, so the router sets the
policy itself on every GET it proxies to Railway — the header reaches browsers and
any downstream cache, not just Cloudflare's edge:

| Response | `Cache-Control` |
|----------|-----------------|
| 2xx under `/_astro/*` (content-hashed, so safe) | `public, max-age=31536000, immutable` |
| other 2xx (HTML) | `public, max-age=60` — an HTML change goes live within a minute |
| 4xx / 5xx | `no-store` |
| 3xx | left as Railway sent it |

A full `200` is also stored in the edge cache with the Cache API (`caches.default`),
so a repeat request within its TTL is served without a round-trip to Railway.
`fetch()`'s `cf.cacheEverything` is not used: on this third-party origin it left HTML
uncached (`cf-cache-status: DYNAMIC`). Errors and 404s are never cached — Railway
restarts on deploy, and a cached 404 for an asset would outlive the restart.
Non-GET requests bypass the cache untouched.

`/docs*` is left uncached — its behaviour is unchanged.

The caching contract is covered by `npm test` (`node --test`, no dependencies),
which CI runs on every change to this directory (`website_router_ci.yml`).

## Notes

- The Astro site is built with `site: 'https://amd-gaia.ai'` and root-relative
  assets (`/_astro/*`), i.e. it is designed to own the apex — which this router
  enables while preserving `/docs`.
