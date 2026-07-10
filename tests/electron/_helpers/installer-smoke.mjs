// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Shared assertions for installer structural smoke tests (issue #941).
//
// One source of truth for:
//   1. The in-resources path layout for the bundled `uv` binary
//      (mirrors electron-builder.yml `extraResources.to: vendor/uv`).
//   2. Parsing BUNDLED_UV_SHA256 out of backend-installer.cjs.
//   3. The full existence + executable-bit + verification check — SHA256
//      pin on linux-x64/win-x64, `codesign --verify --strict` on mac-arm64
//      (see the BUNDLED_UV_SHA256 comment in backend-installer.cjs for why
//      mac-arm64 cannot use a fixed digest pin).
//
// Consumed by tests/electron/appimage-smoke.test.mjs and
// tests/electron/dmg-smoke.test.mjs. A future NSIS smoke test should
// also consume this module so all three installers stay in lockstep.

import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

// Mirrors electron-builder.yml `extraResources.to: vendor/uv` — the single
// source of truth for the in-resources layout. If electron-builder.yml
// changes the `to:` path, every smoke test breaks here with the same
// actionable diff: update UV_RESOURCE_SUBPATH.
export const UV_RESOURCE_SUBPATH = ["vendor", "uv"];

/**
 * Build the runtime-equivalent path to the bundled uv binary inside an
 * already-extracted resources directory.
 *
 * @param {string} resourcesDir
 *   Absolute path to the extracted resources directory. Examples:
 *     - AppImage:  <squashfs-root>/resources
 *     - DMG:       <mountpoint>/<App>.app/Contents/Resources
 * @param {"linux-x64"|"mac-arm64"|"win-x64"} platformKey
 * @returns {string} absolute path to the bundled `uv`/`uv.exe`
 */
export function bundledUvPath(resourcesDir, platformKey) {
  const binary = platformKey === "win-x64" ? "uv.exe" : "uv";
  return path.join(resourcesDir, ...UV_RESOURCE_SUBPATH, platformKey, binary);
}

/**
 * Parse `BUNDLED_UV_SHA256[platformKey]` from backend-installer.cjs source.
 *
 * The constant spans multiple lines once it has 2+ entries. `[^}]*?` spans
 * newlines natively (character classes match `\n` without dotall) and the
 * stop class on `}` is safe because SHA hex strings cannot contain `}`.
 *
 * @param {string} installerCjsPath  absolute path to backend-installer.cjs
 * @param {string} platformKey       e.g. "linux-x64"
 * @returns {string} the 64-character hex digest
 */
export function parseBundledUvSha(installerCjsPath, platformKey) {
  const src = fs.readFileSync(installerCjsPath, "utf8");
  // Defensive: escape regex metacharacters in the platform key, even
  // though the keys we use have none today.
  const escapedKey = platformKey.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(
    `BUNDLED_UV_SHA256\\s*=\\s*\\{[^}]*?"${escapedKey}"\\s*:\\s*"([0-9a-f]{64})"`,
  );
  const m = src.match(re);
  assert.ok(
    m,
    `could not parse BUNDLED_UV_SHA256["${platformKey}"] from ${installerCjsPath}`,
  );
  return m[1];
}

/**
 * Existence + executable-bit (POSIX only) + verification check.
 *
 * mac-arm64 verifies via `codesign --verify --strict` — matching
 * ensureUv()'s runtime check (see the BUNDLED_UV_SHA256 comment in
 * backend-installer.cjs: ad-hoc codesign output is not deterministic
 * across CI runner images, so mac-arm64 has no fixed digest to pin
 * against). Other platforms compare SHA256 against the pin in
 * BUNDLED_UV_SHA256, catching the failure mode that bit issue #849 and
 * motivated #941: a packaged binary whose SHA does not match the pin,
 * which `ensureUv()` would reject at runtime on the user's first launch.
 *
 * @param {string} uvPath              absolute path to packaged `uv` binary
 * @param {string} platformKey         e.g. "mac-arm64"
 * @param {string} installerCjsPath    absolute path to backend-installer.cjs
 */
export function assertUvBinary(uvPath, platformKey, installerCjsPath) {
  assert.ok(fs.existsSync(uvPath), `expected bundled uv at ${uvPath}`);
  if (platformKey !== "win-x64") {
    const st = fs.statSync(uvPath);
    // Any execute bit on any class is enough; squashfs/HFS+/APFS all
    // preserve 0o755 for the source mode set by the CI fetch step.
    assert.ok(
      (st.mode & 0o111) !== 0,
      `uv binary should be executable; mode=${(st.mode & 0o777).toString(8)}`,
    );
  }

  if (platformKey === "mac-arm64") {
    const result = spawnSync("codesign", ["--verify", "--strict", uvPath], {
      encoding: "utf8",
    });
    const output = `${result.stdout || ""}${result.stderr || ""}`.trim();
    assert.equal(
      result.status,
      0,
      `bundled mac-arm64 uv failed \`codesign --verify --strict\`; ` +
        `ensureUv() will reject this at runtime. Output: ${output || "(none)"}`,
    );
    return;
  }

  const expected = parseBundledUvSha(installerCjsPath, platformKey);
  const actual = crypto
    .createHash("sha256")
    .update(fs.readFileSync(uvPath))
    .digest("hex");
  assert.equal(
    actual,
    expected,
    `bundled uv binary SHA256 does not match BUNDLED_UV_SHA256["${platformKey}"]; ` +
      `ensureUv() will reject this at runtime. ` +
      `To fix: update BUNDLED_UV_SHA256["${platformKey}"] in ` +
      `src/gaia/apps/webui/services/backend-installer.cjs to: ${actual}`,
  );
}

/**
 * Resolve the absolute path to backend-installer.cjs from a smoke test
 * file located under tests/electron/. Centralised so a future move of
 * either tree only updates one place.
 *
 * @param {string} testFileUrl  pass `import.meta.url` from the caller
 * @returns {string}
 */
export function backendInstallerPath(testFileUrl) {
  return path.resolve(
    path.dirname(fileURLToPath(testFileUrl)),
    "..",
    "..",
    "src",
    "gaia",
    "apps",
    "webui",
    "services",
    "backend-installer.cjs",
  );
}
