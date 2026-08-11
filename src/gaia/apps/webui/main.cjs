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

const { app, BrowserWindow, dialog, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { spawn } = require("child_process");
const { pathToFileURL } = require("url");

// ── Shared log path ───────────────────────────────────────────────────────────
// Single source of truth used by installSafetyNet AND installMainLogTee so
// both write to the same file without independent path computations that
// could drift apart.
const _GAIA_DIR = path.join(os.homedir(), ".gaia");
const _MAIN_LOG_PATH = path.join(_GAIA_DIR, "electron-main.log");

// ── Safety net (issue #934) ───────────────────────────────────────────────────
// Install top-level error handlers BEFORE any service module is required so
// that synchronous throws at module-load time are caught and shown as a
// GAIA-branded error box instead of Electron's bare JS-error dialog.
// Extracted into main-safety-net.cjs so tests can require it without
// triggering main.cjs side effects (Electron modules, service requires).
// Wrapped in try/catch: a corrupt ASAR or bad path would otherwise bypass the
// very handler we are trying to install, falling through to Electron's bare
// JS-error dialog.
let installSafetyNet, installLogTee, _fatalHandler;
try {
  ({ installSafetyNet, installLogTee } = require("./main-safety-net.cjs"));
  ({ fatal: _fatalHandler } = installSafetyNet({
    logPath: _MAIN_LOG_PATH,
    dialogModule: dialog,
    appModule: app,
  }));
} catch (err) {
  try { process.stderr.write(`[main] safety-net load failed: ${err.message}\n`); } catch { }
  try { dialog.showErrorBox("GAIA failed to start", String((err && err.stack) || err)); } catch { }
  // Synchronous exit: service module requires below have no uncaughtException
  // handler installed, so execution cannot safely continue.
  process.exit(1);
}

// Services (loaded after app.whenReady)
const TrayManager = require("./services/tray-manager.cjs");
const AgentProcessManager = require("./services/agent-process-manager.cjs");
const NotificationService = require("./services/notification-service.cjs");
const PortManager = require("./services/port-manager.cjs");
const { isGaiaBackendProcess } = require("./services/backend-orphan.cjs");
const { buildIndexQuery } = require("./services/index-query.cjs");
const backendInstaller = require("./services/backend-installer.cjs");
const installerProgressDialog = require("./services/backend-installer-progress-dialog.cjs");
const autoUpdater = require("./services/auto-updater.cjs");
const agentSeeder = require("./services/agent-seeder.cjs");
const systemMetrics = require("./services/system-metrics.cjs");
const {
  parseDeepLink,
  extractDeepLinkFromArgv,
  dispatchDeepLink,
  buildInstallPrompt,
} = require("./services/deep-link.cjs");

// ── F7: Ozone hint (issue #782) ─────────────────────────────────────────────
// Electron-recommended switch for distro-agnostic Linux behaviour: picks
// Wayland on Wayland sessions, X11 elsewhere. Must be set before
// app.whenReady() fires.
app.commandLine.appendSwitch("ozone-platform-hint", "auto");

// ── F7: --no-sandbox on Linux (issue #782) ───────────────────────────────────
// chrome-sandbox is deleted from the packaged tree by after-pack.cjs so
// Chromium must use its unprivileged user-namespace sandbox on every launch
// path. The .desktop Exec= line already carries --no-sandbox via
// electron-builder.yml linux.executableArgs, but direct `./GAIA.AppImage`
// invocations bypass the .desktop entry. Appending the switch here makes
// all Linux launch paths behave identically.
if (process.platform === "linux") {
  app.commandLine.appendSwitch("no-sandbox");

  // Workaround: disable Chromium's Wayland color management which crashes on
  // some Wayland compositors that implement the wp_color_manager protocol
  // partially. See issue reports about wayland_wp_color_manager.cc SIGTRAP.
  app.commandLine.appendSwitch("disable-features", "WaylandColorManagement");
}

// ── F7: Log tee to ~/.gaia/electron-main.log (issue #782) ───────────────────
// Users often launch AppImages by double-click, not from a terminal, so
// console output vanishes. Mirror console.log/error to a file so the
// diagnostics bundler has something to attach.
(function installMainLogTee() {
  try {
    try { fs.mkdirSync(_GAIA_DIR, { recursive: true }); } catch { /* ignore */ }
    const logPath = _MAIN_LOG_PATH;

    // Rotate if > 5 MB — truncate to last ~5 MB on startup.
    try {
      const st = fs.statSync(logPath);
      if (st.size > 5 * 1024 * 1024) {
        const fd = fs.openSync(logPath, "r");
        const keep = 5 * 1024 * 1024;
        const buf = Buffer.alloc(keep);
        // readSync can return fewer bytes than requested; only write
        // what we actually read so we don't append zero-padding.
        const bytesRead = fs.readSync(
          fd,
          buf,
          0,
          keep,
          Math.max(0, st.size - keep),
        );
        fs.closeSync(fd);
        fs.writeFileSync(logPath, buf.subarray(0, bytesRead));
      }
    } catch {
      // ENOENT or permission — best-effort; just fall through.
    }

    const stream = fs.createWriteStream(logPath, { flags: "a" });
    // Root-cause fix for #934: stream.write() after end emits 'error'
    // asynchronously — the try/catch in wrap() below doesn't catch it.
    // This listener absorbs the event before it becomes uncaughtException.
    installLogTee({ stream, logPath });
    stream.write(
      `\n──── electron-main opened (${new Date().toISOString()}) pid=${process.pid} ────\n`
    );

    const flushAndEnd = () => {
      try { stream.end(); } catch { /* ignore */ }
    };
    process.on("exit", flushAndEnd);

    const wrap = (origFn, level) => (...args) => {
      try {
        const line = args
          .map((a) =>
            typeof a === "string"
              ? a
              : a instanceof Error
                ? (a.stack || a.message || String(a))
                : JSON.stringify(a)
          )
          .join(" ");
        stream.write(`[${new Date().toISOString()}] ${level} ${line}\n`);
      } catch {
        // swallow — we must not recurse into console.error from here
      }
      return origFn.apply(console, args);
    };
    console.log = wrap(console.log.bind(console), "INFO");
    console.warn = wrap(console.warn.bind(console), "WARN");
    console.error = wrap(console.error.bind(console), "ERROR");
  } catch (err) {
    // If this fails, we silently keep the original console — the app must
    // not refuse to launch over a log-tee failure.
    process.stderr.write(`[main] log tee failed: ${err.message}\n`);
  }
})();

// ── Configuration ──────────────────────────────────────────────────────────

const APP_NAME = "GAIA";
// Default fallback only — at runtime we always allocate a free random port
// via PortManager.findFreePort() to avoid EADDRINUSE on zombie backends
// from prior aborted sessions (issue #782 / T5).
const DEFAULT_BACKEND_PORT = 4200;
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
let backendPort = DEFAULT_BACKEND_PORT;
let healthCheckUrl = `http://localhost:${backendPort}/api/health`;
let backendStderrTail = [];
let isIntentionalKill = false;
let mainWindow = null;

// True until createWindow() runs. Guards window-all-closed from firing app.quit()
// while the backend-installer progress dialog is open (it's the only window during
// bootstrap, so destroying it would trigger a premature quit — issue #934).
let isBootstrapping = true;

/** @type {TrayManager | null} */
let trayManager = null;

/** @type {AgentProcessManager | null} */
let agentProcessManager = null;

/** @type {NotificationService | null} */
let notificationService = null;

/** @type {PortManager} */
const portManager = new PortManager({
  logger: { log: console.log.bind(console), error: console.error.bind(console) },
});

/**
 * Set to true when the user explicitly quits (via tray "Quit" or Cmd+Q).
 * Prevents minimize-to-tray from intercepting the close event.
 */
let isQuitting = false;

// ── Backend Process ────────────────────────────────────────────────────────

/**
 * Terminate a backend left running from a previous launch.
 *
 * The desktop app may be double-clicked multiple times or crash, leaving an
 * orphaned `gaia chat --ui` process. On Windows that process keeps an open
 * handle on `gaia.exe`, so the next upgrade's `uv pip install --refresh` fails
 * with `os error 32` (file in use) — issue #1388. We MUST run this BEFORE the
 * installer (`bootstrapBackend()`), not just before spawning the new backend,
 * so the executable can be replaced.
 *
 * The PID is read from ~/.gaia/backend.pid and verified to be a GAIA backend
 * (conservative match) before signalling, so we never kill unrelated user
 * processes (issue #782 TOCTOU mitigation). Idempotent: the pidfile is removed
 * after the first call, so later calls are no-ops.
 */
async function cleanupOrphanedBackend() {
  try {
    const pidFile = path.join(_GAIA_DIR, "backend.pid");
    if (!fs.existsSync(pidFile)) return;
    try {
      const existingPid = parseInt(fs.readFileSync(pidFile, "utf8").trim(), 10);
      if (!Number.isNaN(existingPid)) {
        console.log(`[main] Found existing backend pidfile (${existingPid}) — verifying process identity`);

        let isBackend = false;
        try {
          isBackend = isGaiaBackendProcess(existingPid);
        } catch (err) {
          console.warn(`[main] Could not verify pid ${existingPid}: ${err.message}`);
        }

        if (!isBackend) {
          console.log(`[main] PID ${existingPid} does not appear to be a GAIA backend; skipping kill`);
        } else {
          try {
            await portManager.killBackend(existingPid);
            console.log(`[main] Cleaned up previous backend pid ${existingPid}`);
          } catch (err) {
            console.warn(`[main] Could not clean previous backend pid ${existingPid}: ${err.message}`);
          }
        }
      }
    } catch (err) {
      console.warn(`[main] Failed reading backend pidfile: ${err.message}`);
    }
    try { fs.unlinkSync(pidFile); } catch { /* ignore */ }
  } catch (err) {
    console.warn(`[main] PID cleanup check failed: ${err.message}`);
  }
}

/**
 * Start the GAIA Python backend. Expects the backend installer to have
 * already ensured the venv is populated — callers should await
 * `bootstrapBackend()` first.
 *
 * Returns the ChildProcess, or null if the gaia binary cannot be found
 * (shouldn't happen post-ensureBackend, but we guard just in case).
 */
async function startBackend() {
  const gaiaCmd = backendInstaller.findGaiaBin();

  if (!gaiaCmd) {
    console.error(
      "[main] GAIA backend not found even after install — cannot start backend"
    );
    return null;
  }

  // F5: always spawn on a free random port. Never reuse/probe — the
  // probe-and-reuse path is spoofable and leaves orphans (issue #782).
  try {
    backendPort = await portManager.findFreePort();
  } catch (err) {
    console.warn(
      `[main] findFreePort failed (${err.message}); falling back to ${DEFAULT_BACKEND_PORT}`
    );
    backendPort = DEFAULT_BACKEND_PORT;
  }
  healthCheckUrl = `http://localhost:${backendPort}/api/health`;
  // Defensively clean up any orphaned backend in case bootstrap was skipped
  // on this path. cleanupOrphanedBackend() is idempotent and a no-op once the
  // pidfile has already been consumed earlier in app startup.
  await cleanupOrphanedBackend();

  console.log(`Starting backend: ${gaiaCmd} chat --ui --ui-port ${backendPort}`);

  // Reset per-spawn state so a fresh crash dialog doesn't mix tails.
  backendStderrTail = [];
  isIntentionalKill = false;

  const child = spawn(
    gaiaCmd,
    ["chat", "--ui", "--ui-port", String(backendPort)],
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
    const chunk = data.toString();
    chunk.split(/\r?\n/).forEach((line) => {
      if (!line) return;
      console.log(`[backend] ${line}`);
      // Cap per-line length so pathological no-newline backend output
      // can't balloon the in-memory tail or the crash-dialog body.
      const capped =
        line.length > 2048 ? line.slice(0, 2048) + "…[truncated]" : line;
      backendStderrTail.push(capped);
      if (backendStderrTail.length > 20) backendStderrTail.shift();
    });
  });

  child.on("error", (err) => {
    console.error("Failed to start backend:", err.message);
  });

  // Write a PID file so subsequent AppImage launches can detect and
  // cleanup this backend if it becomes orphaned. The pidfile is removed
  // when the child exits.
  try {
    try { fs.mkdirSync(_GAIA_DIR, { recursive: true }); } catch { /* ignore */ }
    const pidFile = path.join(_GAIA_DIR, "backend.pid");
    fs.writeFileSync(pidFile, String(child.pid), { mode: 0o600 });
    console.log(`[main] Wrote backend pidfile ${pidFile} (pid=${child.pid})`);
  } catch (err) {
    console.warn(`[main] Could not write backend pidfile: ${err.message}`);
  }

  child.on("exit", (code, signal) => {
    if (code !== 0 && code !== null) {
      console.error(`Backend exited with code ${code} (signal=${signal})`);
    }
    const crashed = !isIntentionalKill && code !== 0 && code !== null;
    backendProcess = null;

    if (crashed && !isQuitting) {
      // Fire-and-forget — don't block the event loop.
      void handleBackendCrash(code, signal);
    }

    // Remove pidfile on exit to avoid leaving stale PID behind.
    try {
      const pidFile = path.join(_GAIA_DIR, "backend.pid");
      if (fs.existsSync(pidFile)) {
        const p = fs.readFileSync(pidFile, "utf8").trim();
        if (p && parseInt(p, 10) === child.pid) {
          try { fs.unlinkSync(pidFile); } catch { /* ignore */ }
        }
      }
    } catch (err) {
      // Non-fatal — just log and continue.
      console.warn(`[main] Failed to remove backend pidfile: ${err.message}`);
    }
  });

  return child;
}

