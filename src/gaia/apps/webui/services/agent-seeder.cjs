// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * agent-seeder.cjs — First-launch bundled-agent seeder.
 *
 * Copies agents bundled with the installer (placed at
 * `<resourcesPath>/agents/` by electron-builder's extraResources rule) into
 * the user's per-agent home directory at `~/.gaia/agents/<agent-id>/`. A
 * `.seeded` sentinel file is written after a successful copy so subsequent
 * launches skip the agent.
 *
 * Design invariants (see .claude/plans/bundle-path-contract.md):
 *   - Source:  path.join(process.resourcesPath, "agents", "<id>")
 *              - Windows: <install>\resources\agents\<id>\
 *              - macOS:   <Bundle>.app/Contents/Resources/agents/<id>/
 *              - Linux:   /opt/<AppName>/resources/agents/<id>/
 *   - Target:  path.join(os.homedir(), ".gaia", "agents", "<id>")
 *   - Sentinel: <target>/.seeded  (exists → already seeded → skip)
 *
 * Write protocol (atomic-ish, crash-safe):
 *   1. Remove any stale `<id>.partial/` sibling from a prior failed run.
 *   2. Copy source → `<id>.partial/`.
 *   3. `fs.renameSync(<id>.partial, <id>)` — atomic on the same filesystem.
 *   4. Write `<id>/.seeded` last, so a partial seed never looks complete.
 *
 * Behaviour:
 *   - Target `<id>/` exists WITH `.seeded` → already seeded, skip.
 *   - Target `<id>/` exists WITHOUT `.seeded` → treat as user-owned data,
 *     log a warning, and skip (never clobber a hand-authored agent).
 *   - `process.resourcesPath` unset (dev / Jest) or source dir missing →
 *     empty result, no error.
 *   - Per-agent failures are isolated: they go into `errors[]` but do not
 *     stop the next agent from being seeded.
 *
 * Pure CommonJS. Only Node stdlib (fs / path / os). No Electron imports so
 * the module is testable without spinning up Electron.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");

// ── Path helpers ─────────────────────────────────────────────────────────

function gaiaHome() {
  return path.join(os.homedir(), ".gaia");
}

function agentsTargetRoot() {
  return path.join(gaiaHome(), "agents");
}

function logsDir() {
  return path.join(gaiaHome(), "logs");
}

function logFilePath() {
  return path.join(logsDir(), "seeder.log");
}

// ── Logging ──────────────────────────────────────────────────────────────

function log(level, message) {
  const line = `${new Date().toISOString()} [${level}] ${message}\n`;
  try {
    fs.mkdirSync(logsDir(), { recursive: true });
    fs.appendFileSync(logFilePath(), line, { encoding: "utf8" });
  } catch {
    // If we cannot write the log, fall back to console so the message
    // isn't lost entirely. We never let logging failure propagate.
  }
  // Also mirror to console so `electron .` tail-of-stdout users see it.
  // eslint-disable-next-line no-console
  const writer =
    level === "ERROR" ? console.error : level === "WARN" ? console.warn : console.log;
  writer(`[agent-seeder] ${message}`);
}

// ── Filesystem helpers ───────────────────────────────────────────────────

/**
 * Recursive copy using fs.cpSync when available (Node 16.7+), falling back
 * to a hand-rolled recursive copy for older runtimes. Electron 40 ships
 * Node 20, so cpSync is always present in production — but we keep the
 * fallback for test environments that might mock cpSync.
 */
function copyDirRecursive(src, dest) {
  if (typeof fs.cpSync === "function") {
    // dereference: true flattens symlinks into their targets rather than
    // copying the symlink itself. This prevents a malicious or accidentally
    // symlinked installer bundle from planting out-of-tree references in
    // ~/.gaia/agents/<id>/.
    fs.cpSync(src, dest, { recursive: true, errorOnExist: false, force: true, dereference: true });
    return;
  }
  // Fallback path (shouldn't normally hit on Electron 40 / Node 20).
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDirRecursive(s, d);
    } else if (entry.isSymbolicLink()) {
      // Skip symlinks in the fallback path for the same reason as dereference:true above.
      log("WARN", `Skipping symlink in installer bundle: ${s}`);
    } else {
      fs.copyFileSync(s, d);
    }
  }
}

function rmDirRecursive(target) {
  fs.rmSync(target, { recursive: true, force: true });
}

