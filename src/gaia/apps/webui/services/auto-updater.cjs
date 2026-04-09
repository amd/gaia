// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * auto-updater.cjs — GAIA Agent UI auto-update service.
 *
 * Wraps `electron-updater` for the GitHub Releases auto-update flow.
 * Implements §4 Layer 3 + §7 Phase F of docs/plans/desktop-installer.mdx.
 *
 * Behavior:
 *   - First check 10 seconds after `init()` is called (typically from
 *     `app.whenReady`)
 *   - Subsequent checks every 4 hours via re-scheduling setTimeout
 *   - Concurrent-check guard so checks never overlap
 *   - Download silently in the background
 *   - On `update-downloaded`, show a native dialog: "Update ready — restart?"
 *   - Renderer integration via IPC channel `gaia:update:status`
 *   - Disabled entirely via GAIA_DISABLE_UPDATE=1 env var (CI / dev / corp)
 *
 * Exports:
 *   - init(mainWindow)        → set up handlers, schedule checks
 *   - destroy()               → tear down timers and IPC handlers
 *   - checkForUpdates()       → manually trigger a check
 *   - getState()              → returns a copy of the current state
 *   - STATES                  → string constants for valid states
 *
 * Design note: `electron` and `electron-updater` are lazy-required inside
 * `init()`. Accessing `electronUpdater.autoUpdater` outside of an Electron
 * runtime throws synchronously (it reads `app.getVersion()` eagerly), so
 * we keep the module load pure. This also makes `GAIA_DISABLE_UPDATE=1`
 * safely short-circuit before touching any Electron APIs — useful in tests
 * and in environments where the Electron app isn't wired up.
 */

"use strict";

const path = require("path");
const fs = require("fs");
const os = require("os");

// ── Constants ────────────────────────────────────────────────────────────────

const CHECK_DELAY_MS = 10 * 1000; // First check 10s after init
const CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000; // Subsequent checks every 4h
const LOG_PATH = path.join(os.homedir(), ".gaia", "electron-updater.log");

const STATES = Object.freeze({
  IDLE: "idle",
  CHECKING: "checking",
  AVAILABLE: "available",
  DOWNLOADING: "downloading",
  DOWNLOADED: "downloaded",
  ERROR: "error",
  DISABLED: "disabled",
});

// ── Module state ─────────────────────────────────────────────────────────────

/** Shape broadcast to the renderer via `gaia:update:status`. */
const state = {
  status: STATES.IDLE,
  version: null,
  progress: 0,
  releaseNotes: null,
  error: null,
};

let mainWindowRef = null;
let checkInProgress = false;
let scheduledTimeout = null;
let initialCheckTimeout = null;
let ipcHandlersRegistered = false;
let initialized = false;

// Lazy-loaded Electron references (populated inside init()).
let electronApi = null; // { dialog, ipcMain }
let autoUpdaterRef = null; // electron-updater's singleton

// ── Logging ──────────────────────────────────────────────────────────────────

function log(level, message, ...args) {
  const ts = new Date().toISOString();
  const extra = args.length ? " " + safeStringify(args) : "";
  const line = `[${ts}] [${level}] ${message}${extra}\n`;
  try {
    fs.mkdirSync(path.dirname(LOG_PATH), { recursive: true });
    fs.appendFileSync(LOG_PATH, line);
  } catch {
    // Non-fatal — logging must never crash the app.
  }
  // Also mirror to stdout so devs see it in `npm start` output.
  try {
    // eslint-disable-next-line no-console
    console.log(`[auto-updater] ${level} ${message}`, ...args);
  } catch {
    // ignore
  }
}

