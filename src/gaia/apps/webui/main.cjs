// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GAIA Agent UI - Electron main process
// Self-contained entry point for the desktop installer.
//
// Starts the Python backend (gaia chat --ui), creates the system tray icon,
// manages OS agent subprocesses, and loads the frontend.
//
// Services (co-located per T0 decision):
//   services/tray-manager.js          — System tray icon + context menu (T1)
//   services/agent-process-manager.js — OS agent subprocess lifecycle (T2)
//   services/notification-service.js  — Desktop notifications + permission prompts (T5)
//   preload.cjs                       — contextBridge for IPC channels (T0/T1)

const { app, BrowserWindow, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawn } = require("child_process");

// Services (loaded after app.whenReady)
const TrayManager = require("./services/tray-manager.cjs");
const AgentProcessManager = require("./services/agent-process-manager.cjs");
const NotificationService = require("./services/notification-service.cjs");
const backendInstaller = require("./services/backend-installer.cjs");
const installerProgressDialog = require("./services/backend-installer-progress-dialog.cjs");
const autoUpdater = require("./services/auto-updater.cjs");

// ── Configuration ──────────────────────────────────────────────────────────

const APP_NAME = "GAIA";
const BACKEND_PORT = 4200;
const HEALTH_CHECK_URL = `http://localhost:${BACKEND_PORT}/api/health`;
const STARTUP_TIMEOUT = 30000;

// Parse CLI args (T11: --minimized flag for auto-start)
const startMinimized = process.argv.includes("--minimized");

// Load app.config.json if available
let appConfig = {};
try {
  const configPath = path.join(__dirname, "app.config.json");
  if (fs.existsSync(configPath)) {
    appConfig = JSON.parse(fs.readFileSync(configPath, "utf8"));
  }
} catch (error) {
  console.warn("Could not load app.config.json:", error.message);
}

const windowConfig = appConfig.window || {
  width: 1200,
  height: 800,
  minWidth: 800,
  minHeight: 500,
};

// ── State ──────────────────────────────────────────────────────────────────

let backendProcess = null;
let mainWindow = null;

/** @type {TrayManager | null} */
let trayManager = null;

/** @type {AgentProcessManager | null} */
let agentProcessManager = null;

/** @type {NotificationService | null} */
let notificationService = null;

/**
 * Set to true when the user explicitly quits (via tray "Quit" or Cmd+Q).
 * Prevents minimize-to-tray from intercepting the close event.
 */
let isQuitting = false;

// ── Backend Process ────────────────────────────────────────────────────────

/**
 * Start the GAIA Python backend. Expects the backend installer to have
 * already ensured the venv is populated — callers should await
 * `bootstrapBackend()` first.
 *
 * Returns the ChildProcess, or null if the gaia binary cannot be found
 * (shouldn't happen post-ensureBackend, but we guard just in case).
 */
function startBackend() {
  const gaiaCmd = backendInstaller.findGaiaBin();

  if (!gaiaCmd) {
    console.error(
      "[main] GAIA backend not found even after install — cannot start backend"
    );
    return null;
  }

  console.log(`Starting backend: ${gaiaCmd} chat --ui --ui-port ${BACKEND_PORT}`);

  const child = spawn(
    gaiaCmd,
    ["chat", "--ui", "--ui-port", String(BACKEND_PORT)],
    {
      cwd: os.homedir(),  // Electron's cwd is "/" on macOS when launched from Finder
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env },
      detached: false,
      windowsHide: true, // Prevent console window flash on Windows
    }
  );

  child.stdout.on("data", (data) => {
    const line = data.toString().trim();
    if (line) console.log(`[backend] ${line}`);
  });

  child.stderr.on("data", (data) => {
    const line = data.toString().trim();
    if (line) console.log(`[backend] ${line}`);
  });

  child.on("error", (err) => {
    console.error("Failed to start backend:", err.message);
  });

  child.on("exit", (code) => {
    if (code !== 0 && code !== null) {
      console.error(`Backend exited with code ${code}`);
    }
    backendProcess = null;
  });

  return child;
}

