// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GAIA Chat - Electron main process
// Self-contained entry point for the desktop installer.
// Starts the Python backend (gaia chat --ui) and loads the frontend.

const { app, BrowserWindow, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");

// ── Configuration ──────────────────────────────────────────────────────────

const APP_NAME = "GAIA Chat";
const BACKEND_PORT = 4200;
const HEALTH_CHECK_URL = `http://localhost:${BACKEND_PORT}/api/health`;
const STARTUP_TIMEOUT = 30000;

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

// ── Backend Process ────────────────────────────────────────────────────────

let backendProcess = null;

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

let mainWindow = null;

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
    icon: path.join(__dirname, "assets", "icon.png"),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  // Remove default menu bar
  mainWindow.setMenuBarVisibility(false);

  // Open external links in the default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
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

  // Create the window
  createWindow();

  // Show loading state
  await loadApp();

  // Wait for backend to be ready, then reload
  if (backendProcess) {
    console.log("Waiting for backend to start...");
    const ready = await waitForBackend(STARTUP_TIMEOUT);

    if (ready) {
      console.log("Backend is ready! Loading app...");
      try {
        await mainWindow.loadURL(`http://localhost:${BACKEND_PORT}`);
      } catch (error) {
        console.error("Failed to load from backend:", error.message);
      }
    } else {
      console.warn("Backend did not respond within timeout.");
    }
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
      loadApp();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    cleanup();
    app.quit();
  }
});

app.on("before-quit", () => {
  cleanup();
});

function cleanup() {
  if (backendProcess) {
    console.log("Stopping backend process...");
    try {
      backendProcess.kill("SIGTERM");
      // Force kill after 3 seconds
      setTimeout(() => {
        try {
          backendProcess.kill("SIGKILL");
        } catch {
          // Already dead
        }
      }, 3000);
    } catch {
      // Already dead
    }
    backendProcess = null;
  }
}
