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
 *   - Sentinel: <target>/.seeded  (this copy completed)
 *   - Marker:  ~/.gaia/seeder/<id>.seeded  ("this machine seeded <id> once";
 *     EXISTENCE is the signal — content is informational, never parsed for
 *     decisions. Lives outside ~/.gaia/agents/ so deleting an agent, or the
 *     whole agents dir, never resurrects it.)
 *
 * Write protocol (atomic-ish, crash-safe):
 *   1. Remove any stale `<id>.partial/` sibling from a prior failed run.
 *   2. Copy source → `<id>.partial/`.
 *   3. `fs.renameSync(<id>.partial, <id>)` — atomic on the same filesystem.
 *   4. Write `<id>/.seeded`, so a partial seed never looks complete.
 *   5. Write the marker LAST — a crash between 4 and 5 self-heals next
 *      launch via the marker back-fill (rule 2 below).
 *
 * Per-agent decision order (markers read fresh on every call, no caching):
 *   1. Marker exists → skip, full stop. A deleted agent is never re-seeded;
 *      a dir the user re-created under the same id is never touched.
 *   2. No marker, target has `.seeded` → back-fill the marker, skip.
 *   3. No marker, target exists WITHOUT `.seeded` → user-owned data,
 *     log a warning, skip, write no marker.
 *   4. No marker, no target → seed via the write protocol above.
 *
 * Recovery: to get a deleted bundled agent back, remove
 * `~/.gaia/seeder/<id>.seeded` and relaunch.
 *
 * Legacy cleanup: the retired `zoo-agent` demo is removed from
 * ~/.gaia/agents/ when it is provably an unmodified seeded copy — sentinel
 * present, no file newer than its seededAt (+5s slack), never through a
 * symlink. Removed ids are reported via the additive `cleaned` array.
 *
 * Behaviour:
 *   - `process.resourcesPath` unset (dev / Jest) → empty result, no error,
 *     no cleanup (never touch the real HOME from dev/test contexts).
 *   - Source dir missing → legacy cleanup still runs, then empty result.
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

function seederMarkersDir() {
  return path.join(gaiaHome(), "seeder");
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

// ── Marker helpers ───────────────────────────────────────────────────────

/**
 * Defense-in-depth: an agent id must be a single plain path segment before
 * it is used in any path.join or marker filename.
 */
function isSinglePathSegment(id) {
  return (
    typeof id === "string" &&
    id.length > 0 &&
    id !== "." &&
    id !== ".." &&
    !id.includes("/") &&
    !id.includes("\\") &&
    !id.includes(path.sep)
  );
}

function markerPath(id) {
  if (!isSinglePathSegment(id)) {
    throw new Error(`Invalid agent id for marker path: ${JSON.stringify(id)}`);
  }
  return path.join(seederMarkersDir(), `${id}.seeded`);
}

function hasMarker(id) {
  return fs.existsSync(markerPath(id));
}

function writeMarker(id, source) {
  fs.mkdirSync(seederMarkersDir(), { recursive: true });
  fs.writeFileSync(
    markerPath(id),
    JSON.stringify(
      { seededAt: new Date().toISOString(), source },
      null,
      2
    ),
    { encoding: "utf8" }
  );
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
  if (!isSinglePathSegment(id)) {
    log("WARN", `Skipping invalid agent id ${JSON.stringify(id)}`);
    return { status: "skipped" };
  }

  const src = path.join(sourceDir, id);
  const target = path.join(targetRoot, id);
  const partial = path.join(targetRoot, `${id}.partial`);
  const sentinel = path.join(target, ".seeded");

  // Rule 1: marker wins, full stop — honors deletion and leaves any
  // user-recreated dir under the same id untouched.
  if (hasMarker(id)) {
    log("INFO", `Skipping "${id}" — already seeded once on this machine (marker present)`);
    return { status: "skipped" };
  }

  // Rule 2: seeded before markers existed → back-fill the marker.
  // Runs every launch, so it also heals a crash between sentinel and marker.
  if (fs.existsSync(sentinel)) {
    try {
      writeMarker(id, src);
      log("INFO", `Skipping "${id}" — already seeded (sentinel present); marker back-filled`);
    } catch (err) {
      log(
        "ERROR",
        `Failed to back-fill marker for "${id}": ${
          err && err.message ? err.message : err
        } — will retry next launch`
      );
    }
    return { status: "skipped" };
  }

  // Rule 3: target exists but no sentinel → user-owned data. Do not touch,
  // write no marker.
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

    // Write sentinel — its presence means "copy completed".
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

    // Marker LAST — a crash here self-heals via the rule-2 back-fill.
    writeMarker(id, src);

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

// ── Legacy cleanup ───────────────────────────────────────────────────────

// Retired bundled ids removed from user machines when provably unmodified.
const LEGACY_CLEANUP_IDS = ["zoo-agent"];

// Filesystem mtime granularity + copy latency headroom for the
// modified-since-seed check.
const MODIFIED_SLACK_MS = 5000;

function listFilesRecursive(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...listFilesRecursive(p));
    } else {
      out.push(p);
    }
  }
  return out;
}

