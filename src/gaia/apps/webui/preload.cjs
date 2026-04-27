// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA Agent UI — Preload script (contextBridge)
 *
 * Exposes IPC channels to the renderer process via `window.gaiaAPI`.
 * Required because main.cjs uses `contextIsolation: true`.
 *
 * Channels:
 *   agent:*          — Agent process management (T2)
 *   tray:*           — Tray icon/config (T1)
 *   notification:*   — Desktop notifications & permission prompts (T5)
 *   install:*        — First-run backend install progress (Phase A)
 *   gaia:update:*    — Auto-update status & manual check (Phase F)
 */

const { contextBridge, ipcRenderer } = require("electron");

// Helper: subscribe to an IPC event and return an unsubscribe function
function onEvent(channel, callback) {
  const handler = (_event, data) => callback(data);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld("gaiaAPI", {
  // ── Agent process management (T2) ─────────────────────────────────────
  agent: {
    start: (id) => ipcRenderer.invoke("agent:start", id),
    stop: (id) => ipcRenderer.invoke("agent:stop", id),
    restart: (id) => ipcRenderer.invoke("agent:restart", id),
    status: (id) => ipcRenderer.invoke("agent:status", id),
    statusAll: () => ipcRenderer.invoke("agent:status-all"),
    sendRpc: (id, method, params) =>
      ipcRenderer.invoke("agent:send-rpc", id, method, params),
    getManifest: () => ipcRenderer.invoke("agent:get-manifest"),
    install: (id) => ipcRenderer.invoke("agent:install", id),
    uninstall: (id) => ipcRenderer.invoke("agent:uninstall", id),

    // Event streams (return unsubscribe functions)
    onStdout: (cb) => onEvent("agent:stdout", cb),
    onStderr: (cb) => onEvent("agent:stderr", cb),
    onStatusChange: (cb) => onEvent("agent:status-change", cb),
    onCrashed: (cb) => onEvent("agent:crashed", cb),
  },

  // ── Tray configuration (T1) ───────────────────────────────────────────
  tray: {
    getConfig: () => ipcRenderer.invoke("tray:get-config"),
    setConfig: (cfg) => ipcRenderer.invoke("tray:set-config", cfg),
    onNavigate: (cb) => onEvent("tray:navigate", cb),
  },

  // ── Notifications & permission prompts (T5) ───────────────────────────
  notification: {
    onPermissionRequest: (cb) =>
      onEvent("notification:permission-request", cb),
    respondPermission: (id, action, remember) =>
      ipcRenderer.invoke("notification:respond", id, action, remember),
    onNotification: (cb) => onEvent("notification:new", cb),
  },
});

// ── Install progress (Phase A) ──────────────────────────────────────────
// Exposed as a separate global so the progress window can use it without
// pulling in the full gaiaAPI surface (and so it keeps working if an
// install dialog runs before the main window is ready).
contextBridge.exposeInMainWorld("gaiaInstall", {
  // Subscribe to progress updates. Returns an unsubscribe function.
  onProgress: (cb) => onEvent("install:progress", cb),

  // Query the current install state (state machine + log/state paths).
  status: () => ipcRenderer.invoke("install:status"),

  // Copy the log file path to the clipboard.
  copyLogPath: () => ipcRenderer.invoke("install:copy-log-path"),

  // Open the log file in the OS's default viewer.
  openLogFile: () => ipcRenderer.invoke("install:open-log-file"),
});

// ── Auto-update (Phase F) ───────────────────────────────────────────────
// Exposed as a separate global so the renderer can show a small "update
// available" chip without pulling in the full gaiaAPI surface. Mirrors
// the naming convention (gaiaInstall, gaiaUpdater, gaiaAPI).
contextBridge.exposeInMainWorld("gaiaUpdater", {
  /** Get the current update state (status, version, progress, error). */
  getStatus: () => ipcRenderer.invoke("gaia:update:get-status"),

  /** Manually trigger a check. Resolves with the post-check state. */
  check: () => ipcRenderer.invoke("gaia:update:check"),

  /**
   * Subscribe to status changes. The callback is invoked with the full
   * state object on every state transition. Returns an unsubscribe fn.
   */
  onStatusChange: (callback) => {
    const handler = (_event, state) => callback(state);
    ipcRenderer.on("gaia:update:status", handler);
    return () => ipcRenderer.removeListener("gaia:update:status", handler);
  },
});
