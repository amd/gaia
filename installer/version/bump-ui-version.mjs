#!/usr/bin/env node

// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Syncs the Agent UI's package.json and package-lock.json version with
 * src/gaia/version.py. util/release_versions.py owns every version field;
 * this runs its `agent-ui` group, so existing callers keep working.
 *
 * Usage:
 *   node installer/version/bump-ui-version.mjs          # stamp package.json + lockfile
 *   node installer/version/bump-ui-version.mjs --check  # verify only; exit 1 on drift
 *
 * Set PYTHON to choose the interpreter (default: python on Windows, python3 elsewhere).
 */

import { spawnSync } from "child_process";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
// Script lives in installer/version/, so repo root is two levels up.
const rootDir = resolve(__dirname, "..", "..");
const script = resolve(rootDir, "util", "release_versions.py");

const args = process.argv.slice(2);
if (args.length > 1 || (args.length === 1 && args[0] !== "--check")) {
  console.error(`Unknown arguments: ${args.join(" ")}. Expected none or --check.`);
  process.exit(2);
}

const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const mode = args[0] === "--check" ? "--check" : "--stamp";
const result = spawnSync(python, [script, mode, "--only", "agent-ui"], {
  cwd: rootDir,
  stdio: "inherit",
});

if (result.error) {
  console.error(
    `\nERROR: could not run ${python} ${script}: ${result.error.message}\n` +
      "Install Python 3.10+, or set PYTHON to its path."
  );
  process.exit(1);
}
process.exit(result.status ?? 1);
