// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA Agent Hub Worker — entry point + router.
 *
 * Routes:
 *   POST /publish                              publish a new agent version (auth)
 *   GET  /index.json                           lightweight catalog
 *   GET  /agents/<id>/manifest.json            per-agent aggregate manifest
 *   GET  /agents/<id>/<version>/<filename>     artifact / raw manifest download
 *   GET  /health                               liveness probe
 */

import { errorResponse, HttpError, json } from "./http";
import { handlePublish } from "./publish";
import { AGENTS_PREFIX, INDEX_KEY } from "./storage";
import type { Env } from "./types";

async function serveObject(bucket: R2Bucket, key: string): Promise<Response> {
  const obj = await bucket.get(key);
  if (!obj) {
    throw new HttpError(404, "not_found", `Object not found: ${key}.`);
  }
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("etag", obj.httpEtag);
  return new Response(obj.body, { headers });
}

async function route(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/health") {
    return json({ status: "ok" });
  }

  if (path === "/publish") {
    if (method !== "POST") {
      throw new HttpError(405, "method_not_allowed", "Use POST to /publish.");
    }
    return handlePublish(request, env);
  }

  if (method === "GET" || method === "HEAD") {
    if (path === "/index.json" || path === "/") {
      return serveObject(env.BUCKET, INDEX_KEY);
    }
    if (path.startsWith("/agents/")) {
      const key = decodeURIComponent(path.slice(1)); // strip leading "/"
      // Guard against path traversal in the object key.
      if (!key.startsWith(AGENTS_PREFIX) || key.includes("..")) {
        throw new HttpError(400, "invalid_path", `Invalid object path: ${path}.`);
      }
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
