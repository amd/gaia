// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * PaneHost owns every tab of the browser pane for one app window. Each tab
 * with a page is a WebContentsView on its own partition, sandboxed and with
 * no preload. The renderer draws the toolbar and a placeholder; this class
 * keeps the native views on top of that placeholder and pushes the whole
 * PaneState to the renderer after every change.
 *
 * Hidden always means view.setVisible(false). Offscreen or zero-size bounds
 * stall or freeze page capture, so they are never used to hide a view.
 */

"use strict";

const { WebContentsView } = require("electron");
const { attachTabPolicy, classifyNavigation } = require("./navigation-policy.cjs");

/** @typedef {import("./contract").TabId} TabId */
/** @typedef {import("./contract").TabState} TabState */
/** @typedef {import("./contract").PaneState} PaneState */
/** @typedef {import("./contract").RecentPage} RecentPage */
/** @typedef {import("./contract").CssRect} CssRect */
/** @typedef {import("./contract").HistoryAction} HistoryAction */

const PANE_PARTITION = "persist:gaia-browser-pane";
// Docks DevTools inside the tab's own view, so it stays in the pane and moves with it.
const DEVTOOLS_MODE = "bottom";
const RECENT_LIMIT = 8;
const ERR_ABORTED = -3;

/** True for the key that toggles the pane: Cmd or Ctrl, Shift and B. */
function isToggleShortcut(input) {
  return (
    input.type === "keyDown" &&
    (input.meta || input.control) &&
    input.shift &&
    !input.alt &&
    input.key.toLowerCase() === "b"
  );
}

/**
 * Turn the placeholder's CSS rect into view bounds in window DIPs, clipped to
 * the window's content. Null when nothing of it is inside the window.
 *
 * @param {CssRect} rect
 * @param {number} zoomFactor The app window's zoom; CSS px times it gives DIPs.
 * @param {{ width: number, height: number }} content
 * @returns {Electron.Rectangle | null}
 */
