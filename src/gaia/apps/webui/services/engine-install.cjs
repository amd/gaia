// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * engine-install.cjs — runs the backend install behind the app window.
 *
 * Holds one status object, sends it to the window's EngineInstallScreen on each
 * change, and waits on that screen's Try again after a failure. The install
 * itself is backend-installer.cjs.
 *
 * Status: { phase: 'running'|'failed', stage, percent, message, error }
 * percent is null when the stage has none to report. error, when failed:
 * { stage, reason, message, logTail, previousVersion, lockedBy }; reason is
 * one of FAILURE_REASONS or null, previousVersion is the version an update can
 * go back to, or null, and lockedBy names the programs holding GAIA's files
 * after a 'locked' failure on Windows (lock-holders.cjs), or is null.
 */

"use strict";

const defaultLockHolders = require("./lock-holders.cjs");

// Keep these in sync with preload.cjs.
const IPC_PROGRESS_EVENT = "install:progress";
const IPC_STATUS_REQUEST = "install:status";
const IPC_RETRY_REQUEST = "install:retry";
const IPC_CONTINUE_REQUEST = "install:continue";
const IPC_OPEN_LOG_FILE = "install:open-log-file";
const IPC_REVERT_REQUEST = "install:revert";
const IPC_CLOSE_LOCKERS_REQUEST = "install:close-lockers";

/** Not an installer stage: the backend is installed and starting. */
const STARTING_STAGE = "start";
const AGENT_REFRESH_STAGE = "agent-refresh";
const CONTINUE = "continue";

/**
 * @param {object} deps
 * @param {object} deps.installer - backend-installer.cjs
 * @param {object} deps.ipcMain
 * @param {object} deps.shell
 * @param {() => Electron.BrowserWindow|null} deps.getWindow
 * @param {boolean} deps.isPackaged
 * @param {(err: Error) => void} deps.onFailed - Runs once the failure is on screen.
 * @param {() => string|null} deps.previousVersion - The version an update can go back to.
 * @param {() => Promise<void>} deps.revert - Installs that version; the app then restarts into it.
 * @param {object} [deps.lockHolders] - lock-holders.cjs
 * @param {number} [deps.ownPid] - This app's pid; its own process tree is never offered for closing.
 * @param {string} [deps.platform]
 */