async function waitForBackend(timeoutMs) {
  const start = Date.now();
  const http = require("http");

  while (Date.now() - start < timeoutMs) {
    try {
      await new Promise((resolve, reject) => {
        const req = http.get(HEALTH_CHECK_URL, (res) => {
          if (res.statusCode === 200) {
            resolve();
          } else {
            reject(new Error(`Status ${res.statusCode}`));
          }
        });
        req.on("error", reject);
        req.setTimeout(2000, () => {
          req.destroy();
          reject(new Error("timeout"));
        });
      });
      return true;
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  return false;
}

// ── Window ─────────────────────────────────────────────────────────────────

function findDistPath() {
  // Check multiple locations (dev vs packaged)
  const candidates = [
    path.join(__dirname, "dist", "index.html"), // Development
    path.join(process.resourcesPath || "", "dist", "index.html"), // Packaged (extraResource)
    path.join(__dirname, "..", "dist", "index.html"), // Alternative packaged
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return path.dirname(candidate);
    }
  }
  return null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: windowConfig.width,
    height: windowConfig.height,
    minWidth: windowConfig.minWidth,
    minHeight: windowConfig.minHeight,
    title: APP_NAME,
    icon: path.join(__dirname, "assets", process.platform === "win32" ? "icon.ico" : "icon.png"),
    show: false, // Don't show until ready (prevents flash)
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, "preload.cjs"), // C2 fix: expose IPC via contextBridge
    },
  });

  // Remove default menu bar
  mainWindow.setMenuBarVisibility(false);

  // Open external links in the default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  // ── Minimize-to-tray on close (C4 fix) ──────────────────────────────
  // Intercept window close — hide instead of closing when tray mode is active
  mainWindow.on("close", (event) => {
    if (!isQuitting && trayManager && trayManager.minimizeToTray) {
      event.preventDefault();
      mainWindow.hide();
      console.log("[main] Window hidden to tray");
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  // Show window when ready (unless --minimized or startMinimized config)
  mainWindow.once("ready-to-show", () => {
    const shouldStartMinimized =
      startMinimized || (trayManager && trayManager.startMinimized);

    if (!shouldStartMinimized) {
      mainWindow.show();
    } else {
      console.log("[main] Starting minimized to tray");
    }
  });

  return mainWindow;
}

async function loadApp() {
  const distPath = findDistPath();

  if (distPath) {
    // Always load the bundled frontend from the asar. The backend only
    // serves the API (no frontend files in the pip package), so loading
    // http://localhost:4200/ would show raw JSON instead of the UI.
    const indexPath = path.join(distPath, "index.html");
    console.log("Loading app from:", indexPath);
    await mainWindow.loadFile(indexPath);
  } else {
    // Show a simple loading/error page
    mainWindow.loadURL(
      `data:text/html,
      <html>
        <head><title>${APP_NAME}</title></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; display:flex; align-items:center; justify-content:center; height:100vh; margin:0; background:#1a1a2e; color:#eee;">
          <div style="text-align:center;">
            <h1>${APP_NAME}</h1>
            <p>Waiting for backend to start...</p>
            <p style="color:#888; font-size:12px;">Backend: http://localhost:${BACKEND_PORT}</p>
          </div>
        </body>
      </html>`
    );
  }
}

// ── Services Setup ─────────────────────────────────────────────────────────

function initializeServices() {
  console.log("[main] Initializing services...");

  // T2: Agent Process Manager (manages OS agent subprocesses)
  agentProcessManager = new AgentProcessManager(mainWindow);

  // T1: Tray Manager (system tray icon + context menu)
  trayManager = new TrayManager(mainWindow, { backendPort: BACKEND_PORT });
  trayManager.create();

  // T5: Notification Service (routes agent notifications to OS + renderer)
  notificationService = new NotificationService(
    mainWindow,
    agentProcessManager,
    trayManager
  );

  console.log("[main] Services initialized");
}

// ── Windows Jump List (T11) ────────────────────────────────────────────────

function setupJumpList() {
  if (process.platform !== "win32") return;

  try {
    app.setJumpList([
      {
        type: "tasks",
        items: [
          {
            type: "task",
            title: "New Task",
            description: "Start a new agent task",
            program: process.execPath,
            args: "",
            iconPath: process.execPath,
            iconIndex: 0,
          },
          {
            type: "task",
            title: "Agent Manager",
            description: "View and manage OS agents",
            program: process.execPath,
            args: "--show-agents",
            iconPath: process.execPath,
            iconIndex: 0,
          },
        ],
      },
    ]);
    console.log("[main] Windows Jump List configured");
  } catch (err) {
    console.warn("[main] Could not set Jump List:", err.message);
  }
}

// ── Backend Bootstrap (Phase A) ───────────────────────────────────────────

/**
 * Ensure the Python backend is installed before the main window loads.
 *
 * Shows a borderless progress window while the install runs. On failure,
 * surfaces a retry / manual / quit dialog. Loops until the user either
 * succeeds, chooses manual install, or quits.
 *
 * Returns true if the backend is ready, false if the user chose to quit.
 */
async function bootstrapBackend() {
  // Fast-path: if an install is obviously not needed (binary present and
  // version matches), skip the progress window entirely and go straight to
  // ensureBackend which will confirm readiness.
  const existingBin = backendInstaller.findGaiaBin();
  if (existingBin) {
    const installedVersion = backendInstaller.getInstalledVersion(existingBin);
    let expectedVersion = null;
    try {
      const pkg = JSON.parse(
        fs.readFileSync(path.join(__dirname, "package.json"), "utf8")
      );
      expectedVersion = pkg.version;
    } catch {
      // ignore
    }
    if (installedVersion && installedVersion === expectedVersion) {
      console.log(
        `[main] GAIA backend already at ${installedVersion} — skipping bootstrap UI`
      );
      // Clean up any stale state file so the state machine reflects reality.
      backendInstaller.setState(backendInstaller.STATES.READY, {
        version: expectedVersion,
        installedVersion,
      });
      return true;
    }
  }

  // Slow path: need to install or upgrade. Show the progress window.
  let keepTrying = true;
  while (keepTrying) {
    const progress = installerProgressDialog.createProgressWindow();

    try {
      await backendInstaller.ensureBackend({
        onProgress: progress.onProgress,
      });
      progress.close();
      console.log("[main] Backend bootstrap complete");
      return true;
    } catch (err) {
      progress.close();
      console.error(
        `[main] Backend bootstrap failed: ${err && err.message ? err.message : err}`
      );

      const errorInfo = {
        message: (err && err.message) || "GAIA install failed.",
        stage: (err && err.stage) || null,
        suggestion: (err && err.suggestion) || null,
      };

      const choice = await installerProgressDialog.showFailureDialog(
        null,
        errorInfo
      );

      if (choice === "retry") {
        continue; // loop
      }
      if (choice === "manual") {
        // The user was directed to the docs in an external browser. Quit so
        // they can complete the manual install and restart.
        return false;
      }
      return false; // quit
    }
  }
  return false;
}

// ── App Lifecycle ──────────────────────────────────────────────────────────

// Note: electron-squirrel-startup was removed in Phase C of the
// desktop-installer plan. electron-builder's NSIS target does not need
// Squirrel's first-run shortcut bookkeeping — NSIS creates the Start Menu
// and Desktop shortcuts itself at install time.

// ── Single-instance lock ─────────────────────────────────────────────────
//
// GAIA Agent UI is a desktop app that the user may inadvertently launch
// twice (double-click in Finder, second click on the dock icon, second
// click in the Start Menu, autostart firing while the user already has
// the app open, etc.). Without a lock, two Electron instances would race:
//
//   • Both call backend-installer.cjs concurrently — interleaved log
//     writes, state file (~/.gaia/electron-install-state.json) flapping
//     between INSTALLING records, possibly half-installed venvs.
//   • Both spawn the Python backend on port 4200 — second crashes.
//   • Both register IPC handlers via ipcMain.handle(...) — Electron
//     throws "Attempted to register a second handler" and the second
//     instance dies.
//   • Two tray icons, two auto-updater singletons.
//
// requestSingleInstanceLock() is the standard Electron pattern: the first
// process to call it gets `true`, every subsequent launch on the same
// machine gets `false` and should immediately quit. The first instance
// receives a `second-instance` event and surfaces its window.
const gotTheSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotTheSingleInstanceLock) {
  console.log("[main] Another GAIA Agent UI instance is already running — quitting");
  app.quit();
  // Use process.exit so we bail BEFORE app.whenReady() fires below.
  // app.quit() alone is async and the rest of this file would still
  // execute, racing with the first instance.
  process.exit(0);
}

app.on("second-instance", (_event, _argv, _cwd) => {
  // A second launch happened while we were running. Surface our window
  // (the user almost certainly wanted to see it). mainWindow may be null
  // if we're still in the bootstrap phase — in that case the first
  // instance is already showing the install progress dialog and there's
  // nothing else to do.
  if (typeof mainWindow !== "undefined" && mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    if (!mainWindow.isVisible()) mainWindow.show();
    mainWindow.focus();
  }
});

app.whenReady().then(async () => {
  // Phase A: ensure the Python backend is installed BEFORE creating the
  // main window. The progress dialog owns the UI during this phase.
  const bootstrapOk = await bootstrapBackend();
  if (!bootstrapOk) {
    console.log("[main] Backend bootstrap aborted — quitting");
    app.quit();
    return;
  }

  // Start the Python backend
  backendProcess = startBackend();

  // Create the window (hidden until ready-to-show)
  createWindow();

  // Initialize services (tray, agent manager, notifications)
  initializeServices();

  // Phase F: start the auto-updater (non-blocking). First check runs on
  // a 10s delay inside the module so it never competes with app launch.
  // Any failure here is logged and swallowed — the app continues to run
  // even if auto-update is unavailable.
  try {
    autoUpdater.init(mainWindow);
  } catch (err) {
    console.error(
      "[main] Failed to init auto-updater:",
      err && err.message ? err.message : err
    );
  }

  // Setup Windows Jump List (T11)
  setupJumpList();

  // Show loading state
  await loadApp();

  // Wait for backend API to be reachable. The bundled frontend
  // (loaded from dist/index.html in the asar) auto-detects when the
  // API becomes available and dismisses its "Cannot connect" banner.
  // We do NOT reload the window with http://localhost:4200/ because
  // the pip-installed backend has no frontend files — only the API.
  if (backendProcess) {
    console.log("Waiting for backend to start...");
    const ready = await waitForBackend(STARTUP_TIMEOUT);
    if (ready) {
      console.log("Backend API is ready on port", BACKEND_PORT);
    } else {
      console.warn("Backend did not respond within timeout.");
    }
  }

  // Auto-start enabled agents (T2)
  if (agentProcessManager) {
    try {
      await agentProcessManager.startAllEnabled();
    } catch (err) {
      console.error("Failed to auto-start agents:", err.message);
    }
  }

  app.on("activate", async () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
      // Re-wire existing services to the new window (don't re-create — IPC handlers are already registered)
      if (agentProcessManager) agentProcessManager.mainWindow = mainWindow;
      if (trayManager) trayManager.mainWindow = mainWindow;
      if (notificationService) notificationService.mainWindow = mainWindow;
      try {
        await loadApp();
      } catch (err) {
        console.error("[main] Failed to load app on activate:", err.message);
      }
    } else if (mainWindow) {
      mainWindow.show();
    }
  });
});

