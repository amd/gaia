// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Shared assertions for installer structural smoke tests (issue #941).
//
// One source of truth for:
//   1. The in-resources path of the frozen `gaia` core (mirrors
//      electron-builder.yml `extraResources.to: core`).
//   2. The existence + executable-bit + signature + `--version` check that
//      tells a packaged core apart from one the user's PC can't start.
//
// Consumed by tests/electron/appimage-smoke.test.mjs and
// tests/electron/dmg-smoke.test.mjs.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

// Mirrors electron-builder.yml `extraResources.to: core` and BUNDLED_GAIA_BIN
// in backend-installer.cjs.
export const CORE_RESOURCE_SUBPATH = ["core"];

/**
 * The frozen `gaia` inside an already-extracted resources directory.
 *
 * @param {string} resourcesDir
 *   Absolute path to the extracted resources directory. Examples:
 *     - AppImage:  <squashfs-root>/resources
 *     - DMG:       <mountpoint>/<App>.app/Contents/Resources
 * @param {"linux-x64"|"mac-arm64"|"win-x64"} platformKey
 * @returns {string} absolute path to the bundled `gaia`/`gaia.exe`
 */
export function bundledCorePath(resourcesDir, platformKey) {
  const binary = platformKey === "win-x64" ? "gaia.exe" : "gaia";
  return path.join(resourcesDir, ...CORE_RESOURCE_SUBPATH, binary);
}

/**
 * The bundled core is there, executable, signed (macOS), and starts.
 *
 * `--version` imports the CLI, so a core whose libraries the installer
 * dropped or whose signature the packaging broke fails here, not on a
 * user's first launch.
 *
 * @param {string} corePath     absolute path to the packaged `gaia`
 * @param {string} platformKey  e.g. "mac-arm64"
 */
export function assertCoreBinary(corePath, platformKey) {
  assert.ok(fs.existsSync(corePath), `expected the frozen core at ${corePath}`);
  if (platformKey !== "win-x64") {
    const st = fs.statSync(corePath);
    assert.ok(
      (st.mode & 0o111) !== 0,
      `the core should be executable; mode=${(st.mode & 0o777).toString(8)}`,
    );
  }
  if (platformKey === "mac-arm64") {
    const sign = spawnSync("codesign", ["--verify", "--strict", corePath], {
      encoding: "utf8",
    });
    assert.equal(
      sign.status,
      0,
      `the bundled core failed \`codesign --verify --strict\`: ` +
        `${`${sign.stdout || ""}${sign.stderr || ""}`.trim() || "(no output)"}`,
    );
  }
  const run = spawnSync(corePath, ["--version"], {
    encoding: "utf8",
    timeout: 60_000,
  });
  const output = `${run.stdout || ""}${run.stderr || ""}`.trim();
  assert.equal(run.status, 0, `\`gaia --version\` exited ${run.status}: ${output}`);
  assert.match(output, /\d+\.\d+\.\d+/, `unexpected \`gaia --version\` output: ${output}`);
}
