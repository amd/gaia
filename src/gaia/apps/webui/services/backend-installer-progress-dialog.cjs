// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * backend-installer-progress-dialog.cjs — Electron progress UI for the
 * first-run backend install.
 *
 * Owns the Electron-specific presentation layer for the install flow.
 * The core install logic lives in `backend-installer.cjs`, which is pure
 * Node.js; this module renders a borderless BrowserWindow with embedded
 * HTML/JS and wires up IPC events so the install module's progress
 * callbacks stream through to the renderer.
 *
 * Responsibilities:
 *   - Show a borderless progress window (stage + percent + message)
 *   - Expose an `onProgress(stage, percent, message)` callback for the
 *     install module
 *   - Show a failure dialog with Retry / Manual / Quit buttons
 *   - Surface the log file path (copy / open)
 *
 * Exposed API:
 *   - createProgressWindow() → { window, onProgress, close }
 *   - showFailureDialog(parentWindow, errorInfo) → Promise<'retry'|'manual'|'quit'>
 */

"use strict";

const path = require("path");
const { BrowserWindow, dialog, ipcMain, shell, clipboard } = require("electron");

const installer = require("./backend-installer.cjs");
const { appIconPath } = require("./app-icon.cjs");

// IPC channels. Keep these in sync with preload.cjs.
const IPC_PROGRESS_EVENT = "install:progress";
const IPC_STATUS_REQUEST = "install:status";
const IPC_COPY_LOG_PATH = "install:copy-log-path";
const IPC_OPEN_LOG_FILE = "install:open-log-file";

// Track whether one-time IPC handlers have been registered so we don't
// attach them twice if createProgressWindow() runs multiple times.
let ipcHandlersRegistered = false;

function registerIpcHandlers() {
  if (ipcHandlersRegistered) return;
  ipcHandlersRegistered = true;

  ipcMain.handle(IPC_STATUS_REQUEST, () => {
    return {
      state: installer.getState(),
      logPath: installer.getLogPath(),
      statePath: installer.getStatePath(),
    };
  });

  ipcMain.handle(IPC_COPY_LOG_PATH, () => {
    try {
      clipboard.writeText(installer.getLogPath());
      return { ok: true };
    } catch (err) {
      return { ok: false, message: err.message };
    }
  });

  ipcMain.handle(IPC_OPEN_LOG_FILE, async () => {
    try {
      const logPath = installer.getLogPath();
      const result = await shell.openPath(logPath);
      return { ok: !result, message: result || null };
    } catch (err) {
      return { ok: false, message: err.message };
    }
  });
}

// ── Embedded HTML ───────────────────────────────────────────────────────────

/**
 * HTML payload for the progress window. Rendered via `loadURL('data:...')`.
 * The script uses `window.gaiaInstall` exposed by preload.cjs.
 */
