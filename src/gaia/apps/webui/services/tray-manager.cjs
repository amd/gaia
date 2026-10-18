// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA Agent UI — Tray Manager (T1)
 *
 * The system tray icon: what GAIA is doing right now, what needs the user,
 * and the menu to act on it. Co-located alongside main.cjs per the T0
 * co-location decision.
 *
 *   - The icon and tooltip follow the state the window reports over
 *     `tray:set-activity` (idle, working, waiting for the model, needs you,
 *     error); see tray-state.cjs and tray-icons.cjs.
 *   - The menu: status line, Needs you, New chat, recent chats, Free memory,
 *     Settings, Quit. Menu actions reach the window over `tray:command`.
 *   - Windows gets a taskbar overlay for needs-you and unread.
 *   - Tray settings (close to tray, start at login, start minimized) are read
 *     and saved over `tray:get-config` / `tray:set-config`.
 */

const { Tray, Menu, nativeImage, ipcMain, app, dialog } = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { drawStateIcon, drawOverlayIcon, STATES } = require("./tray-icons.cjs");
const { validateActivity, deriveTrayView, buildMenuTemplate } = require("./tray-state.cjs");
const { backendJson, fetchWorkInProgress } = require("./quit-guard.cjs");

/** Why the model can't be freed right now, from the backend's work list; null when it can. */
function describeBusy(work) {
  const answers = work.answers.length;
  const docs = work.indexing.length;
  if (answers === 0 && docs === 0) return null;
  const parts = [];
  if (answers > 0) parts.push(answers === 1 ? "An answer is being written." : `${answers} answers are being written.`);
  if (docs > 0) parts.push(docs === 1 ? "A document is being indexed." : `${docs} documents are being indexed.`);
  return parts.join(" ");
}

// ── Constants ────────────────────────────────────────────────────────────

const GAIA_DIR = path.join(os.homedir(), ".gaia");
const CONFIG_PATH = path.join(GAIA_DIR, "tray-config.json");
const LINUX_AUTOSTART_FILE = "gaia-agent-ui.desktop";

// ── Default config ───────────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  tray: {
    minimizeToTray: true,
    startMinimized: false,
    startOnLogin: false,
  },
  agents: {},
  notices: {
    firstClose: false,
  },
};

/**
 * Tray icon bitmaps per platform: the size the tray draws at (DIP) and each
 * scale's source asset. Windows trays are 16 DIP; Linux StatusNotifier hosts
 * scale a 32px icon down, which stays sharp where upscaling blurs; macOS
 * menu-bar extras are template images the OS tints for light and dark.
 */
const ICON_SPECS = {
  win32: {
    template: false,
    reps: [
      { scale: 1, px: 16, file: "tray-icon.png" },
      { scale: 1.25, px: 20, file: "tray-icon.png" },
      { scale: 1.5, px: 24, file: "tray-icon.png" },
      { scale: 2, px: 32, file: "tray-icon.png" },
    ],
  },
  darwin: {
    template: true,
    reps: [
      { scale: 1, px: 16, file: "tray-iconTemplate.png" },
      { scale: 2, px: 32, file: "tray-iconTemplate@2x.png" },
    ],
  },
  linux: {
    template: false,
    reps: [
      { scale: 1, px: 32, file: "tray-icon.png" },
      { scale: 2, px: 64, file: "tray-icon@2x.png" },
    ],
  },
};

const OVERLAY_REPS = [
  { scale: 1, px: 16 },
  { scale: 2, px: 32 },
];

const AGENT_LOG_LEVELS = ["debug", "info", "warn", "error"];

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Validate an `agents` section (id → AgentConfig), dropping unknown keys. */
function validateAgents(agents, where) {
  if (!isPlainObject(agents)) {
    throw new TypeError(`${where}: agents must be an object`);
  }
  const out = {};
  for (const [id, cfg] of Object.entries(agents)) {
    if (id.trim() === "") {
      throw new TypeError(`${where}: agent id must be a non-empty string`);
    }
    if (!isPlainObject(cfg)) {
      throw new TypeError(`${where}: agents.${id} must be an object`);
    }
    for (const key of ["autoStart", "restartOnCrash"]) {
      if (typeof cfg[key] !== "boolean") {
        throw new TypeError(`${where}: agents.${id}.${key} must be a boolean`);
      }
    }
    if (!AGENT_LOG_LEVELS.includes(cfg.logLevel)) {
      throw new TypeError(
        `${where}: agents.${id}.logLevel must be one of ${AGENT_LOG_LEVELS.join(", ")}`
      );
    }
    out[id] = {
      autoStart: cfg.autoStart,
      restartOnCrash: cfg.restartOnCrash,
      logLevel: cfg.logLevel,
    };
  }
  return out;
}

