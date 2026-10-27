// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The browser pane service: one pane session with its policy, one PaneHost per
 * app window, and the IPC channels the window's toolbar calls. preload.cjs
 * exposes the same channel names as window.gaiaBrowserPane.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const { PaneHost, PANE_PARTITION } = require("./pane-host.cjs");
const { installSessionPolicy } = require("./navigation-policy.cjs");

/** @typedef {import("./contract").RecentPage} RecentPage */

const CHANNELS = Object.freeze({
  getState: "pane:get-state",
  state: "pane:state",
  command: "pane:command",
  setBounds: "pane:set-bounds",
  setVisible: "pane:set-visible",
  setOccluded: "pane:set-occluded",
  openTab: "pane:open-tab",
  closeTab: "pane:close-tab",
  selectTab: "pane:select-tab",
  navigate: "pane:navigate",
  history: "pane:history",
  toggleDevTools: "pane:toggle-devtools",
  clearData: "pane:clear-data",
  openExternal: "pane:open-external",
  setActiveSession: "pane:set-active-session",
  setControl: "pane:set-control",
  setViewport: "pane:set-viewport",
  togglePicking: "pane:toggle-picking",
  picked: "pane:picked",
});

const HISTORY_ACTIONS = new Set(["back", "forward", "reload", "stop"]);

/** Recent pages kept as a small JSON file, so the new tab page survives a restart. */
function fileRecentStore(filePath) {
  return {
    /** @returns {RecentPage[]} */
    load() {
      let raw;
      try {
        raw = fs.readFileSync(filePath, "utf8");
      } catch (err) {
        if (err.code === "ENOENT") return [];
        console.error(`[browser-pane] Could not read recent pages from ${filePath}: ${err.message}`);
        return [];
      }
      try {
        const pages = JSON.parse(raw);
        if (!Array.isArray(pages)) throw new Error("expected a list");
        return pages.filter((p) => p && typeof p.url === "string" && typeof p.title === "string");
      } catch (err) {
        console.error(`[browser-pane] Ignoring unreadable recent pages in ${filePath}: ${err.message}`);
        return [];
      }
    },
    /** @param {RecentPage[]} pages */
    save(pages) {
      const tmp = `${filePath}.tmp`;
      try {
        fs.writeFileSync(tmp, JSON.stringify(pages));
        fs.renameSync(tmp, filePath);
      } catch (err) {
        console.error(`[browser-pane] Could not save recent pages to ${filePath}: ${err.message}`);
      }
    },
  };
}

function requireString(value, what) {
  if (typeof value !== "string" || value === "") {
    throw new Error(`Browser pane: ${what} must be a non-empty string, got ${JSON.stringify(value)}`);
  }
  return value;
}

function parseRect(rect) {
  const ok =
    rect &&
    ["x", "y", "width", "height"].every((k) => typeof rect[k] === "number" && Number.isFinite(rect[k]));
  return ok ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null;
}

const VIEWPORT_LIMITS = { min: 200, max: 4000 };

/** The Responsive control's choice, or null; anything else is a bug in the renderer. */
function parseViewportChoice(choice) {
  if (choice === null) return null;
  const size = choice && choice.size;
  const scheme = choice && choice.colorScheme;
  const dim = (n) => Number.isInteger(n) && n >= VIEWPORT_LIMITS.min && n <= VIEWPORT_LIMITS.max;
  const sizeOk = size === null || (size && dim(size.width) && dim(size.height) && typeof size.mobile === "boolean");
  if (!choice || !sizeOk || (scheme !== null && scheme !== "light" && scheme !== "dark")) {
    throw new Error(`Browser pane: bad viewport ${JSON.stringify(choice)}`);
  }
  return { size: size && { width: size.width, height: size.height, mobile: size.mobile }, colorScheme: scheme };
}

/**
 * @param {{
 *   ipcMain: Electron.IpcMain,
 *   session: Electron.Session,
 *   shell: Electron.Shell,
 *   userDataDir: string,
 *   downloadsDir: () => string,
 *   blockedPorts: () => number[],
 * }} deps
 */
