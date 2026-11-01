// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The router's caching contract: Railway sends no Cache-Control, so every
// cacheable response must leave the router with one, and successful GETs must be
// served from the edge cache on the second request instead of round-tripping to
// Railway (#3548). Runs under `node --test` with fetch and caches.default faked.

import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";

import router from "../src/index.js";

const ORIGIN = "website-production-82ab.up.railway.app";
const ENV = { WEBSITE_ORIGIN: ORIGIN };

class FakeCache {
  constructor() {
    this.store = new Map();
    this.puts = [];
  }
  async match(request) {
    const hit = this.store.get(request.url);
    return hit ? hit.clone() : undefined;
  }
  async put(request, response) {
    assert.equal(request.method, "GET");
    assert.notEqual(response.status, 206, "the Cache API rejects partial content");
    this.puts.push(request.url);
    this.store.set(request.url, response.clone());
  }
}

let calls;
let cache;
let reply;

function ctx() {
  const pending = [];
  return { waitUntil: (p) => pending.push(p), settle: () => Promise.all(pending) };
}

async function get(path, init = {}) {
  const c = ctx();
  const res = await router.fetch(new Request(`https://amd-gaia.ai${path}`, init), ENV, c);
  await c.settle();
  return res;
}

beforeEach(() => {
  calls = [];
  cache = new FakeCache();
  reply = () => new Response("<html>ok</html>", { status: 200, headers: { etag: '"abc"' } });
  globalThis.caches = { default: cache };
  globalThis.fetch = async (request) => {
    calls.push(request);
    return reply(request);
  };
});

afterEach(() => {
  delete globalThis.caches;
  delete globalThis.fetch;
});

describe("Cache-Control on Railway responses", () => {
  it("gives HTML a 60 s public policy", async () => {
    const res = await get("/hub");
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("cache-control"), "public, max-age=60");
    assert.equal(res.headers.get("etag"), '"abc"');
    assert.equal(await res.text(), "<html>ok</html>");
  });

  it("gives content-hashed /_astro/* assets a year, immutable", async () => {
    const res = await get("/_astro/hub.zr_7_tm1.css");
    assert.equal(res.headers.get("cache-control"), "public, max-age=31536000, immutable");
  });

  for (const path of ["/_astro", "/_astrofoo/x.css", "/hub/_astro/x.css"]) {
    it(`does not treat ${path} as an immutable asset`, async () => {
      const res = await get(path);
      assert.equal(res.headers.get("cache-control"), "public, max-age=60");
    });
  }

  it("replaces a Cache-Control the origin might send", async () => {
    reply = () => new Response("x", { headers: { "cache-control": "private, max-age=9999" } });
    const res = await get("/");
    assert.equal(res.headers.get("cache-control"), "public, max-age=60");
  });

  for (const status of [404, 410, 500, 501]) {
    it(`marks a ${status} no-store and never caches it`, async () => {
      reply = () => new Response("nope", { status });
      const first = await get("/_astro/missing.js");
      assert.equal(first.status, status);
      assert.equal(first.headers.get("cache-control"), "no-store");
      await get("/_astro/missing.js");
      assert.equal(calls.length, 2);
      assert.deepEqual(cache.puts, []);
    });
  }

  it("leaves a redirect's headers alone and does not cache it", async () => {
    reply = () =>
      new Response(null, { status: 301, headers: { location: "https://amd-gaia.ai/hub/" } });
    const res = await get("/hub");
    assert.equal(res.status, 301);
    assert.equal(res.headers.get("cache-control"), null);
    assert.deepEqual(cache.puts, []);
  });
});

describe("edge cache", () => {
  it("serves the second GET from cache without touching Railway", async () => {
    await get("/");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, `https://${ORIGIN}/`);
    assert.equal(calls[0].method, "GET");
    assert.deepEqual(cache.puts, [`https://${ORIGIN}/`]);

    const second = await get("/");
    assert.equal(calls.length, 1);
    assert.equal(second.status, 200);
    assert.equal(second.headers.get("cache-control"), "public, max-age=60");
    assert.equal(await second.text(), "<html>ok</html>");
  });

  it("keys the cache on the query string", async () => {
    await get("/hub?q=a");
    await get("/hub?q=b");
    assert.equal(calls.length, 2);
  });

  it("does not store a 206 partial response", async () => {
    reply = () => new Response("par", { status: 206 });
    const res = await get("/_astro/big.js", { headers: { range: "bytes=0-2" } });
    assert.equal(res.status, 206);
    assert.deepEqual(cache.puts, []);
  });

  for (const method of ["POST", "HEAD", "PUT"]) {
    it(`passes ${method} straight through without the cache`, async () => {
      cache.match = () => assert.fail("a non-GET must not read the cache");
      const res = await get("/api", { method, body: method === "HEAD" ? undefined : "x" });
      assert.equal(calls.length, 1);
      assert.equal(calls[0].method, method);
      assert.equal(res.headers.get("cache-control"), null);
      assert.deepEqual(cache.puts, []);
    });
  }

  it("does not cache an unreachable origin", async () => {
    globalThis.fetch = async () => {
      throw new Error("connection refused");
    };
    const res = await get("/");
    assert.equal(res.status, 502);
    assert.equal(res.headers.get("cache-control"), "no-store");
    assert.deepEqual(cache.puts, []);
  });

  it("does not cache an upstream 503", async () => {
    reply = () => new Response("down", { status: 503 });
    const res = await get("/");
    assert.equal(res.status, 502);
    assert.equal(res.headers.get("cache-control"), "no-store");
    assert.deepEqual(cache.puts, []);
  });
});

describe("unchanged paths", () => {
  it("proxies /docs to Mintlify without the cache or a new Cache-Control", async () => {
    cache.match = () => assert.fail("/docs must not read the cache");
    const res = await get("/docs/guides/chat");
    assert.equal(calls[0].url, "https://amd-gaia.ai/docs/guides/chat");
    assert.equal(res.headers.get("cache-control"), null);
  });

  it("serves a no-store config error when WEBSITE_ORIGIN is malformed", async () => {
    const res = await router.fetch(
      new Request("https://amd-gaia.ai/"),
      { WEBSITE_ORIGIN: "https://bad" },
      ctx()
    );
    assert.equal(res.status, 502);
    assert.equal(res.headers.get("cache-control"), "no-store");
    assert.equal(calls.length, 0);
  });
});
