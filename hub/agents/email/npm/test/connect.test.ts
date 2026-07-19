// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * connectSidecar (attach mode): waits for /health, version-checks a server we did
 * NOT spawn, and returns a client bound to it. The counterpart to startSidecar for
 * the fast dev loop — no child process, nothing to shut down.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { connectSidecar } from "../src/lifecycle.js";
import { VersionMismatchError } from "../src/errors.js";
import { SCHEMA_VERSION } from "../src/types.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** A fake global fetch that answers /health and /version, capturing headers. */
function stubServer(opts: { apiVersion?: string } = {}) {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  const impl = vi.fn(async (url: string | URL, init?: RequestInit) => {
    const u = String(url);
    const headers: Record<string, string> = {};
    if (init?.headers) {
      for (const [k, v] of Object.entries(init.headers as Record<string, string>)) {
        headers[k.toLowerCase()] = v;
      }
    }
    calls.push({ url: u, headers });
    if (u.endsWith("/health")) return jsonResponse({ status: "ok", service: "gaia-agent-email" });
    if (u.endsWith("/version"))
      return jsonResponse({ apiVersion: opts.apiVersion ?? SCHEMA_VERSION, agentVersion: "0.4.0" });
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", impl);
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("connectSidecar", () => {
  it("attaches to a running server and returns a bound client + parsed host/port", async () => {
    stubServer();
    const dev = await connectSidecar({ baseUrl: "http://127.0.0.1:8131" });
    expect(dev.host).toBe("127.0.0.1");
    expect(dev.port).toBe(8131);
    expect(dev.baseUrl).toBe("http://127.0.0.1:8131");
    // The returned client actually works against the same server.
    const health = await dev.client.health();
    expect(health.status).toBe("ok");
  });

  it("forwards an auth token on requests when supplied", async () => {
    const calls = stubServer();
    const dev = await connectSidecar({ baseUrl: "http://127.0.0.1:8131", authToken: "tok-123" });
    expect(dev.authToken).toBe("tok-123");
    await dev.client.version();
    // Every captured call that carried the token must present it as a bearer.
    const versioned = calls.filter((c) => c.url.endsWith("/version"));
    expect(versioned.some((c) => c.headers.authorization === "Bearer tok-123")).toBe(true);
  });

  it("throws VersionMismatchError on a major contract mismatch", async () => {
    stubServer({ apiVersion: "99.0" });
    await expect(connectSidecar({ baseUrl: "http://127.0.0.1:8131" })).rejects.toBeInstanceOf(
      VersionMismatchError,
    );
  });

  it("skips the version check when verifyVersion is false", async () => {
    stubServer({ apiVersion: "99.0" }); // would fail the check if it ran
    const dev = await connectSidecar({
      baseUrl: "http://127.0.0.1:8131",
      verifyVersion: false,
    });
    expect(dev.baseUrl).toBe("http://127.0.0.1:8131");
  });

  it("requires a baseUrl", async () => {
    await expect(
      connectSidecar({} as unknown as Parameters<typeof connectSidecar>[0]),
    ).rejects.toBeInstanceOf(TypeError);
  });
});
