// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA version normalization helper.
 *
 * GAIA uses 4-part versions internally (e.g. 0.15.4.1) for hotfix releases,
 * but strict SemVer (which NSIS, MSI, SquirrelMac, and Apple bundle version
 * fields all require) only accepts 3 parts (x.y.z).
 *
 * The normalizer concatenates the last two parts so releases retain their
 * ordering:
 *
 *   "1.2.3"      -> "1.2.3"     (pass-through)
 *   "0.15.4.1"   -> "0.15.41"   (4-part collapse)
 *   "0.15.4.10"  -> "0.15.410"  (double-digit hotfix)
 *
 * This is a direct port of `toSemVer()` from the retired
 * `src/gaia/apps/webui/forge.config.cjs`. Ported during Phase C of the
 * desktop-installer plan so both the electron-builder config and any
 * future release tooling can share a single implementation.
 *
 * Usage:
 *   import { toSemVer } from './normalize.mjs';
 *
 *   node installer/version/normalize.mjs 0.15.4.1
 *   => "0.15.41"
 */

/**
 * Convert a GAIA version string to strict SemVer.
 *
 * @param {string} version  A dotted version string, e.g. "0.17.2" or "0.15.4.1"
 * @returns {string}        A 3-part SemVer string.
 */
export function toSemVer(version) {
  if (typeof version !== "string" || !version) {
    throw new TypeError(`toSemVer: expected non-empty string, got ${version}`);
  }
  const parts = version.split(".");
  if (parts.length <= 3) return version;
  return `${parts[0]}.${parts[1]}.${parts.slice(2).join("")}`;
}

// CLI invocation: `node installer/version/normalize.mjs <version>`
if (import.meta.url === `file://${process.argv[1]}`) {
  const v = process.argv[2];
  if (!v) {
    console.error("usage: normalize.mjs <version>");
    process.exit(1);
  }
  process.stdout.write(`${toSemVer(v)}\n`);
}