// ── Window-all-closed (C4 fix) ────────────────────────────────────────────
// Don't quit when window is hidden — tray keeps app alive
app.on("window-all-closed", () => {
  // If minimize-to-tray is active, the window is just hidden, not closed.
  // Only quit on macOS if the user explicitly quit (Cmd+Q).
  const trayActive = trayManager && trayManager.minimizeToTray;

  if (!trayActive && process.platform !== "darwin") {
    // Trigger the will-quit path which handles async cleanup properly
    app.quit();
  }
  // Otherwise: no-op. App stays running via system tray.
});

// ── Quit lifecycle ─────────────────────────────────────────────────────────
// Electron's before-quit does NOT await async handlers.
// We use will-quit + event.preventDefault() to perform async cleanup, then re-quit.

let cleanupDone = false;

app.on("before-quit", () => {
  isQuitting = true;
});

app.on("will-quit", (event) => {
  if (cleanupDone) return; // Cleanup already finished, let the app quit

  event.preventDefault(); // Prevent quit until cleanup is done
  console.log("[main] will-quit: performing async cleanup...");

  cleanup().then(() => {
    cleanupDone = true;
    console.log("[main] Cleanup complete, quitting...");
    app.quit(); // Re-trigger quit — cleanupDone prevents infinite loop
  }).catch((err) => {
    console.error("[main] Cleanup error:", err.message);
    cleanupDone = true;
    app.quit();
  });
});

