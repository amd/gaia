#!/usr/bin/env node

// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Syncs the version from version.txt into the chat webui package.json.
 *
 * Usage:
 *   node scripts/bump-chat-version.mjs          # reads version.txt and syncs package.json
 *   node scripts/bump-chat-version.mjs --check  # verify package.json matches version.txt (used in CI)
 */

import { readFileSync, writeFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const rootDir = resolve(__dirname, "..");

const WEBUI_DIR = resolve(
  rootDir,
  "src",
  "gaia",
  "apps",
  "chat",
  "webui"
);

const PACKAGE_PATH = resolve(WEBUI_DIR, "package.json");
const VERSION_FILE = resolve(WEBUI_DIR, "version.txt");

// Read version.txt
const version = readFileSync(VERSION_FILE, "utf8").trim();

if (!/^\d+\.\d+\.\d+(-[\w.]+)?$/.test(version)) {
  console.error(`\nERROR: Invalid version in version.txt: "${version}"`);
  console.error("  Expected format: x.y.z or x.y.z-beta.1");
  process.exit(1);
}

const checkOnly = process.argv[2] === "--check";

if (checkOnly) {
  // --- Check mode (CI) ---
  console.log(`version.txt: ${version}\n`);

  const pkg = JSON.parse(readFileSync(PACKAGE_PATH, "utf8"));
  if (pkg.version !== version) {
    console.log(`FAIL: ${pkg.name}@${pkg.version} -- expected ${version}`);
    console.log(
      '\nRun "node scripts/bump-chat-version.mjs" to sync package.json to version.txt.'
    );
    process.exit(1);
  } else {
    console.log(`OK: ${pkg.name}@${pkg.version}`);
    console.log("\nPackage version matches version.txt.");
  }
} else {
  // --- Sync mode ---
  console.log(`\nSyncing package to version ${version} (from version.txt)\n`);

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

  console.log(`
──────────────────────────────────────────
Package synced to v${version}.

Next steps:

  git add -A
  git commit -m "chore: release chat-ui v${version}"
  git tag chat-v${version}
  git push origin main --tags

Pushing the tag triggers the CI/CD pipeline.
──────────────────────────────────────────
`);
}
