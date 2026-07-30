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

// Never let an error or a 404 inherit the long TTL: a Railway restart mid-deploy
// would otherwise pin a broken /_astro/* asset at the edge for a year.
function cacheTtlByStatus(okTtl) {
  return { "200-299": okTtl, "404": 1, "500-599": 0 };
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

async function proxy(target, request, { label, origin, hint, cf }) {
  let response;
  try {
    response = await fetch(
      new Request(target, request),
      cf ? { cf } : undefined
    );
  } catch (err) {
    return upstreamError(label, origin, err, hint);
  }
  if (response.status >= 502) {
    return upstreamError(label, origin, `HTTP ${response.status}`, hint);
  }
  return response;
}

// `url.hostname = x` silently ignores anything that is not a bare host, so a
// pasted "https://host" would leave the target on amd-gaia.ai and quietly serve
// the whole apex from Mintlify. Read it back instead of trusting the setter.
function resolveWebsiteOrigin(env) {
  const origin = env && env.WEBSITE_ORIGIN;
  if (!origin) {
    throw new Error(
      "website-router: WEBSITE_ORIGIN is not set. Add it under [vars] in " +
        "workers/website-router/wrangler.toml (value: the Railway public " +
        "hostname of the 'website' service, e.g. website-production-82ab.up.railway.app) " +
        "and redeploy with `npx wrangler deploy`."
    );
  }
  const probe = new URL("https://placeholder.invalid/");
  probe.hostname = origin;
  if (probe.hostname !== origin.toLowerCase()) {
    throw new Error(
      `website-router: WEBSITE_ORIGIN in workers/website-router/wrangler.toml is not a ` +
        `bare hostname (got "${origin}"). Use "website-production-82ab.up.railway.app", ` +
        `not a full URL, and no scheme, port, path or spaces.`
    );
  }
  return probe.hostname;
}

export default {
  async fetch(request, env) {
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

    const websiteOrigin = resolveWebsiteOrigin(env);

    // Everything else -> the Railway website.
    const target = new URL(url.toString());
    target.protocol = "https:";
    target.hostname = websiteOrigin;
    target.port = "";

    const cacheable = request.method === "GET";

    return proxy(target.toString(), request, {
      label: "Railway",
      origin: websiteOrigin,
      hint: "Check the Railway 'website' service in project gaia-agent-hub.",
      cf: cacheable
        ? {
            cacheEverything: true,
            cacheTtlByStatus: cacheTtlByStatus(
              isImmutableAsset(url.pathname) ? ASSET_CACHE_TTL : HTML_CACHE_TTL
            ),
          }
        : undefined,
    });
  },
};
