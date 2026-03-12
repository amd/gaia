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
 *   - Build/rebuild dynamic context menu (agent list + status indicators)
 *   - Handle "minimize to tray" on window close (configurable)
 *   - Handle "show window" on tray click / double-click
 *   - Animate tray icon between normal and active states
 *   - Expose IPC handlers for renderer to query/update tray config
 *   - Handle tray tooltip updates
 */

const { Tray, Menu, nativeImage, ipcMain, app } = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");

// ── Constants ────────────────────────────────────────────────────────────

const GAIA_DIR = path.join(os.homedir(), ".gaia");
const CONFIG_PATH = path.join(GAIA_DIR, "tray-config.json");

// Status indicators for context menu labels
const STATUS_ICONS = {
  running: "\u25CF", // ● filled circle
  stopped: "\u25CB", // ○ empty circle
  not_installed: "\u25CC", // ◌ dotted circle
  error: "\u25C6", // ◆ diamond
};

// Icon animation interval (ms) when agents are active
const ICON_ANIMATE_INTERVAL = 800;

// ── Default config ───────────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  tray: {
    minimizeToTray: true,
    startMinimized: false,
    startOnLogin: false,
    showNotificationBadge: true,
  },
  agents: {},
};

// ── TrayManager ──────────────────────────────────────────────────────────

class TrayManager {
  /**
   * @param {Electron.BrowserWindow} mainWindow
   * @param {import('./agent-process-manager')} agentProcessManager
   */
  constructor(mainWindow, agentProcessManager) {
    /** @type {Electron.BrowserWindow} */
    this.mainWindow = mainWindow;

    /** @type {import('./agent-process-manager')} */
    this.agentProcessManager = agentProcessManager;

    /** @type {Electron.Tray | null} */
    this.tray = null;

    /** @type {object} */
    this.config = this._loadConfig();

    /** @type {NodeJS.Timeout | null} */
    this._animateTimer = null;

    /** @type {boolean} */
    this._animateState = false;

    /** @type {Electron.NativeImage} */
    // Use the same app icon as the window/installer (icon.ico on Windows, icon.png elsewhere)
    const trayIconFile = process.platform === "win32" ? "icon.ico" : "icon.png";
    this._iconNormal = this._loadIcon(trayIconFile);

    /** @type {Electron.NativeImage} */
    this._iconActive = this._loadIcon(trayIconFile);

    /** @type {number} */
    this._unreadNotificationCount = 0;

    this._registerIpcHandlers();
  }

  // ── Public API ───────────────────────────────────────────────────────

  /** Create the tray icon and wire up events. Call once after app.whenReady(). */
  create() {
    if (this.tray) return;

    this.tray = new Tray(this._iconNormal);
    this.tray.setToolTip("GAIA Agent UI");

    // Single-click: show/focus window
    this.tray.on("click", () => this._showWindow());

    // Double-click (Windows): show/focus window
    this.tray.on("double-click", () => this._showWindow());

    this._rebuildContextMenu();
    console.log("[tray] System tray icon created");
  }

  /** Destroy the tray icon. Call before app.quit(). */
  destroy() {
    this._stopIconAnimation();
    if (this.tray) {
      this.tray.destroy();
      this.tray = null;
    }
    console.log("[tray] System tray icon destroyed");
  }

  /** Update the context menu (e.g. after agent status change). */
  refresh() {
    this._rebuildContextMenu();
  }

  /** Set the unread notification badge count on the tray icon. */
  setNotificationCount(count) {
    this._unreadNotificationCount = count;

    if (!this.tray) return;

    // macOS: set title next to icon
    if (process.platform === "darwin" && count > 0) {
      this.tray.setTitle(String(count));
    } else if (process.platform === "darwin") {
      this.tray.setTitle("");
    }

    // Update tooltip
    const tooltip =
      count > 0
        ? `GAIA Agent UI (${count} notification${count > 1 ? "s" : ""})`
        : "GAIA Agent UI";
    this.tray.setToolTip(tooltip);

    this._rebuildContextMenu();
  }

  /**
   * Start/stop the icon animation based on whether any agents are running.
   * Called by main.cjs whenever agent statuses change.
   */
  updateIconAnimation() {
    if (!this.agentProcessManager) return;

    const statuses = this.agentProcessManager.getAllAgentStatuses();
    const anyRunning = Object.values(statuses).some((s) => s.running);

    if (anyRunning && !this._animateTimer) {
      this._startIconAnimation();
    } else if (!anyRunning && this._animateTimer) {
      this._stopIconAnimation();
    }
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

    const menuItems = [];

    // Show Window
    menuItems.push({
      label: "Show Window",
      click: () => this._showWindow(),
    });

    menuItems.push({ type: "separator" });

    // Agent list with status indicators
    const agentEntries = this._buildAgentMenuItems();
    if (agentEntries.length > 0) {
      menuItems.push(...agentEntries);
      menuItems.push({ type: "separator" });
    }

    // Bulk actions
    menuItems.push({
      label: "Start All Enabled",
      click: () => this._startAllAgents(),
    });
    menuItems.push({
      label: "Stop All",
      click: () => this._stopAllAgents(),
    });

    menuItems.push({ type: "separator" });

    // Notifications
    const notifLabel =
      this._unreadNotificationCount > 0
        ? `Notifications (${this._unreadNotificationCount})`
        : "Notifications";
    menuItems.push({
      label: notifLabel,
      click: () => {
        this._showWindow();
        this._sendToRenderer("tray:navigate", "notifications");
      },
    });

    // Settings
    menuItems.push({
      label: "Settings",
      click: () => {
        this._showWindow();
        this._sendToRenderer("tray:navigate", "settings");
      },
    });

    menuItems.push({ type: "separator" });

    // About
    menuItems.push({
      label: "About GAIA",
      click: () => {
        this._showWindow();
        this._sendToRenderer("tray:navigate", "about");
      },
    });

    // Quit
    menuItems.push({
      label: "Quit",
      click: () => this._quit(),
    });

    const contextMenu = Menu.buildFromTemplate(menuItems);
    this.tray.setContextMenu(contextMenu);
  }

