// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * PaneHost owns every tab of the browser pane for one app window. Each tab
 * with a page is a WebContentsView on its own partition, sandboxed and with
 * no preload. The renderer draws the toolbar and a placeholder; this class
 * keeps the native views on top of that placeholder and pushes the whole
 * PaneState to the renderer after every change.
 *
 * Every tab belongs to one chat: the chat shown when it opened, or the
 * new-chat screen (chat null) until its first message makes the chat. The
 * window shows one chat's tabs at a time, and an agent only ever reaches its
 * own chat's tabs. Other chats' pages stay alive, hidden, up to
 * LIVE_VIEW_CAP; past it the least recently shown chat's pages close and
 * load again from their saved history when that chat shows.
 *
 * Hidden always means view.setVisible(false). Offscreen or zero-size bounds
 * stall or freeze page capture, so they are never used to hide a view.
 */

"use strict";

const { WebContentsView, nativeImage } = require("electron");
const { attachTabPolicy, classifyNavigation, isApprovalFreeHost } = require("./navigation-policy.cjs");
const { TabCdp } = require("./cdp-session.cjs");
const { BridgeError } = require("./bridge-error.cjs");
const input = require("./input.cjs");
const { PickerSession } = require("./picker.cjs");

/** @typedef {import("./contract").TabId} TabId */
/** @typedef {import("./contract").ChatKey} ChatKey */
/** @typedef {import("./contract").TabState} TabState */
/** @typedef {import("./contract").PaneState} PaneState */
/** @typedef {import("./contract").RecentPage} RecentPage */
/** @typedef {import("./contract").CssRect} CssRect */
/** @typedef {import("./contract").HistoryAction} HistoryAction */

const PANE_PARTITION = "persist:gaia-browser-pane";
// Docks DevTools inside the tab's own view, so it stays in the pane and moves with it.
const DEVTOOLS_MODE = "bottom";
const RECENT_LIMIT = 8;
// Chats whose recent pages are kept; the least recently visited drop first.
const RECENT_CHATS = 50;
const ERR_ABORTED = -3;
// Size a page lays out at before the pane has ever been shown; hidden, it still captures.
const HIDDEN_BOUNDS = { x: 0, y: 0, width: 1024, height: 768 };
// Keeps "GAIA is reading this page" up long enough to be seen between quick reads.
const BUSY_LINGER_MS = 1500;
// Lets a script redirect fire after load, so the agent reads where the page settled.
const SETTLE_MS = 400;
const LOAD_TIMEOUT_MS = 30000;
const EVAL_TIMEOUT_MS = 15000;
const ACT_TIMEOUT_MS = 15000;
// After the user clicks or types in a tab, the agent's input waits this long there.
const USER_PAUSE_MS = 4000;
// "GAIA is using this tab" stays up this long after an action, covering the model's think time between steps.
const ACT_LINGER_MS = 10000;
// The page reports the agent's own CDP input back as input events for a moment after it lands.
const AGENT_ECHO_MS = 100;
const USER_INPUT = new Set(["mouseDown", "rawKeyDown", "keyDown", "touchStart"]);
const CONTROLS = new Set(["shared", "user", "private"]);
// Electron answers a page's alert, confirm and prompt in this internal webContents
// listener: a native box over the whole window, and prompt() always cancelled.
// The pane replaces it so the user answers in the pane and the agent can answer too.
// tests/electron/pane-dialogs.test.cjs pins the Electron versions this was read in.
const RUN_DIALOG = "-run-dialog";
const CANCEL_DIALOGS = "-cancel-dialogs";
const LEAVE_MESSAGE = "Leave this page? Changes you made may not be saved.";
// Accepting a "leave this page?" box for a navigation the page started allows the next leave this long.
const LEAVE_ONCE_MS = 30000;
const SCRIPT_TIMEOUT_MS = 5000;
const MAX_SCRIPT_RESULT = 4000;
const STOPPED_FOR_DIALOG = "A box opened on the page, so GAIA stopped this step there.";
// Pages kept alive across all chats. Each is a renderer process of roughly 50 to 150 MB,
// so 12 stays near 1 to 2 GB while holding a QA chat's tabs and a few chats besides.
const LIVE_VIEW_CAP = 12;

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

/** True for a page on this computer (localhost, 127.x, ::1, *.localhost): the only pages browser_eval runs on. */
function isLoopbackUrl(url) {
  try {
    const parsed = new URL(url);
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && isApprovalFreeHost(parsed.hostname);
  } catch {
    return false;
  }
}

/**
 * The page script browser_eval runs: the model's expression, awaited, as JSON
 * capped at MAX_SCRIPT_RESULT characters. Elements, functions and cycles are
 * written as short markers.
 */