/**
 * A path as one quoted Exec= argument: the Desktop Entry spec escapes " ` $ \
 * inside the quotes, then escapes every backslash again as a string value.
 */
function desktopExecArg(file) {
  // "%" starts a field code (%f, %u…) anywhere in Exec=, so it is doubled.
  const quoted = `"${file.replace(/%/g, "%%").replace(/["`$\\]/g, (c) => `\\${c}`)}"`;
  return quoted.replace(/\\/g, "\\\\");
}

// ── TrayManager ──────────────────────────────────────────────────────────

class TrayManager {
  /**
   * @param {Electron.BrowserWindow} mainWindow
   * @param {object} [options]
   * @param {number} [options.backendPort=4200] - The GAIA service's port, for Free memory
   * @param {() => Electron.BrowserWindow} options.reopenWindow - Recreate a closed window (macOS keeps the app running without one)
   */
  constructor(mainWindow, options = {}) {
    /** @type {Electron.BrowserWindow} */
    this.mainWindow = mainWindow;

    /** @type {number} */
    this._backendPort = options.backendPort || 4200;

    if (typeof options.reopenWindow !== "function") {
      throw new TypeError("TrayManager needs options.reopenWindow to bring back a closed window");
    }
    this._reopenWindow = options.reopenWindow;

    /** Menu commands waiting for a reopened window to start listening. */
    this._pendingCommands = [];

    /** @type {Electron.Tray | null} */
    this.tray = null;

    /** @type {number} Unread notifications reflected in the tooltip/badge. */
    this._notificationCount = 0;

    /** Last snapshot from the window; null until it first reports. */
    this._activity = null;

    /** False once the GAIA service process has exited. */
    this._backendAlive = true;

    this._freeingMemory = false;

    /** @type {ReturnType<typeof deriveTrayView> | null} */
    this._view = null;
    this._menuKey = null;
    this._overlayKey = null;

    /** @type {object} */
    this.config = this._loadConfig();

    this._iconSpec = ICON_SPECS[process.platform] || ICON_SPECS.linux;

    /** @type {Map<string, Electron.NativeImage>} One image per tray state, built on first use. */
    this._icons = new Map();

    /** @type {Electron.NativeImage} */
    this._icon = this._iconFor("idle");

    this._registerIpcHandlers();
  }

  // ── Public API ───────────────────────────────────────────────────────

