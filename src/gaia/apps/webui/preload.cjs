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