function scriptSource(expression) {
  const body = expression.trim().replace(/;+$/, "");
  return `(async () => {
  let value;
  try {
    value = await (
${body}
    );
  } catch (e) {
    return { error: (e && e.name ? e.name + ": " : "") + ((e && e.message) || String(e)) };
  }
  const seen = new WeakSet();
  const json = JSON.stringify(value, (key, v) => {
    if (typeof v === "bigint") return v.toString();
    if (typeof v === "function") return "[function]";
    if (typeof Node !== "undefined" && v instanceof Node) return "[" + (v.nodeName || "node").toLowerCase() + " element]";
    if (typeof v === "object" && v !== null) {
      if (seen.has(v)) return "[already shown]";
      seen.add(v);
    }
    return v;
  });
  const text = json === undefined ? "undefined" : json;
  return { text: text.slice(0, ${MAX_SCRIPT_RESULT}), truncated: text.length > ${MAX_SCRIPT_RESULT} };
})()`;
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

function isShortcut(input, key) {
  return (
    input.type === "keyDown" &&
    (input.meta || input.control) &&
    input.shift &&
    !input.alt &&
    input.key.toLowerCase() === key
  );
}

/** True for the key that toggles the pane: Cmd or Ctrl, Shift and B. */
function isToggleShortcut(input) {
  return isShortcut(input, "b");
}

/** True for the key that starts or ends picking elements: Cmd or Ctrl, Shift and S. */
function isPickShortcut(input) {
  return isShortcut(input, "s");
}

/** A crop as a PNG data URL no wider than maxWidth. */
function encodeCrop(png, maxWidth) {
  let image = nativeImage.createFromBuffer(png);
  if (image.getSize().width > maxWidth) image = image.resize({ width: maxWidth });
  return image.toPNG().toString("base64");
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
   *   recentStore: { load: () => Map<ChatKey, RecentPage[]>, save: (pages: Map<ChatKey, RecentPage[]>) => void },
   *   goneChats: Set<string>,
   *   onToggleRequest: () => void,
   *   onShowRequest: (chat: string) => void,
   *   openExternal: (url: string) => void,
   *   onPicked: (event: import("./contract").PickEvent) => void,
   *   createCdp?: (webContents: Electron.WebContents) => TabCdp,
   *   createPicker?: (deps: ConstructorParameters<typeof PickerSession>[0]) => PickerSession,
   * }} deps
   */
  constructor(deps) {
    this.deps = deps;
    this.window = deps.window;
    /** Every chat's tabs; each record's `chat` says whose. */
    /** @type {Map<TabId, { chat: ChatKey, state: TabState, view: Electron.WebContentsView | null }>} */
    this.tabs = new Map();
    /** Each chat's own active tab. A chat with no tabs has no entry. */
    /** @type {Map<ChatKey, TabId>} */
    this.activeByChat = new Map();
    /** @type {ChatKey} The chat the window shows; the bridge's attach answers from it. */
    this.shown = null;
    /** Chats most recently shown first: whose pages to close first past LIVE_VIEW_CAP. */
    /** @type {ChatKey[]} */
    this.recency = [null];
    this.nextId = 1;
    this.visible = false;
    this.occluded = false;
    /** @type {Electron.Rectangle | null} */
    this.bounds = null;
    /** Each chat's recent pages, the chat visited last at the end. */
    /** @type {Map<ChatKey, RecentPage[]>} */
    this.recent = deps.recentStore.load();
    this.disposed = false;
    /** Chats the user deleted, shared by every window this app run; their ids never come back. */
    /** @type {Set<string>} */
    this.gone = deps.goneChats;
    /** @type {{ tabId: TabId, session: PickerSession } | null} */
    this.picker = null;
    this.pickRuns = 0;
    this.nextPickRef = 1;
  }

  /** The tab the user sees: the shown chat's active tab, or null while that chat has none. */
  get activeTabId() {
    return this.activeByChat.get(this.shown) ?? null;
  }

  /** @returns {PaneState} */
  state() {
    return {
      chat: this.shown,
      visible: this.visible,
      occluded: this.occluded,
      tabs: this.chatTabs(this.shown).map((t) => ({ ...t.state })),
      activeTabId: this.activeTabId,
      recent: (this.recent.get(this.shown) ?? []).slice(),
      picking: this.picker !== null,
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

  /**
   * The chat the window shows and whether its pane is open, set together so
   * a switch never shows one chat's pages under another's open state. An
   * open pane on a chat with no tabs gets a new tab page.
   *
   * @param {ChatKey} chat
   * @param {boolean} visible
   */
  setView(chat, visible) {
    if (chat !== null) this.requireLiveChat(chat);
    const before = this.shown;
    if (chat !== before) {
      this.stopPicking();
      this.shown = chat;
      this.recency = [chat, ...this.recency.filter((c) => c !== chat)];
      if (before !== null) this.releaseDialogs(before);
      for (const tab of this.chatTabs(chat)) if (tab.saved) this.restoreView(tab);
    }
    this.visible = visible;
    if (!visible) this.stopPicking();
    if (visible && this.activeTabId === null) this.activeByChat.set(chat, this.addTab(chat));
    this.trimViews(chat);
    this.syncViews();
    this.publish();
  }

  /**
   * The new-chat screen's first message made `sessionId`: its tabs, and the
   * screen itself if it still shows, become that chat's. Idempotent.
   *
   * @param {string} sessionId
   */
  claimDraft(sessionId) {
    this.requireLiveChat(sessionId);
    const moved = this.chatTabs(null);
    for (const tab of moved) tab.chat = sessionId;
    const draftActive = this.activeByChat.get(null);
    this.activeByChat.delete(null);
    if (draftActive !== undefined && !this.activeByChat.has(sessionId)) this.activeByChat.set(sessionId, draftActive);
    const draftPages = this.recent.get(null);
    if (draftPages) {
      this.recent.delete(null);
      for (const page of [...draftPages].reverse()) this.remember(sessionId, page.url, page.title);
    }
    if (this.shown === null) {
      this.shown = sessionId;
      this.recency = [sessionId, ...this.recency.filter((c) => c !== sessionId)];
    }
    this.syncViews();
    this.publish();
  }

  /**
   * The user deleted the chat: close its tabs, and refuse its id from now on.
   *
   * @param {string} sessionId
   */
  forgetChat(sessionId) {
    this.gone.add(sessionId);
    for (const tab of this.chatTabs(sessionId)) this.dropTab(tab);
    this.activeByChat.delete(sessionId);
    this.recency = this.recency.filter((c) => c !== sessionId);
    if (this.recent.delete(sessionId)) this.deps.recentStore.save(this.recent);
    this.syncViews();
    this.publish();
  }

  /** @param {string} chat */
  requireLiveChat(chat) {
    if (this.gone.has(chat)) throw new Error(`Chat ${chat} was deleted, so the browser pane has no tabs for it.`);
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
    const tab = this.shownTab();
    const view = tab && tab.view;
    if (!occluded || !wasShown || !view) return null;
    // A hidden view still captures a fresh frame, so hiding first keeps the overlay clear.
    const image = await view.webContents.capturePage();
    return image.toDataURL();
  }

  // ── Tabs ──────────────────────────────────────────────────────────────

  /**
   * A new tab in the shown chat, from the user.
   *
   * @param {string | null} url null opens a new tab page.
   * @returns {TabId}
   */
  openTab(url) {
    return this.openTabIn(this.shown, url);
  }

  /**
   * A new tab that becomes `chat`'s active tab. A popup opens in its opener's chat.
   *
   * @param {ChatKey} chat
   * @param {string | null} url
   * @returns {TabId}
   */
  openTabIn(chat, url) {
    if (chat !== null) this.requireLiveChat(chat);
    if (chat === this.shown) this.stopPicking();
    const id = this.addTab(chat);
    this.activeByChat.set(chat, id);
    if (url !== null) this.navigateTab(this.requireTab(id), url);
    this.syncViews();
    this.publish();
    return id;
  }

  /** @param {TabId} id */
  closeTab(id) {
    const tab = this.userTab(id);
    const { chat } = tab;
    const order = this.chatTabs(chat).map((t) => t.state.id);
    const index = order.indexOf(id);
    this.dropTab(tab);
    const rest = order.filter((t) => t !== id);
    if (rest.length === 0) {
      // Closing the chat's last tab leaves a new tab page, never an empty open pane.
      this.activeByChat.set(chat, this.addTab(chat));
    } else if (this.activeByChat.get(chat) === id) {
      this.activeByChat.set(chat, rest[Math.min(index, rest.length - 1)]);
    }
    this.syncViews();
    this.publish();
  }

  /** @param {TabId} id */
  selectTab(id) {
    const tab = this.userTab(id);
    this.activate(tab);
  }

  /** Make `tab` its chat's active tab. */
  activate(tab) {
    if (tab.chat === this.shown && tab.state.id !== this.activeTabId) this.stopPicking();
    this.activeByChat.set(tab.chat, tab.state.id);
    this.syncViews();
    this.publish();
  }

  /**
   * @param {TabId} id
   * @param {string} url Already a full URL; the address bar normalizes what was typed.
   */
  navigate(id, url) {
    this.navigateTab(this.userTab(id), url);
  }

  navigateTab(tab, url) {
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
    tab.leaving = () => this.load(tab, url);
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
    this.historyStep(this.userTab(id), action);
  }

  historyStep(tab, action) {
    if (!tab.view) throw new Error(`Tab ${tab.state.id} has no page to ${action}; open a page in it first.`);
    const wc = tab.view.webContents;
    if (action !== "stop") tab.leaving = () => this.historyLoad(tab, action);
    if (action === "back") wc.navigationHistory.goBack();
    else if (action === "forward") wc.navigationHistory.goForward();
    else if (action === "reload") wc.reload();
    else if (action === "stop") wc.stop();
    else throw new Error(`Unknown history action "${action}"; use back, forward, reload or stop.`);
  }

  /** Repeat a history step that "leave this page?" stopped, and wait for its load. */
  historyLoad(tab, action) {
    const done = this.nextLoad(tab.view.webContents);
    this.historyStep(tab, action);
    return done;
  }

  /** @param {TabId} id */
  toggleDevTools(id) {
    const tab = this.userTab(id);
    if (!tab.view) throw new Error(`Tab ${id} has no page to inspect; open a page in it first.`);
    const wc = tab.view.webContents;
    if (wc.isDevToolsOpened()) wc.closeDevTools();
    else wc.openDevTools({ mode: DEVTOOLS_MODE });
  }

  /**
   * Who may drive a tab: shared with the agent, held by the user (the agent
   * is paused there), or private (the agent can't see it at all).
   *
   * @param {TabId} id
   * @param {import("./contract").TabControl} control
   */
  setControl(id, control) {
    if (!CONTROLS.has(control)) throw new Error(`Unknown tab control "${control}"; use shared, user or private.`);
    const tab = this.userTab(id);
    tab.state.control = control;
    // Resume means go now, not after the last click's pause runs out.
    if (control === "shared") tab.userInputAt = 0;
    this.publish();
  }

  /**
   * The user's Responsive choice. While it is set, an agent viewport request
   * is reported back to the agent and not applied.
   *
   * @param {TabId} id
   * @param {import("./contract").ViewportChoice | null} choice null fills the pane again.
   */
  async setUserViewport(id, choice) {
    const tab = this.userTab(id);
    tab.state.viewport = choice ? { ...choice, source: "user" } : null;
    this.publish();
    await this.applyViewport(tab);
  }

  /** Clears the pane's cookies, storage, cache, tab histories and recent pages. Nothing outside the pane. */
  async clearBrowsingData() {
    const ses = this.deps.session;
    await Promise.all([ses.clearStorageData(), ses.clearCache(), ses.clearAuthCache()]);
    this.recent.clear();
    this.deps.recentStore.save(this.recent);
    for (const tab of this.tabs.values()) {
      if (tab.saved) {
        const current = tab.saved.entries[tab.saved.index];
        tab.saved = { entries: [{ url: current.url, title: current.title }], index: 0 };
        tab.state.canGoBack = false;
        tab.state.canGoForward = false;
      }
      if (!tab.view) continue;
      tab.view.webContents.navigationHistory.clear();
      this.readNavigation(tab);
    }
    this.publish();
  }

  /** @param {TabId} id */
  openExternal(id) {
    const { url } = this.userTab(id).state;
    if (url === null || !isWebUrl(url)) {
      throw new Error(`Tab ${id} has no web page to open in the system browser.`);
    }
    this.deps.openExternal(url);
  }

  // ── Element picker ────────────────────────────────────────────────────

  /** Start picking elements on the tab the user is looking at, or end it if it runs. */
  async togglePicking() {
    if (this.picker) {
      this.stopPicking();
      return;
    }
    const tab = this.shownTab();
    if (!tab || !tab.view || tab.state.url === null) {
      throw new Error("There is no page to pick from: open a page in the browser pane first.");
    }
    const wc = tab.view.webContents;
    const run = ++this.pickRuns;
    const session = (this.deps.createPicker || ((d) => new PickerSession(d)))({
      cdp: tab.cdp,
      wc,
      allocateRef: () => `p${this.nextPickRef++}`,
      encodeCrop,
      onSelection: (elements) => this.deps.onPicked({ run, elements }),
      onEnd: () => {
        if (this.picker && this.picker.session === session) {
          this.picker = null;
          this.publish();
        }
      },
    });
    this.picker = { tabId: tab.state.id, session };
    this.publish();
    try {
      await session.start();
    } catch (err) {
      this.picker = null;
      this.publish();
      throw new Error(`Could not start picking elements on ${tab.state.url}: ${err.message}`);
    }
    // Arrow keys and Escape reach the picker only when the page has focus.
    wc.focus();
  }

  /** End picking, keeping the chips already made. */
  stopPicking() {
    if (!this.picker) return;
    const { session } = this.picker;
    this.picker = null;
    this.publish();
    session.stop().catch((err) => console.error(`[browser-pane] Ending the element picker failed: ${err.message}`));
  }

  // ── Agent ops (the bridge) ────────────────────────────────────────────
  //
  // Every op that loads or reads a page takes allowedOrigins: loopback plus
  // the origins Python's site gate approved for that one call. A page on any
  // other origin is never read, and a navigation that would land on one
  // while the op runs is stopped.
  //
  // Every op names its chat (sessionId) and only ever finds that chat's
  // tabs: another chat's tab id is "no such tab", like a closed one.

  /** @param {string} sessionId */
  attachSession(sessionId) {
    if (this.disposed || sessionId !== this.shown || this.gone.has(sessionId)) return { status: "not_in_desktop" };
    return { status: "attached", tabId: this.activeTabId, paneVisible: this.isShown() };
  }

  /** @param {string} sessionId */
  requireSession(sessionId) {
    if (sessionId !== this.shown || this.gone.has(sessionId)) {
      throw new BridgeError(
        "not_in_desktop",
        "The user switched GAIA desktop to another chat, so this chat can't use the browser pane now. Ask the user to switch back to this chat."
      );
    }
  }

  /**
   * The tabs the agent may know about: its own chat's, without private ones; an active private tab reads as none.
   *
   * @param {string} sessionId
   */
  listTabs(sessionId) {
    const active = this.activeByChat.get(sessionId) ?? null;
    const visible = this.chatTabs(sessionId).filter(({ state }) => state.control !== "private");
    return {
      tabs: visible.map(({ state }) => ({
        id: state.id,
        url: state.url,
        title: state.title,
        active: state.id === active,
        openedBy: state.openedBy,
        control: state.control,
      })),
      activeTabId: visible.some(({ state }) => state.id === active) ? active : null,
    };
  }

  /**
   * @param {string} sessionId
   * @param {TabId | null} id null means the chat's active tab, the one the user sees.
   */
  agentTab(sessionId, id) {
    const active = this.activeByChat.get(sessionId);
    if (id === null && active === undefined) {
      throw new BridgeError("no_page", "This chat has no tab in the browser pane yet. Open one with browser_open(url).");
    }
    const tab = this.tabs.get(id === null ? active : id);
    if (tab && tab.state.control === "private" && id === null) {
      throw new BridgeError(
        "private_tab",
        "The user marked the tab they are looking at private, so GAIA can't see or use it. Open your own tab with browser_open(url), or ask the user to switch tabs."
      );
    }
    // Another chat's tab and a private tab are invisible to the agent, so naming one gets the same answer as a closed tab.
    if (!tab || tab.chat !== sessionId || tab.state.control === "private") throw new BridgeError("no_such_tab", `No browser pane tab "${id}"; it may have been closed.`);
    return tab;
  }

  /** Agent input lands only in a tab the user has not taken over and has not touched in the last few seconds. */
  requireAgentInput(tab) {
    if (tab.state.control === "user") {
      throw new BridgeError(
        "user_active",
        "FAILED: this step was not done. The user took control of this browser tab, so GAIA is paused there. Stop using the browser now. Tell the user which steps are not done, and ask how to continue (they can press Resume in the browser pane)."
      );
    }
    if (Date.now() - tab.userInputAt < USER_PAUSE_MS) {
      throw new BridgeError(
        "user_active",
        "FAILED: this step was not done. The user is clicking or typing in this browser tab right now. Stop using the browser now. Tell the user which steps are not done, and ask how to continue."
      );
    }
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

  /**
   * Run one agent op on a tab: shows the strip, and stops navigations off the
   * allowed origins. An acting op shows "GAIA is using this tab", which stays
   * up between the agent's steps; touching the page then takes the tab over.
   */
  async withAgent(tab, allowedOrigins, fn, { acting = false } = {}) {
    tab.busy = (tab.busy || 0) + 1;
    clearTimeout(tab.busyTimer);
    tab.state.agentBusy = true;
    if (acting) {
      clearTimeout(tab.actTimer);
      tab.state.agentActing = true;
    }
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
        tab.busyTimer.unref?.();
      }
      if (acting) {
        tab.actTimer = setTimeout(() => {
          if (!this.tabs.has(tab.state.id)) return;
          tab.state.agentActing = false;
          this.publish();
        }, ACT_LINGER_MS);
        tab.actTimer.unref?.();
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
    const id = this.addTab(sessionId);
    const tab = this.requireTab(id);
    tab.state.openedBy = sessionId;
    if (sessionId === this.shown) this.stopPicking();
    this.activeByChat.set(sessionId, id);
    this.syncViews();
    this.deps.onShowRequest(sessionId);
    await this.withAgent(tab, allowedOrigins, () => this.agentLoad(tab, url));
    return this.listTabs(sessionId);
  }

  /** browser_tabs(switch_to): show one of the chat's own tabs. */
  agentSelect(sessionId, id) {
    this.activate(this.agentTab(sessionId, id));
    return this.listTabs(sessionId);
  }

  /**
   * @param {string} sessionId
   * @param {TabId | null} id
   * @param {string} to A URL, or back, forward or reload.
   * @param {string[]} allowedOrigins
   */
  async agentNavigate(sessionId, id, to, allowedOrigins) {
    const tab = this.agentTab(sessionId, id);
    this.requireAgentInput(tab);
    this.requireNoDialog(tab);
    if (to === "back" || to === "forward" || to === "reload") {
      if (!tab.view) throw new BridgeError("no_page", "This tab has no page yet, so it has no history.");
      const wc = tab.view.webContents;
      const nav = wc.navigationHistory;
      if (to === "back" && !nav.canGoBack()) throw new BridgeError("navigation_failed", "There is no earlier page in this tab.", { url: wc.getURL() });
      if (to === "forward" && !nav.canGoForward()) throw new BridgeError("navigation_failed", "There is no later page in this tab.", { url: wc.getURL() });
      await this.withAgent(tab, allowedOrigins, async () => {
        const done = this.nextLoad(wc);
        this.historyStep(tab, to);
        const went = await this.raceDialog(tab, done);
        if (went.dialog) throw this.dialogError(went.dialog);
        await this.settle(wc);
      });
    } else {
      this.checkAgentUrl(to, allowedOrigins);
      this.deps.onShowRequest(sessionId);
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
      const loaded = await this.raceDialog(tab, withTimeout(this.load(tab, url), LOAD_TIMEOUT_MS, `Loading ${url}`));
      if (loaded.dialog) throw this.dialogError(loaded.dialog);
    } catch (err) {
      if (tab.scope && tab.scope.blocked) throw this.blockedError(tab.scope.blocked);
      // "Leave this page?" stops the load first; the box is the answer, not the failed load.
      if (tab.state.dialog) throw this.dialogError(tab.state.dialog);
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
  async agentEvaluate(sessionId, id, script, args, frames, allowedOrigins) {
    const tab = this.agentTab(sessionId, id);
    const wc = this.requirePage(tab, allowedOrigins);
    this.requireNoDialog(tab);
    return this.withAgent(tab, allowedOrigins, async () => {
      const main = wc.mainFrame;
      const targets = frames === "all" ? [main, ...main.framesInSubtree.filter((f) => f !== main)] : [main];
      const code = `(${script})(${JSON.stringify(args)})`;
      const results = [];
      for (const frame of targets) {
        const frameId = frame === main ? null : `${frame.processId}:${frame.routingId}`;
        try {
          const read = await this.raceDialog(tab, withTimeout(frame.executeJavaScript(code, false), EVAL_TIMEOUT_MS, "Reading the page"));
          if (read.dialog) throw this.dialogError(read.dialog);
          results.push({ frameId, url: frame.url, ok: true, value: read.value });
        } catch (err) {
          if (err instanceof BridgeError && err.code === "dialog_open") throw err;
          results.push({ frameId, url: frame.url, ok: false, error: err.message });
        }
      }
      this.requirePage(tab, allowedOrigins);
      return { tabId: tab.state.id, url: wc.getURL(), frames: results };
    });
  }

  /** @param {string} sessionId @param {TabId | null} id */
  async agentOrigin(sessionId, id) {
    const tab = this.agentTab(sessionId, id);
    const url = tab.view ? tab.view.webContents.getURL() || tab.state.url : tab.state.url;
    const origin = url ? originOf(url) : null;
    const hasCookies = origin ? (await this.deps.session.cookies.get({ url: origin })).length > 0 : false;
    return { tabId: tab.state.id, url, origin, hasCookies };
  }

  async agentScreenshot(sessionId, id, clip, maxWidth, allowedOrigins) {
    const tab = this.agentTab(sessionId, id);
    const wc = this.requirePage(tab, allowedOrigins);
    this.requireNoDialog(tab);
    return this.withAgent(tab, allowedOrigins, async () => {
      let rect = null;
      if (clip) {
        const ctx = input.context(tab.cdp);
        const box = await input.elementBox(ctx, input.frameFor(wc, clip.frameId), clip.selector);
        // The clip is in page coordinates: the box is in the viewport, so add how far the page scrolled.
        const scrolled = await ctx.exec(wc.mainFrame, "({ x: scrollX, y: scrollY })");
        rect = { ...box, x: box.x + scrolled.x, y: box.y + scrolled.y };
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

  async agentLogs(sessionId, id, query, allowedOrigins) {
    const tab = this.agentTab(sessionId, id);
    const wc = this.requirePage(tab, allowedOrigins);
    return this.withAgent(tab, allowedOrigins, async () => ({ ...tab.cdp.logs(query), url: wc.getURL() }));
  }

  /**
   * The only way the agent changes a page: a click, typing, or a key, as
   * trusted input (input.cjs). Refused in a tab the user holds or has just
   * touched. Waits for a navigation the action starts, and follows a popup
   * the pane opened as a new tab.
   *
   * @param {string} sessionId
   * @param {TabId | null} id
   * @param {import("./contract").ActAction} action
   * @param {import("./contract").ActTarget | null} target
   * @param {string[]} allowedOrigins
   */
  async agentAct(sessionId, id, action, target, allowedOrigins) {
    const tab = this.agentTab(sessionId, id);
    this.requireAgentInput(tab);
    const wc = this.requirePage(tab, allowedOrigins);
    this.requireNoDialog(tab);
    const frame = target ? input.frameFor(wc, target.frameId) : null;
    // Input in a frame lands on that frame's site, which needs approval like a page on it.
    if (frame && frame !== wc.mainFrame && !inScope(input.frameUrl(frame), allowedOrigins)) {
      const origin = originOf(input.frameUrl(frame)) || frame.url;
      throw new BridgeError("off_scope", `That element is in a frame from ${origin}, which this call was not approved to use.`, { origin });
    }
    this.deps.onShowRequest(sessionId);
    const before = this.activeByChat.get(sessionId);
    return this.withAgent(
      tab,
      allowedOrigins,
      async () => {
        const stop = { reason: null };
        tab.dispatching = true;
        let done;
        try {
          const acting = input.perform({ ctx: input.context(tab.cdp, stop), action, frame, selector: target && target.selector });
          done = await this.raceDialog(tab, withTimeout(acting, ACT_TIMEOUT_MS, "Acting on the page"));
        } catch (err) {
          if (err instanceof BridgeError) throw err;
          throw new BridgeError("internal", `Could not act on the page: ${err.message}`);
        } finally {
          tab.dispatching = false;
          tab.agentEchoUntil = Date.now() + AGENT_ECHO_MS;
        }
        // The page waits on the box, so the rest of this action must never land after it is answered.
        if (done.dialog) stop.reason = STOPPED_FOR_DIALOG;
        else await this.settle(wc);
        const reply = { tabId: tab.state.id, url: wc.getURL(), title: wc.getTitle() };
        const after = this.activeByChat.get(sessionId);
        if (after !== before && after !== undefined) reply.adoptedTabId = after;
        if (tab.state.dialog) reply.dialog = tab.state.dialog;
        return reply;
      },
      { acting: true }
    );
  }

  /**
   * Run the model's JavaScript expression in the page's main world and
   * return its value as JSON. Only on a page on this computer, and only in a
   * tab the agent may act in; anything else is refused, never asked about.
   *
   * @param {string} sessionId
   * @param {TabId | null} id
   * @param {string} expression
   */
  async agentScript(sessionId, id, expression) {
    const tab = this.agentTab(sessionId, id);
    this.requireAgentInput(tab);
    const wc = tab.view && tab.view.webContents;
    const url = (wc && wc.getURL()) || tab.state.url;
    if (!wc || url === null) {
      throw new BridgeError("no_page", "The browser pane shows a new tab page with no web page in it. Open one with browser_open(url).");
    }
    if (!isLoopbackUrl(url)) {
      throw new BridgeError(
        "unsupported",
        `browser_eval runs only on the user's own app on this computer (localhost), and this page is on ${originOf(url) || url}. Read it with browser_snapshot instead.`
      );
    }
    this.requireNoDialog(tab);
    return this.withAgent(
      tab,
      [],
      async () => {
        const reply = { tabId: tab.state.id, url: wc.getURL() };
        let ran;
        try {
          ran = await this.raceDialog(tab, withTimeout(wc.mainFrame.executeJavaScript(scriptSource(expression), false), SCRIPT_TIMEOUT_MS, "The script"));
        } catch (err) {
          return { ...reply, ok: false, error: err.message };
        }
        if (ran.dialog) return { ...reply, ok: false, error: STOPPED_FOR_DIALOG, dialog: ran.dialog };
        if (ran.value.error !== undefined) return { ...reply, ok: false, error: ran.value.error };
        return { ...reply, ok: true, json: ran.value.text, truncated: ran.value.truncated };
      },
      { acting: true }
    );
  }

  // ── Page boxes (alert, confirm, prompt, "leave this page?") ───────────
  //
  // One box at most per tab, held in tab.state.dialog until someone answers
  // it. The user answers in the pane; the agent answers with browser_dialog.
  // A box the agent's own action opened is dismissed when its turn ends, so
  // the page is never left waiting on nobody.

  /** @param {any} tab */
  requireNoDialog(tab) {
    if (tab.state.dialog) throw this.dialogError(tab.state.dialog);
  }

  dialogError(dialog) {
    return new BridgeError("dialog_open", `Nothing was done: the page is showing a ${dialog.type} box ("${dialog.message}"). Answer it first.`, { dialog });
  }

  /** Waits on `work`, or on a box opening in `tab`, whichever comes first. */
  async raceDialog(tab, work) {
    let opened;
    const shown = new Promise((resolve) => {
      opened = resolve;
    });
    tab.dialogWaiters.add(opened);
    try {
      const first = await Promise.race([work.then((value) => ({ value, dialog: null })), shown.then((dialog) => ({ value: undefined, dialog }))]);
      // The page is waiting on the box, so `work` finishes only once it is answered.
      if (first.dialog) work.catch(() => {});
      return first;
    } finally {
      tab.dialogWaiters.delete(opened);
    }
  }

  /**
   * @param {any} tab
   * @param {Omit<import("./contract").PageDialog, "openedBy">} dialog
   * @param {((accept: boolean, text: string) => void) | { leave: (() => Promise<unknown>) | null }} answer
   */
  openDialog(tab, dialog, answer) {
    const openedBy = tab.busy > 0 ? "agent" : "page";
    tab.state.dialog = { ...dialog, openedBy };
    tab.dialogAnswer = answer;
    for (const notify of tab.dialogWaiters) notify(tab.state.dialog);
    this.publish();
  }

  /** Forget the box: Chromium closed it (the page navigated or closed). */
  dropDialog(tab) {
    if (!tab.state.dialog) return;
    tab.state.dialog = null;
    tab.dialogAnswer = null;
    this.publish();
  }

  /**
   * Answer the tab's box. For "leave this page?", accepting repeats the
   * navigation that was stopped, when the pane started it.
   *
   * @returns {{ dialog: import("./contract").PageDialog, leaving: Promise<unknown> | null }}
   */
  answerDialog(tab, accept, text) {
    const dialog = tab.state.dialog;
    if (!dialog) {
      throw new BridgeError("not_actionable", "There is no box open on the page in the browser pane. Call browser_snapshot() to see the page.");
    }
    const answer = tab.dialogAnswer;
    this.dropDialog(tab);
    if (dialog.type !== "beforeunload") {
      answer(accept, dialog.type === "prompt" && accept ? (text === null ? dialog.defaultText : text) : "");
      return { dialog, leaving: null };
    }
    if (!accept) return { dialog, leaving: null };
    tab.leaveOnceUntil = Date.now() + LEAVE_ONCE_MS;
    return { dialog, leaving: answer.leave ? answer.leave() : null };
  }

  /** The user's answer from the pane: any of the shown chat's tabs, any box. */
  userAnswerDialog(id, accept, text) {
    const { leaving } = this.answerDialog(this.userTab(id), accept, text);
    if (leaving) leaving.catch((err) => console.warn(`[browser-pane] Leaving the page did not finish: ${err.message}`));
  }

  /** Python's site gate reads the box before an answer, to decide whether to ask the user. */
  agentDialog(sessionId, id) {
    const tab = this.agentTab(sessionId, id);
    return { tabId: tab.state.id, dialog: tab.state.dialog };
  }

  /**
   * The agent answers the box. Dismissing is always allowed; accepting a
   * confirm, prompt or "leave this page?" needs the page's site approved.
   *
   * @param {string} sessionId
   * @param {TabId | null} id
   * @param {boolean} accept
   * @param {string | null} text
   * @param {string[]} allowedOrigins
   */
  async agentAnswerDialog(sessionId, id, accept, text, allowedOrigins) {
    const tab = this.agentTab(sessionId, id);
    this.requireAgentInput(tab);
    const pending = tab.state.dialog;
    if (pending && accept && pending.type !== "alert" && !inScope(pending.url, allowedOrigins)) {
      const origin = originOf(pending.url) || pending.url;
      throw new BridgeError("off_scope", `The box is from ${origin}, which this call was not approved to answer.`, { origin });
    }
    return this.withAgent(
      tab,
      allowedOrigins,
      async () => {
        const { dialog, leaving } = this.answerDialog(tab, accept, text);
        const wc = tab.view.webContents;
        if (leaving) {
          const left = await this.raceDialog(tab, withTimeout(leaving, LOAD_TIMEOUT_MS, "Leaving the page")).catch((err) => {
            throw new BridgeError("navigation_failed", err.message, { url: wc.getURL() });
          });
          if (!left.dialog) await this.settle(wc);
        } else {
          // A page often opens a second box right after the first.
          await this.settle(wc);
        }
        return { tabId: tab.state.id, answered: dialog, url: wc.getURL(), title: wc.getTitle(), dialog: tab.state.dialog };
      },
      { acting: true }
    );
  }

  /** Dismiss the boxes this chat's agent opened and left, in its tabs still shared with it. */
  releaseDialogs(sessionId) {
    let dismissed = 0;
    for (const tab of this.chatTabs(sessionId)) {
      const d = tab.state.dialog;
      if (!d || d.openedBy !== "agent" || tab.state.control !== "shared") continue;
      this.answerDialog(tab, false, null);
      dismissed += 1;
    }
    return { dismissed };
  }

  /** Called by Electron when a page's beforeunload asks to stay; it must decide now. */
  onPreventUnload(tab, event) {
    const leave = tab.leaving;
    tab.leaving = null;
    if (Date.now() < tab.leaveOnceUntil) {
      tab.leaveOnceUntil = 0;
      event.preventDefault();
      return;
    }
    this.openDialog(tab, { type: "beforeunload", message: LEAVE_MESSAGE, defaultText: "", url: tab.view.webContents.getURL() }, { leave });
  }

  /**
   * Emulate a screen size and color scheme for the agent. The user's own
   * Responsive choice wins: then nothing changes and applied is false.
   *
   * @param {string} sessionId
   * @param {TabId | null} id
   * @param {import("./contract").ViewportChoice} choice
   */
  async agentViewport(sessionId, id, choice) {
    const tab = this.agentTab(sessionId, id);
    this.requireAgentInput(tab);
    if (!tab.view) throw new BridgeError("no_page", "This tab has no page yet. Open one with browser_open(url) first.");
    const current = tab.state.viewport;
    if (current && current.source === "user") return { tabId: tab.state.id, applied: false, viewport: current };
    tab.state.viewport = choice.size || choice.colorScheme ? { ...choice, source: "agent" } : null;
    this.deps.onShowRequest(sessionId);
    this.publish();
    await this.applyViewport(tab);
    return { tabId: tab.state.id, applied: true, viewport: tab.state.viewport };
  }

  async applyViewport(tab) {
    if (!tab.cdp) return;
    const v = tab.state.viewport;
    if (v && v.size) {
      const { width, height, mobile } = v.size;
      await tab.cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 0, mobile });
    } else {
      await tab.cdp.send("Emulation.clearDeviceMetricsOverride");
    }
    await tab.cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: (v && v.colorScheme) || "" }] });
  }

  /** A click or keystroke from the user: the agent waits, and mid-task it hands the tab to the user. */
  noteUserInput(tab) {
    tab.userInputAt = Date.now();
    if (tab.state.agentActing && tab.state.control === "shared") {
      tab.state.control = "user";
      this.publish();
    }
  }

  dispose() {
    this.stopPicking();
    this.disposed = true;
    for (const tab of this.tabs.values()) this.destroyView(tab.view);
    this.tabs.clear();
  }

  // ── Internals ─────────────────────────────────────────────────────────

  /**
   * @param {ChatKey} chat
   * @returns {TabId}
   */
  addTab(chat) {
    const id = /** @type {TabId} */ (`tab-${this.nextId++}`);
    this.tabs.set(id, {
      chat,
      view: null,
      /** Set while the page is discarded: its history, to load again when its chat shows. */
      saved: null,
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
        agentActing: false,
        control: "shared",
        viewport: null,
        dialog: null,
      },
      cdp: null,
      busy: 0,
      busyTimer: null,
      actTimer: null,
      scope: null,
      userInputAt: 0,
      dispatching: false,
      agentEchoUntil: 0,
      dialogAnswer: null,
      dialogWaiters: new Set(),
      leaving: null,
      leaveOnceUntil: 0,
    });
    return id;
  }

  /** @param {ChatKey} chat The chat's tabs, in the order they opened. */
  chatTabs(chat) {
    return [...this.tabs.values()].filter((t) => t.chat === chat);
  }

  /** The tab the user sees, or null while the shown chat has none. */
  shownTab() {
    const id = this.activeTabId;
    return id === null ? null : this.requireTab(id);
  }

  /** @param {TabId} id */
  requireTab(id) {
    const tab = this.tabs.get(id);
    if (!tab) throw new Error(`No browser pane tab "${id}"; it may have been closed.`);
    return tab;
  }

  /** A tab the user acts on from the pane: only the shown chat's tabs are on screen. */
  userTab(id) {
    const tab = this.requireTab(id);
    if (tab.chat !== this.shown) throw new Error(`Browser pane tab "${id}" belongs to another chat than the one showing.`);
    return tab;
  }

  /** Close a tab's page and forget the tab. The caller picks the chat's next active tab. */
  dropTab(tab) {
    if (this.picker && this.picker.tabId === tab.state.id) this.stopPicking();
    this.destroyView(tab.view);
    this.tabs.delete(tab.state.id);
  }

  /**
   * Past LIVE_VIEW_CAP live pages, close whole chats' pages, least recently
   * shown first. Never the shown chat's, `keep`'s, or a chat whose page is
   * busy with the agent or waiting on a box.
   *
   * @param {ChatKey} keep
   */
  trimViews(keep) {
    let live = [...this.tabs.values()].filter((t) => t.view).length;
    if (live <= LIVE_VIEW_CAP) return;
    const owners = new Set([...this.tabs.values()].map((t) => t.chat));
    const oldestFirst = [...owners].filter((c) => !this.recency.includes(c)).concat([...this.recency].reverse());
    for (const chat of oldestFirst) {
      if (live <= LIVE_VIEW_CAP) break;
      if (chat === this.shown || chat === keep) continue;
      const pages = this.chatTabs(chat).filter((t) => t.view);
      if (pages.length === 0 || pages.some((t) => t.busy > 0 || t.state.dialog)) continue;
      for (const tab of pages) this.discard(tab);
      live -= pages.length;
    }
  }

  /**
   * Close a hidden tab's page but keep its history, which carries each
   * entry's last saved scroll position and form values, to load it again.
   */
  discard(tab) {
    const nav = tab.view.webContents.navigationHistory;
    const entries = nav.getAllEntries();
    // A page that never finished its first load has no entry yet; its address is all there is.
    tab.saved = entries.length > 0 ? { entries, index: nav.getActiveIndex() } : { entries: [{ url: tab.state.url, title: tab.state.title }], index: 0 };
    this.destroyView(tab.view);
    tab.view = null;
    tab.cdp = null;
    tab.state.loading = false;
    tab.state.devToolsOpen = false;
  }

  /** Load a discarded tab's page again where it was. */
  restoreView(tab) {
    const { saved } = tab;
    tab.saved = null;
    const wc = this.ensureView(tab).webContents;
    // did-fail-load puts a failure on the tab; this only keeps the rejection handled.
    wc.navigationHistory.restore(saved).catch((err) => console.warn(`[browser-pane] Reloading ${tab.state.url} did not finish: ${err.message}`));
    if (tab.state.viewport) {
      this.applyViewport(tab).catch((err) => console.error(`[browser-pane] Could not apply the tab's screen size again: ${err.message}`));
    }
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
    this.trimViews(tab.chat);
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
    // A discarded or closed page can still send late events; only the tab's current page counts.
    const live = () => !this.disposed && this.tabs.has(s.id) && tab.view !== null && tab.view.webContents === wc;
    const update = (fn) => (...args) => {
      if (!live()) return;
      fn(...args);
      this.publish();
    };

    attachTabPolicy(wc, {
      blockedPorts: this.deps.blockedPorts,
      openTab: (url) => this.openTabIn(tab.chat, url),
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

    wc.removeAllListeners(RUN_DIALOG);
    wc.on(RUN_DIALOG, (info, callback) => {
      if (!live()) return callback(false, "");
      // Chromium shows one box per page at a time; a second can only come from a frame racing the first.
      if (s.dialog) return callback(false, "");
      const url = (info.frame && info.frame.url) || wc.getURL();
      this.openDialog(tab, { type: info.dialogType, message: String(info.messageText || ""), defaultText: String(info.defaultPromptText || ""), url }, callback);
    });
    wc.on(CANCEL_DIALOGS, () => {
      if (s.dialog && s.dialog.type !== "beforeunload") this.dropDialog(tab);
    });
    wc.on("will-prevent-unload", (event) => {
      if (live()) this.onPreventUnload(tab, event);
    });
    wc.on("did-start-loading", update(() => { s.loading = true; }));
    wc.on("did-stop-loading", update(() => {
      s.loading = false;
      this.readNavigation(tab);
    }));
    wc.on("did-start-navigation", update((details) => {
      if (!details.isMainFrame || details.isSameDocument) return;
      // Started, so beforeunload already let it go: nothing left to repeat, and no box to keep.
      tab.leaving = null;
      if (s.dialog && s.dialog.type === "beforeunload") this.dropDialog(tab);
      if (this.picker && this.picker.tabId === s.id) this.stopPicking();
      s.notice = null;
      tab.cdp.onNavigationStarted();
    }));
    wc.on("did-navigate", update((_event, url) => {
      s.url = url;
      this.readNavigation(tab);
      this.remember(tab.chat, url, wc.getTitle());
    }));
    wc.on("did-navigate-in-page", update((_event, url, isMainFrame) => {
      if (!isMainFrame) return;
      s.url = url;
      this.readNavigation(tab);
    }));
    wc.on("page-title-updated", update((_event, title) => {
      s.title = title;
      if (s.url) this.remember(tab.chat, s.url, title);
    }));
    wc.on("did-fail-load", update((_event, code, description, url, isMainFrame) => {
      if (!isMainFrame || code === ERR_ABORTED) return;
      s.notice = { kind: "load-failed", url, message: `${description} (${code})` };
    }));
    wc.on("render-process-gone", update((_event, details) => {
      s.loading = false;
      this.dropDialog(tab);
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
    wc.on("input-event", (_event, event) => {
      if (!USER_INPUT.has(event.type) || tab.dispatching || Date.now() < tab.agentEchoUntil) return;
      if (live()) this.noteUserInput(tab);
    });
    wc.on("before-input-event", (event, input) => {
      if (isToggleShortcut(input)) {
        event.preventDefault();
        this.deps.onToggleRequest();
      } else if (isPickShortcut(input)) {
        event.preventDefault();
        this.togglePicking().catch((err) => console.error(`[browser-pane] ${err.message}`));
      }
    });
  }

  /** Put a visited page at the top of its chat's new tab page list. */
  remember(chat, url, title) {
    if (!isWebUrl(url)) return;
    const rest = (this.recent.get(chat) ?? []).filter((p) => p.url !== url);
    this.recent.delete(chat);
    this.recent.set(chat, [{ url, title: title || url }, ...rest].slice(0, RECENT_LIMIT));
    for (const old of [...this.recent.keys()].slice(0, -RECENT_CHATS)) this.recent.delete(old);
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
  ACT_LINGER_MS,
  LIVE_VIEW_CAP,
  BridgeError,
  CANCEL_DIALOGS,
  LEAVE_MESSAGE,
  RUN_DIALOG,
  DEVTOOLS_MODE,
  PANE_PARTITION,
  PaneHost,
  USER_PAUSE_MS,
  inScope,
  isLoopbackUrl,
  isPickShortcut,
  isToggleShortcut,
  toViewBounds,
};
