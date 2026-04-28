// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Tests for src/gaia/apps/webui/services/index-query.cjs and the
// renderer-side allowlist regex in src/gaia/apps/webui/src/utils/apiBase.ts.
//
// The literal #851 regression was: the renderer hardcoded `localhost:4200`
// while port-manager picked a random port. Three layers must agree:
//
//   1. main.cjs spawns the backend with `--ui-port <N>` (port-manager picks N).
//   2. main.cjs writes that same N into the index.html `?api=` query string.
//   3. apiBase.ts in the renderer reads `?api=`, validates it against an
//      allowlist (loopback only), and uses it as the API base URL.
//
// This file pins all three contracts so a future edit to any one of them
// fails CI before the regression can ship. Uses `node:test` so it can run
// via `node --test tests/electron/test_loadapp_query.mjs`, matching the
// existing test_port_manager.mjs convention.
//
// Sister test: tests/electron/test_port_manager.mjs (port allocation).

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const indexQueryPath = path.resolve(
  __dirname,
  "..",
  "..",
  "src",
  "gaia",
  "apps",
  "webui",
  "services",
  "index-query.cjs"
);

const { buildIndexQuery, API_PATH } = require(indexQueryPath);

// ─── buildIndexQuery: positive cases ─────────────────────────────────

test("buildIndexQuery: typical random port produces loopback URL", () => {
  assert.deepStrictEqual(
    buildIndexQuery(9876),
    { api: "http://127.0.0.1:9876/api" }
  );
});

test("buildIndexQuery: default port (4200) still works", () => {
  // The DEFAULT_BACKEND_PORT fallback path in main.cjs must produce a
  // valid URL too — port-manager.findFreePort() rejection lands here.
  assert.deepStrictEqual(
    buildIndexQuery(4200),
    { api: "http://127.0.0.1:4200/api" }
  );
});

test("buildIndexQuery: max valid port (65535)", () => {
  assert.deepStrictEqual(
    buildIndexQuery(65535),
    { api: "http://127.0.0.1:65535/api" }
  );
});

// ─── buildIndexQuery: negative cases ─────────────────────────────────
//
// These are unreachable from the production path today (port-manager's
// catch falls back to DEFAULT_BACKEND_PORT = 4200), but the throws guard
// against future direct callers passing nonsense.

test("buildIndexQuery: rejects 0", () => {
  assert.throws(() => buildIndexQuery(0), /invalid port/);
});

test("buildIndexQuery: rejects negative", () => {
  assert.throws(() => buildIndexQuery(-1), /invalid port/);
});

test("buildIndexQuery: rejects undefined", () => {
  assert.throws(() => buildIndexQuery(undefined), /invalid port/);
});

test("buildIndexQuery: rejects > 65535", () => {
  assert.throws(() => buildIndexQuery(70000), /invalid port/);
});

test("buildIndexQuery: rejects non-integer", () => {
  assert.throws(() => buildIndexQuery(1234.5), /invalid port/);
});

test("buildIndexQuery: rejects string", () => {
  assert.throws(() => buildIndexQuery("8080"), /invalid port/);
});

// ─── API_PATH constant ───────────────────────────────────────────────

test("API_PATH is /api (no trailing slash)", () => {
  // Trailing slash would produce `//path` URLs in apiFetch (api.ts).
  assert.strictEqual(API_PATH, "/api");
});

// ─── Renderer-side allowlist regex round-trip ────────────────────────
//
// We intentionally re-derive the regex here from apiBase.ts source rather
// than duplicate it as a literal: the source is the contract; if it
// changes, the test must reflect the change deliberately.

const apiBaseTsPath = path.resolve(
  __dirname,
  "..",
  "..",
  "src",
  "gaia",
  "apps",
  "webui",
  "src",
  "utils",
  "apiBase.ts"
);

