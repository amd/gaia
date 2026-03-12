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
const { spawn } = require("child_process");

// Services (loaded after app.whenReady)
const TrayManager = require("./services/tray-manager.cjs");
const AgentProcessManager = require("./services/agent-process-manager.cjs");
const NotificationService = require("./services/notification-service.cjs");

// ── Configuration ──────────────────────────────────────────────────────────

const APP_NAME = "GAIA Agent UI";
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

function findGaiaCommand() {
  const isWindows = process.platform === "win32";

  // Check common locations
  const candidates = isWindows
    ? ["gaia.exe", "gaia", "gaia.cmd"]
    : ["gaia"];

  for (const cmd of candidates) {
    try {
      const { execSync } = require("child_process");
      const check = isWindows ? `where ${cmd}` : `which ${cmd}`;
      execSync(check, { stdio: "ignore" });
      return cmd;
    } catch {
      continue;
    }
  }
  return null;
}

function startBackend() {
  const gaiaCmd = findGaiaCommand();

  if (!gaiaCmd) {
    console.warn(
      "Warning: gaia CLI not found. Backend will not start automatically."
    );
    console.warn("Install with: pip install amd-gaia");
    return null;
  }

  console.log(`Starting backend: ${gaiaCmd} chat --ui --ui-port ${BACKEND_PORT}`);

  const child = spawn(
    gaiaCmd,
    ["chat", "--ui", "--ui-port", String(BACKEND_PORT)],
    {
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
    // Load the built frontend directly (for when backend serves it)
    // First try loading from the backend URL
    try {
      await mainWindow.loadURL(`http://localhost:${BACKEND_PORT}`);
      console.log("Loaded app from backend server");
      return;
    } catch {
      // Fall through to loading from file
    }

    // Load from built files
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
  trayManager = new TrayManager(mainWindow, agentProcessManager);
  trayManager.create();

  // T5: Notification Service (routes agent notifications to OS + renderer)
  notificationService = new NotificationService(
    mainWindow,
    agentProcessManager,
    trayManager
  );

  // Update tray context menu when agent statuses change
  agentProcessManager.on("status-change", () => {
    trayManager.refresh();
    trayManager.updateIconAnimation();
  });

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

// ── App Lifecycle ──────────────────────────────────────────────────────────

// Handle creating/removing shortcuts on Windows when installing/uninstalling
try {
  if (require("electron-squirrel-startup")) {
    app.quit();
  }
} catch {
  // electron-squirrel-startup not available
}

app.whenReady().then(async () => {
  // Start the Python backend
  backendProcess = startBackend();

  // Create the window (hidden until ready-to-show)
  createWindow();

  // Initialize services (tray, agent manager, notifications)
  initializeServices();

  // Setup Windows Jump List (T11)
  setupJumpList();

  // Show loading state
  await loadApp();

  // Wait for backend to be ready, then reload
  if (backendProcess) {
    console.log("Waiting for backend to start...");
    const ready = await waitForBackend(STARTUP_TIMEOUT);

    if (ready && mainWindow && !mainWindow.isDestroyed()) {
      console.log("Backend is ready! Loading app...");
      try {
        await mainWindow.loadURL(`http://localhost:${BACKEND_PORT}`);
      } catch (error) {
        console.error("Failed to load from backend:", error.message);
      }
    } else if (!ready) {
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
