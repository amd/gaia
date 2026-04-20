// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * electron-builder afterPack hook for GAIA Agent UI.
 *
 * Ports the locale-pruning logic from the retired forge.config.cjs
 * `postPackage` hook. Chromium ships ~50 locale .pak files that add
 * ~45 MB to the install size. GAIA is English-only so we strip every
 * locale except en-US.
 *
 * electron-builder calls this after copying the Electron binary and
 * bundled app files to `context.appOutDir`. The locales/ directory
 * lives in different places per-platform:
 *
 *   Windows: <appOutDir>/locales/<lang>.pak
 *   Linux:   <appOutDir>/locales/<lang>.pak
 *   macOS:   <appOutDir>/<productName>.app/Contents/Frameworks/
 *            Electron Framework.framework/Versions/A/Resources/<lang>.lproj/
 *
 * We walk `appOutDir` recursively looking for any directory named
 * "locales" (Windows/Linux) or any directory containing *.lproj
 * subdirectories (macOS), then delete the non-English entries.
 *
 * Reference: desktop-installer.mdx §7 Phase C.
 */

"use strict";

const fs = require("fs");
const path = require("path");

// Windows/Linux Chromium ships one .pak per locale. Keep en-US.pak only.
const KEEP_PAK = new Set(["en-US.pak"]);

// macOS Electron ships .lproj directories (one per locale). Keep en.lproj
// (and the base "Base.lproj" if present — it contains the default layouts).
const KEEP_LPROJ = new Set(["en.lproj", "Base.lproj"]);

/**
 * Recursively locate any directory named "locales" under `root`.
 * Returns an array of absolute paths.
 */
function findLocalesDirs(root) {
  const results = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const abs = path.join(current, entry.name);
      if (entry.name === "locales") {
        results.push(abs);
        // Don't descend into locales/ — nothing else of interest.
        continue;
      }
      stack.push(abs);
    }
  }
  return results;
}

/**
 * Recursively locate any directory that *contains* *.lproj subdirectories
 * (typical for macOS frameworks). Returns an array of absolute paths to
 * the parent directories, not to the individual .lproj dirs themselves.
 */
function findLprojParents(root) {
  const results = new Set();
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    let hasLproj = false;
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const abs = path.join(current, entry.name);
      if (entry.name.endsWith(".lproj")) {
        hasLproj = true;
      } else {
        stack.push(abs);
      }
    }
    if (hasLproj) results.add(current);
  }
  return Array.from(results);
}

/**
 * Return the on-disk size of `p` (file or directory) in bytes.
 */
function sizeOf(p) {
  let total = 0;
  try {
    const stat = fs.statSync(p);
    if (stat.isFile()) return stat.size;
    if (stat.isDirectory()) {
      for (const entry of fs.readdirSync(p)) {
        total += sizeOf(path.join(p, entry));
      }
    }
  } catch {
    // missing / inaccessible — ignore
  }
  return total;
}

function prettyBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Delete every file in `localesDir` whose name is not in KEEP_PAK.
 */
function pruneLocalesDir(localesDir) {
  let saved = 0;
  let removed = 0;
  let entries;
  try {
    entries = fs.readdirSync(localesDir);
  } catch {
    return { saved, removed };
  }
  for (const name of entries) {
    if (KEEP_PAK.has(name)) continue;
    const abs = path.join(localesDir, name);
    saved += sizeOf(abs);
    try {
      fs.rmSync(abs, { recursive: true, force: true });
      removed += 1;
    } catch (err) {
      console.warn(`[after-pack] failed to remove ${abs}: ${err.message}`);
    }
  }
  return { saved, removed };
}

/**
 * Delete every *.lproj directory under `parent` except those in KEEP_LPROJ.
 */
function pruneLprojParent(parent) {
  let saved = 0;
  let removed = 0;
  let entries;
  try {
    entries = fs.readdirSync(parent, { withFileTypes: true });
  } catch {
    return { saved, removed };
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    if (!entry.name.endsWith(".lproj")) continue;
    if (KEEP_LPROJ.has(entry.name)) continue;
    const abs = path.join(parent, entry.name);
    saved += sizeOf(abs);
    try {
      fs.rmSync(abs, { recursive: true, force: true });
      removed += 1;
    } catch (err) {
      console.warn(`[after-pack] failed to remove ${abs}: ${err.message}`);
    }
  }
  return { saved, removed };
}

module.exports = async function afterPack(context) {
  const root = context.appOutDir;
  const platform =
    (context.packager && context.packager.platform && context.packager.platform.name) ||
    process.platform;
  console.log(
    `[after-pack] pruning Chromium locales under ${root} (platform=${platform})`
  );

  let totalSaved = 0;
  let totalRemoved = 0;

  // Windows + Linux: locales/*.pak
  for (const dir of findLocalesDirs(root)) {
    const { saved, removed } = pruneLocalesDir(dir);
    totalSaved += saved;
    totalRemoved += removed;
    if (removed > 0) {
      console.log(
        `[after-pack] pruned ${removed} files (${prettyBytes(saved)}) from ${dir}`
      );
    }
  }

  // macOS: various *.lproj directories in the Electron framework bundle.
  for (const parent of findLprojParents(root)) {
    const { saved, removed } = pruneLprojParent(parent);
    totalSaved += saved;
    totalRemoved += removed;
    if (removed > 0) {
      console.log(
        `[after-pack] pruned ${removed} .lproj dirs (${prettyBytes(saved)}) from ${parent}`
      );
    }
  }

  console.log(
    `[after-pack] done — removed ${totalRemoved} locale entries, saved ${prettyBytes(
      totalSaved
    )}`
  );
};
