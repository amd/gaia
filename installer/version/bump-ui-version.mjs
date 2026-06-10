#!/usr/bin/env node

// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Syncs the version from src/gaia/version.py into the agent-ui webui
 * package.json and package-lock.json. GAIA uses a single version source of
 * truth in version.py.
 *
 * Usage:
 *   node installer/version/bump-ui-version.mjs          # reads version.py and syncs package.json + lockfile
 *   node installer/version/bump-ui-version.mjs --check  # verify package.json + lockfile match version.py (used in CI)
 */

import { readFileSync, writeFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
// Script lives in installer/version/, so repo root is two levels up.
const rootDir = resolve(__dirname, "..", "..");

const VERSION_PY = resolve(rootDir, "src", "gaia", "version.py");
const PACKAGE_PATH = resolve(
  rootDir,
  "src",
  "gaia",
  "apps",
  "webui",
  "package.json"
);
const PACKAGE_LOCK_PATH = resolve(
  rootDir,
  "src",
  "gaia",
  "apps",
  "webui",
  "package-lock.json"
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

// The lockfile stores the root package version twice — the top-level
// `.version` and `.packages[""].version` — each immediately after a
// `"name": "<pkg>"` line that no dependency shares. This targets exactly
// those two: group 1 is the text up to the opening quote, group 2 the
// version value, group 3 the closing quote.
function lockVersionRegex(pkgName) {
  const escaped = pkgName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(
    `("name":\\s*"${escaped}",\\s*\\n\\s*"version":\\s*")([^"]+)(")`,
    "g"
  );
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
  const lockVersions = [
    ...readFileSync(PACKAGE_LOCK_PATH, "utf8").matchAll(
      lockVersionRegex(pkg.name)
    ),
  ].map((m) => m[2]);

  const pkgOk = pkg.version === version;
  const lockOk =
    lockVersions.length === 2 && lockVersions.every((v) => v === version);

  if (!pkgOk || !lockOk) {
    if (!pkgOk) {
      console.log(
        `FAIL: package.json ${pkg.name}@${pkg.version} -- expected ${version}`
      );
    }
    if (lockVersions.length !== 2) {
      console.log(
        `FAIL: package-lock.json -- expected 2 root version fields, found ${lockVersions.length}`
      );
    } else if (!lockOk) {
      console.log(
        `FAIL: package-lock.json root version ${[
          ...new Set(lockVersions),
        ].join(", ")} -- expected ${version}`
      );
    }
    console.log(
      '\nRun "node installer/version/bump-ui-version.mjs" to sync package.json + lockfile to version.py.'
    );
    process.exit(1);
  }

  console.log(`OK: ${pkg.name}@${pkg.version} (package.json + lockfile)`);
  console.log("\nPackage and lockfile versions match version.py.");
} else {
  // --- Sync mode ---
  console.log(`\nSyncing package to version ${version} (from version.py)\n`);

  try {
    const pkg = JSON.parse(readFileSync(PACKAGE_PATH, "utf8"));
    const old = pkg.version;
    pkg.version = version;
    writeFileSync(PACKAGE_PATH, JSON.stringify(pkg, null, 2) + "\n", "utf8");
    console.log(`  package.json       ${old} -> ${version}`);

    // Keep the lockfile root version in sync via a targeted regex (see
    // lockVersionRegex); rewriting the whole file or `npm install` would
    // churn transitive entries.
    const lockContent = readFileSync(PACKAGE_LOCK_PATH, "utf8");
    let count = 0;
    const newLock = lockContent.replace(
      lockVersionRegex(pkg.name),
      (_m, prefix, _old, suffix) => {
        count += 1;
        return `${prefix}${version}${suffix}`;
      }
    );
    if (count !== 2) {
      console.error(
        `\n  ERROR: expected 2 root version fields in package-lock.json, matched ${count}.`
      );
      console.error(
        "  The lockfile structure changed — update bump-ui-version.mjs to match."
      );
      process.exit(1);
    }
    writeFileSync(PACKAGE_LOCK_PATH, newLock, "utf8");
    console.log(`  package-lock.json  -> ${version}`);
  } catch (err) {
    console.error(`  ERROR: ${err.message}`);
    process.exit(1);
  }

  console.log(`\nDone. Package version synced to v${version} from version.py.\n`);
}
