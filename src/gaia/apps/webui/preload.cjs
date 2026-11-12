// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA Agent UI — Preload script (contextBridge)
 *
 * Exposes IPC channels to the renderer process via `window.gaiaAPI`.
 * Required because main.cjs uses `contextIsolation: true`.
 *
 * Channels:
 *   tray:*           — Tray icon/config (T1)
 *   notification:*   — Desktop notifications & permission prompts (T5)
 *   install:*        — Backend install status, retry and log (Phase A)
 *   gaia:update:*    — Auto-update status & manual check (Phase F)
 *   power:*          — Keep the PC awake while a chat turn runs (#5226)
 *   dialog:*         — The OS folder picker
 *   backend:*        — Backend restarts after a crash (services/backend-supervisor.cjs)
 */

const { contextBridge, ipcRenderer } = require("electron");

// Helper: subscribe to an IPC event and return an unsubscribe function
function onEvent(channel, callback) {
  const handler = (_event, data) => callback(data);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld("gaiaAPI", {
  // ── Tray configuration (T1) ───────────────────────────────────────────
  tray: {
    getConfig: () => ipcRenderer.invoke("tray:get-config"),
    setConfig: (cfg) => ipcRenderer.invoke("tray:set-config", cfg),
    // What GAIA is doing, for the tray icon, tooltip and menu.
    setActivity: (activity) => ipcRenderer.invoke("tray:set-activity", activity),
    // Tray menu actions: open-chat, new-chat, open-settings, refresh-status.
    onCommand: (cb) => onEvent("tray:command", cb),
    onNavigate: (cb) => onEvent("tray:navigate", cb),
  },

  // ── Notifications & permission prompts (T5) ───────────────────────────
  notification: {
    onPermissionRequest: (cb) =>
      onEvent("notification:permission-request", cb),
    respondPermission: (id, action, remember) =>
      ipcRenderer.invoke("notification:respond", id, action, remember),
    onNotification: (cb) => onEvent("notification:new", cb),
    // Chat alerts (#5226)
    show: (payload) => ipcRenderer.invoke("notification:show", payload),
    flash: () => ipcRenderer.invoke("notification:flash"),
    setAttentionCount: (count) =>
      ipcRenderer.invoke("notification:attention", count),
  },

  // ── Keep the PC awake while GAIA works (#5226) ────────────────────────
  power: {
    setKeepAwake: (on) => ipcRenderer.invoke("power:keep-awake", on),
  },

  system: {
    // Opens the OS microphone privacy pane; resolves false where there is none.
    openMicSettings: () => ipcRenderer.invoke("system:open-mic-settings"),
  },

  // ── OS folder picker; resolves the chosen path, or null on cancel ─────
  dialog: {
    pickFolder: (defaultPath) => ipcRenderer.invoke("dialog:pick-folder", defaultPath),
  },

  // ── GAIA's backend process: restarts after a crash, and its new port ──
  backend: {
    getState: () => ipcRenderer.invoke("backend:get-state"),
    restart: () => ipcRenderer.invoke("backend:restart"),
    onState: (cb) => onEvent("backend:state", cb),
  },
});

// ── Window frame ─────────────────────────────────────────────────────────
// Says whether the app draws its own top row and where the native controls sit in it.
contextBridge.exposeInMainWorld("gaiaWindow", {
  /**
   * "mac": traffic lights at the top left; "overlay": controls at the top right;
   * "native": the system frame. Must match titleBarKind in services/window-frame.cjs
   * (a sandboxed preload can't require it).
   */
  titleBar: { darwin: "mac", win32: "overlay" }[process.platform] || "native",
  /** Match the native window controls to the app: { color, symbolColor } as #rrggbb. */
  setColors: (colors) => ipcRenderer.invoke("window:set-titlebar-colors", colors),
});

// ── Backend install (Phase A) ───────────────────────────────────────────
// EngineInstallScreen runs with no backend, so this stands apart from gaiaAPI.
contextBridge.exposeInMainWorld("gaiaInstall", {
  // Subscribe to status updates. Returns an unsubscribe function.
  onProgress: (cb) => onEvent("install:progress", cb),

  // The current status: { phase, stage, percent, message, error, logPath }.
  status: () => ipcRenderer.invoke("install:status"),

  // After a failure, install again. Resolves false when none is waiting.
  retry: () => ipcRenderer.invoke("install:retry"),

  // Goes on past a failed agent update; the next launch tries it again.
  continueWithoutAgentUpdate: () => ipcRenderer.invoke("install:continue"),

  // After a failed update, go back to the version before it; the app restarts into it.
  revert: () => ipcRenderer.invoke("install:revert"),

  // After a 'locked' failure held only by GAIA processes: ends those and installs again.
  closeLockingPrograms: () => ipcRenderer.invoke("install:close-lockers"),

  // Open the log file in the OS's default viewer.
  openLogFile: () => ipcRenderer.invoke("install:open-log-file"),
});

// ── Auto-update (Phase F) ───────────────────────────────────────────────
// Exposed as a separate global so the renderer can show a small "update
// available" chip without pulling in the full gaiaAPI surface. Mirrors
// the naming convention (gaiaInstall, gaiaUpdater, gaiaAPI).
contextBridge.exposeInMainWorld("gaiaUpdater", {
  /** Get the current update state (status, version, progress, error, currentVersion, pinnedVersion). */
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

  /**
   * Fetch available GitHub releases for this platform. Resolves with an
   * array of ReleaseInfo objects (newest first) or { error: string } when
   * the network call fails.
   */
  listReleases: () => ipcRenderer.invoke("gaia:update:list-releases"),

  /**
   * Download and install a specific tagged release, then prompt restart.
   * Persists a version pin so auto-update stays paused (AC2).
   *
   * @param {string} tag  e.g. "v0.20.0"
   */
  installVersion: (tag) => ipcRenderer.invoke("gaia:update:install-version", tag),

  /** Go back to the version before the last update, and pin it. */
  revertUpdate: () => ipcRenderer.invoke("gaia:update:revert"),

  /**
   * Resume normal auto-updates — clears the version pin set by installVersion.
   * Resolves with the updated state.
   */
  resumeUpdates: () => ipcRenderer.invoke("gaia:update:resume"),

  /** Turn auto-update on or off (persisted; off by default). Resolves with the state. */
  setAutoUpdate: (enabled) => ipcRenderer.invoke("gaia:update:set-auto-update", enabled),

  /** Offer release candidates as updates (independent of auto-update). Resolves with the state. */
  setIncludePrereleases: (enabled) =>
    ipcRenderer.invoke("gaia:update:set-include-prereleases", enabled),

  /** "Update now": download the offered update; the restart prompt follows. */
  downloadUpdate: () => ipcRenderer.invoke("gaia:update:download"),

  /** Restart into a downloaded update. */
  restart: () => ipcRenderer.invoke("gaia:update:restart"),
});