  /** Build context menu entries for each agent. */
  _buildAgentMenuItems() {
    if (!this.agentProcessManager) return [];

    const statuses = this.agentProcessManager.getAllAgentStatuses();
    const manifest = this.agentProcessManager.getManifest();
    const items = [];

    // Always show the Chat Agent (Python backend) first
    items.push({
      label: `${STATUS_ICONS.running} Chat Agent`,
      enabled: false, // informational only; managed separately
    });

    // Show OS agents from manifest
    if (manifest && manifest.agents) {
      for (const agent of manifest.agents) {
        const status = statuses[agent.id] || {};
        const icon = status.running
          ? STATUS_ICONS.running
          : status.installed !== false
            ? STATUS_ICONS.stopped
            : STATUS_ICONS.not_installed;

        const submenu = [];

        if (status.running) {
          submenu.push({
            label: "Stop",
            click: () => this.agentProcessManager.stopAgent(agent.id),
          });
        } else if (status.installed !== false) {
          submenu.push({
            label: "Start",
            click: () => this.agentProcessManager.startAgent(agent.id),
          });
        }

        submenu.push({
          label: "Terminal",
          click: () => {
            this._showWindow();
            this._sendToRenderer("tray:navigate", `terminal:${agent.id}`);
          },
        });

        items.push({
          label: `${icon} ${agent.name}`,
          submenu: submenu.length > 0 ? submenu : undefined,
        });
      }
    }

    return items;
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

  /**
   * Safely send an IPC message to the renderer.
   * @param {string} channel
   * @param {*} data
   */
  _sendToRenderer(channel, data) {
    try {
      if (this.mainWindow && !this.mainWindow.isDestroyed()) {
        this.mainWindow.webContents.send(channel, data);
      }
    } catch (err) {
      console.warn("[tray] Could not send to renderer:", err.message);
    }
  }

  async _quit() {
    console.log("[tray] Quit requested — stopping all agents...");

    // Stop all managed agents gracefully
    if (this.agentProcessManager) {
      try {
        await this.agentProcessManager.stopAll();
      } catch (err) {
        console.error("[tray] Error stopping agents during quit:", err.message);
      }
    }

    // Let Electron know we're actually quitting (not hiding)
    app.quit();
  }

  // ── Private: Agent bulk operations ───────────────────────────────────

  async _startAllAgents() {
    if (!this.agentProcessManager) return;
    try {
      await this.agentProcessManager.startAllEnabled();
      this._rebuildContextMenu();
    } catch (err) {
      console.error("[tray] Error starting all agents:", err.message);
    }
  }

  async _stopAllAgents() {
    if (!this.agentProcessManager) return;
    try {
      await this.agentProcessManager.stopAll();
      this._rebuildContextMenu();
    } catch (err) {
      console.error("[tray] Error stopping all agents:", err.message);
    }
  }

  // ── Private: Icon animation ──────────────────────────────────────────

  _startIconAnimation() {
    if (this._animateTimer) return;
    this._animateTimer = setInterval(() => {
      if (!this.tray) return;
      this._animateState = !this._animateState;
      this.tray.setImage(
        this._animateState ? this._iconActive : this._iconNormal
      );
    }, ICON_ANIMATE_INTERVAL);
  }

  _stopIconAnimation() {
    if (this._animateTimer) {
      clearInterval(this._animateTimer);
      this._animateTimer = null;
    }
    this._animateState = false;
    if (this.tray) {
      this.tray.setImage(this._iconNormal);
    }
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
        // Merge with defaults to ensure all keys exist
        return {
          ...DEFAULT_CONFIG,
          ...loaded,
          tray: { ...DEFAULT_CONFIG.tray, ...(loaded.tray || {}) },
          agents: { ...DEFAULT_CONFIG.agents, ...(loaded.agents || {}) },
        };
      }
    } catch (err) {
      console.warn("[tray] Could not load tray config:", err.message);
    }
    return { ...DEFAULT_CONFIG };
  }

  _saveConfig() {
    try {
      // Ensure directory exists
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
      // Deep-merge incoming config
      if (cfg.tray) {
        this.config.tray = { ...this.config.tray, ...cfg.tray };
      }
      if (cfg.agents) {
        this.config.agents = { ...this.config.agents, ...cfg.agents };
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
      console.log(
        `[tray] Login item ${enabled ? "enabled" : "disabled"}`
      );
    } catch (err) {
      console.warn("[tray] Could not set login item:", err.message);
    }
  }
}

module.exports = TrayManager;
