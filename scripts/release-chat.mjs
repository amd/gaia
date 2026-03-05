#!/usr/bin/env node

// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * One-command release for GAIA Chat npm package.
 * Reads version.txt, syncs package.json, commits, tags, and pushes.
 * The CI pipeline handles the rest (build, test, publish to npm).
 *
 * Usage:
 *   node scripts/release-chat.mjs
 *
 * Just edit version.txt first, then run this.
 */

import { readFileSync, writeFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { execSync } from "child_process";

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

// Relative path for git commands
const PACKAGE_REL = "src/gaia/apps/chat/webui/package.json";
const VERSION_REL = "src/gaia/apps/chat/webui/version.txt";

function run(cmd, opts = {}) {
  console.log(`  $ ${cmd}`);
  return execSync(cmd, { cwd: rootDir, stdio: "inherit", ...opts });
}

function runCapture(cmd) {
  return execSync(cmd, { cwd: rootDir, encoding: "utf8" }).trim();
}

// --- Read version.txt ---
const version = readFileSync(VERSION_FILE, "utf8").trim();

if (!/^\d+\.\d+\.\d+(-[\w.]+)?$/.test(version)) {
  console.error(`\nERROR: Invalid version in version.txt: "${version}"`);
  console.error("  Expected format: x.y.z or x.y.z-beta.1");
  process.exit(1);
}

const tag = `chat-v${version}`;

console.log(`\nReleasing ${tag}\n`);

// --- Check working tree is clean (except version changes) ---
const dirtyFiles = runCapture("git status --porcelain")
  .split("\n")
  .filter((line) => line.trim())
  .filter(
    (line) =>
      !line.includes("version.txt") && !line.includes("package.json")
  );

if (dirtyFiles.length > 0) {
  console.error(
    "ERROR: Working tree has uncommitted changes (besides version files):"
  );
  dirtyFiles.forEach((f) => console.error(`   ${f}`));
  console.error("\nCommit or stash them first.");
  process.exit(1);
}

// --- Check tag doesn't already exist ---
try {
  runCapture(`git rev-parse refs/tags/${tag}`);
  console.error(
    `ERROR: Tag ${tag} already exists. Bump version.txt to a new version.`
  );
  process.exit(1);
} catch {
  // Good -- tag doesn't exist yet
}

// --- Sync package.json ---
console.log("Syncing package version...\n");

const pkg = JSON.parse(readFileSync(PACKAGE_PATH, "utf8"));
const old = pkg.version;
pkg.version = version;
writeFileSync(PACKAGE_PATH, JSON.stringify(pkg, null, 2) + "\n", "utf8");
console.log(`  package.json  ${old} -> ${version}`);

// --- Git: stage, commit, tag, push ---
console.log("\nCommitting...\n");
run(`git add ${VERSION_REL} ${PACKAGE_REL}`);

// Check if there are staged changes to commit
const staged = runCapture("git diff --cached --name-only");
if (staged) {
  run(`git commit -m "chore: release ${tag}"`);
} else {
  console.log("  (no version changes to commit -- versions already match)");
}

console.log("\nTagging...\n");
run(`git tag ${tag}`);

console.log("\nPushing...\n");
run(`git push origin HEAD --tags`);

console.log(`
──────────────────────────────────────────
Released ${tag}

What happens next:
  1. CI builds the frontend and runs tests
  2. You'll get a GitHub notification to approve publishing
  3. Once approved, the package is published to npm

Track it at: https://github.com/amd/gaia/actions
──────────────────────────────────────────
`);
