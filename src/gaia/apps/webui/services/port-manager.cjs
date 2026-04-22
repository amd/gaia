// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * port-manager.cjs — Owns port allocation, backend termination, and
 * diagnostics bundle writing for the GAIA Agent UI.
 *
 * Extracted from main.cjs (issue #782 / T5) so main.cjs stays lean and
 * the probe-and-reuse mistake cannot reappear. The correct pattern is
 * "always spawn on a free random port" — if reuse is ever added later
 * it must require a nonce + UID ownership match, not a bare service-string
 * compare.
 *
 * Pure CommonJS, Node built-ins only (no Electron imports) so it can be
 * unit-tested without an Electron runtime.
 */

"use strict";

const net = require("net");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

/**
 * Resolve the GAIA home directory lazily so tests can point it elsewhere
 * via the `GAIA_HOME_OVERRIDE` env var without re-requiring the module.
 */
function gaiaHome() {
  return process.env.GAIA_HOME_OVERRIDE || path.join(os.homedir(), ".gaia");
}

/**
 * Ask the kernel for a free TCP port by binding to port 0, reading the
 * assigned port, and closing. There is an unavoidable TOCTOU window —
 * the backend process has to (re-)bind immediately or another process
 * on the box can race in. In practice the window is <10 ms and the
 * backend binds on startup, so this is the standard pattern.
 */
function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((err) => {
        if (err) return reject(err);
        resolve(port);
      });
    });
  });
}

/**
 * Terminate a spawned backend: SIGTERM, wait up to 3 s, then SIGKILL.
 * Accepts either a ChildProcess reference (preferred — lets us
 * short-circuit via `exitCode` so PID reuse can never hit us) or a raw
 * pid (useful for tests). Safe to call with nullish.
 */
async function killBackend(procOrPid, logger) {
  const log = (logger && logger.log) || console.log;
  const logError = (logger && logger.error) || console.error;

  if (!procOrPid) return;

  // Distinguish ChildProcess vs raw pid. A ChildProcess has both .pid
  // and .exitCode/.signalCode; typeof === "object" is enough.
  const isChildProcess = typeof procOrPid === "object";
  const proc = isChildProcess ? procOrPid : null;
  const pid = isChildProcess ? procOrPid.pid : procOrPid;

  if (!pid) return;

  // If we have the ChildProcess handle, trust it — the exitCode is set
  // synchronously by Node's child_process before any pid reuse could
  // happen, so this closes the PID-reuse TOCTOU that a pid-only API has.
  if (proc && (proc.exitCode !== null || proc.signalCode !== null)) {
    log(`[port-manager] child pid ${pid} already exited (exitCode=${proc.exitCode} signal=${proc.signalCode})`);
    return;
  }

  // Is the process still alive? kill(pid, 0) throws ESRCH if not.
  // Only relied on when the caller passed a raw pid (tests); callers
  // with a ChildProcess were already short-circuited above.
  const alive = (p) => {
    try {
      process.kill(p, 0);
      return true;
    } catch {
      return false;
    }
  };

  if (!alive(pid)) {
    log(`[port-manager] pid ${pid} already gone`);
    return;
  }

  try {
    process.kill(pid, "SIGTERM");
    log(`[port-manager] SIGTERM sent to pid ${pid}`);
  } catch (err) {
    logError(`[port-manager] SIGTERM failed for pid ${pid}: ${err.message}`);
    return;
  }

  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (!alive(pid)) {
      log(`[port-manager] pid ${pid} exited after SIGTERM`);
      return;
    }
    await new Promise((r) => setTimeout(r, 100));
  }

  try {
    process.kill(pid, "SIGKILL");
    log(`[port-manager] SIGKILL sent to pid ${pid} (did not exit on SIGTERM)`);
  } catch (err) {
    logError(`[port-manager] SIGKILL failed for pid ${pid}: ${err.message}`);
    return;
  }

  // Poll until the kernel reaps the process so callers can safely rebind
  // the port immediately after we return. 1 s cap at 50 ms intervals —
  // SIGKILL is synchronous in the kernel but reaping the zombie can lag.
  const killDeadline = Date.now() + 1000;
  while (Date.now() < killDeadline) {
    if (!alive(pid)) return;
    await new Promise((r) => setTimeout(r, 50));
  }
  logError(
    `[port-manager] pid ${pid} still alive 1s after SIGKILL — orphan risk`,
  );
}

/**
 * Write a diagnostics bundle (.tar.gz) containing the known log/state
 * files under ~/.gaia. Prefers shell-out to `tar` (standard on every
 * Linux distro and macOS); falls back to a best-effort concatenated
 * text blob if `tar` is missing (very rare — mostly Windows from node,
 * where this code path isn't the primary route anyway).
 *
 * Returns the absolute path of the written bundle.
 */
async function writeDiagnosticsBundle(destPath, logger) {
  const logError = (logger && logger.error) || console.error;
  if (!destPath) {
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    destPath = path.join(gaiaHome(), `diagnostics-${ts}.tgz`);
  }

  try {
    fs.mkdirSync(path.dirname(destPath), { recursive: true });
  } catch {
    // ignore — best effort
  }

  const candidates = [
    "electron-install.log",
    "electron-install.log.prev",
    "gaia.log",
    "electron-main.log",
    "electron-install-state.json",
  ];

  const present = candidates.filter((rel) =>
    fs.existsSync(path.join(gaiaHome(), rel))
  );

  if (present.length === 0) {
    // Still write an empty marker so the caller's "here is the file"
    // message doesn't point at vapor.
    fs.writeFileSync(destPath, "");
    return destPath;
  }

  try {
    // -C so paths inside the archive are relative (no /home/... leakage).
    execFileSync("tar", ["-czf", destPath, "-C", gaiaHome(), ...present], {
      stdio: ["ignore", "ignore", "pipe"],
      timeout: 10000,
    });
    return destPath;
  } catch (err) {
    // Fallback: concatenate the text files into a single blob. This is
    // NOT a tar archive; rename so we don't lie about the format.
    const fallback = destPath.replace(/\.tgz$/, ".txt");
    const chunks = [];
    for (const rel of present) {
      const abs = path.join(gaiaHome(), rel);
      try {
        chunks.push(`==== ${rel} ====\n`);
        chunks.push(fs.readFileSync(abs, "utf8"));
        chunks.push("\n");
      } catch (readErr) {
        chunks.push(`(could not read ${rel}: ${readErr.message})\n`);
      }
    }
    fs.writeFileSync(fallback, chunks.join(""));
    logError(
      `[port-manager] tar unavailable (${err.message}); wrote plain-text bundle to ${fallback}`
    );
    return fallback;
  }
}

class PortManager {
  // Instance wrappers so callers can pattern-match the other services
  // (TrayManager, AgentProcessManager) and inject a logger once.
  constructor({ logger } = {}) {
    this.logger = logger || null;
  }

  findFreePort() {
    return findFreePort();
  }

  killBackend(procOrPid) {
    return killBackend(procOrPid, this.logger);
  }

  writeDiagnosticsBundle(destPath) {
    return writeDiagnosticsBundle(destPath, this.logger);
  }
}

module.exports = PortManager;
module.exports.PortManager = PortManager;
module.exports.findFreePort = findFreePort;
module.exports.killBackend = killBackend;
module.exports.writeDiagnosticsBundle = writeDiagnosticsBundle;