function createEngineInstall({
  installer,
  ipcMain,
  shell,
  getWindow,
  isPackaged,
  onFailed,
  previousVersion,
  revert,
  lockHolders = defaultLockHolders,
  ownPid = process.pid,
  platform = process.platform,
}) {
  let status = { phase: "running", stage: null, percent: null, message: "", error: null };
  let wakeForRetry = null;
  let lockedFiles = null;
  let closing = false;
  let quitting = null;
  let installing = null;

  /** Who holds `files` open, summarized for the screen; null when Windows can't say. */
  async function findHolders(files) {
    try {
      const holders = lockHolders.summarizeHolders(await lockHolders.queryLockHolders(files), ownPid);
      console.log(
        `[main] Holding ${files.join(", ")}: ` +
          (holders.map((h) => `${h.name} pid ${h.pid} (${h.path || "path unknown"}, ${h.isGaia ? "GAIA" : "not GAIA"})`).join("; ") || "nothing now")
      );
      return holders;
    } catch (err) {
      console.error(`[main] Could not find which programs hold ${files.join(", ")}: ${err.message}`);
      return null;
    }
  }

  function publish(next) {
    status = { ...status, ...next };
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send(IPC_PROGRESS_EVENT, status);
  }

  ipcMain.handle(IPC_STATUS_REQUEST, () => ({ ...status, logPath: installer.getLogPath() }));

  // Resolves false when no failure is waiting, so a double click can't start a second install.
  ipcMain.handle(IPC_RETRY_REQUEST, () => {
    if (!wakeForRetry) return false;
    wakeForRetry(true);
    return true;
  });

  // Only a failed agent update can be passed: the app works without it, and it
  // stays recorded, so the next launch tries again. A failed install can't.
  ipcMain.handle(IPC_CONTINUE_REQUEST, () => {
    if (!wakeForRetry || status.error?.stage !== AGENT_REFRESH_STAGE) return false;
    wakeForRetry(CONTINUE);
    return true;
  });

  // After a failed update: go back to the version before it. Its errors reach the screen.
  ipcMain.handle(IPC_REVERT_REQUEST, async () => {
    if (!wakeForRetry || !status.error?.previousVersion) return false;
    await revert();
    return true;
  });

  // Ends the GAIA processes the screen named, then installs again. Takes no
  // pids from the renderer, and ends only a process that is still the one
  // named, so a pid reused since cannot be hit.
  ipcMain.handle(IPC_CLOSE_LOCKERS_REQUEST, async () => {
    const shown = status.error?.lockedBy;
    if (!wakeForRetry || closing || !shown?.length || !shown.every((h) => h.isGaia)) return false;
    closing = true;
    try {
      const current = await lockHolders.queryLockHolders(lockedFiles);
      const fresh = lockHolders.summarizeHolders(current, ownPid);
      const pids = shown
        .filter((h) => fresh.some((f) => f.isGaia && f.pid === h.pid && f.path === h.path))
        .map((h) => h.pid);
      console.log(`[main] Ending GAIA processes holding the install: ${pids.join(", ") || "none still running"}`);
      await lockHolders.endProcessTrees(pids);
    } finally {
      closing = false;
    }
    wakeForRetry(true);
    return true;
  });

  // Takes no path: the renderer only ever opens the installer's own log.
  ipcMain.handle(IPC_OPEN_LOG_FILE, async () => {
    const problem = await shell.openPath(installer.getLogPath());
    return { ok: !problem, message: problem || null };
  });

  /**
   * Install, and after a failure wait for Try again. Resolves true once the
   * backend is installed, false when stop() ended the install or the wait.
   */
  async function run() {
    quitting = new AbortController();
    for (;;) {
      publish({ phase: "running", stage: null, percent: null, message: "", error: null });
      try {
        installing = installer.ensureBackend({
          isPackaged,
          onProgress: (stage, percent, message) => publish({ stage, percent, message }),
          signal: quitting.signal,
        });
        await installing;
        return true;
      } catch (err) {
        if (quitting.signal.aborted) {
          console.log(`[main] Backend install stopped for the quit: ${err.message}`);
          return false;
        }
        console.error(`[main] Backend install failed: ${err.stack || err}`);
        lockedFiles = err.reason === "locked" && err.lockedFiles?.length ? err.lockedFiles : null;
        const lockedBy = lockedFiles && platform === "win32" ? await findHolders(lockedFiles) : null;
        const retry = new Promise((resolve) => {
          wakeForRetry = resolve;
        });
        publish({
          phase: "failed",
          error: {
            stage: err.stage || null,
            reason: err.reason || null,
            // The rest is the tool's output, which the log tail carries.
            message: String(err.message).split("\n")[0],
            logTail: logTailOrWhy(),
            previousVersion: previousVersion(),
            lockedBy,
          },
        });
        onFailed(err);
        const retrying = await retry;
        wakeForRetry = null;
        if (retrying === CONTINUE) return true;
        if (!retrying) return false;
      }
    }
  }

  /** The log's last lines; a log that can't be read says why instead of crashing the failure screen. */
  function logTailOrWhy() {
    try {
      return installer.readLogTail();
    } catch (err) {
      console.error(`[main] Could not read the install log: ${err.stack || err}`);
      return [`The install log could not be read: ${err.message}`];
    }
  }

  return {
    run,
    /** The install is done and the backend process is coming up. */
    starting: () => publish({ stage: STARTING_STAGE, percent: null, message: "" }),
    /**
     * Quitting: stop a running install, with every process it started, or
     * release a run() waiting on Try again. Resolves once the install has ended.
     */
    stop: async () => {
      if (wakeForRetry) wakeForRetry(false);
      if (!quitting) return;
      quitting.abort();
      // run() reports the outcome; this only waits for the processes to be gone.
      await installing?.then(
        () => {},
        () => {}
      );
    },
  };
}

module.exports = {
  createEngineInstall,
  STARTING_STAGE,
  IPC_PROGRESS_EVENT,
  IPC_STATUS_REQUEST,
  IPC_RETRY_REQUEST,
  IPC_CONTINUE_REQUEST,
  IPC_OPEN_LOG_FILE,
  IPC_REVERT_REQUEST,
  IPC_CLOSE_LOCKERS_REQUEST,
};
