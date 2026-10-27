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

const { WebContentsView, nativeImage } = require("electron");
const { attachTabPolicy, classifyNavigation, isApprovalFreeHost } = require("./navigation-policy.cjs");
const { TabCdp } = require("./cdp-session.cjs");

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
// Size a page lays out at before the pane has ever been shown; hidden, it still captures.
const HIDDEN_BOUNDS = { x: 0, y: 0, width: 1024, height: 768 };
// Keeps "GAIA is reading this page" up long enough to be seen between quick reads.
const BUSY_LINGER_MS = 1500;
// Lets a script redirect fire after load, so the agent reads where the page settled.
const SETTLE_MS = 400;
const LOAD_TIMEOUT_MS = 30000;
const EVAL_TIMEOUT_MS = 15000;

/** An agent op's failure, with a code from BridgeErrorCode and a message written for the model. */
class BridgeError extends Error {
  /**
   * @param {import("./contract").BridgeErrorCode} code
   * @param {string} message
   * @param {Record<string, unknown>} [data]
   */
  constructor(code, message, data = {}) {
    super(message);
    this.code = code;
    this.data = data;
  }
}

/** True when an agent op may read or land on `url`: loopback, or an origin the user approved. */
function inScope(url, allowedOrigins) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.href === "about:blank") return true;
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
  return isApprovalFreeHost(parsed.hostname) || allowedOrigins.includes(parsed.origin);
}

function originOf(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.origin : null;
  } catch {
    return null;
  }
}