function createBrowserPaneService({ ipcMain, session, shell, userDataDir, downloadsDir, blockedPorts }) {
  /** @type {PaneHost | null} */
  let host = null;
  const recentStore = fileRecentStore(path.join(userDataDir, "browser-pane-recent.json"));

  installSessionPolicy(session, {
    blockedPorts,
    downloadsDir,
    onPermissionDenied: (webContents, permission, origin) => {
      console.log(`[browser-pane] Denied ${permission} for ${origin || "a pane page"}`);
      if (host) host.notePermissionDenied(webContents, permission, origin);
    },
  });

  function requireHost(event) {
    if (!host) throw new Error("The browser pane is not ready: the GAIA window has not opened yet.");
    if (event.sender !== host.window.webContents) {
      throw new Error("Only the GAIA window can drive the browser pane.");
    }
    return host;
  }

  const handle = (channel, fn) => {
    ipcMain.handle(channel, (event, ...args) => fn(requireHost(event), ...args));
  };

  handle(CHANNELS.getState, (h) => h.state());
  handle(CHANNELS.setVisible, (h, visible) => h.setVisible(visible === true));
  handle(CHANNELS.setOccluded, (h, occluded) => h.setOccluded(occluded === true));
  handle(CHANNELS.openTab, (h, url) => h.openTab(url === null ? null : requireString(url, "url")));
  handle(CHANNELS.closeTab, (h, id) => h.closeTab(requireString(id, "tab id")));
  handle(CHANNELS.selectTab, (h, id) => h.selectTab(requireString(id, "tab id")));
  handle(CHANNELS.navigate, (h, id, url) => h.navigate(requireString(id, "tab id"), requireString(url, "url")));
  handle(CHANNELS.history, (h, id, action) => {
    if (!HISTORY_ACTIONS.has(action)) {
      throw new Error(`Browser pane: unknown history action ${JSON.stringify(action)}`);
    }
    h.history(requireString(id, "tab id"), action);
  });
  handle(CHANNELS.toggleDevTools, (h, id) => h.toggleDevTools(requireString(id, "tab id")));
  handle(CHANNELS.clearData, (h) => h.clearBrowsingData());
  handle(CHANNELS.openExternal, (h, id) => h.openExternal(requireString(id, "tab id")));
  handle(CHANNELS.setControl, (h, id, control) => h.setControl(requireString(id, "tab id"), control));
  handle(CHANNELS.togglePicking, (h) => h.togglePicking());
  handle(CHANNELS.setViewport, (h, id, choice) => h.setUserViewport(requireString(id, "tab id"), parseViewportChoice(choice)));

  // Sent on every resize, so it is fire-and-forget; a throw here would reach the crash handler.
  ipcMain.on(CHANNELS.setBounds, (event, rect) => {
    const parsed = parseRect(rect);
    if (!host || event.sender !== host.window.webContents || !parsed) {
      console.error(`[browser-pane] Ignored bounds ${JSON.stringify(rect)} from an unexpected sender or shape`);
      return;
    }
    host.setBounds(parsed);
  });

  ipcMain.on(CHANNELS.setActiveSession, (event, sessionId) => {
    const valid = sessionId === null || (typeof sessionId === "string" && sessionId !== "" && sessionId.length <= 200);
    if (!host || event.sender !== host.window.webContents || !valid) {
      console.error(`[browser-pane] Ignored active session ${JSON.stringify(sessionId)} from an unexpected sender or shape`);
      return;
    }
    host.setActiveSession(sessionId);
  });

  return {
    /** The window's PaneHost, for the bridge; null while no window is open. */
    getHost: () => host,
    /** Give the pane to a (re)created app window. Tabs of a closed window close with it. */
    attach(window) {
      if (host) host.dispose();
      const send = (channel, payload) => {
        if (!window.isDestroyed()) window.webContents.send(channel, payload);
      };
      const created = new PaneHost({
        window,
        session,
        recentStore,
        blockedPorts,
        publish: (state) => send(CHANNELS.state, state),
        onToggleRequest: () => send(CHANNELS.command, "toggle"),
        onShowRequest: () => send(CHANNELS.command, "show"),
        onPicked: (event) => send(CHANNELS.picked, event),
        openExternal: (url) => {
          shell.openExternal(url).catch((err) =>
            console.error(`[browser-pane] Could not open ${url} in the system browser: ${err.message}`)
          );
        },
      });
      host = created;
      window.on("closed", () => {
        created.dispose();
        if (host === created) host = null;
      });
      return created;
    },
  };
}

module.exports = { CHANNELS, PANE_PARTITION, createBrowserPaneService, fileRecentStore };
