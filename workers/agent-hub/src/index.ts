// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA Agent Hub Worker — entry point + router.
 *
 * Routes:
 *   POST /publish                              publish a new agent version (auth)
 *   POST /publish/skill                        publish a new skill version (auth)
 *   GET  /index.json                           lightweight catalog (all lanes)
 *   GET  /agents/<id>/manifest.json            per-agent aggregate manifest
 *   GET  /agents/<id>/<version>/<filename>     artifact / raw manifest download
 *                                              (agents/tui/ and agents/terminal-hub/
 *                                              resolve each other's objects)
 *   GET  /skills/<name>/manifest.json          per-skill aggregate manifest
 *   GET  /skills/<name>/<version>/<filename>   skill bundle / SKILL.md download
 *   POST /reindex                              rebuild index.json from R2 (auth)
 *   GET  /health                               liveness probe
 */

import { readResolvedAgentManifest, rebuildIndex } from "./catalog";
import { errorResponse, HttpError, json } from "./http";
import { handlePublish } from "./publish";
import { handleSkillPublish } from "./skill-publish";
import { AGENTS_PREFIX, aliasOf, INDEX_KEY, SKILLS_PREFIX } from "./storage";
import type { Env } from "./types";

/** Serve the first of `keys` that exists; 404 names the first (the one requested). */
async function serveObject(bucket: R2Bucket, ...keys: string[]): Promise<Response> {
  let obj: R2ObjectBody | null = null;
  for (const key of keys) {
    obj = await bucket.get(key);
    if (obj) break;
  }
  if (!obj) {
    throw new HttpError(404, "not_found", `Object not found: ${keys[0]}.`);
  }
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("etag", obj.httpEtag);
  return new Response(obj.body, { headers });
}

/**
 * Serve `agents/<id>/...`. An id in an alias pair reads as one package: its
 * manifest is the merged view, and any other object falls back to the alias.
 */
async function serveAgentObject(bucket: R2Bucket, key: string): Promise<Response> {
  const [, id, ...rest] = key.split("/");
  const alias = aliasOf(id);
  if (!alias) return serveObject(bucket, key);
  const tail = rest.join("/");
  if (tail === "manifest.json") {
    const manifest = await readResolvedAgentManifest(bucket, id);
    if (!manifest) throw new HttpError(404, "not_found", `Object not found: ${key}.`);
    return json(manifest);
  }
  return serveObject(bucket, key, `${AGENTS_PREFIX}${alias}/${tail}`);
}

async function route(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/health") {
    // `build` is the commit this Worker was deployed from. A fixed "ok"
    // cannot tell a current Worker from a months-stale one — which is how an
    // outdated manifest validator went unnoticed until every release failed.
    return json({ status: "ok", build: env.WORKER_BUILD ?? "unknown" });
  }

  if (path === "/publish") {
    if (method !== "POST") {
      throw new HttpError(405, "method_not_allowed", "Use POST to /publish.");
    }
    return handlePublish(request, env);
  }

  if (path === "/publish/skill") {
    if (method !== "POST") {
      throw new HttpError(405, "method_not_allowed", "Use POST to /publish/skill.");
    }
    return handleSkillPublish(request, env);
  }

  if (path === "/reindex") {
    if (method !== "POST") {
      throw new HttpError(405, "method_not_allowed", "Use POST to /reindex.");
    }
    // Maintainer-only rebuild of index.json from the immutable R2 objects.
    // rebuildIndex is idempotent + non-destructive — it re-derives the catalog
    // from the stored artifacts, so a Worker schema change (e.g. the scorecard /
    // evaluation fields) can be backfilled without re-publishing a version.
    // Guarded by REINDEX_TOKEN, a secret separate from the CI PUBLISH_TOKENS.
    if (!env.REINDEX_TOKEN) {
      throw new HttpError(
        500,
        "config_error",
        "REINDEX_TOKEN is not set. Run `wrangler secret put REINDEX_TOKEN`."
      );
    }
    if (request.headers.get("Authorization") !== `Bearer ${env.REINDEX_TOKEN}`) {
      throw new HttpError(401, "unauthorized", "Missing or invalid reindex token.");
    }
    const baseUrl = new URL(request.url).origin;
    const index = await rebuildIndex(env.BUCKET, new Date(), baseUrl);
    return json({ status: "reindexed", agents: index.agents.length });
  }

  if (method === "GET" || method === "HEAD") {
    if (path === "/index.json" || path === "/") {
      return serveObject(env.BUCKET, INDEX_KEY);
    }
    if (path.startsWith("/agents/") || path.startsWith("/skills/")) {
      const key = decodeURIComponent(path.slice(1)); // strip leading "/"
      // Guard against path traversal in the object key.
      const prefix = path.startsWith("/agents/") ? AGENTS_PREFIX : SKILLS_PREFIX;
      if (!key.startsWith(prefix) || key.includes("..")) {
        throw new HttpError(400, "invalid_path", `Invalid object path: ${path}.`);
      }
      if (prefix === AGENTS_PREFIX) return serveAgentObject(env.BUCKET, key);
      return serveObject(env.BUCKET, key);
    }
  }

  throw new HttpError(404, "not_found", `No route for ${method} ${path}.`);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      return await route(request, env);
    } catch (err) {
      if (err instanceof HttpError) {
        return errorResponse(err);
      }
      // Unexpected failure — surface a 500 with the message, never swallow it.
      const message = err instanceof Error ? err.message : String(err);
      return errorResponse(new HttpError(500, "internal_error", message));
    }
  },
};