/**
 * Show a user-facing crash dialog with the last ~20 stderr lines and
 * offer to write a diagnostics bundle. Invoked from child.on("exit")
 * when the kill was NOT initiated by us.
 */
async function handleBackendCrash(code, signal) {
  const tail = backendStderrTail.slice(-20).join("\n") || "(no stderr captured)";
  const detail =
    `The GAIA backend exited unexpectedly (code=${code}, signal=${signal}).\n\n` +
    `Recent log output:\n${tail}`;

  try {
    const choice = await dialog.showMessageBox({
      type: "error",
      title: "GAIA backend crashed",
      message: "GAIA backend crashed",
      detail,
      buttons: ["Copy diagnostics", "Quit"],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });

    if (choice.response === 0) {
      try {
        const bundlePath = await portManager.writeDiagnosticsBundle();
        await dialog.showMessageBox({
          type: "info",
          title: "Diagnostics saved",
          message: "Diagnostics bundle written",
          detail: `Attach this file to your bug report:\n${bundlePath}`,
          buttons: ["OK"],
        });
      } catch (err) {
        console.error("[main] Could not write diagnostics bundle:", err.message);
        dialog.showErrorBox(
          "Could not write diagnostics",
          `Failed to write diagnostics bundle: ${err.message}`
        );
      }
    }
  } catch (err) {
    console.error("[main] handleBackendCrash failed:", err.message);
  }
}