/**
 * Remove retired seeded demo agents (currently `zoo-agent`) from
 * ~/.gaia/agents/. Deletes ONLY when the dir is provably an unmodified
 * seeded copy; anything ambiguous is left in place. Failures are logged
 * and retried on the next launch (the sentinel keys the retry — no marker
 * is involved, since a retired id can never be re-seeded anyway).
 *
 * @returns {string[]} ids actually removed (verified gone from disk).
 */
function cleanupLegacyAgents() {
  const cleaned = [];

  for (const id of LEGACY_CLEANUP_IDS) {
    const target = path.join(agentsTargetRoot(), id);

    let st;
    try {
      st = fs.lstatSync(target);
    } catch {
      continue; // not present — nothing to clean
    }

    // Never rm through a symlink — it would delete the link target.
    if (st.isSymbolicLink()) {
      log(
        "WARN",
        `Legacy "${id}" at ${target} is a symlink — leaving it untouched`
      );
      continue;
    }

    if (!st.isDirectory()) {
      log("WARN", `Legacy "${id}" at ${target} is not a directory — leaving it untouched`);
      continue;
    }

    // No sentinel → user-authored, not ours to remove.
    const sentinel = path.join(target, ".seeded");
    if (!fs.existsSync(sentinel)) {
      continue;
    }

    let seededAtMs;
    try {
      seededAtMs = Date.parse(
        JSON.parse(fs.readFileSync(sentinel, "utf8")).seededAt
      );
      if (!Number.isFinite(seededAtMs)) {
        throw new Error("seededAt missing or not a valid timestamp");
      }
    } catch (err) {
      log(
        "WARN",
        `Cannot determine seededAt for legacy "${id}" (${
          err && err.message ? err.message : err
        }) — treating as user-modified, leaving it untouched`
      );
      continue;
    }

    // Any file newer than the seed time means the user customized it —
    // it is their work now, never delete it.
    let modified = false;
    try {
      for (const file of listFilesRecursive(target)) {
        if (fs.lstatSync(file).mtimeMs > seededAtMs + MODIFIED_SLACK_MS) {
          log(
            "WARN",
            `Legacy "${id}" was modified after seeding (${file}) — leaving it untouched`
          );
          modified = true;
          break;
        }
      }
    } catch (err) {
      log(
        "WARN",
        `Cannot inspect legacy "${id}" for modifications (${
          err && err.message ? err.message : err
        }) — leaving it untouched`
      );
      continue;
    }
    if (modified) {
      continue;
    }

    try {
      fs.rmSync(target, { recursive: true, force: true });
    } catch (err) {
      log(
        "ERROR",
        `Failed to remove legacy "${id}" at ${target}: ${
          err && err.message ? err.message : err
        } — will retry next launch`
      );
      continue;
    }

    // Report success only after verifying it is actually gone.
    if (fs.existsSync(target)) {
      log(
        "ERROR",
        `Legacy "${id}" still present after removal attempt: ${target} — will retry next launch`
      );
      continue;
    }

    log("INFO", `Removed legacy seeded agent "${id}" from ${target}`);
    cleaned.push(id);
  }

  return cleaned;
}

/**
 * Seed all bundled agents found under `<resourcesPath>/agents/`.
 *
 * Idempotent — safe to call on every app launch.
 *
 * @returns {Promise<{seeded: string[], skipped: string[], errors: {id: string, error: Error}[], cleaned: string[]}>}
 */
async function seedBundledAgents() {
  const result = { seeded: [], skipped: [], errors: [], cleaned: [] };

  // Guard against dev / test environments where resourcesPath is unset.
  if (!process.resourcesPath) {
    log(
      "INFO",
      "process.resourcesPath is undefined — skipping bundled-agent seeding"
    );
    return result;
  }

  // Legacy cleanup runs before the missing-source guard: an upgraded
  // install must shed the retired zoo-agent even if this build ships no
  // bundled agents.
  result.cleaned = cleanupLegacyAgents();

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
      `skipped=${result.skipped.length} errors=${result.errors.length} ` +
      `cleaned=${result.cleaned.length}`
  );

  return result;
}

module.exports = {
  seedBundledAgents,
  // Exposed for tests — do not rely on these from production code.
  _internals: {
    seedOneAgent,
    cleanupLegacyAgents,
    agentsTargetRoot,
    seederMarkersDir,
    logFilePath,
  },
};