function withTimeout(promise, ms, what) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new BridgeError("timeout", `${what} took longer than ${ms / 1000} s.`)), ms);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

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
   *   onShowRequest: () => void,
   *   openExternal: (url: string) => void,
   *   createCdp?: (webContents: Electron.WebContents) => TabCdp,
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
    /** The chat session the window shows, reported by the renderer; the bridge's attach answers from it. */
    this.activeSessionId = null;
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
    this.load(tab, url).catch((err) => {
      // did-fail-load puts the failure on the tab; this only keeps the rejection handled.
      console.warn(`[browser-pane] ${url} did not finish loading: ${err.message}`);
    });
  }

  /** Start loading an already-allowed URL; resolves when the page loads, rejects if it doesn't. */
  load(tab, url) {
    const view = this.ensureView(tab);
    tab.state.notice = null;
    tab.state.url = url;
    const loading = view.webContents.loadURL(url);
    this.publish();
    return loading;
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

  // ── Agent ops (the bridge) ────────────────────────────────────────────
  //
  // Every op that loads or reads a page takes allowedOrigins: loopback plus
  // the origins Python's site gate approved for that one call. A page on any
  // other origin is never read, and a navigation that would land on one
  // while the op runs is stopped.

  /** @param {string | null} sessionId */
  setActiveSession(sessionId) {
    this.activeSessionId = sessionId;
  }

  /** @param {string} sessionId */
  attachSession(sessionId) {
    if (this.disposed || sessionId !== this.activeSessionId) return { status: "not_in_desktop" };
    return { status: "attached", tabId: this.activeTabId, paneVisible: this.isShown() };
  }

  /** @param {string} sessionId */
  requireSession(sessionId) {
    if (sessionId !== this.activeSessionId) {
      throw new BridgeError(
        "not_in_desktop",
        "The user switched GAIA desktop to another chat, so this chat can't use the browser pane now. Ask the user to switch back to this chat."
      );
    }
  }

  listTabs() {
    return {
      tabs: [...this.tabs.values()].map(({ state }) => ({
        id: state.id,
        url: state.url,
        title: state.title,
        active: state.id === this.activeTabId,
        openedBy: state.openedBy,
      })),
      activeTabId: this.activeTabId,
    };
  }

  /** @param {TabId | null} id null means the tab the user is looking at. */
  agentTab(id) {
    const tab = this.tabs.get(id === null ? this.activeTabId : id);
    if (!tab) throw new BridgeError("no_such_tab", `No browser pane tab "${id}"; it may have been closed.`);
    return tab;
  }

  requirePage(tab, allowedOrigins) {
    const wc = tab.view && tab.view.webContents;
    if (!wc || tab.state.url === null) {
      throw new BridgeError("no_page", "The browser pane shows a new tab page with no web page in it. Open one with browser_open(url).");
    }
    const url = wc.getURL() || tab.state.url;
    if (!inScope(url, allowedOrigins)) {
      throw new BridgeError("off_scope", `The page is on ${originOf(url) || url}, which this call was not approved to read.`, {
        origin: originOf(url) || url,
      });
    }
    return wc;
  }

  /** Run one agent op on a tab: shows the strip, and stops navigations off the allowed origins. */
  async withAgent(tab, allowedOrigins, fn) {
    tab.busy = (tab.busy || 0) + 1;
    clearTimeout(tab.busyTimer);
    tab.state.agentBusy = true;
    tab.scope = { allowed: allowedOrigins, blocked: null };
    this.publish();
    try {
      const result = await fn();
      if (tab.scope.blocked) throw this.blockedError(tab.scope.blocked);
      return result;
    } finally {
      tab.scope = null;
      tab.busy -= 1;
      if (tab.busy === 0) {
        tab.busyTimer = setTimeout(() => {
          if (tab.busy !== 0 || !this.tabs.has(tab.state.id)) return;
          tab.state.agentBusy = false;
          this.publish();
        }, BUSY_LINGER_MS);
      }
    }
  }

  blockedError(url) {
    const origin = originOf(url) || url;
    return new BridgeError("blocked_navigation", `Stopped the page from going to ${url}; ${origin} was not approved.`, { url, origin });
  }

  /** @param {string} sessionId @param {string} url @param {string[]} allowedOrigins */
  async agentOpen(sessionId, url, allowedOrigins) {
    this.checkAgentUrl(url, allowedOrigins);
    const id = this.addTab();
    const tab = this.requireTab(id);
    tab.state.openedBy = sessionId;
    this.activeTabId = id;
    this.syncViews();
    this.deps.onShowRequest();
    await this.withAgent(tab, allowedOrigins, () => this.agentLoad(tab, url));
    return this.listTabs();
  }

  /**
   * @param {TabId | null} id
   * @param {string} to A URL, or back, forward or reload.
   * @param {string[]} allowedOrigins
   */
  async agentNavigate(id, to, allowedOrigins) {
    const tab = this.agentTab(id);
    if (to === "back" || to === "forward" || to === "reload") {
      if (!tab.view) throw new BridgeError("no_page", "This tab has no page yet, so it has no history.");
      const wc = tab.view.webContents;
      const nav = wc.navigationHistory;
      if (to === "back" && !nav.canGoBack()) throw new BridgeError("navigation_failed", "There is no earlier page in this tab.", { url: wc.getURL() });
      if (to === "forward" && !nav.canGoForward()) throw new BridgeError("navigation_failed", "There is no later page in this tab.", { url: wc.getURL() });
      await this.withAgent(tab, allowedOrigins, async () => {
        const done = this.nextLoad(wc);
        this.history(tab.state.id, to);
        await done;
        await this.settle(wc);
      });
    } else {
      this.checkAgentUrl(to, allowedOrigins);
      this.deps.onShowRequest();
      await this.withAgent(tab, allowedOrigins, () => this.agentLoad(tab, to));
    }
    const wc = tab.view.webContents;
    return { tabId: tab.state.id, url: wc.getURL(), title: wc.getTitle() };
  }

  checkAgentUrl(url, allowedOrigins) {
    const verdict = classifyNavigation(url, { blockedPorts: this.deps.blockedPorts() });
    if (!verdict.allowed) throw new BridgeError("blocked_url", verdict.message, { url });
    if (!inScope(url, allowedOrigins)) throw this.blockedError(url);
  }

  async agentLoad(tab, url) {
    try {
      await withTimeout(this.load(tab, url), LOAD_TIMEOUT_MS, `Loading ${url}`);
    } catch (err) {
      if (tab.scope && tab.scope.blocked) throw this.blockedError(tab.scope.blocked);
      if (err instanceof BridgeError) throw err;
      throw new BridgeError("navigation_failed", err.message, { url });
    }
    await this.settle(tab.view.webContents);
  }

  /** Resolves when the next navigation in `wc` finishes, or rejects after LOAD_TIMEOUT_MS. */
  nextLoad(wc) {
    let done;
    const finished = new Promise((resolve) => {
      done = resolve;
      wc.once("did-stop-loading", done);
      // A same-document history step never starts loading, so it never stops either.
      wc.once("did-navigate-in-page", done);
    });
    return withTimeout(finished, LOAD_TIMEOUT_MS, "The page").finally(() => {
      wc.removeListener("did-stop-loading", done);
      wc.removeListener("did-navigate-in-page", done);
    });
  }

  /** Wait briefly for a script redirect, and for its load if one starts. */
  async settle(wc) {
    let started = false;
    const onStart = (details) => {
      if (details && details.isMainFrame && !details.isSameDocument) started = true;
    };
    wc.on("did-start-navigation", onStart);
    await new Promise((resolve) => setTimeout(resolve, SETTLE_MS));
    wc.removeListener("did-start-navigation", onStart);
    if (started && wc.isLoading()) await this.nextLoad(wc).catch(() => {});
  }

  /**
   * Run a script in the page's frames. The script is Python's own (the
   * snapshot or find script), never text from the model.
   */
  async agentEvaluate(id, script, args, frames, allowedOrigins) {
    const tab = this.agentTab(id);
    const wc = this.requirePage(tab, allowedOrigins);
    return this.withAgent(tab, allowedOrigins, async () => {
      const main = wc.mainFrame;
      const targets = frames === "all" ? [main, ...main.framesInSubtree.filter((f) => f !== main)] : [main];
      const code = `(${script})(${JSON.stringify(args)})`;
      const results = [];
      for (const frame of targets) {
        const frameId = frame === main ? null : `${frame.processId}:${frame.routingId}`;
        try {
          const value = await withTimeout(frame.executeJavaScript(code, false), EVAL_TIMEOUT_MS, "Reading the page");
          results.push({ frameId, url: frame.url, ok: true, value });
        } catch (err) {
          results.push({ frameId, url: frame.url, ok: false, error: err.message });
        }
      }
      this.requirePage(tab, allowedOrigins);
      return { tabId: tab.state.id, url: wc.getURL(), frames: results };
    });
  }

  /** @param {TabId | null} id */
  async agentOrigin(id) {
    const tab = this.agentTab(id);
    const url = tab.view ? tab.view.webContents.getURL() || tab.state.url : tab.state.url;
    const origin = url ? originOf(url) : null;
    const hasCookies = origin ? (await this.deps.session.cookies.get({ url: origin })).length > 0 : false;
    return { tabId: tab.state.id, url, origin, hasCookies };
  }

  async agentScreenshot(id, clip, maxWidth, allowedOrigins) {
    const tab = this.agentTab(id);
    const wc = this.requirePage(tab, allowedOrigins);
    return this.withAgent(tab, allowedOrigins, async () => {
      let rect = null;
      if (clip) {
        if (clip.frameId !== null) {
          throw new BridgeError("unsupported", "A picture of an element inside a frame is not supported yet; take one of the whole page instead.");
        }
        rect = await wc.mainFrame.executeJavaScript(
          `(() => { const el = document.querySelector(${JSON.stringify(clip.selector)}); if (!el) return null;
            const r = el.getBoundingClientRect(); return { x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height }; })()`,
          false
        );
        if (!rect || rect.width < 1 || rect.height < 1) {
          throw new BridgeError("element_not_found", "That element is not on the page any more, or has no size. Call browser_snapshot() to get current refs.");
        }
      }
      let image;
      try {
        image = nativeImage.createFromBuffer(await withTimeout(tab.cdp.screenshot(rect), EVAL_TIMEOUT_MS, "Taking the picture"));
      } catch (err) {
        if (err instanceof BridgeError) throw err;
        throw new BridgeError("internal", `Could not take a picture of the page: ${err.message}`);
      }
      if (image.getSize().width > maxWidth) image = image.resize({ width: maxWidth });
      const size = image.getSize();
      this.requirePage(tab, allowedOrigins);
      return { png: image.toPNG().toString("base64"), width: size.width, height: size.height, url: wc.getURL() };
    });
  }

  async agentLogs(id, query, allowedOrigins) {
    const tab = this.agentTab(id);
    const wc = this.requirePage(tab, allowedOrigins);
    return this.withAgent(tab, allowedOrigins, async () => ({ ...tab.cdp.logs(query), url: wc.getURL() }));
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
        openedBy: null,
        agentBusy: false,
      },
      cdp: null,
      busy: 0,
      busyTimer: null,
      scope: null,
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
    if (!this.bounds) view.setBounds(HIDDEN_BOUNDS);
    tab.view = view;
    tab.cdp = (this.deps.createCdp || ((wc) => new TabCdp(wc)))(view.webContents);
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
      // While an agent op runs, the page may not wander to a site the user didn't approve.
      stopsAgent: (url) => {
        if (!tab.scope || inScope(url, tab.scope.allowed)) return false;
        tab.scope.blocked = url;
        return true;
      },
    });

    wc.on("did-start-loading", update(() => { s.loading = true; }));
    wc.on("did-stop-loading", update(() => {
      s.loading = false;
      this.readNavigation(tab);
    }));
    wc.on("did-start-navigation", update((details) => {
      if (!details.isMainFrame || details.isSameDocument) return;
      s.notice = null;
      tab.cdp.onNavigationStarted();
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
  BridgeError,
  DEVTOOLS_MODE,
  PANE_PARTITION,
  PaneHost,
  inScope,
  isToggleShortcut,
  toViewBounds,
};