async function cleanup() {
  // Phase F: tear down auto-updater timers and IPC handlers.
  try {
    autoUpdater.destroy();
  } catch (err) {
    console.error(
      "[main] Error tearing down auto-updater:",
      err && err.message ? err.message : err
    );
  }

  // Clean up notification timers
  if (notificationService) {
    notificationService.destroy();
    notificationService = null;
  }

  // Stop all managed OS agents gracefully
  if (agentProcessManager) {
    console.log("Stopping all managed agents...");
    try {
      await agentProcessManager.stopAll();
    } catch (err) {
      console.error("Error stopping agents:", err.message);
    }
    agentProcessManager = null;
  }

  // Destroy tray icon
  if (trayManager) {
    trayManager.destroy();
    trayManager = null;
  }

  // Stop the Python backend
  if (backendProcess) {
    console.log("Stopping backend process...");
    const proc = backendProcess; // Save reference before nulling
    backendProcess = null;

    try {
      proc.kill("SIGTERM");
    } catch {
      // Already dead
    }

    // Wait for the process to exit, with a force-kill fallback
    await new Promise((resolve) => {
      // Check if already exited (exitCode is set once the process exits)
      if (proc.exitCode !== null) {
        resolve();
        return;
      }

      const forceKillTimer = setTimeout(() => {
        try {
          proc.kill("SIGKILL");
        } catch {
          // Already dead
        }
        resolve();
      }, 3000);

      proc.once("exit", () => {
        clearTimeout(forceKillTimer);
        resolve();
      });
    });
  }
}
