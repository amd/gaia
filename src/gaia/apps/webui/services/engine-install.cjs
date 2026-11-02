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
 * { stage, reason, message, logTail }; reason is one of FAILURE_REASONS or null.
 */

"use strict";

// Keep these in sync with preload.cjs.
const IPC_PROGRESS_EVENT = "install:progress";
const IPC_STATUS_REQUEST = "install:status";
const IPC_RETRY_REQUEST = "install:retry";
const IPC_CONTINUE_REQUEST = "install:continue";
const IPC_OPEN_LOG_FILE = "install:open-log-file";

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
 */
function createEngineInstall({ installer, ipcMain, shell, getWindow, isPackaged, onFailed }) {
  let status = { phase: "running", stage: null, percent: null, message: "", error: null };
  let wakeForRetry = null;

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

  // Takes no path: the renderer only ever opens the installer's own log.
  ipcMain.handle(IPC_OPEN_LOG_FILE, async () => {
    const problem = await shell.openPath(installer.getLogPath());
    return { ok: !problem, message: problem || null };
  });

  /**
   * Install, and after a failure wait for Try again. Resolves true once the
   * backend is installed, false when stop() ended the wait.
   */
  async function run() {
    for (;;) {
      publish({ phase: "running", stage: null, percent: null, message: "", error: null });
      try {
        await installer.ensureBackend({
          isPackaged,
          onProgress: (stage, percent, message) => publish({ stage, percent, message }),
        });
        return true;
      } catch (err) {
        console.error(`[main] Backend install failed: ${err.stack || err}`);
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
            logTail: installer.readLogTail(),
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

  return {
    run,
    /** The install is done and the backend process is coming up. */
    starting: () => publish({ stage: STARTING_STAGE, percent: null, message: "" }),
    /** Quitting: release a run() that is waiting on Try again. */
    stop: () => {
      if (wakeForRetry) wakeForRetry(false);
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
};