async function waitForBackend(timeoutMs) {
  const start = Date.now();
  const http = require("http");

  while (Date.now() - start < timeoutMs) {
    try {
      await new Promise((resolve, reject) => {
        const req = http.get(healthCheckUrl, (res) => {
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
    console.log("[main] ready-to-show fired");
    const shouldStartMinimized =
      startMinimized || (trayManager && trayManager.startMinimized);

    if (!shouldStartMinimized) {
      console.log("[main] mainWindow.show() called");
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
    //
    // Pass the real backend base URL as a query parameter so the renderer
    // can reach whatever random port port-manager picked (see #851). The
    // renderer (apiBase.ts) validates this value against an allowlist
    // before using it — keep buildIndexQuery in sync with TRUSTED_API_RE.
    const indexPath = path.join(distPath, "index.html");
    const indexQuery = buildIndexQuery(backendPort);
    console.log("Loading app from:", indexPath, "api:", indexQuery.api);
    // Use pathToFileURL so the file:// URL always has forward slashes on
    // Windows — Chromium 130+ (Electron 40) rejects backslash file URLs
    // that Node's url.format() (used by loadFile) produces on Windows.
    const fileUrl = pathToFileURL(indexPath);
    fileUrl.search = new URLSearchParams(indexQuery).toString();
    await mainWindow.loadURL(fileUrl.href);
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
            <p style="color:#888; font-size:12px;">Backend: http://localhost:${backendPort}</p>
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
  // getBackendPort lets its install/uninstall handlers proxy to the live
  // Python backend's install runtime (issue #1721) on whatever random port
  // port-manager picked this session.
  agentProcessManager = new AgentProcessManager(mainWindow, {
    getBackendPort: () => backendPort,
  });

  // T1: Tray Manager (system tray icon + context menu)
  trayManager = new TrayManager(mainWindow, { backendPort });
  trayManager.create();

  // T5: Notification Service (routes agent notifications to OS + renderer)
  notificationService = new NotificationService(
    mainWindow,
    agentProcessManager,
    trayManager
  );

  // #2007: System metrics for the observability dashboard
  systemMetrics.registerIpcHandlers();

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
        isPackaged: app.isPackaged,
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

// ── gaia:// deep-link install bridge (issue #1725) ──────────────────────────
//
// The website's "Open in GAIA" button opens a `gaia://hub/install/<id>` URL.
// The OS routes it to this app (registered as the gaia:// protocol client);
// we parse it and hand the agent id to the install runtime (issue #1721).
//
// Sources of the URL differ per OS:
//   • macOS      — the `open-url` app event (registered before whenReady).
//   • Win/Linux  — a command-line argument, either at cold start (process.argv)
//                  or on a second launch (the `second-instance` event argv).
// A malformed/unsupported link surfaces a loud error dialog — never a silent
// no-op (deep-link.cjs throws with an actionable message).

/** Holds a deep link that arrived before services were ready. */
let pendingDeepLinkUrl = null;

/**
 * Register this app as the handler for the gaia:// scheme. In dev (`electron .`)
 * the launcher is Electron itself, so the app path must be threaded through
 * explicitly; packaged builds register their own executable.
 */
function registerProtocolHandler() {
  try {
    let registered;
    if (process.defaultApp && process.argv.length >= 2) {
      // Dev: `electron . <args>` — point the scheme at this script.
      registered = app.setAsDefaultProtocolClient("gaia", process.execPath, [
        path.resolve(process.argv[1]),
      ]);
    } else {
      registered = app.setAsDefaultProtocolClient("gaia");
    }
    if (registered) {
      console.log("[main] Registered as gaia:// protocol client");
    } else {
      console.warn(
        "[main] Could not register gaia:// protocol client — deep-link installs may not route to this app"
      );
    }
  } catch (err) {
    console.warn(
      `[main] setAsDefaultProtocolClient failed: ${err && err.message ? err.message : err}`
    );
  }
}

/**
 * Entry point for any inbound deep link. Queues the URL if services aren't up
 * yet, otherwise dispatches immediately. Parse failures are surfaced loudly.
 */
function handleDeepLink(rawUrl) {
  let command;
  try {
    command = parseDeepLink(rawUrl);
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    console.error(`[main] Rejected deep link: ${message}`);
    try {
      dialog.showErrorBox("Invalid GAIA link", message);
    } catch {
      /* dialog may be unavailable pre-ready — the log line above still fires */
    }
    return;
  }

  // If services aren't wired yet (cold start still bootstrapping), stash it.
  if (!agentProcessManager) {
    console.log("[main] Deep link received before services ready — queuing");
    pendingDeepLinkUrl = rawUrl;
    return;
  }

  void runDeepLink(command);
}

/**
 * Explicit, INFORMED per-agent confirmation for a web-triggered install. This
 * is the security gate: the user must confirm the SPECIFIC agent — and see
 * its real trust tier and permissions, not just its bare id — before any
 * download happens (issue #2196 review; hardened so the shown facts match
 * the backend's non-verified gate, which now covers every non-verified
 * agent, not just native ones). Defaults to the safe (Cancel) option.
 *
 * Fails closed: if the agent can't be found in the catalog, or the backend
 * is unreachable, the install is refused with an explanatory error — never
 * installed blind.
 * @returns {Promise<boolean>}
 */
async function confirmDeepLinkInstall(command) {
  let entry;
  try {
    entry = await agentProcessManager.fetchCatalogEntry(command.agentId);
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    console.error(
      `[main] Refusing deep-link install of "${command.agentId}": ${message}`
    );
    try {
      dialog.showErrorBox("Could not verify this agent", message);
    } catch {
      /* best-effort */
    }
    return false;
  }

  const prompt = buildInstallPrompt(entry);
  const choice = await dialog.showMessageBox(
    mainWindow && !mainWindow.isDestroyed() ? mainWindow : null,
    {
      type: "question",
      buttons: [prompt.requiresTrust ? "Trust & Install" : "Install", "Cancel"],
      defaultId: 1, // Enter selects the safe option
      cancelId: 1,
      title: prompt.title,
      message: prompt.message,
      detail: prompt.detail,
      noLink: true,
    }
  );
  return !!choice && choice.response === 0;
}

/** Wire the injected deep-link dispatcher to this process's runtime effects. */
async function runDeepLink(command) {
  console.log(`[main] Deep-link install request: ${command.agentId}`);
  try {
    await dispatchDeepLink(command, {
      confirm: confirmDeepLinkInstall,
      installAgent: (agentId, opts) => agentProcessManager.installAgent(agentId, opts),
      focusWindow: () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          if (mainWindow.isMinimized()) mainWindow.restore();
          if (!mainWindow.isVisible()) mainWindow.show();
          mainWindow.focus();
        }
      },
      logger: { log: console.log.bind(console), error: console.error.bind(console) },
    });
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    console.error(
      `[main] Deep-link install of "${command.agentId}" failed: ${message}`
    );
    try {
      dialog.showErrorBox(`Could not install "${command.agentId}"`, message);
    } catch {
      /* best-effort */
    }
  }
}

/** Drain a queued deep link, plus any gaia:// URL present in the cold-start argv. */
function processStartupDeepLinks() {
  if (pendingDeepLinkUrl) {
    const url = pendingDeepLinkUrl;
    pendingDeepLinkUrl = null;
    handleDeepLink(url);
    return;
  }
  const fromArgv = extractDeepLinkFromArgv(process.argv);
  if (fromArgv) handleDeepLink(fromArgv);
}

// macOS delivers deep links via this event; it can fire before whenReady, so
// register it at module load and let handleDeepLink queue as needed.
app.on("open-url", (event, url) => {
  event.preventDefault();
  handleDeepLink(url);
});

registerProtocolHandler();

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

app.on("second-instance", (_event, argv, _cwd) => {
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

  // Win/Linux: a `gaia://…` deep link from the second launch arrives as an argv
  // entry. Route it into the already-running first instance (issue #1725).
  const deepLink = extractDeepLinkFromArgv(argv);
  if (deepLink) handleDeepLink(deepLink);
});

app.whenReady().then(async () => {
  // Phase 0: seed bundled agents BEFORE the Python backend starts, so the
  // agent registry sees them on its first discovery pass. Failures here are
  // non-fatal — the app must still launch even if seeding is blocked (e.g.
  // permission error on ~/.gaia/agents).
  try {
    const seedResult = await agentSeeder.seedBundledAgents();
    if (seedResult.seeded.length > 0) {
      console.log("[main] Seeded agents:", seedResult.seeded);
    }
    if (seedResult.cleaned.length > 0) {
      console.log("[main] Cleaned legacy agents:", seedResult.cleaned);
    }
    if (seedResult.errors.length > 0) {
      console.warn(
        "[main] Agent seeding errors:",
        seedResult.errors.map((e) => e.id)
      );
    }
  } catch (err) {
    console.warn("[main] Agent seeding failed (non-fatal):", err);
  }

  // Phase A0: kill any orphaned backend from a previous launch BEFORE the
  // installer runs. On Windows a live `gaia chat --ui` holds an open handle on
  // gaia.exe, so an upgrade's `uv pip install --refresh` fails with os error 32
  // unless that process is gone first (issue #1388).
  await cleanupOrphanedBackend();

  // Phase A: ensure the Python backend is installed BEFORE creating the
  // main window. The progress dialog owns the UI during this phase.
  const bootstrapOk = await bootstrapBackend();
  if (!bootstrapOk) {
    console.log("[main] Backend bootstrap aborted — quitting");
    app.quit();
    return;
  }

  // Start the Python backend
  backendProcess = await startBackend();

  // Create the window (hidden until ready-to-show)
  createWindow();
  isBootstrapping = false; // progress dialog is gone; window-all-closed may now quit

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

  // Act on any gaia:// deep link that arrived during bootstrap or via the
  // cold-start command line (issue #1725). Services are now wired.
  processStartupDeepLinks();

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
      console.log("Backend API is ready on port", backendPort);
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
}).catch((err) => {
  // Route explicit rejection through the safety-net so the user gets a
  // GAIA-branded dialog and a stack trace in the log (issue #934).
  _fatalHandler(err);
});

// ── Window-all-closed (C4 fix) ────────────────────────────────────────────
// Don't quit when window is hidden — tray keeps app alive
app.on("window-all-closed", () => {
  // During bootstrap the progress dialog is the only open window. Destroying
  // it (progress.close()) fires this event before the main window exists, which
  // would trigger a premature app.quit() that races with the startup sequence
  // and causes loadURL() to fail with ERR_FAILED (-2) — issue #934.
  if (isBootstrapping) return;

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
  // Mark any subsequent backend exit as intentional so we don't pop the
  // crash dialog during normal shutdown.
  isIntentionalKill = true;
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

  // Stop the Python backend via the port-manager (SIGTERM → wait 3 s → SIGKILL).
  // F5: previously we did this inline; extracted so main.cjs stays lean and
  // to prevent orphan leaks across runs (issue #782).
  if (backendProcess) {
    console.log("Stopping backend process...");
    const proc = backendProcess;
    backendProcess = null;
    isIntentionalKill = true;

    try {
      // Pass the ChildProcess handle (not a bare pid) so port-manager
      // can short-circuit via `proc.exitCode` and close the PID-reuse
      // TOCTOU window.
      await portManager.killBackend(proc);
    } catch (err) {
      console.error("Error stopping backend:", err.message);
    }

    // If the child object still reports alive (race), give its exit
    // listener a brief moment to fire before we continue teardown.
    if (proc.exitCode === null) {
      await new Promise((resolve) => {
        const timer = setTimeout(resolve, 500);
        proc.once("exit", () => {
          clearTimeout(timer);
          resolve();
        });
      });
    }
  }
}