function loadTrustedApiRegex() {
  const src = fs.readFileSync(apiBaseTsPath, "utf8");
  // Find: const TRUSTED_API_RE = /<pattern>/;
  const match = src.match(/TRUSTED_API_RE\s*=\s*\/(.+?)\/;/);
  assert.ok(
    match,
    "apiBase.ts must export TRUSTED_API_RE as a single-line regex literal " +
      "ending in `/;`. If you reformatted the declaration (multi-line, " +
      "added a flag, trailing comment), update loadTrustedApiRegex() here."
  );
  return new RegExp(match[1]);
}

test("renderer accepts the URL main.cjs writes (positive round-trip)", () => {
  const re = loadTrustedApiRegex();
  const q = buildIndexQuery(12345);
  // Simulate the round-trip: main.cjs writes ?api=<encoded>; renderer
  // reads via URLSearchParams (which auto-decodes); regex must accept.
  const search = "?api=" + encodeURIComponent(q.api);
  const parsed = new URLSearchParams(search).get("api");
  assert.strictEqual(parsed, q.api);
  assert.match(parsed, re, `regex ${re} must accept ${parsed}`);
});

test("renderer rejects attacker-controlled api param (negative)", () => {
  // Defense-in-depth: if a future deep-link or file-association handler
  // ever lets an external URL set ?api=, the regex must reject anything
  // that is not loopback. SSRF/data-exfil class.
  const re = loadTrustedApiRegex();
  for (const evil of [
    "http://evil.com/api",
    "https://example.org/api",
    "http://192.168.1.1:8080/api",
    "http://127.0.0.1.attacker.com/api",
    "javascript:alert(1)",
    "file:///etc/passwd",
    "http://127.0.0.1:9999/api/extra",   // path beyond /api
    "http://127.0.0.1:9999/api/",         // trailing slash → would produce //
    "http://127.0.0.1/api",               // no port
    "http://127.0.0.1:abc/api",           // non-numeric port
  ]) {
    assert.doesNotMatch(
      evil,
      re,
      `regex must reject hostile value: ${evil}`
    );
  }
});

test("renderer accepts both 127.0.0.1 and localhost", () => {
  // main.cjs uses 127.0.0.1 today; legacy fallback in apiBase.ts uses
  // localhost. Both must pass the allowlist.
  const re = loadTrustedApiRegex();
  assert.match("http://127.0.0.1:9876/api", re);
  assert.match("http://localhost:9876/api", re);
});

// ─── Spawn-args ↔ query-string port-equality (the literal #851) ──────
//
// This is the regression the issue describes: port-manager picks N,
// main.cjs spawns `gaia chat --ui --ui-port N`, but the renderer was
// told a different port (or no port). We verify by reading main.cjs as
// source and asserting both call sites consume the same `backendPort`
// variable.

const mainCjsPath = path.resolve(
  __dirname,
  "..",
  "..",
  "src",
  "gaia",
  "apps",
  "webui",
  "main.cjs"
);

test("main.cjs spawn args and index query share the same backendPort", () => {
  const src = fs.readFileSync(mainCjsPath, "utf8");

  // Spawn argument list must reference backendPort (issue #851 broke
  // because the renderer side did not).
  assert.match(
    src,
    /"--ui-port",\s*String\(backendPort\)/,
    "spawn() must pass --ui-port String(backendPort) to gaia chat --ui"
  );

  // The index-query call must consume the same variable, not a literal
  // or a separate constant.
  assert.match(
    src,
    /buildIndexQuery\(backendPort\)/,
    "loadApp() must call buildIndexQuery(backendPort) — not a literal"
  );

  // Belt-and-braces: there must NOT be any hardcoded literal API URL
  // in loadApp() that would shadow the dynamic value.
  const loadAppMatch = src.match(/async function loadApp\(\)\s*{([\s\S]*?)\n}/);
  assert.ok(loadAppMatch, "main.cjs must declare loadApp()");
  const loadAppBody = loadAppMatch[1];
  assert.doesNotMatch(
    loadAppBody,
    /["'`]http:\/\/(127\.0\.0\.1|localhost):\d+\/api["'`]/,
    "loadApp() must not contain a hardcoded API URL literal"
  );
});