  /** Create the tray icon and wire up events. Call once after app.whenReady(). */
  create() {
    if (this.tray) return;

    this.tray = new Tray(this._icon);

    // Windows: a click shows the window. macOS and most Linux trays open the
    // menu on click instead, where "Show GAIA" does the same.
    this.tray.on("click", () => this._showWindow());
    this.tray.on("double-click", () => this._showWindow());

    this._render();
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

  /** Rebuild the icon, tooltip and menu from the current state. */
  refresh() {
    this._menuKey = null;
    this._overlayKey = null;
    this._render();
  }

  /**
   * Reflect the unread-notification count in the tray tooltip, the dock /
   * launcher badge, and the Windows taskbar overlay.
   *
   * @param {number} count Unread notifications; negative/NaN is treated as 0.
   */
  setNotificationCount(count) {
    const unread =
      typeof count === "number" && Number.isFinite(count) && count > 0
        ? Math.floor(count)
        : 0;
    if (unread !== this._notificationCount) {
      console.log(`[tray] Unread notifications: ${unread}`);
    }
    this._notificationCount = unread;

    // Dock (macOS) / Unity launcher (Linux) badge. Windows has no
    // app.setBadgeCount equivalent; the taskbar overlay stands in for it.
    if (process.platform !== "win32" && typeof app.setBadgeCount === "function") {
      app.setBadgeCount(unread);
    }
    this._render();
  }

  /**
   * Take a snapshot of what GAIA is doing from the window.
   * @throws {TypeError} When the snapshot is malformed; nothing changes then.
   */
  setActivity(raw) {
    this._activity = validateActivity(raw);
    this._render();
    // The window reports once its tray bridge listens, so held commands can go.
    const win = this.mainWindow;
    if (this._pendingCommands.length && win && !win.isDestroyed()) {
      for (const command of this._pendingCommands.splice(0)) {
        win.webContents.send("tray:command", command);
      }
    }
  }

  /** The window was closed (not hidden): what it last reported no longer holds. */
  windowClosed() {
    this._activity = null;
    // A new window has no overlay yet, whatever the last one showed.
    this._overlayKey = null;
    this._render();
  }

  /** Mark the GAIA service process as running or exited. */
  setBackendAlive(alive) {
    this._backendAlive = Boolean(alive);
    this._render();
  }

  /** @returns {string} The tray state now showing: one of tray-icons.STATES. */
  get state() {
    return this._view ? this._view.state : "idle";
  }

  /** @returns {number} The unread count last passed to setNotificationCount. */
  get notificationCount() {
    return this._notificationCount || 0;
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

  /** @returns {boolean} Whether the one-time "still running in the tray" notice is still due. */
  get closeNoticeDue() {
    return !this.config.notices.firstClose;
  }

  /**
   * Tell the user, once, that closing the window left GAIA running.
   * Saved as shown before it opens, so a crash mid-dialog never repeats it.
   * @returns {Promise<boolean>} True when the user chose to quit from the notice.
   */
  async showCloseNotice() {
    if (!this.closeNoticeDue) return false;
    const next = { ...this.config, notices: { ...this.config.notices, firstClose: true } };
    this._saveConfig(next);
    this.config = next;
    const where = process.platform === "darwin" ? "menu bar" : "tray";
    const { response } = await dialog.showMessageBox({
      type: "info",
      title: "GAIA is still running",
      message: `GAIA is still running in the ${where}.`,
      detail: `Quit from the ${where} menu to stop it. Closing the window keeps your model loaded, so the next chat starts quickly.`,
      buttons: ["OK", "Quit GAIA"],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    return response === 1;
  }

  // ── Private: Rendering ───────────────────────────────────────────────

  _render() {
    this._view = deriveTrayView(this._activity, {
      backendAlive: this._backendAlive,
      unread: this._notificationCount,
    });
    const trayAlive =
      this.tray &&
      !(typeof this.tray.isDestroyed === "function" && this.tray.isDestroyed());
    if (trayAlive) {
      const icon = this._iconFor(this._view.state);
      if (icon !== this._icon) {
        this._icon = icon;
        this.tray.setImage(icon);
        console.log(`[tray] State: ${this._view.state}`);
      }
      this.tray.setToolTip(this._view.tooltip);
      // Linux trays never show the tooltip and only redraw the menu on
      // setContextMenu, so the menu is rebuilt whenever its text changes.
      const menuKey = JSON.stringify([
        this._view.statusLine,
        this._view.needsYou,
        this._view.recent,
        this._view.canFreeMemory,
        this._view.freeMemoryHint,
      ]);
      if (menuKey !== this._menuKey) {
        this._menuKey = menuKey;
        this.tray.setContextMenu(
          Menu.buildFromTemplate(buildMenuTemplate(this._view, this._menuActions()))
        );
      }
    }
    this._renderOverlay();
  }

  /** Windows has no badge, so the taskbar button carries needs-you and unread. */
  _renderOverlay() {
    if (process.platform !== "win32") return;
    const win = this.mainWindow;
    if (!win || win.isDestroyed() || typeof win.setOverlayIcon !== "function") return;
    const needs = this._view.state === "needs-you" ? this._view.needsYou.length : 0;
    const unread = this._notificationCount;
    let key = "none";
    if (needs > 0) key = `needs-you:${needs}`;
    else if (unread > 0) key = `unread:${unread}`;
    if (key === this._overlayKey) return;
    this._overlayKey = key;
    if (needs > 0) {
      win.setOverlayIcon(
        this._overlayImage("needs-you"),
        `${needs} ${needs === 1 ? "chat needs" : "chats need"} you`
      );
    } else if (unread > 0) {
      win.setOverlayIcon(
        this._overlayImage("unread"),
        `${unread} unread notification${unread === 1 ? "" : "s"}`
      );
    } else {
      win.setOverlayIcon(null, "");
    }
  }

  _menuActions() {
    return {
      openChat: (sessionId) => this._command({ type: "open-chat", sessionId }),
      newChat: () => this._command({ type: "new-chat" }),
      showWindow: () => this._showWindow(),
      freeMemory: () => {
        // A menu click has no caller to report to, so the user hears about it.
        this._freeMemory().catch((err) => {
          console.error("[tray] Free memory failed:", err);
          dialog.showErrorBox("GAIA", `Free memory failed: ${err.message}`);
        });
      },
      openSettings: () => this._command({ type: "open-settings", section: "general" }),
      quit: () => this._quit(),
    };
  }

  /** Show the window and hand it a menu action. */
  _command(command) {
    const reopened = this._showWindow();
    if (reopened) {
      this._pendingCommands.push(command);
      return;
    }
    this.mainWindow.webContents.send("tray:command", command);
  }

  // ── Private: Free memory ─────────────────────────────────────────────

  async _freeMemory() {
    if (this._freeingMemory || !this._view || !this._view.canFreeMemory) return;
    // Set before the first dialog, so a second click can't open a second one.
    this._freeingMemory = true;
    try {
      await this._freeMemoryFlow(this._view.modelName);
    } finally {
      this._freeingMemory = false;
    }
  }

  async _freeMemoryFlow(model) {
    const failed = (message, detail) =>
      dialog.showMessageBox({
        type: "error",
        title: "Free memory",
        message,
        detail,
        buttons: ["OK"],
        noLink: true,
      });

    // The window only knows its own chats; the backend knows every answer
    // and index job, so ask it now rather than poll it all the time.
    let work;
    try {
      work = await fetchWorkInProgress(this._backendPort);
    } catch (err) {
      await failed("GAIA couldn't check whether the model is in use.", err.message);
      return;
    }
    const busy = describeBusy(work);
    if (busy) {
      await dialog.showMessageBox({
        type: "info",
        title: "Free memory",
        message: "The model is in use.",
        detail: `${busy} Try again when it has finished.`,
        buttons: ["OK"],
        noLink: true,
      });
      return;
    }

    const { response } = await dialog.showMessageBox({
      type: "question",
      title: "Free memory?",
      message: `Unload ${model} from memory?`,
      detail:
        "This frees the memory the model uses. Your next message, here or in another GAIA app, loads it again, which takes a moment.",
      buttons: ["Free memory", "Cancel"],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });
    if (response !== 0) return;
    try {
      await backendJson(this._backendPort, "/api/system/unload-model", {
        method: "POST",
        body: { model_name: model },
        timeoutMs: 30000,
      });
    } catch (err) {
      await failed("GAIA couldn't free the model's memory.", err.message);
      return;
    }
    console.log(`[tray] Unloaded ${model} to free memory`);
    // The backend just confirmed it; the window's next status read agrees.
    if (this._activity) this._activity = { ...this._activity, model: null };
    this._render();
    const win = this.mainWindow;
    if (win && !win.isDestroyed()) {
      win.webContents.send("tray:command", { type: "refresh-status" });
    }
  }

  // ── Private: Window management ───────────────────────────────────────

  /** @returns {boolean} True when the window had to be recreated. */
  _showWindow() {
    if (!this.mainWindow || this.mainWindow.isDestroyed()) {
      this.mainWindow = this._reopenWindow();
      this._overlayKey = null;
      this._renderOverlay();
      return true;
    }
    if (this.mainWindow.isMinimized()) {
      this.mainWindow.restore();
    }
    this.mainWindow.show();
    this.mainWindow.focus();
    return false;
  }

  _quit() {
    console.log("[tray] Quit requested");
    // main.cjs's before-quit asks first when work would be lost.
    app.quit();
  }

  // ── Private: Icons ───────────────────────────────────────────────────

  /**
   * The tray image for a state: the logo from the platform's small assets
   * (never the 4K app icon) with that state's badge drawn on.
   * @returns {Electron.NativeImage}
   */
  _iconFor(state) {
    if (!STATES.includes(state)) throw new Error(`[tray] unknown tray state "${state}"`);
    const cached = this._icons.get(state);
    if (cached) return cached;
    const spec = this._iconSpec;
    const image = nativeImage.createEmpty();
    for (const rep of spec.reps) {
      let source = this._loadIcon(rep.file);
      if (source.getSize().width !== rep.px) {
        source = source.resize({ width: rep.px, height: rep.px, quality: "best" });
      }
      const buffer = drawStateIcon(
        { width: rep.px, height: rep.px, buffer: source.toBitmap() },
        state,
        { template: spec.template }
      );
      image.addRepresentation({ scaleFactor: rep.scale, width: rep.px, height: rep.px, buffer });
    }
    // macOS menu-bar extras must be template images: the OS tints the
    // alpha mask for light/dark mode.
    if (spec.template) image.setTemplateImage(true);
    this._icons.set(state, image);
    return image;
  }

  _overlayImage(kind) {
    const key = `overlay:${kind}`;
    const cached = this._icons.get(key);
    if (cached) return cached;
    const image = nativeImage.createEmpty();
    for (const rep of OVERLAY_REPS) {
      image.addRepresentation({
        scaleFactor: rep.scale,
        width: rep.px,
        height: rep.px,
        buffer: drawOverlayIcon(kind, rep.px),
      });
    }
    this._icons.set(key, image);
    return image;
  }

  _loadIcon(filename) {
    // __dirname is services/, assets/ is one level up alongside main.cjs
    const iconPath = path.join(__dirname, "..", "assets", filename);
    if (!fs.existsSync(iconPath)) {
      throw new Error(`[tray] icon asset missing: ${iconPath}`);
    }
    const img = nativeImage.createFromPath(iconPath);
    // An undecodable file yields an empty image, which trays draw as a blank
    // or placeholder square instead of erroring.
    if (img.isEmpty()) {
      throw new Error(`[tray] icon asset could not be decoded: ${iconPath}`);
    }
    return img;
  }

  // ── Private: Config persistence ──────────────────────────────────────

  _loadConfig() {
    let loaded = null;
    try {
      if (fs.existsSync(CONFIG_PATH)) {
        loaded = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
      }
    } catch (err) {
      console.warn("[tray] Could not load tray config:", err.message);
    }
    if (!isPlainObject(loaded)) {
      return {
        ...DEFAULT_CONFIG,
        tray: { ...DEFAULT_CONFIG.tray },
        agents: {},
        notices: { ...DEFAULT_CONFIG.notices },
      };
    }

    // Scoped to the agents section: one bad entry must not cost the user their
    // tray settings, which the next save would then overwrite with defaults.
    let agents = {};
    try {
      agents = validateAgents(loaded.agents ?? {}, CONFIG_PATH);
    } catch (err) {
      console.error(
        `[tray] Ignoring invalid agents section in ${CONFIG_PATH}: ${err.message}. ` +
          "Agent settings fall back to defaults; tray settings are kept."
      );
    }

    // Repair on read: the pre-validation handler could store wrong-typed
    // values, and set-config re-reads stored keys the payload omits.
    const stored = isPlainObject(loaded.tray) ? loaded.tray : {};
    const tray = { ...DEFAULT_CONFIG.tray };
    for (const key of Object.keys(DEFAULT_CONFIG.tray)) {
      if (typeof stored[key] === "boolean") {
        tray[key] = stored[key];
      } else if (key in stored) {
        console.warn(
          `[tray] Ignoring non-boolean ${key} in ${CONFIG_PATH}; using default`
        );
      }
    }

    const storedNotices = isPlainObject(loaded.notices) ? loaded.notices : {};
    const notices = { ...DEFAULT_CONFIG.notices };
    for (const key of Object.keys(DEFAULT_CONFIG.notices)) {
      if (typeof storedNotices[key] === "boolean") notices[key] = storedNotices[key];
    }

    return {
      ...DEFAULT_CONFIG,
      ...loaded,
      tray,
      agents,
      notices,
    };
  }

  _saveConfig(config) {
    try {
      fs.mkdirSync(GAIA_DIR, { recursive: true });
      fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2), "utf8");
    } catch (err) {
      throw new Error(
        `Could not save settings to ${CONFIG_PATH}: ${err.message}. ` +
          "Check that the file and its folder are writable."
      );
    }
    console.log("[tray] Config saved to", CONFIG_PATH);
  }

  // ── Private: IPC handlers ────────────────────────────────────────────

  _registerIpcHandlers() {
    ipcMain.handle("tray:set-activity", (_event, activity) => {
      this.setActivity(activity);
    });

    ipcMain.handle("tray:get-config", () => {
      return this.config;
    });

    ipcMain.handle("tray:set-config", (_event, cfg) => {
      if (!isPlainObject(cfg)) {
        throw new TypeError("tray:set-config expects an object payload");
      }
      if (cfg.tray !== undefined && !isPlainObject(cfg.tray)) {
        throw new TypeError("tray:set-config: tray must be an object");
      }
      const agents =
        cfg.agents === undefined
          ? null
          : validateAgents(cfg.agents, "tray:set-config");

      let tray = null;
      if (cfg.tray !== undefined) {
        tray = {};
        for (const key of Object.keys(DEFAULT_CONFIG.tray)) {
          const value = key in cfg.tray ? cfg.tray[key] : this.config.tray[key];
          if (typeof value !== "boolean") {
            throw new TypeError(`tray:set-config: tray.${key} must be a boolean`);
          }
          tray[key] = value;
        }
      }

      if (tray === null && agents === null) {
        return this.config;
      }
      const next = {
        ...this.config,
        tray: tray ?? this.config.tray,
        agents: agents ? { ...this.config.agents, ...agents } : this.config.agents,
      };
      // The OS login item first: if it can't be set, nothing is saved, so the
      // toggle never claims a state the OS doesn't have.
      const loginChanged =
        tray !== null && tray.startOnLogin !== this.config.tray.startOnLogin;
      if (loginChanged) this._applyLoginItemSetting(tray.startOnLogin);
      try {
        this._saveConfig(next);
      } catch (err) {
        // Keep the OS and the saved toggle in step: undo the login item.
        if (loginChanged) this._applyLoginItemSetting(this.config.tray.startOnLogin);
        throw err;
      }
      this.config = next;

      return this.config;
    });
  }

  /**
   * Register or remove GAIA as a login item. "Start minimized" decides
   * whether that launch shows the window, the same as any other launch.
   * @throws {Error} When the OS refused; the caller's save is skipped.
   */
  _applyLoginItemSetting(enabled) {
    if (process.platform === "linux") {
      this._applyLinuxAutostart(enabled);
    } else {
      const item = { path: app.getPath("exe"), args: [] };
      const what = enabled ? "add GAIA to" : "remove GAIA from";
      try {
        app.setLoginItemSettings({ ...item, openAtLogin: enabled });
      } catch (err) {
        throw new Error(`Could not ${what} the apps that start at sign-in: ${err.message}`);
      }
      // The OS can refuse without throwing; read back what it actually has.
      const now = app.getLoginItemSettings(item);
      if (enabled && now.status === "requires-approval") {
        throw new Error(
          "macOS needs your approval to start GAIA at sign-in. Allow it in System Settings → General → Login Items, then turn this on again."
        );
      }
      if (now.openAtLogin !== enabled) {
        throw new Error(
          `Could not ${what} the apps that start at sign-in: the system still reports it ${now.openAtLogin ? "on" : "off"}. ` +
            "Check your startup apps in the system settings."
        );
      }
    }
    console.log(`[tray] Login item ${enabled ? "enabled" : "disabled"}`);
  }

  /** Electron has no login items on Linux; desktops read XDG autostart entries. */
  _applyLinuxAutostart(enabled) {
    const configHome = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
    const file = path.join(configHome, "autostart", LINUX_AUTOSTART_FILE);
    // An AppImage's exe is a temporary mount; APPIMAGE is the file the user runs.
    const exe = process.env.APPIMAGE || app.getPath("exe");
    try {
      if (enabled) {
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.writeFileSync(
          file,
          [
            "[Desktop Entry]",
            "Type=Application",
            "Name=GAIA",
            "Comment=Start GAIA when you sign in",
            `Exec=${desktopExecArg(exe)}`,
            "X-GNOME-Autostart-enabled=true",
            "",
          ].join("\n"),
          "utf8"
        );
      } else if (fs.existsSync(file)) {
        fs.unlinkSync(file);
      }
    } catch (err) {
      throw new Error(
        `Could not ${enabled ? "write" : "remove"} the autostart entry ${file}: ${err.message}. ` +
          "Check that the folder is writable."
      );
    }
  }
}

module.exports = TrayManager;