function safeStringify(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

// ── State management ─────────────────────────────────────────────────────────

function broadcastState() {
  if (!mainWindowRef) return;
  try {
    if (mainWindowRef.isDestroyed && mainWindowRef.isDestroyed()) return;
    if (mainWindowRef.webContents && !mainWindowRef.webContents.isDestroyed()) {
      mainWindowRef.webContents.send("gaia:update:status", { ...state });
    }
  } catch (err) {
    log("warn", "Failed to broadcast state:", err && err.message);
  }
}

function setState(patch) {
  Object.assign(state, patch);
  broadcastState();
}

function getState() {
  return { ...state };
}

// ── Env gate ─────────────────────────────────────────────────────────────────

function isDisabled() {
  return process.env.GAIA_DISABLE_UPDATE === "1";
}

// ── Core check ───────────────────────────────────────────────────────────────

async function checkForUpdates() {
  if (isDisabled()) {
    setState({ status: STATES.DISABLED });
    log("info", "Update check skipped — GAIA_DISABLE_UPDATE=1");
    return;
  }
  if (!autoUpdaterRef) {
    log("warn", "checkForUpdates called before init — ignoring");
    return;
  }
  if (checkInProgress) {
    log("info", "Skipping check — another check is already in progress");
    return;
  }
  checkInProgress = true;
  setState({ status: STATES.CHECKING, error: null });
  try {
    log("info", "Checking for updates...");
    await autoUpdaterRef.checkForUpdates();
  } catch (err) {
    setState({
      status: STATES.ERROR,
      error: (err && err.message) || String(err),
    });
    log("error", "Update check failed:", err && err.message);
  } finally {
    checkInProgress = false;
  }
}

function scheduleNextCheck() {
  if (scheduledTimeout) {
    clearTimeout(scheduledTimeout);
    scheduledTimeout = null;
  }
  scheduledTimeout = setTimeout(async () => {
    try {
      await checkForUpdates();
    } catch (err) {
      log("error", "Scheduled check threw:", err && err.message);
    }
    scheduleNextCheck();
  }, CHECK_INTERVAL_MS);
}

// ── Event wiring ─────────────────────────────────────────────────────────────

function wireAutoUpdaterEvents() {
  autoUpdaterRef.on("checking-for-update", () => {
    setState({ status: STATES.CHECKING });
  });

  autoUpdaterRef.on("update-available", (info) => {
    const releaseNotes =
      typeof info.releaseNotes === "string" ? info.releaseNotes : null;
    setState({
      status: STATES.AVAILABLE,
      version: info.version || null,
      releaseNotes,
      error: null,
    });
    log("info", `Update available: ${info.version}`);
  });

  autoUpdaterRef.on("update-not-available", (info) => {
    // Reset to idle so the UI hides any stale "available" chip.
    setState({
      status: STATES.IDLE,
      version: null,
      progress: 0,
      releaseNotes: null,
      error: null,
    });
    log("info", `No update available (current ${info && info.version})`);
  });

  autoUpdaterRef.on("download-progress", (progress) => {
    const percent =
      progress && typeof progress.percent === "number"
        ? Math.max(0, Math.min(100, Math.round(progress.percent)))
        : 0;
    setState({
      status: STATES.DOWNLOADING,
      progress: percent,
    });
  });

  autoUpdaterRef.on("update-downloaded", async (info) => {
    setState({
      status: STATES.DOWNLOADED,
      version: (info && info.version) || state.version,
      progress: 100,
      error: null,
    });
    log("info", `Update downloaded: ${info && info.version}`);

    if (!electronApi || !electronApi.dialog) {
      log("warn", "No dialog available — skipping restart prompt");
      return;
    }
    try {
      const choice = await electronApi.dialog.showMessageBox(
        mainWindowRef && !mainWindowRef.isDestroyed() ? mainWindowRef : null,
        {
          type: "info",
          buttons: ["Restart now", "Later"],
          defaultId: 0,
          cancelId: 1,
          title: "Update ready",
          message: `GAIA Agent UI ${info && info.version ? info.version : ""} has been downloaded.`,
          detail:
            "Restart the app to apply the update. Your chat history will be preserved.",
        }
      );
      if (choice && choice.response === 0) {
        log("info", "User chose to restart — calling quitAndInstall");
        // (isSilent=false, isForceRunAfter=true) — run the installer UI on
        // Windows and relaunch after the update is applied.
        autoUpdaterRef.quitAndInstall(false, true);
      } else {
        log("info", "User deferred restart — will install on next quit");
      }
    } catch (err) {
      log("error", "Failed to show restart dialog:", err && err.message);
    }
  });

  autoUpdaterRef.on("error", (err) => {
    setState({
      status: STATES.ERROR,
      error: (err && err.message) || String(err),
    });
    log("error", "electron-updater error:", err && err.message);
  });
}

function registerIpcHandlers() {
  if (ipcHandlersRegistered || !electronApi || !electronApi.ipcMain) return;
  const { ipcMain } = electronApi;

  ipcMain.handle("gaia:update:get-status", () => getState());
  ipcMain.handle("gaia:update:check", async () => {
    await checkForUpdates();
    return getState();
  });
  ipcHandlersRegistered = true;
}

function unregisterIpcHandlers() {
  if (!ipcHandlersRegistered || !electronApi || !electronApi.ipcMain) return;
  const { ipcMain } = electronApi;
  try {
    ipcMain.removeHandler("gaia:update:get-status");
  } catch {
    // ignore
  }
  try {
    ipcMain.removeHandler("gaia:update:check");
  } catch {
    // ignore
  }
  ipcHandlersRegistered = false;
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Initialize the auto-updater. Safe to call multiple times — subsequent
 * calls update the window reference only.
 *
 * Must NOT block app launch: if anything goes wrong the caller catches and
 * continues, and this function itself never throws.
 *
 * @param {Electron.BrowserWindow | null} mainWindow
 */
function init(mainWindow) {
  mainWindowRef = mainWindow || null;

  // Disabled path — short-circuit BEFORE touching any Electron APIs so
  // this works in plain Node tests where require('electron') returns a
  // string and the electron-updater singleton throws on access.
  if (isDisabled()) {
    setState({ status: STATES.DISABLED });
    log("info", "Auto-updater disabled via GAIA_DISABLE_UPDATE=1");
    return;
  }

  if (initialized) {
    // Just refresh the window reference and push the current state down.
    broadcastState();
    return;
  }

  // Lazy-load Electron and electron-updater. Any failure here is logged
  // and the updater stays in `idle` — we never crash the app.
  try {
    // eslint-disable-next-line global-require
    const electron = require("electron");
    // eslint-disable-next-line global-require
    const electronUpdater = require("electron-updater");

    if (!electron || !electron.app || !electron.ipcMain || !electron.dialog) {
      log(
        "warn",
        "Electron APIs unavailable — auto-updater will not be active"
      );
      return;
    }

    electronApi = {
      dialog: electron.dialog,
      ipcMain: electron.ipcMain,
    };
    autoUpdaterRef = electronUpdater.autoUpdater;
  } catch (err) {
    log("error", "Failed to load electron-updater:", err && err.message);
    setState({
      status: STATES.ERROR,
      error: (err && err.message) || "Failed to load electron-updater",
    });
    return;
  }

  // Configure electron-updater. Provider config comes from
  // electron-builder.yml (`publish:` block) at build time; we only set
  // behavioral flags here.
  try {
    autoUpdaterRef.autoDownload = true;
    autoUpdaterRef.autoInstallOnAppQuit = true;
    autoUpdaterRef.disableWebInstaller = true;
    autoUpdaterRef.allowDowngrade = false;
    // Allow pre-releases only if explicitly opted in (for beta channels later).
    autoUpdaterRef.allowPrerelease =
      process.env.GAIA_UPDATE_PRERELEASE === "1";

    autoUpdaterRef.logger = {
      info: (m) => log("info", String(m)),
      warn: (m) => log("warn", String(m)),
      error: (m) => log("error", String(m)),
      debug: (m) => log("debug", String(m)),
    };
  } catch (err) {
    log("warn", "Failed to configure autoUpdater flags:", err && err.message);
  }

  try {
    wireAutoUpdaterEvents();
  } catch (err) {
    log("error", "Failed to wire autoUpdater events:", err && err.message);
    return;
  }

  try {
    registerIpcHandlers();
  } catch (err) {
    log("warn", "Failed to register IPC handlers:", err && err.message);
  }

  // First check after CHECK_DELAY_MS, then every CHECK_INTERVAL_MS.
  initialCheckTimeout = setTimeout(async () => {
    try {
      await checkForUpdates();
    } catch (err) {
      log("error", "Initial check threw:", err && err.message);
    }
    scheduleNextCheck();
  }, CHECK_DELAY_MS);

  initialized = true;
  log(
    "info",
    `Auto-updater initialized; first check in ${CHECK_DELAY_MS}ms`
  );
}

/** Tear down timers and IPC handlers. Safe to call multiple times. */
function destroy() {
  if (initialCheckTimeout) {
    clearTimeout(initialCheckTimeout);
    initialCheckTimeout = null;
  }
  if (scheduledTimeout) {
    clearTimeout(scheduledTimeout);
    scheduledTimeout = null;
  }
  unregisterIpcHandlers();
  mainWindowRef = null;
  // Keep `initialized` true — calling init() again after destroy() is not a
  // supported lifecycle and we'd need to re-wire electron-updater events
  // which cannot be reliably cleaned up via its public API.
}

module.exports = {
  init,
  destroy,
  checkForUpdates,
  getState,
  STATES,
};
