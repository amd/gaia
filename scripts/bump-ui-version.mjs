#!/usr/bin/env node

// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Syncs the version from src/gaia/version.py into the agent-ui webui package.json.
 * GAIA uses a single version source of truth in version.py.
 *
 * Usage:
 *   node scripts/bump-ui-version.mjs          # reads version.py and syncs package.json
 *   node scripts/bump-ui-version.mjs --check  # verify package.json matches version.py (used in CI)
 */

import { readFileSync, writeFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const rootDir = resolve(__dirname, "..");

const VERSION_PY = resolve(rootDir, "src", "gaia", "version.py");
const PACKAGE_PATH = resolve(
  rootDir,
  "src",
  "gaia",
  "apps",
  "webui",
  "package.json"
);

// Read version from version.py
function readVersionPy() {
  const content = readFileSync(VERSION_PY, "utf8");
  const match = content.match(/__version__\s*=\s*"([^"]+)"/);
  if (!match) {
    console.error(`\nERROR: Could not parse __version__ from ${VERSION_PY}`);
    process.exit(1);
  }
  return match[1];
}

const version = readVersionPy();

if (!/^\d+\.\d+\.\d+/.test(version)) {
  console.error(`\nERROR: Invalid version in version.py: "${version}"`);
  console.error("  Expected format: x.y.z or x.y.z.w");
  process.exit(1);
}

const checkOnly = process.argv[2] === "--check";

if (checkOnly) {
  // --- Check mode (CI) ---
  console.log(`version.py: ${version}\n`);

  const pkg = JSON.parse(readFileSync(PACKAGE_PATH, "utf8"));
  if (pkg.version !== version) {
    console.log(`FAIL: ${pkg.name}@${pkg.version} -- expected ${version}`);
    console.log(
      '\nRun "node scripts/bump-ui-version.mjs" to sync package.json to version.py.'
    );
    process.exit(1);
  } else {
    console.log(`OK: ${pkg.name}@${pkg.version}`);
    console.log("\nPackage version matches version.py.");
  }
} else {
  // --- Sync mode ---
  console.log(`\nSyncing package to version ${version} (from version.py)\n`);

  try {
    const pkg = JSON.parse(readFileSync(PACKAGE_PATH, "utf8"));
    const old = pkg.version;
    pkg.version = version;
    writeFileSync(PACKAGE_PATH, JSON.stringify(pkg, null, 2) + "\n", "utf8");
    console.log(`  package.json  ${old} -> ${version}`);
  } catch (err) {
    console.error(`  ERROR: ${err.message}`);
    process.exit(1);
  }

  console.log(`\nDone. Package version synced to v${version} from version.py.\n`);
}
