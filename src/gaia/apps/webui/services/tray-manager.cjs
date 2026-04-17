// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA Agent UI — Tray Manager (T1)
 *
 * Manages the Electron system tray icon, context menu, and minimize-to-tray
 * behaviour. Co-located alongside main.cjs per the T0 co-location decision.
 *
 * Responsibilities:
 *   - Create Tray instance with GAIA icon on app startup
 *   - Build context menu (Show Window, Open in Browser, Quit)
 *   - Handle "minimize to tray" on window close (configurable)
 *   - Handle "show window" on tray click / double-click
 *   - Expose IPC handlers for renderer to query/update tray config
 */

const { Tray, Menu, nativeImage, ipcMain, app, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");

// ── Constants ────────────────────────────────────────────────────────────

const GAIA_DIR = path.join(os.homedir(), ".gaia");
const CONFIG_PATH = path.join(GAIA_DIR, "tray-config.json");

// ── Default config ───────────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  tray: {
    minimizeToTray: true,
    startMinimized: false,
    startOnLogin: false,
  },
};

// ── TrayManager ──────────────────────────────────────────────────────────

class TrayManager {
  /**
   * @param {Electron.BrowserWindow} mainWindow
   * @param {object} [options]
   * @param {number} [options.backendPort=4200] - Backend port for "Open in Browser"
   */
  constructor(mainWindow, options = {}) {
    /** @type {Electron.BrowserWindow} */
    this.mainWindow = mainWindow;

    /** @type {number} */
    this._backendPort = options.backendPort || 4200;

    /** @type {Electron.Tray | null} */
    this.tray = null;

    /** @type {object} */
    this.config = this._loadConfig();

    /** @type {Electron.NativeImage} */
    const trayIconFile = process.platform === "win32" ? "icon.ico" : "icon.png";
    this._icon = this._loadIcon(trayIconFile);

    this._registerIpcHandlers();
  }

  // ── Public API ───────────────────────────────────────────────────────

  /** Create the tray icon and wire up events. Call once after app.whenReady(). */
  create() {
    if (this.tray) return;

    this.tray = new Tray(this._icon);
    this.tray.setToolTip("GAIA");

    // Single-click: show/focus window
    this.tray.on("click", () => this._showWindow());

    // Double-click (Windows): show/focus window
    this.tray.on("double-click", () => this._showWindow());

    this._rebuildContextMenu();
    console.log("[tray] System tray icon created");
  }

  /** Destroy the tray icon. Call before app.quit(). */
  destroy() {
    if (this.tray) {
      this.tray.destroy();
      this.tray = null;
    }
    console.log("[tray] System tray icon destroyed");
  }

  /** Update the context menu. */
  refresh() {
    this._rebuildContextMenu();
  }

  /** @returns {boolean} Whether minimize-to-tray is enabled. */
  get minimizeToTray() {
    return this.config.tray.minimizeToTray;
  }

  /** @returns {boolean} Whether app should start minimized. */
  get startMinimized() {
    return this.config.tray.startMinimized;
  }

  /** @returns {boolean} Whether app should start on login. */
  get startOnLogin() {
    return this.config.tray.startOnLogin;
  }

  // ── Private: Context Menu ────────────────────────────────────────────

  _rebuildContextMenu() {
    if (!this.tray) return;

    const contextMenu = Menu.buildFromTemplate([
      {
        label: "Show Window",
        click: () => this._showWindow(),
      },
      {
        label: "Open in Browser",
        click: () => shell.openExternal(`http://localhost:${this._backendPort}`),
      },
      { type: "separator" },
      {
        label: "Quit",
        click: () => this._quit(),
      },
    ]);
    this.tray.setContextMenu(contextMenu);
  }

  // ── Private: Window management ───────────────────────────────────────

  _showWindow() {
    if (!this.mainWindow || this.mainWindow.isDestroyed()) return;

    if (this.mainWindow.isMinimized()) {
      this.mainWindow.restore();
    }
    this.mainWindow.show();
    this.mainWindow.focus();
  }

  async _quit() {
    console.log("[tray] Quit requested");
    app.quit();
  }

  // ── Private: Icon loading ────────────────────────────────────────────

  _loadIcon(filename) {
    // __dirname is services/, assets/ is one level up alongside main.cjs
    const iconPath = path.join(__dirname, "..", "assets", filename);
    try {
      if (fs.existsSync(iconPath)) {
        return nativeImage.createFromPath(iconPath);
      }
    } catch (err) {
      console.warn(`[tray] Could not load icon ${filename}:`, err.message);
    }
    // Return empty image as fallback (Electron will show a default)
    return nativeImage.createEmpty();
  }

  // ── Private: Config persistence ──────────────────────────────────────

  _loadConfig() {
    try {
      if (fs.existsSync(CONFIG_PATH)) {
        const raw = fs.readFileSync(CONFIG_PATH, "utf8");
        const loaded = JSON.parse(raw);
        return {
          ...DEFAULT_CONFIG,
          ...loaded,
          tray: { ...DEFAULT_CONFIG.tray, ...(loaded.tray || {}) },
        };
      }
    } catch (err) {
      console.warn("[tray] Could not load tray config:", err.message);
    }
    return { ...DEFAULT_CONFIG };
  }

  _saveConfig() {
    try {
      if (!fs.existsSync(GAIA_DIR)) {
        fs.mkdirSync(GAIA_DIR, { recursive: true });
      }
      fs.writeFileSync(CONFIG_PATH, JSON.stringify(this.config, null, 2), "utf8");
      console.log("[tray] Config saved to", CONFIG_PATH);
    } catch (err) {
      console.error("[tray] Could not save tray config:", err.message);
    }
  }

  // ── Private: IPC handlers ────────────────────────────────────────────

  _registerIpcHandlers() {
    ipcMain.handle("tray:get-config", () => {
      return this.config;
    });

    ipcMain.handle("tray:set-config", (_event, cfg) => {
      if (cfg.tray) {
        this.config.tray = { ...this.config.tray, ...cfg.tray };
      }

      this._saveConfig();

      // Apply login-item setting if changed
      if (cfg.tray && "startOnLogin" in cfg.tray) {
        this._applyLoginItemSetting(cfg.tray.startOnLogin);
      }

      return this.config;
    });
  }

  /** Register/unregister the app from OS login startup. */
  _applyLoginItemSetting(enabled) {
    try {
      app.setLoginItemSettings({
        openAtLogin: enabled,
        path: app.getPath("exe"),
        args: enabled ? ["--minimized"] : [],
      });
      console.log(`[tray] Login item ${enabled ? "enabled" : "disabled"}`);
    } catch (err) {
      console.warn("[tray] Could not set login item:", err.message);
    }
  }
}

module.exports = TrayManager;
