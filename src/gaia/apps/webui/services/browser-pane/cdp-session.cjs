// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * One tab's Chrome DevTools Protocol attachment, through webContents.debugger,
 * never a debugging port. It keeps the tab's console and network activity in
 * two bounded rings from the moment the tab opens, so the agent can read what
 * happened before it was asked, and takes screenshots through
 * Page.captureScreenshot, which stays fresh while the view is hidden.
 */

"use strict";

/** @typedef {import("./contract").LogEntry} LogEntry */
/** @typedef {import("./contract").NetworkEntry} NetworkEntry */

const PROTOCOL_VERSION = "1.3";
const RING_SIZE = 500;
const MAX_TEXT = 500;
const MAX_URL = 300;
const MAX_PENDING_REQUESTS = 1000;
const MAX_FRAME_DEPTH = 16;
const AUTO_ATTACH = { autoAttach: true, waitForDebuggerOnStart: false, flatten: true };

const CONSOLE_LEVELS = {
  error: "error",
  assert: "error",
  warning: "warning",
  info: "info",
  log: "log",
  debug: "log",
  verbose: "log",
};

function truncate(text, max) {
  const s = String(text ?? "");
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

/** A bounded log whose entries carry a per-tab sequence number, the reader's cursor. */
class Ring {
  constructor(capacity = RING_SIZE) {
    this.capacity = capacity;
    /** @type {Array<{ seq: number }>} */
    this.entries = [];
    this.nextSeq = 1;
  }

  push(entry) {
    const stamped = { seq: this.nextSeq++, ...entry };
    this.entries.push(stamped);
    if (this.entries.length > this.capacity) this.entries.shift();
    return stamped;
  }

  clear() {
    this.entries = [];
  }

  /**
   * @param {{ afterSeq: number, limit: number, keep: (e: object) => boolean }} q
   * @returns {{ entries: object[], lastSeq: number, dropped: number }}
   */
  query({ afterSeq, limit, keep }) {
    const lastSeq = this.nextSeq - 1;
    const oldest = this.entries.length ? this.entries[0].seq : this.nextSeq;
    const dropped = Math.max(0, oldest - afterSeq - 1);
    const matching = this.entries.filter((e) => e.seq > afterSeq && keep(e));
    return { entries: matching.slice(-limit), lastSeq, dropped };
  }
}

function remoteText(arg) {
  if (!arg) return "";
  if (arg.value !== undefined) return typeof arg.value === "string" ? arg.value : JSON.stringify(arg.value);
  return arg.unserializableValue || arg.description || arg.type || "";
}

function sourceOf(url, lineNumber) {
  if (!url) return undefined;
  return typeof lineNumber === "number" ? `${url}:${lineNumber + 1}` : url;
}

/**
 * @param {string} method
 * @param {any} params
 * @returns {Omit<LogEntry, "seq"> | null}
 */
function consoleEntry(method, params) {
  const at = Date.now();
  if (method === "Runtime.consoleAPICalled") {
    const frame = params.stackTrace && params.stackTrace.callFrames && params.stackTrace.callFrames[0];
    return {
      at,
      level: CONSOLE_LEVELS[params.type] || "log",
      text: truncate((params.args || []).map(remoteText).join(" "), MAX_TEXT),
      source: frame ? sourceOf(frame.url, frame.lineNumber) : undefined,
    };
  }
  if (method === "Runtime.exceptionThrown") {
    const d = params.exceptionDetails || {};
    const text = (d.exception && d.exception.description) || d.text || "Uncaught exception";
    return { at, level: "error", text: truncate(text, MAX_TEXT), source: sourceOf(d.url, d.lineNumber) };
  }
  if (method === "Log.entryAdded") {
    const e = params.entry || {};
    return {
      at,
      level: CONSOLE_LEVELS[e.level] || "log",
      text: truncate(e.text, MAX_TEXT),
      source: sourceOf(e.url, e.lineNumber),
    };
  }
  return null;
}

class TabCdp {
  /**
   * @param {Electron.WebContents} webContents
   * @param {{ error: (...args: any[]) => void }} [logger]
   */
  constructor(webContents, logger = console) {
    this.wc = webContents;
    this.logger = logger;
    this.console = new Ring();
    this.network = new Ring();
    /** @type {Map<string, { method: string, url: string, resourceType: string, startedAt: number, status: number | null }>} */
    this.pending = new Map();
    this.attached = false;
    this.detachReason = null;
    /** @type {Set<(params: any) => void>} */
    this.bindingListeners = new Set();
    /** Child sessions for out-of-process frames: sessionId -> the session that attached it. */
    this.sessions = new Map();
    this.ready = this.attach();
  }

  async attach() {
    const dbg = this.wc.debugger;
    try {
      dbg.attach(PROTOCOL_VERSION);
      this.attached = true;
      dbg.on("message", (_event, method, params, sessionId) => {
        if (sessionId) this.onChildMessage(method, params || {}, sessionId);
        else this.onMessage(method, params || {});
      });
      dbg.on("detach", (_event, reason) => {
        this.attached = false;
        this.detachReason = reason;
      });
      await Promise.all([dbg.sendCommand("Runtime.enable"), dbg.sendCommand("Log.enable"), dbg.sendCommand("Network.enable")]);
      // Out-of-process frames get sessions of their own, so a hit test can follow a point into them.
      await dbg.sendCommand("Target.setAutoAttach", AUTO_ATTACH);
    } catch (err) {
      this.attached = false;
      this.detachReason = err.message;
      this.logger.error(`[browser-pane] Could not attach DevTools protocol to a tab: ${err.message}`);
    }
  }

  /** A new page starts with empty logs, as DevTools does without "Preserve log". */
  onNavigationStarted() {
    this.console.clear();
    this.network.clear();
    this.pending.clear();
  }

  /** Calls `fn` with each Runtime.bindingCalled event; returns its unsubscribe. */
  onBinding(fn) {
    this.bindingListeners.add(fn);
    return () => this.bindingListeners.delete(fn);
  }

  onMessage(method, params) {
    if (method === "Target.attachedToTarget" || method === "Target.detachedFromTarget") {
      this.onChildMessage(method, params, null);
      return;
    }
    if (method === "Runtime.bindingCalled") {
      for (const fn of this.bindingListeners) fn(params);
      return;
    }
    const entry = consoleEntry(method, params);
    if (entry) {
      this.console.push(entry);
      return;
    }
    if (method === "Network.requestWillBeSent") this.requestStarted(params);
    else if (method === "Network.responseReceived") {
      const req = this.pending.get(params.requestId);
      if (req) req.status = params.response ? params.response.status : null;
    } else if (method === "Network.loadingFinished") this.requestEnded(params.requestId, null);
    else if (method === "Network.loadingFailed") {
      this.requestEnded(params.requestId, params.canceled ? "canceled" : params.errorText || "failed");
    }
  }

  requestStarted(params) {
    const url = (params.request && params.request.url) || "";
    if (url.startsWith("data:")) return;
    if (params.redirectResponse) {
      const hop = this.pending.get(params.requestId);
      if (hop) {
        hop.status = params.redirectResponse.status;
        this.requestEnded(params.requestId, null);
      }
    }
    if (this.pending.size >= MAX_PENDING_REQUESTS) this.pending.delete(this.pending.keys().next().value);
    this.pending.set(params.requestId, {
      method: (params.request && params.request.method) || "GET",
      url,
      resourceType: params.type || "Other",
      startedAt: Date.now(),
      status: null,
    });
  }

  requestEnded(requestId, failure) {
    const req = this.pending.get(requestId);
    if (!req) return;
    this.pending.delete(requestId);
    /** @type {Omit<NetworkEntry, "seq">} */
    const entry = {
      at: req.startedAt,
      method: req.method,
      url: truncate(req.url, MAX_URL),
      status: failure ? null : req.status,
      resourceType: req.resourceType,
      durationMs: Date.now() - req.startedAt,
    };
    if (failure) entry.failure = failure;
    this.network.push(entry);
  }

  /**
   * @param {{ kind: "console" | "network", afterSeq: number, errorsOnly: boolean, pattern: string | null, limit: number }} q
   */
  logs({ kind, afterSeq, errorsOnly, pattern, limit }) {
    const needle = pattern ? pattern.toLowerCase() : null;
    const isConsole = kind === "console";
    const keep = (e) => {
      if (errorsOnly && (isConsole ? e.level !== "error" : e.status !== null && e.status < 400)) return false;
      if (!needle) return true;
      const hay = isConsole ? `${e.text} ${e.source || ""}` : `${e.method} ${e.url} ${e.status ?? ""} ${e.failure || ""}`;
      return hay.toLowerCase().includes(needle);
    };
    return (isConsole ? this.console : this.network).query({ afterSeq, limit, keep });
  }

  /** One CDP command on this tab, once attached. */
  async send(method, params = {}, sessionId = null) {
    await this.ready;
    if (!this.attached) {
      throw new Error(`the DevTools protocol is not attached to this tab (${this.detachReason || "unknown reason"})`);
    }
    return sessionId ? this.wc.debugger.sendCommand(method, params, sessionId) : this.wc.debugger.sendCommand(method, params);
  }

  /** Track the sessions of out-of-process frames, and attach to their own frames in turn. */
  onChildMessage(method, params, parentSession) {
    if (method === "Target.attachedToTarget" && params.targetInfo && params.targetInfo.type === "iframe") {
      this.sessions.set(params.sessionId, parentSession);
      this.wc.debugger.sendCommand("Target.setAutoAttach", AUTO_ATTACH, params.sessionId).catch((err) => {
        this.logger.error(`[browser-pane] Could not follow the frames inside an out-of-process frame: ${err.message}`);
      });
    } else if (method === "Target.detachedFromTarget") {
      this.sessions.delete(params.sessionId);
    }
  }

  /**
   * Every frame on the page as Chromium lays it out, from this tab's session
   * and its frames' own: frameId -> { sessionId, parentId, url }. Read in the
   * browser, so nothing the page runs can change the answer.
   */
  async frameMap() {
    const frames = new Map();
    for (const sessionId of [null, ...this.sessions.keys()]) {
      let tree;
      try {
        ({ frameTree: tree } = await this.send("Page.getFrameTree", {}, sessionId));
      } catch (err) {
        if (sessionId && !this.sessions.has(sessionId)) continue; // detached while we looked
        throw err;
      }
      const walk = (node) => {
        frames.set(node.frame.id, { sessionId, parentId: node.frame.parentId || null, url: node.frame.url || "" });
        for (const child of node.childFrames || []) walk(child);
      };
      walk(tree);
    }
    return frames;
  }

  /**
   * The site a point in the page's viewport lands on: the innermost frame
   * under it, followed into out-of-process frames, as an http(s) origin or
   * null. A trusted click goes there whatever the page's own scripts claim.
   */
  async landingOrigin(x, y) {
    const frames = await this.frameMap();
    let sessionId = null;
    for (let depth = 0; depth < MAX_FRAME_DEPTH; depth += 1) {
      await this.send("DOM.getDocument", { depth: 0 }, sessionId);
      const hit = await this.send("DOM.getNodeForLocation", { x: Math.round(x), y: Math.round(y), includeUserAgentShadowDOM: true, ignorePointerEventsNone: false }, sessionId);
      const { node } = await this.send("DOM.describeNode", { backendNodeId: hit.backendNodeId }, sessionId);
      const inner = node.frameId && node.frameId !== hit.frameId ? frames.get(node.frameId) : null;
      if (!inner || inner.sessionId === sessionId) return siteOf(frames, inner ? node.frameId : hit.frameId);
      const { model } = await this.send("DOM.getBoxModel", { backendNodeId: hit.backendNodeId }, sessionId);
      x -= model.content[0];
      y -= model.content[1];
      sessionId = inner.sessionId;
    }
    return null;
  }

  /**
   * The sites of frames a picture of the page would show that `allowed`
   * doesn't cover, outermost only: a frame with no box (hidden, or no size)
   * shows nothing and is left out.
   */
  async shownUnapprovedSites(allowed) {
    const frames = await this.frameMap();
    const approved = (id) => {
      const site = siteOf(frames, id);
      return site !== null && allowed.includes(site);
    };
    const sites = [];
    for (const [id, f] of frames) {
      if (!f.parentId || approved(id) || !approved(f.parentId)) continue;
      const parent = frames.get(f.parentId);
      if (!parent) continue;
      let model = null;
      try {
        const owner = await this.send("DOM.getFrameOwner", { frameId: id }, parent.sessionId);
        ({ model } = await this.send("DOM.getBoxModel", { backendNodeId: owner.backendNodeId }, parent.sessionId));
      } catch (err) {
        if (!/box model|No node|not found/i.test(err.message)) throw err;
      }
      if (model && model.width * model.height > 0) sites.push(siteOf(frames, id));
    }
    return sites;
  }

  /**
   * PNG of the viewport, or of `clip` (CSS pixels relative to the page) when given.
   * @returns {Promise<Buffer>}
   */
  async screenshot(clip) {
    const params = { format: "png", captureBeyondViewport: Boolean(clip) };
    if (clip) params.clip = { ...clip, scale: 1 };
    const { data } = await this.send("Page.captureScreenshot", params);
    return Buffer.from(data, "base64");
  }
}

/**
 * The http(s) origin a frame's content belongs to, or null. A blank or srcdoc
 * frame holds what the frame around it wrote, so it counts as that frame.
 */
function siteOf(frames, id) {
  for (let f = frames.get(id), hops = 0; f && hops < MAX_FRAME_DEPTH; f = frames.get(f.parentId), hops += 1) {
    let url;
    try {
      url = new URL(f.url);
    } catch {
      return null;
    }
    if (url.protocol === "http:" || url.protocol === "https:") return url.origin;
    if (url.href !== "about:blank" && url.href !== "about:srcdoc") return null;
  }
  return null;
}

module.exports = { Ring, TabCdp, consoleEntry, siteOf, RING_SIZE };