function buildProgressHtml({ logPath }) {
  const escapedLog = String(logPath || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Installing GAIA</title>
<style>
  /* Standalone window: it loads from a data: URL and never sees
   * src/styles/index.css, so the dark theme's role values are mirrored
   * here. Keep them in step with that file; nothing below names a hex. */
  :root {
    --bg-primary: #1A1917;
    --bg-tertiary: #2C2B27;
    --border: #4A4740;
    --text-primary: #EFECE6;
    --text-secondary: #B3ADA5;
    --text-muted: #958F87;
    --accent-solid: #D4B47E;
  }
  html, body {
    margin: 0;
    padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    user-select: none;
    -webkit-user-select: none;
    overflow: hidden;
  }
  body {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    padding: 32px;
    box-sizing: border-box;
    text-align: center;
  }
  h1 {
    font-size: 18px;
    font-weight: 600;
    margin: 0 0 8px 0;
    color: var(--text-primary);
  }
  .subtitle {
    font-size: 13px;
    color: var(--text-secondary);
    margin: 0 0 24px 0;
  }
  .stage {
    font-size: 12px;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
    min-height: 15px;
  }
  /* The rule carries the track's edge: no fill clears the 1.5:1 chrome
   * floor against the canvas, but --border does at 1.56:1. */
  .bar {
    width: 360px;
    max-width: 100%;
    height: 10px;
    box-sizing: border-box;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 9999px;
    overflow: hidden;
    margin-bottom: 12px;
  }
  .fill {
    height: 100%;
    background: var(--accent-solid);
    border-radius: 9999px;
    transition: width 300ms ease-out;
    width: 0%;
  }
  .fill.indeterminate {
    width: 30% !important;
    animation: slide 1.4s ease-in-out infinite;
  }
  @keyframes slide {
    0%   { margin-left: 0%;   }
    50%  { margin-left: 70%;  }
    100% { margin-left: 0%;   }
  }
  @media (prefers-reduced-motion: reduce) {
    .fill { transition: none; }
    .fill.indeterminate { animation: none; margin-left: 35%; }
  }
  .percent {
    font-size: 12px;
    color: var(--text-secondary);
    margin-bottom: 16px;
    min-height: 14px;
  }
  .message {
    font-size: 13px;
    color: var(--text-primary);
    max-width: 420px;
    min-height: 36px;
    line-height: 1.4;
  }
  .footer {
    position: absolute;
    bottom: 10px;
    left: 0;
    right: 0;
    text-align: center;
    font-size: 10px;
    color: var(--text-muted);
  }
  code {
    font-family: "SF Mono", Monaco, Consolas, monospace;
    font-size: 10px;
    color: var(--text-muted);
  }
</style>
</head>
<body>
  <h1>Setting up GAIA</h1>
  <p class="subtitle">First-launch backend install — this can take a few minutes</p>
  <div class="stage" id="stage">Starting…</div>
  <div class="bar"><div class="fill indeterminate" id="fill"></div></div>
  <div class="percent" id="percent"></div>
  <div class="message" id="message">Running pre-flight checks…</div>
  <div class="footer">Log: <code>${escapedLog}</code></div>
<script>
  (function() {
    const stageEl = document.getElementById('stage');
    const fillEl = document.getElementById('fill');
    const percentEl = document.getElementById('percent');
    const messageEl = document.getElementById('message');

    function onProgress(payload) {
      if (!payload) return;
      const stage = payload.stage || '';
      const percent = typeof payload.percent === 'number' ? payload.percent : null;
      const message = payload.message || '';

      if (stage) stageEl.textContent = stage;
      if (message) messageEl.textContent = message;

      if (percent != null && percent >= 0) {
        fillEl.classList.remove('indeterminate');
        fillEl.style.width = Math.max(0, Math.min(100, percent)) + '%';
        percentEl.textContent = percent + '%';
      }
    }

    if (window.gaiaInstall && typeof window.gaiaInstall.onProgress === 'function') {
      window.gaiaInstall.onProgress(onProgress);
    } else {
      // Fallback: listen directly via ipcRenderer shim if preload didn't load.
      console.warn('gaiaInstall API not available in progress window');
    }
  })();
</script>
</body>
</html>`;
}

// ── Progress window factory ─────────────────────────────────────────────────

/**
 * Create a borderless progress window and return an `onProgress` callback
 * suitable for passing to `backend-installer.ensureBackend({ onProgress })`.
 *
 * Returns:
 *   {
 *     window: BrowserWindow,
 *     onProgress: (stage, percent, message) => void,
 *     close: () => void,
 *   }
 */
function createProgressWindow() {
  registerIpcHandlers();

  const window = new BrowserWindow({
    width: 480,
    height: 280,
    resizable: false,
    minimizable: true,
    maximizable: false,
    fullscreenable: false,
    frame: false,
    transparent: false,
    show: false,
    backgroundColor: "#1A1917", // --bg-primary, dark theme (see the :root block above)
    title: "Installing GAIA",
    icon: appIconPath(),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, "..", "preload.cjs"),
    },
  });

  window.setMenuBarVisibility(false);

  const html = buildProgressHtml({ logPath: installer.getLogPath() });
  window.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));

  window.once("ready-to-show", () => {
    if (!window.isDestroyed()) window.show();
  });

  // Buffer progress events until the renderer has finished loading — events
  // sent before `did-finish-load` are lost because the renderer's IPC
  // listener hasn't attached yet. Once loaded, the most recent state is
  // replayed (and flushed for subsequent delivery).
  let rendererReady = false;
  let lastProgress = null;

  window.webContents.once("did-finish-load", () => {
    rendererReady = true;
    if (lastProgress && !window.isDestroyed()) {
      try {
        window.webContents.send(IPC_PROGRESS_EVENT, lastProgress);
      } catch {
        // non-fatal
      }
    }
  });

  const onProgress = (stage, percent, message) => {
    lastProgress = { stage, percent, message };
    if (window.isDestroyed()) return;
    if (!rendererReady) return; // will be replayed on did-finish-load
    try {
      window.webContents.send(IPC_PROGRESS_EVENT, lastProgress);
    } catch (err) {
      // Non-fatal
      // eslint-disable-next-line no-console
      console.error("[install-progress] send failed:", err.message);
    }
  };

  const close = () => {
    if (!window.isDestroyed()) {
      try {
        window.destroy();
      } catch {
        // ignore
      }
    }
  };

  window.on("closed", () => {
    if (lastProgress) {
      // eslint-disable-next-line no-console
      console.log(
        `[install-progress] window closed at stage=${lastProgress.stage} percent=${lastProgress.percent}`
      );
    }
  });

  return { window, onProgress, close };
}

// ── Failure dialog ──────────────────────────────────────────────────────────

/**
 * Show a modal failure dialog with Retry / Manual / Quit options.
 * Returns 'retry', 'manual', or 'quit'.
 */
async function showFailureDialog(parentWindow, errorInfo = {}) {
  const {
    message = "GAIA backend install failed.",
    stage = null,
    suggestion = null,
  } = errorInfo;

  const logPath = installer.getLogPath();
  const statePath = installer.getStatePath();

  const detail = [
    stage ? `Stage: ${stage}` : null,
    suggestion ? `\n${suggestion}` : null,
    `\nLog file: ${logPath}`,
    `State file: ${statePath}`,
  ]
    .filter(Boolean)
    .join("\n");

  // Copy/Open log return to the same dialog. "Install uv" is offered only
  // when uv is what failed; for any other stage it would just repeat Retry.
  const uvFailed = stage === "ensure-uv";
  const actions = [
    ...(uvFailed ? [["install-uv", "Install uv (auto)"]] : []),
    ["retry", "Retry"],
    ["manual", "Manual install instructions"],
    ["copy-log", "Copy log path"],
    ["open-log", "Open log file"],
    ["quit", "Quit"],
  ];
  const buttons = actions.map(([, label]) => label);

  const show = async () => {
    const result = await dialog.showMessageBox(parentWindow || null, {
      type: "error",
      title: "GAIA install failed",
      message,
      detail,
      buttons,
      defaultId: 0,
      cancelId: buttons.length - 1,
      noLink: true,
    });

    const action = (actions[result.response] || ["quit"])[0];
    switch (action) {
      case "install-uv": {
        // Run the full packaged install (ensureBackend), with progress, so one
        // click retries everything, not only uv; re-show with the new error.
        const { onProgress, close } = createProgressWindow();
        try {
          await installer.ensureBackend({ onProgress, isPackaged: true });
          try { close(); } catch {}
          return "retry";
        } catch (err) {
          try { close(); } catch {}
          const nextInfo = Object.assign({}, errorInfo, {
            message: err.message || String(err),
            stage: err.stage || "ensure-backend",
            suggestion: err.suggestion || errorInfo.suggestion,
          });
          return showFailureDialog(parentWindow, nextInfo);
        }
      }
      case "retry":
        return "retry";
      case "manual": {
        try {
          await shell.openExternal("https://amd-gaia.ai/docs/quickstart#cli-install");
        } catch {
          // ignore
        }
        return "manual";
      }
      case "copy-log": {
        try {
          clipboard.writeText(logPath);
        } catch {
          // ignore
        }
        return showFailureDialog(parentWindow, errorInfo);
      }
      case "open-log": {
        try {
          await shell.openPath(logPath);
        } catch {
          // ignore
        }
        return showFailureDialog(parentWindow, errorInfo);
      }
      default:
        return "quit";
    }
  };

  return show();
}

// ── Pre-check failure dialogs ───────────────────────────────────────────────

/**
 * Show a simple retryable error dialog. Used for disk-space / offline errors
 * before install begins.
 * Returns 'retry' or 'quit'.
 */
async function showPreCheckFailureDialog(parentWindow, { title, message, detail }) {
  const result = await dialog.showMessageBox(parentWindow || null, {
    type: "warning",
    title: title || "GAIA cannot start",
    message: message || "A pre-flight check failed.",
    detail: detail || "",
    buttons: ["Retry", "Quit"],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  });
  return result.response === 0 ? "retry" : "quit";
}

module.exports = {
  createProgressWindow,
  showFailureDialog,
  showPreCheckFailureDialog,
  // IPC channel names for preload.cjs
  IPC_PROGRESS_EVENT,
  IPC_STATUS_REQUEST,
  IPC_COPY_LOG_PATH,
  IPC_OPEN_LOG_FILE,
};
