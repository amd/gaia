// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Router for amd-gaia.ai: /docs* stays on Mintlify, everything else -> the
// Railway Astro website. Lets the Astro site own the domain root
// (/, /hub, /_astro, …) while the existing Mintlify docs stay live at /docs.
//
// /docs is served by re-fetching the SAME zone (amd-gaia.ai). Cloudflare does
// not let a Worker route be the target of a same-zone fetch(), so that
// subrequest bypasses this Worker and goes straight to the zone origin
// (Mintlify) — loop-free, with the correct Host: amd-gaia.ai for tenant
// resolution. Everything else is reverse-proxied to the Railway website by its
// own public hostname (which is also the Host it routes by).

const DOCS_ORIGIN = "amd-gaia.ai";

const ASSET_CACHE_TTL = 31536000;
const HTML_CACHE_TTL = 60;

function isDocs(pathname) {
  return pathname === "/docs" || pathname.startsWith("/docs/");
}

// Astro emits content-hashed filenames under /_astro, so they are immutable.
function isImmutableAsset(pathname) {
  return pathname.startsWith("/_astro/");
}

// Railway sends no Cache-Control, so the router states the policy itself — for
// browsers and for the edge cache (the Cache API honours this header).
function cacheControlFor(pathname, status) {
  if (status >= 200 && status < 300) {
    return isImmutableAsset(pathname)
      ? `public, max-age=${ASSET_CACHE_TTL}, immutable`
      : `public, max-age=${HTML_CACHE_TTL}`;
  }
  // Never let an error or a 404 be cached: a Railway restart mid-deploy would
  // otherwise pin a broken /_astro/* asset for a year.
  if (status >= 400) return "no-store";
  return null;
}

function upstreamError(label, origin, detail, hint) {
  console.error(
    `website-router: ${label} origin ${origin} failed (${detail}) — ${hint}`
  );
  return new Response(
    `website-router: upstream ${label} origin ${origin} is unreachable (${detail}). ${hint}`,
    {
      status: 502,
      headers: {
        "content-type": "text/plain; charset=utf-8",
        "cache-control": "no-store",
      },
    }
  );
}

async function proxy(target, request, { label, origin, hint }) {
  let response;
  try {
    response = await fetch(new Request(target, request));
  } catch (err) {
    return upstreamError(label, origin, err, hint);
  }
  if (response.status >= 502) {
    return upstreamError(label, origin, `HTTP ${response.status}`, hint);
  }
  return response;
}

// Never throw for a config error: a thrown Worker renders Cloudflare's generic
// "Error 1101" page, which names neither this Worker nor the cause.
function configError(detail) {
  console.error(`website-router: ${detail}`);
  return new Response(
    `website-router: ${detail}\n\nSet it under [vars] in ` +
      `workers/website-router/wrangler.toml to the Railway public hostname of the ` +
      `'website' service — a bare hostname such as ` +
      `website-production-82ab.up.railway.app, with no scheme, port, path or ` +
      `spaces — then redeploy with \`npx wrangler deploy\`.\n`,
    {
      status: 502,
      headers: {
        "content-type": "text/plain; charset=utf-8",
        "cache-control": "no-store",
      },
    }
  );
}

// `url.hostname = x` silently ignores anything that is not a bare host, so a
// pasted "https://host" would leave the target on amd-gaia.ai and quietly serve
// the whole apex from Mintlify. Read it back instead of trusting the setter.
function resolveWebsiteOrigin(env) {
  const origin = env && env.WEBSITE_ORIGIN;
  if (!origin) {
    return { error: configError("WEBSITE_ORIGIN is not set.") };
  }
  const probe = new URL("https://placeholder.invalid/");
  probe.hostname = origin;
  if (probe.hostname !== origin.toLowerCase()) {
    return {
      error: configError(
        `WEBSITE_ORIGIN is not a bare hostname (got "${origin}").`
      ),
    };
  }
  return { origin: probe.hostname };
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // Docs: same-zone fetch -> Cloudflare sends it to the origin (Mintlify),
    // bypassing this route. Preserves method/headers/body and Host: amd-gaia.ai.
    if (isDocs(url.pathname)) {
      const docsUrl = new URL(url.toString());
      docsUrl.protocol = "https:";
      docsUrl.hostname = DOCS_ORIGIN;
      docsUrl.port = "";
      return proxy(docsUrl.toString(), request, {
        label: "Mintlify",
        origin: DOCS_ORIGIN,
        hint: "Check the Mintlify project serving the amd-gaia.ai docs, and the zone's origin DNS record.",
      });
    }

    const { origin: websiteOrigin, error: configFailure } =
      resolveWebsiteOrigin(env);
    if (configFailure) return configFailure;

    // Everything else -> the Railway website.
    const target = new URL(url.toString());
    target.protocol = "https:";
    target.hostname = websiteOrigin;
    target.port = "";

    const railway = {
      label: "Railway",
      origin: websiteOrigin,
      hint: "Check the Railway 'website' service in project gaia-agent-hub.",
    };
    if (request.method !== "GET") {
      return proxy(target.toString(), request, railway);
    }

    // The Cache API, not fetch()'s cf options: those left HTML uncached at the
    // edge (cf-cache-status: DYNAMIC) on this third-party origin.
    const cache = caches.default;
    const cacheKey = new Request(target.toString(), { method: "GET" });
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const upstream = await proxy(target.toString(), request, railway);
    const cacheControl = cacheControlFor(url.pathname, upstream.status);
    if (cacheControl === null) return upstream;

    const response = new Response(upstream.body, upstream);
    response.headers.set("cache-control", cacheControl);
    // Only a full 200: the Cache API rejects a 206 partial response.
    if (upstream.status === 200) {
      ctx.waitUntil(cache.put(cacheKey, response.clone()));
    }
    return response;
  },
};
