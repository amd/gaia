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
Remove the `routes` entry from `wrangler.toml` and redeploy, or delete the route in
the Cloudflare dashboard (Workers &rarr; website-router &rarr; Triggers) / via the API.
To take the Worker down entirely: `npx wrangler delete`.

## Configuration

`WEBSITE_ORIGIN` (the Railway public hostname) lives in the `[vars]` block of
`wrangler.toml`. If the Railway service URL changes, update it there and redeploy —
no code change.

It must be a **bare hostname** (`website-production-82ab.up.railway.app`) — no
scheme, port or path. `url.hostname = …` silently ignores anything else, so a
pasted `https://…` would leave the target on `amd-gaia.ai` and quietly serve the
whole apex from Mintlify. The Worker reads the value back after assigning it and
throws an error naming the var if it is missing or malformed, rather than falling
back to a guess.

## When an origin is down

Both origins are proxied through a wrapper that turns a thrown `fetch()` or an
upstream `>= 502` into a **502 naming the failing origin and where to look** —
e.g. `website-router: upstream Railway origin … is unreachable (…). Check the
Railway 'website' service in project gaia-agent-hub.` Without it, Cloudflare
serves its generic "Error 1101 Worker threw exception" page, which names neither
Railway nor Mintlify and sends on-call to the wrong system. There is deliberately
no fallback to stale or substitute content — the error surfaces.

## Caching

Railway's `serve` emits `ETag` but no `Cache-Control`, and Worker subrequests to a
third-party hostname are not edge-cached by default — so every byte round-tripped
to Railway. The Worker sets `cf.cacheEverything` on GET requests to Railway:
`/_astro/*` for a year (filenames are content-hashed, so this is safe) and 60s for
everything else, so an HTML change goes live within a minute.

TTLs are set per status (`cacheTtlByStatus`), never as a blanket `cacheTtl` — 404s
get 1s and 5xx are not cached at all. Railway restarts on deploy, and a blanket TTL
would let an asset 404 served during that window stick at the edge for a year.

`/docs*` is left uncached — its behaviour is unchanged.

## Notes

- The Astro site is built with `site: 'https://amd-gaia.ai'` and root-relative
  assets (`/_astro/*`), i.e. it is designed to own the apex — which this router
  enables while preserving `/docs`.