function toViewBounds(rect, zoomFactor, content) {
  const left = Math.max(0, Math.round(rect.x * zoomFactor));
  const top = Math.max(0, Math.round(rect.y * zoomFactor));
  const right = Math.min(content.width, Math.round((rect.x + rect.width) * zoomFactor));
  const bottom = Math.min(content.height, Math.round((rect.y + rect.height) * zoomFactor));
  if (right <= left || bottom <= top) return null;
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function isWebUrl(url) {
  return /^https?:/i.test(url);
}

class PaneHost {
  /**
   * @param {{
   *   window: Electron.BrowserWindow,
   *   session: Electron.Session,
   *   publish: (state: PaneState) => void,
   *   blockedPorts: () => number[],
   *   recentStore: { load: () => RecentPage[], save: (pages: RecentPage[]) => void },
   *   onToggleRequest: () => void,
   *   openExternal: (url: string) => void,
   * }} deps
   */
  constructor(deps) {
    this.deps = deps;
    this.window = deps.window;
    /** @type {Map<TabId, { state: TabState, view: Electron.WebContentsView | null }>} */
    this.tabs = new Map();
    this.nextId = 1;
    this.visible = false;
    this.occluded = false;
    /** @type {Electron.Rectangle | null} */
    this.bounds = null;
    /** @type {RecentPage[]} */
    this.recent = deps.recentStore.load();
    this.disposed = false;
    /** @type {TabId} */
    this.activeTabId = this.addTab();
  }

  /** @returns {PaneState} */
  state() {
    return {
      visible: this.visible,
      occluded: this.occluded,
      tabs: [...this.tabs.values()].map((t) => ({ ...t.state })),
      activeTabId: this.activeTabId,
      recent: this.recent.slice(),
    };
  }

  // ── Placement ─────────────────────────────────────────────────────────

  /** @param {CssRect} rect */
  setBounds(rect) {
    const content = this.window.getContentBounds();
    const zoom = this.window.webContents.getZoomFactor();
    this.bounds = toViewBounds(rect, zoom, content);
    this.syncViews();
  }

  /** @param {boolean} visible */
  setVisible(visible) {
    this.visible = visible;
    this.syncViews();
    this.publish();
  }

  /**
   * While an app overlay covers the pane the live view is hidden, since a
   * native view would paint over the overlay. Resolves with a still frame of
   * the page for the placeholder, or null when no page was showing.
   *
   * @param {boolean} occluded
   * @returns {Promise<string | null>}
   */
  async setOccluded(occluded) {
    const wasShown = this.isShown();
    this.occluded = occluded;
    this.syncViews();
    this.publish();
    const view = this.activeTab().view;
    if (!occluded || !wasShown || !view) return null;
    // A hidden view still captures a fresh frame, so hiding first keeps the overlay clear.
    const image = await view.webContents.capturePage();
    return image.toDataURL();
  }

  // ── Tabs ──────────────────────────────────────────────────────────────

  /**
   * @param {string | null} url null opens a new tab page.
   * @returns {TabId}
   */
  openTab(url) {
    const id = this.addTab();
    this.activeTabId = id;
    if (url !== null) this.navigate(id, url);
    this.syncViews();
    this.publish();
    return id;
  }

  /** @param {TabId} id */
  closeTab(id) {
    const order = [...this.tabs.keys()];
    const index = order.indexOf(id);
    const tab = this.requireTab(id);
    this.destroyView(tab.view);
    this.tabs.delete(id);
    if (this.tabs.size === 0) {
      this.activeTabId = this.addTab();
    } else if (this.activeTabId === id) {
      const rest = order.filter((t) => t !== id);
      this.activeTabId = rest[Math.min(index, rest.length - 1)];
    }
    this.syncViews();
    this.publish();
  }

  /** @param {TabId} id */
  selectTab(id) {
    this.requireTab(id);
    this.activeTabId = id;
    this.syncViews();
    this.publish();
  }

  /**
   * @param {TabId} id
   * @param {string} url Already a full URL; the address bar normalizes what was typed.
   */
  navigate(id, url) {
    const tab = this.requireTab(id);
    const verdict = classifyNavigation(url, { blockedPorts: this.deps.blockedPorts() });
    if (!verdict.allowed) {
      tab.state.notice = { kind: "blocked", url, message: verdict.message };
      this.publish();
      return;
    }
    const view = this.ensureView(tab);
    tab.state.notice = null;
    tab.state.url = url;
    view.webContents.loadURL(url).catch((err) => {
      // did-fail-load puts the failure on the tab; this only keeps the rejection handled.
      console.warn(`[browser-pane] ${url} did not finish loading: ${err.message}`);
    });
    this.publish();
  }

  /**
   * @param {TabId} id
   * @param {HistoryAction} action
   */
  history(id, action) {
    const tab = this.requireTab(id);
    if (!tab.view) throw new Error(`Tab ${id} has no page to ${action}; open a page in it first.`);
    const wc = tab.view.webContents;
    if (action === "back") wc.navigationHistory.goBack();
    else if (action === "forward") wc.navigationHistory.goForward();
    else if (action === "reload") wc.reload();
    else if (action === "stop") wc.stop();
    else throw new Error(`Unknown history action "${action}"; use back, forward, reload or stop.`);
  }

  /** @param {TabId} id */
  toggleDevTools(id) {
    const tab = this.requireTab(id);
    if (!tab.view) throw new Error(`Tab ${id} has no page to inspect; open a page in it first.`);
    const wc = tab.view.webContents;
    if (wc.isDevToolsOpened()) wc.closeDevTools();
    else wc.openDevTools({ mode: DEVTOOLS_MODE });
  }

  /** Clears the pane's cookies, storage, cache, tab histories and recent pages. Nothing outside the pane. */
  async clearBrowsingData() {
    const ses = this.deps.session;
    await Promise.all([ses.clearStorageData(), ses.clearCache(), ses.clearAuthCache()]);
    this.recent = [];
    this.deps.recentStore.save(this.recent);
    for (const tab of this.tabs.values()) {
      if (!tab.view) continue;
      tab.view.webContents.navigationHistory.clear();
      this.readNavigation(tab);
    }
    this.publish();
  }

  /** @param {TabId} id */
  openExternal(id) {
    const { url } = this.requireTab(id).state;
    if (url === null || !isWebUrl(url)) {
      throw new Error(`Tab ${id} has no web page to open in the system browser.`);
    }
    this.deps.openExternal(url);
  }

  dispose() {
    this.disposed = true;
    for (const tab of this.tabs.values()) this.destroyView(tab.view);
    this.tabs.clear();
  }

  // ── Internals ─────────────────────────────────────────────────────────

  /** @returns {TabId} */
  addTab() {
    const id = /** @type {TabId} */ (`tab-${this.nextId++}`);
    this.tabs.set(id, {
      view: null,
      state: {
        id,
        url: null,
        title: "New tab",
        loading: false,
        canGoBack: false,
        canGoForward: false,
        devToolsOpen: false,
        notice: null,
      },
    });
    return id;
  }

  activeTab() {
    return this.requireTab(this.activeTabId);
  }

  /** @param {TabId} id */
  requireTab(id) {
    const tab = this.tabs.get(id);
    if (!tab) throw new Error(`No browser pane tab "${id}"; it may have been closed.`);
    return tab;
  }

  isShown() {
    return this.visible && !this.occluded && this.bounds !== null;
  }

  syncViews() {
    if (this.disposed) return;
    const shown = this.isShown();
    for (const [id, tab] of this.tabs) {
      if (!tab.view) continue;
      if (this.bounds) tab.view.setBounds(this.bounds);
      tab.view.setVisible(shown && id === this.activeTabId);
    }
  }

  publish() {
    if (this.disposed || this.window.isDestroyed()) return;
    this.deps.publish(this.state());
  }

  ensureView(tab) {
    if (tab.view) return tab.view;
    const view = new WebContentsView({
      webPreferences: {
        partition: PANE_PARTITION,
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    view.setVisible(false);
    tab.view = view;
    this.watch(tab);
    this.window.contentView.addChildView(view);
    this.syncViews();
    return view;
  }

  destroyView(view) {
    if (!view) return;
    if (!this.window.isDestroyed()) this.window.contentView.removeChildView(view);
    if (!view.webContents.isDestroyed()) view.webContents.close();
  }

  readNavigation(tab) {
    const wc = tab.view.webContents;
    tab.state.canGoBack = wc.navigationHistory.canGoBack();
    tab.state.canGoForward = wc.navigationHistory.canGoForward();
  }

  watch(tab) {
    const wc = tab.view.webContents;
    const s = tab.state;
    const update = (fn) => (...args) => {
      if (this.disposed || !this.tabs.has(s.id)) return;
      fn(...args);
      this.publish();
    };

    attachTabPolicy(wc, {
      blockedPorts: this.deps.blockedPorts,
      openTab: (url) => this.openTab(url),
      onBlocked: update((url, message) => {
        s.notice = { kind: "blocked", url, message };
      }),
    });

    wc.on("did-start-loading", update(() => { s.loading = true; }));
    wc.on("did-stop-loading", update(() => {
      s.loading = false;
      this.readNavigation(tab);
    }));
    wc.on("did-start-navigation", update((details) => {
      if (details.isMainFrame && !details.isSameDocument) s.notice = null;
    }));
    wc.on("did-navigate", update((_event, url) => {
      s.url = url;
      this.readNavigation(tab);
      this.remember(url, wc.getTitle());
    }));
    wc.on("did-navigate-in-page", update((_event, url, isMainFrame) => {
      if (!isMainFrame) return;
      s.url = url;
      this.readNavigation(tab);
    }));
    wc.on("page-title-updated", update((_event, title) => {
      s.title = title;
      if (s.url) this.remember(s.url, title);
    }));
    wc.on("did-fail-load", update((_event, code, description, url, isMainFrame) => {
      if (!isMainFrame || code === ERR_ABORTED) return;
      s.notice = { kind: "load-failed", url, message: `${description} (${code})` };
    }));
    wc.on("render-process-gone", update((_event, details) => {
      s.loading = false;
      s.notice = {
        kind: "load-failed",
        url: s.url || "",
        message: `The page stopped working (${details.reason}). Reload to try again.`,
      };
    }));
    // Chromium refuses file: links from web pages before will-navigate fires; its console line is the only sign.
    wc.on("console-message", (event) => {
      const refused = /^Not allowed to load local resource: (\S+)/.exec(event.message || "");
      if (!refused) return;
      update(() => {
        s.notice = { kind: "blocked", url: refused[1], message: classifyNavigation(refused[1], { blockedPorts: [] }).message };
      })();
    });
    wc.on("devtools-opened", update(() => { s.devToolsOpen = true; }));
    wc.on("devtools-closed", update(() => { s.devToolsOpen = false; }));
    wc.on("before-input-event", (event, input) => {
      if (!isToggleShortcut(input)) return;
      event.preventDefault();
      this.deps.onToggleRequest();
    });
  }

  /** Put a visited page at the top of the new tab page's list. */
  remember(url, title) {
    if (!isWebUrl(url)) return;
    const rest = this.recent.filter((p) => p.url !== url);
    this.recent = [{ url, title: title || url }, ...rest].slice(0, RECENT_LIMIT);
    this.deps.recentStore.save(this.recent);
  }

  /** Called by the policy when a page asked for a permission it was refused. */
  notePermissionDenied(webContents, permission, origin) {
    for (const tab of this.tabs.values()) {
      if (tab.view && tab.view.webContents === webContents) {
        tab.state.notice = { kind: "permission-denied", permission, origin };
        this.publish();
        return;
      }
    }
  }
}

module.exports = {
  DEVTOOLS_MODE,
  PANE_PARTITION,
  PaneHost,
  isToggleShortcut,
  toViewBounds,
};
