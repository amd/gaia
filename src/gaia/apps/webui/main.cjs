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

const {
  app,
  autoUpdater: electronAutoUpdater,
  BrowserWindow,
  Menu,
  dialog,
  ipcMain,
  powerMonitor,
  session,
  shell,
} = require("electron");
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
// Set when a fatal error quits the app: the crash dialog was the question,
// so the quit guard must not ask again.
let fatalQuitting = false;
try {
  ({ installSafetyNet, installLogTee } = require("./main-safety-net.cjs"));
  ({ fatal: _fatalHandler } = installSafetyNet({
    logPath: _MAIN_LOG_PATH,
    dialogModule: dialog,
    appModule: app,
    beforeQuit: () => {
      fatalQuitting = true;
    },
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
const { appIconPath, appUserModelId } = require("./services/app-icon.cjs");
const {
  attachWindowGuards,
  confirmQuit,
  fetchWorkInProgress,
  installQuitGuard,
  QUIT,
  RESTART_FOR_UPDATE,
} = require("./services/quit-guard.cjs");
const AgentProcessManager = require("./services/agent-process-manager.cjs");
const NotificationService = require("./services/notification-service.cjs");
const KeepAwake = require("./services/keep-awake.cjs");
const PortManager = require("./services/port-manager.cjs");
const { isGaiaBackendProcess } = require("./services/backend-orphan.cjs");
const { showBackendCrashDialog } = require("./services/backend-crash-dialog.cjs");
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
const {
  isAllowedExternalUrl,
  isInAppNavigation,
  describeBlockedUrl,
} = require("./services/link-policy.cjs");
const { createStartupDeepLinkQueue } = require("./services/deep-link-queue.cjs");
const { installWindowKeys } = require("./services/window-keys.cjs");
const { installQuitCleanup } = require("./services/quit-cleanup.cjs");
const { chromiumSwitches } = require("./services/chromium-switches.cjs");
const { titleBarKind, titleBarOptions } = require("./services/window-frame.cjs");
const { resolveDevQaMode } = require("./services/dev-qa-mode.cjs");
const { createBrowserPaneService, PANE_PARTITION } = require("./services/browser-pane/ipc.cjs");

// No ozone switch: Electron 38+ already picks Wayland or X11, and users override with --ozone-platform=x11.
for (const [name, value] of chromiumSwitches({ platform: process.platform, env: process.env })) {
  if (value === undefined) app.commandLine.appendSwitch(name);
  else app.commandLine.appendSwitch(name, value);
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

// GAIA_BIN in an unpackaged build: use that backend and leave the user's install alone.
const devQa = resolveDevQaMode({ isPackaged: app.isPackaged, env: process.env });
if (devQa) console.log(`[main] Dev QA mode: backend ${devQa.gaiaBin}; installer, pidfile and gaia:// skipped`);

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
// The in-flight startBackend(), so a quit during startup can wait for the
// spawn and stop it instead of leaving it behind.
let backendStarting = null;
// Set as cleanup() begins; startup stops at its next step once it is.
let shutdownStarted = false;
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

/** @type {KeepAwake | null} */
let keepAwake = null;

/** @type {ReturnType<typeof createBrowserPaneService> | null} */
let browserPane = null;

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
  const gaiaCmd = devQa ? devQa.gaiaBin : backendInstaller.findGaiaBin();

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
    throw new Error(
      `Could not reserve a local port for the GAIA backend: ${err.message}. ` +
        "Check that security software isn't blocking 127.0.0.1, then relaunch GAIA. " +
        `Logs: ${_MAIN_LOG_PATH}`,
      { cause: err }
    );
  }
  healthCheckUrl = `http://localhost:${backendPort}/api/health`;
  // Defensively clean up any orphaned backend in case bootstrap was skipped
  // on this path. cleanupOrphanedBackend() is idempotent and a no-op once the
  // pidfile has already been consumed earlier in app startup.
  // Dev QA mode leaves it alone: it belongs to the installed app's backend.
  if (!devQa) await cleanupOrphanedBackend();

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
      // GAIA_APP_PID: GAIA's daemon unloads its models once this app exits
      // and no other GAIA client is using them.
      env: { ...process.env, GAIA_APP_PID: String(process.pid) },
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
  if (!devQa) {
    try {
      try { fs.mkdirSync(_GAIA_DIR, { recursive: true }); } catch { /* ignore */ }
      const pidFile = path.join(_GAIA_DIR, "backend.pid");
      fs.writeFileSync(pidFile, String(child.pid), { mode: 0o600 });
      console.log(`[main] Wrote backend pidfile ${pidFile} (pid=${child.pid})`);
    } catch (err) {
      console.warn(`[main] Could not write backend pidfile: ${err.message}`);
    }
  }

  child.on("exit", (code, signal) => {
    if (code !== 0 && code !== null) {
      console.error(`Backend exited with code ${code} (signal=${signal})`);
    }
    const crashed = !isIntentionalKill && code !== 0 && code !== null;
    backendProcess = null;
    if (trayManager && !isQuitting) trayManager.setBackendAlive(false);

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
 * Tell the user the backend stopped and offer Restart / Save diagnostics /
 * Quit. Invoked from child.on("exit") when the kill was NOT initiated by us.
 */
async function handleBackendCrash(code, signal) {
  try {
    const choice = await showBackendCrashDialog({
      dialog,
      app,
      code,
      signal,
      tailLines: backendStderrTail.slice(-8),
      writeDiagnostics: () => portManager.writeDiagnosticsBundle(),
      // The backend is gone, so there is no running work to ask about.
      beforeQuit: () => { fatalQuitting = true; },
      parent: mainWindow,
    });
    console.log(`[main] Backend crash: user chose ${choice}`);
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

/** Refuse a link loudly — the user clicked it, so they get told why nothing happened. */
function reportBlockedLink(url) {
  const message = describeBlockedUrl(url);
  console.error(`[main] ${message}`);
  // Non-blocking and parented: showErrorBox would freeze the main process.
  dialog
    .showMessageBox(mainWindow && !mainWindow.isDestroyed() ? mainWindow : null, {
      type: "warning",
      title: "Link blocked",
      message: "Link blocked",
      detail: message,
      buttons: ["OK"],
      noLink: true,
    })
    .catch((err) =>
      console.error(
        `[main] Could not show the link-blocked dialog: ${err && err.message ? err.message : err}`
      )
    );
}

// Windows and macOS draw the native controls into the app's own top row; Linux
// keeps the system frame (services/window-frame.cjs).
const TITLE_BAR_KIND = titleBarKind(process.platform);
const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

// The renderer sends its canvas and text colors so the controls match the theme.
ipcMain.handle("window:set-titlebar-colors", (_event, colors) => {
  const { color, symbolColor } = colors || {};
  if (!HEX_COLOR.test(color) || !HEX_COLOR.test(symbolColor)) {
    throw new Error(`Title bar colors must be #rrggbb, got ${JSON.stringify(colors)}`);
  }
  if (TITLE_BAR_KIND === "overlay" && mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.setTitleBarOverlay({ color, symbolColor });
  }
  return true;
});

// Registered once: createWindow runs again on every macOS reopen, and the session is shared.
function titleDownloadSaveDialogs() {
  // An export is a blob: download, and Chromium titles its Save dialog with the blob URL.
  session.defaultSession.on("will-download", (_event, item) => {
    item.setSaveDialogOptions({
      title: "Save from GAIA",
      defaultPath: path.join(app.getPath("downloads"), item.getFilename()),
    });
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    ...titleBarOptions(process.platform),
    width: windowConfig.width,
    height: windowConfig.height,
    minWidth: windowConfig.minWidth,
    minHeight: windowConfig.minHeight,
    title: APP_NAME,
    icon: appIconPath(),
    show: false, // Don't show until ready (prevents flash)
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, "preload.cjs"), // C2 fix: expose IPC via contextBridge
    },
  });

  // No File/Edit/View menu: hiding the bar alone still lets Alt bring it back.
  // macOS keeps its menu, which carries copy, paste and quit.
  if (process.platform !== "darwin") {
    Menu.setApplicationMenu(null);
    installWindowKeys(mainWindow.webContents, { devTools: !app.isPackaged });
  }
  mainWindow.setMenuBarVisibility(false);

  // Open external links in the default browser. The URL here is already
  // RESOLVED against the file:// document, so a scheme-less markdown link
  // arrives as file:///C:/… — check the scheme before handing it to the OS.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternalUrl(url)) {
      shell.openExternal(url);
    } else {
      reportBlockedLink(url);
    }
    return { action: "deny" };
  });

  // Nothing may navigate the app window away from its own document. In-page
  // anchors pass; web links open in the browser; everything else is refused.
  mainWindow.webContents.on("will-navigate", (event, url) => {
    const currentUrl = mainWindow.webContents.getURL();
    if (isInAppNavigation(url, currentUrl)) return;
    event.preventDefault();
    if (isAllowedExternalUrl(url)) {
      shell.openExternal(url);
      return;
    }
    reportBlockedLink(url);
  });

  // ── Minimize-to-tray on close (C4 fix), first-close notice, quit guard ──
  attachWindowGuards(mainWindow, {
    app,
    guard: quitGuard,
    getTray: () => trayManager,
    isQuitting: () => isQuitting,
    platform: process.platform,
    reportNoticeError: (err) => {
      console.error("[main] First-close notice failed:", err.message);
      const where = process.platform === "darwin" ? "menu bar" : "tray";
      dialog.showErrorBox(
        "GAIA is still running",
        `GAIA is still running in the ${where}. Quit from the ${where} menu to stop it.\n\n` +
          `(The usual notice failed: ${err.message})`
      );
    },
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
    if (trayManager) trayManager.windowClosed();
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
    // Show a simple loading/error page. Percent-encoded: an unescaped `#`
    // in a data: URL starts the fragment and truncates the document there.
    const waitingHtml = `<!DOCTYPE html>
      <html lang="en">
        <head>
          <title>${APP_NAME}</title>
          <style>
            /* Standalone page: it loads from a data: URL and never sees
             * src/styles/index.css, so the dark theme's role values are
             * mirrored here. Keep them in step with that file. */
            :root {
              --bg-primary: #1A1917;
              --text-primary: #EFECE6;
              --text-secondary: #B3ADA5;
            }
            body {
              font-family: -apple-system, BlinkMacSystemFont, sans-serif;
              display: flex;
              align-items: center;
              justify-content: center;
              height: 100vh;
              margin: 0;
              background: var(--bg-primary);
              color: var(--text-primary);
            }
            .hint { color: var(--text-secondary); font-size: 12px; }
          </style>
        </head>
        <body>
          <div style="text-align:center;">
            <h1>${APP_NAME}</h1>
            <p>Waiting for backend to start...</p>
            <p class="hint">Backend: http://localhost:${backendPort}</p>
          </div>
        </body>
      </html>`;
    mainWindow.loadURL(
      "data:text/html;charset=utf-8," + encodeURIComponent(waitingHtml)
    );
  }
}

// ── Services Setup ─────────────────────────────────────────────────────────

/**
 * Recreate the window after it was closed (macOS keeps the app running).
 * Services are re-pointed at it, not re-created: their IPC handlers stay.
 * @returns {Electron.BrowserWindow}
 */
function reopenWindow() {
  createWindow();
  // Asked for from the tray or the dock: show it even when starting minimized.
  mainWindow.once("ready-to-show", () => mainWindow && mainWindow.show());
  if (agentProcessManager) agentProcessManager.mainWindow = mainWindow;
  if (trayManager) trayManager.mainWindow = mainWindow;
  if (notificationService) notificationService.mainWindow = mainWindow;
  if (browserPane) browserPane.attach(mainWindow);
  loadApp().catch((err) => {
    console.error("[main] Failed to load the reopened window:", err.message);
    dialog.showErrorBox("GAIA", `GAIA couldn't reopen its window: ${err.message}`);
  });
  return mainWindow;
}

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
  trayManager = new TrayManager(mainWindow, {
    backendPort,
    reopenWindow: () => reopenWindow(),
  });
  trayManager.create();
  try {
    trayManager.syncLoginItem();
  } catch (err) {
    console.error(`[main] Could not read the sign-in start setting: ${err.message}`);
  }

  // T5: Notification Service (routes agent notifications to OS + renderer)
  notificationService = new NotificationService(
    mainWindow,
    agentProcessManager,
    trayManager
  );

  // #5226: hold off idle sleep while a chat turn runs
  keepAwake = new KeepAwake();

  // #2007: System metrics for the observability dashboard
  systemMetrics.registerIpcHandlers();

  // A browser beside the chat; its pages can't reach GAIA's own backend.
  browserPane = createBrowserPaneService({
    ipcMain,
    session: session.fromPartition(PANE_PARTITION),
    shell,
    userDataDir: app.getPath("userData"),
    downloadsDir: () => app.getPath("downloads"),
    blockedPorts: () => [backendPort],
  });
  browserPane.attach(mainWindow);

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

/** First install found a Lemonade already running: use it, or install GAIA's own? */
async function chooseLemonadeInDialog(found, parent) {
  // Modal to the progress window and in front: otherwise it can open behind
  // other windows while the install sits at 70% waiting for it.
  const owner = parent && !parent.isDestroyed() ? parent : null;
  if (owner) {
    if (owner.isMinimized()) owner.restore();
    owner.show();
    owner.moveTop();
    owner.focus();
    // Windows can refuse focus to a background app; the taskbar flash is the hint.
    if (!owner.isFocused()) owner.flashFrame(true);
  }
  const box = {
    type: "question",
    title: "Lemonade Server found",
    message: `A Lemonade Server (v${found.version}) is already running at ${found.url}. Use it instead of installing GAIA's own?`,
    detail: "This avoids running two servers and keeping two copies of each model.",
    buttons: ["Use my Lemonade", "Install GAIA's own"],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  };
  const { response } = owner
    ? await dialog.showMessageBox(owner, box)
    : await dialog.showMessageBox(box);
  if (owner && !owner.isDestroyed()) owner.flashFrame(false);
  return response === 0 ? "external" : "gaia";
}

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
        chooseLemonade: (found) => chooseLemonadeInDialog(found, progress.window),
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

// Holds gaia:// links until startup has waited for the backend (the install
// gate verifies the agent through its catalog API), then dispatches each once.
const startupDeepLinks = createStartupDeepLinkQueue({
  dispatch: (rawUrl) => {
    void runDeepLink(parseDeepLink(rawUrl));
  },
  reportNotReady: reportDeepLinksNotReady,
  logger: console,
});

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
 * Entry point for any inbound deep link. Validates it now (a bad link is
 * refused loudly and immediately), then hands it to the startup queue, which
 * holds it until the backend is ready and dispatches it immediately after.
 */
function handleDeepLink(rawUrl) {
  try {
    parseDeepLink(rawUrl);
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

  startupDeepLinks.accept(rawUrl);
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

/**
 * Release queued deep links, plus any gaia:// URL in the cold-start argv. Call
 * once, after startup has waited for the backend.
 *
 * @param {boolean} backendReady Whether the backend API answered its health check.
 */
function processStartupDeepLinks(backendReady) {
  startupDeepLinks.drain({ backendReady, argvUrl: extractDeepLinkFromArgv(process.argv) });
}

/** The backend never came up: tell the user instead of dispatching blind. */
function reportDeepLinksNotReady(urls) {
  const why = backendProcess
    ? `did not answer within ${Math.round(STARTUP_TIMEOUT / 1000)}s`
    : "could not be started";
  const message =
    `GAIA could not open ${urls.map((u) => `"${u}"`).join(", ")} because ` +
    `its backend ${why}, so the agent cannot be verified before install. ` +
    `Check ${_MAIN_LOG_PATH} for the startup error, then click the link ` +
    "again once GAIA is running.";
  console.error(`[main] ${message}`);
  try {
    dialog.showErrorBox("GAIA is not ready yet", message);
  } catch {
    /* best-effort */
  }
}

// macOS delivers deep links via this event; it can fire before whenReady, so
// register it at module load and let handleDeepLink queue as needed.
app.on("open-url", (event, url) => {
  event.preventDefault();
  handleDeepLink(url);
});

if (!devQa) registerProtocolHandler();

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

// Before any window: the taskbar reads the ID when a window first appears.
if (process.platform === "win32") {
  app.setAppUserModelId(appUserModelId(app.isPackaged));
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

/**
 * Seed agents, stop an orphaned backend and install or upgrade the backend.
 * Every step writes to the user's ~/.gaia, so dev QA mode skips them all.
 * @returns {Promise<boolean>} false when the user quit the installer.
 */
async function prepareInstalledBackend() {
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
  return bootstrapBackend();
}

app.whenReady().then(async () => {
  const bootstrapOk = devQa ? true : await prepareInstalledBackend();
  if (!bootstrapOk) {
    console.log("[main] Backend bootstrap aborted — quitting");
    app.quit();
    return;
  }

  // Start the Python backend
  if (shutdownStarted) return;
  backendStarting = startBackend();
  backendProcess = await backendStarting;
  if (shutdownStarted) {
    console.log("[main] Quit during startup — not opening the window");
    return;
  }

  // Create the window (hidden until ready-to-show)
  titleDownloadSaveDialogs();
  createWindow();
  isBootstrapping = false; // progress dialog is gone; window-all-closed may now quit

  // Initialize services (tray, agent manager, notifications)
  initializeServices();

  // Phase F: start the auto-updater (non-blocking). First check runs on
  // a 10s delay inside the module so it never competes with app launch.
  // Any failure here is logged and swallowed — the app continues to run
  // even if auto-update is unavailable.
  try {
    autoUpdater.init(mainWindow, { confirmRestart: confirmUpdateRestart });
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
  let backendReady = false;
  if (backendProcess) {
    console.log("Waiting for backend to start...");
    backendReady = await waitForBackend(STARTUP_TIMEOUT);
    if (backendReady) {
      console.log("Backend API is ready on port", backendPort);
    } else {
      console.warn("Backend did not respond within timeout.");
    }
  }

  // Act on any gaia:// deep link that arrived during bootstrap or via the
  // cold-start command line (issue #1725) — after the backend is listening,
  // because the install gate queries its catalog API.
  processStartupDeepLinks(backendReady);

  // Auto-start enabled agents (T2); in dev QA mode the installed app owns them.
  if (agentProcessManager && !devQa) {
    try {
      await agentProcessManager.startAllEnabled();
    } catch (err) {
      console.error("Failed to auto-start agents:", err.message);
    }
  }

  // macOS and Linux: the shutdown is the OS's, so don't ask.
  powerMonitor.on("shutdown", () => quitGuard.allow());

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      reopenWindow();
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

/** Only while the GAIA service runs: without it, no answer is running. */
function workCanBeLost() {
  return Boolean(trayManager && backendProcess);
}

/** Ask about losing work in front of the window, even when it was hidden in the tray. */
function askAboutRunningWork(action) {
  return confirmQuit({
    action,
    getWork: () => fetchWorkInProgress(backendPort),
    showMessageBox: (opts) => {
      if (!mainWindow || mainWindow.isDestroyed()) return dialog.showMessageBox(opts);
      if (!mainWindow.isVisible()) mainWindow.show();
      return dialog.showMessageBox(mainWindow, opts);
    },
  });
}

/** The update dialog's "Restart now": the installer starts at once, so ask first. */
function confirmUpdateRestart() {
  return workCanBeLost() ? askAboutRunningWork(RESTART_FOR_UPDATE) : Promise.resolve(true);
}

// Every quit asks first while an answer or index job would be lost; an
// update restart (asked in confirmUpdateRestart) and an OS shutdown don't.
const quitGuard = installQuitGuard({
  app,
  updater: electronAutoUpdater,
  shouldAsk: () => !fatalQuitting && workCanBeLost(),
  confirm: () => askAboutRunningWork(QUIT),
  onQuitting: () => {
    isQuitting = true;
    // Mark any subsequent backend exit as intentional so we don't pop the
    // crash dialog during normal shutdown.
    isIntentionalKill = true;
  },
  onError: (err) => {
    console.error("[main] Quit check failed:", err);
    dialog.showErrorBox(
      "GAIA",
      `GAIA couldn't ask whether to stop the running work: ${err.message}\n\n` +
        "Quit again within a minute to quit anyway; any answer still being written will be lost."
    );
  },
});

installQuitCleanup({ app, cleanup });

async function cleanup() {
  shutdownStarted = true;

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

  if (keepAwake) {
    keepAwake.destroy();
    keepAwake = null;
  }

  // The agents and the backend stop side by side: each waits out its own
  // grace period, and in series they could overrun the quit's time limit.
  await Promise.all([stopAgentsForQuit(), stopBackendForQuit()]);

  // Destroy tray icon
  if (trayManager) {
    trayManager.destroy();
    trayManager = null;
  }
}

async function stopAgentsForQuit() {
  if (!agentProcessManager) return;
  console.log("Stopping all managed agents...");
  try {
    await agentProcessManager.stopAll();
  } catch (err) {
    console.error("Error stopping agents:", err.message);
  }
  agentProcessManager = null;
}

async function stopBackendForQuit() {
  // A quit during startup: wait for the spawn so it can be stopped below.
  if (backendStarting) {
    try {
      const started = await backendStarting;
      if (started && !backendProcess && started.exitCode === null && started.signalCode === null) {
        backendProcess = started;
      }
    } catch (err) {
      console.error("[main] Backend start failed during quit:", err.message);
    }
  }

  // Stop the Python backend and everything it started via the port-manager
  // (SIGTERM → wait 3 s → SIGKILL).
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