function isDirectory(p) {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

// ── Seeding core ─────────────────────────────────────────────────────────

/**
 * Seed a single agent directory. Returns a category string:
 *   "seeded"  — copied successfully, sentinel written.
 *   "skipped" — already seeded or user-owned; left untouched.
 *   "error"   — copy failed; partial data cleaned up (best effort).
 *
 * Throws only on programmer error. All IO errors are caught and logged.
 */
function seedOneAgent(sourceDir, targetRoot, id) {
  const src = path.join(sourceDir, id);
  const target = path.join(targetRoot, id);
  const partial = path.join(targetRoot, `${id}.partial`);
  const sentinel = path.join(target, ".seeded");

  // Already seeded?
  if (fs.existsSync(sentinel)) {
    log("INFO", `Skipping "${id}" — already seeded (sentinel present)`);
    return { status: "skipped" };
  }

  // Target exists but no sentinel → user-owned data. Do not touch.
  if (fs.existsSync(target)) {
    log(
      "WARN",
      `Skipping "${id}" — target exists without .seeded sentinel ` +
        `(treating as user-owned data): ${target}`
    );
    return { status: "skipped" };
  }

  // Verify the source is actually a directory before doing anything.
  if (!isDirectory(src)) {
    log("WARN", `Skipping "${id}" — source is not a directory: ${src}`);
    return { status: "skipped" };
  }

  try {
    // Clean up any leftover from a prior failed run.
    if (fs.existsSync(partial)) {
      log("INFO", `Removing stale partial directory for "${id}": ${partial}`);
      rmDirRecursive(partial);
    }

    // Ensure the parent exists.
    fs.mkdirSync(targetRoot, { recursive: true });

    // Copy into sibling, then atomically rename.
    copyDirRecursive(src, partial);
    fs.renameSync(partial, target);

    // Write sentinel LAST — its presence means "copy completed".
    fs.writeFileSync(
      sentinel,
      JSON.stringify(
        {
          seededAt: new Date().toISOString(),
          source: src,
        },
        null,
        2
      ),
      { encoding: "utf8" }
    );

    log("INFO", `Seeded "${id}" from ${src} to ${target}`);
    return { status: "seeded" };
  } catch (err) {
    // Best-effort cleanup. If the rename already happened (partial no longer
    // exists but target does and has no sentinel), remove target so the next
    // launch retries cleanly instead of treating it as user-owned data.
    try {
      if (fs.existsSync(partial)) {
        rmDirRecursive(partial);
      } else if (fs.existsSync(target) && !fs.existsSync(sentinel)) {
        rmDirRecursive(target);
      }
    } catch {
      // ignore — original error is more important
    }

    log(
      "ERROR",
      `Failed to seed "${id}": ${err && err.message ? err.message : err}`
    );
    return { status: "error", error: err };
  }
}

/**
 * Seed all bundled agents found under `<resourcesPath>/agents/`.
 *
 * Idempotent — safe to call on every app launch.
 *
 * @returns {Promise<{seeded: string[], skipped: string[], errors: {id: string, error: Error}[]}>}
 */
async function seedBundledAgents() {
  const result = { seeded: [], skipped: [], errors: [] };

  // Guard against dev / test environments where resourcesPath is unset.
  if (!process.resourcesPath) {
    log(
      "INFO",
      "process.resourcesPath is undefined — skipping bundled-agent seeding"
    );
    return result;
  }

  const sourceDir = path.join(process.resourcesPath, "agents");

  if (!fs.existsSync(sourceDir) || !isDirectory(sourceDir)) {
    // Not an error — a build might simply ship without bundled agents.
    // In a packaged Electron app the directory is expected to exist, so raise
    // to WARN; in dev/test contexts leave it at INFO.
    let isPackaged = false;
    try {
      isPackaged = require("electron").app?.isPackaged === true;
    } catch (_) {
      // not in an Electron context (tests, CLI)
    }
    log(
      isPackaged ? "WARN" : "INFO",
      `No bundled agents directory at ${sourceDir} — nothing to seed`
    );
    return result;
  }

  let entries;
  try {
    entries = fs.readdirSync(sourceDir, { withFileTypes: true });
  } catch (err) {
    log(
      "ERROR",
      `Failed to read bundled agents directory ${sourceDir}: ${
        err && err.message ? err.message : err
      }`
    );
    return result;
  }

  const targetRoot = agentsTargetRoot();

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const id = entry.name;

    const outcome = seedOneAgent(sourceDir, targetRoot, id);
    if (outcome.status === "seeded") {
      result.seeded.push(id);
    } else if (outcome.status === "skipped") {
      result.skipped.push(id);
    } else {
      result.errors.push({ id, error: outcome.error });
    }
  }

  log(
    "INFO",
    `Seeding complete — seeded=${result.seeded.length} ` +
      `skipped=${result.skipped.length} errors=${result.errors.length}`
  );

  return result;
}

module.exports = {
  seedBundledAgents,
  // Exposed for tests — do not rely on these from production code.
  _internals: {
    seedOneAgent,
    agentsTargetRoot,
    logFilePath,
  },
};
