// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The element picker on one pane tab. picker-world.js runs in a CDP isolated
 * world and reports the user's selection through a binding exposed only to
 * that world, so the page can neither see the picker nor fake a pick. For
 * each selected element this gives it a pick ref (p1, p2 ...), has the world
 * stamp the ref on the element, reads the React component in the page's main
 * world (fiber expandos are invisible from an isolated world), takes a crop,
 * and hands the renderer a PickedElement per chip.
 */

"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

/** @typedef {import("./contract").PickedElement} PickedElement */

const WORLD_SOURCE = fs.readFileSync(path.join(__dirname, "picker-world.js"), "utf8");
const PICK_ATTR = "data-gaia-pick";
const STYLE_PROPS = Object.freeze([
  "display", "position", "width", "height", "margin", "padding", "gap",
  "color", "background-color", "font-family", "font-size", "font-weight", "line-height",
  "border", "border-radius", "box-shadow", "opacity", "z-index",
]);
const LIMITS = Object.freeze({ url: 2000, title: 300, label: 120, selector: 500, role: 40, name: 200, text: 300, html: 1536 });
const CROP_MAX_WIDTH = 800;
const CROP_MAX_HEIGHT = 1200;

// Runs in the page's main world, where React keeps its fibers on the DOM node.
// React 18 and older stamp _debugSource; React 19 dev keeps a _debugStack whose
// frame inside the component gives its file and line.
const REACT_LOOKUP_JS = `(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const key = Object.keys(el).find((k) => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$"));
  if (!key) return null;
  const host = el[key];
  for (let f = host; f; f = f.return) {
    const t = f.type;
    const fn = typeof t === "function" ? t : t && typeof t === "object" ? t.render || t.type : null;
    const name = (t && t.displayName) || (fn && (fn.displayName || fn.name)) || "";
    if (!name || typeof name !== "string") continue;
    let source = null;
    const ds = host._debugSource || f._debugSource;
    if (ds && typeof ds.fileName === "string") source = { file: ds.fileName, line: Number(ds.lineNumber) || 0 };
    const stack = host._debugStack && String(host._debugStack.stack || "");
    if (!source && stack) {
      const frame = stack.split("\\n").find((l) => l.includes(" " + name + " ") || l.includes(" " + name + "("));
      const m = frame && /\\(?((?:https?|file):[^\\s()]+):(\\d+):\\d+\\)?\\s*$/.exec(frame);
      if (m) {
        let file = m[1];
        try { const u = new URL(file); file = u.pathname; } catch {}
        source = { file, line: Number(m[2]) || 0 };
      }
    }
    return { name, source };
  }
  return null;
}`;

function clip(value, max) {
  const s = String(value ?? "");
  return s.length > max ? s.slice(0, max) : s;
}

function finite(n) {
  return typeof n === "number" && Number.isFinite(n) ? n : 0;
}

/** Only the curated properties, as short strings. */
function pickStyles(styles) {
  const out = {};
  for (const prop of STYLE_PROPS) {
    if (styles && typeof styles[prop] === "string") out[prop] = clip(styles[prop], 200);
  }
  return out;
}

/** What the main world reported, kept only when it has the expected shape. */
function parseComponent(found) {
  if (!found || typeof found.name !== "string" || !found.name) return null;
  const src = found.source;
  const source =
    src && typeof src.file === "string" && src.file
      ? { file: clip(src.file, 300), line: Math.max(0, Math.floor(finite(src.line))) }
      : null;
  return { name: clip(found.name, 120), source };
}

/**
 * One chip's data from what the world described, capped the way the UI
 * server validates it. The world computes the fields in page context, so a
 * page can make them long or odd; it can't make them escape these limits.
 *
 * @returns {PickedElement}
 */
function buildPickedElement(raw, { ref, component, crop }) {
  const r = raw.rect || {};
  return {
    ref,
    url: clip(raw.url, LIMITS.url),
    title: clip(raw.title, LIMITS.title),
    tag: /^[a-z][a-z0-9-]{0,39}$/.test(raw.tag) ? raw.tag : "element",
    label: clip(raw.label, LIMITS.label),
    selector: clip(raw.selector, LIMITS.selector),
    role: clip(raw.role, LIMITS.role),
    name: clip(raw.name, LIMITS.name),
    text: clip(raw.text, LIMITS.text),
    rect: { x: finite(r.x), y: finite(r.y), width: Math.max(0, finite(r.width)), height: Math.max(0, finite(r.height)) },
    component,
    outerHTML: clip(raw.outerHTML, LIMITS.html),
    styles: pickStyles(raw.styles),
    crop,
  };
}

/** The crop's rect in page coordinates, or null when the element has no size to picture. */
function cropRect(raw) {
  const r = raw.rect || {};
  const s = raw.scroll || {};
  const width = Math.min(finite(r.width), 4000);
  const height = Math.min(finite(r.height), CROP_MAX_HEIGHT);
  if (width < 1 || height < 1) return null;
  return { x: Math.max(0, finite(r.x) + finite(s.x)), y: Math.max(0, finite(r.y) + finite(s.y)), width, height };
}

class PickerSession {
  /**
   * @param {{
   *   cdp: import("./cdp-session.cjs").TabCdp,
   *   wc: Electron.WebContents,
   *   allocateRef: () => string,
   *   encodeCrop: (png: Buffer, maxWidth: number) => string,
   *   onSelection: (elements: PickedElement[]) => void,
   *   onEnd: () => void,
   *   logger?: { error: (...args: any[]) => void },
   * }} deps
   */
  constructor(deps) {
    this.deps = deps;
    this.logger = deps.logger || console;
    this.binding = `__gaiaPick_${crypto.randomBytes(8).toString("hex")}`;
    this.worldName = `gaia-picker-${crypto.randomBytes(8).toString("hex")}`;
    this.contextId = null;
    this.ended = false;
    /** @type {Map<string, string>} world token to pick ref, so a chip keeps its ref while others come and go */
    this.refs = new Map();
    /** @type {Map<string, { component: PickedElement["component"], crop: string | null }>} by ref */
    this.extras = new Map();
    this.queue = Promise.resolve();
    this.offBinding = null;
  }

  async start() {
    const { cdp } = this.deps;
    this.offBinding = cdp.onBinding((params) => this.onBinding(params));
    await cdp.send("Runtime.addBinding", { name: this.binding, executionContextName: this.worldName });
    const { frameTree } = await cdp.send("Page.getFrameTree");
    const { executionContextId } = await cdp.send("Page.createIsolatedWorld", {
      frameId: frameTree.frame.id,
      worldName: this.worldName,
      grantUniveralAccess: false,
    });
    this.contextId = executionContextId;
    const cfg = { binding: this.binding, pickAttr: PICK_ATTR, styleProps: STYLE_PROPS, maxHtml: LIMITS.html, maxText: LIMITS.text, maxName: LIMITS.name };
    await this.inWorld(`(${WORLD_SOURCE})(${JSON.stringify(cfg)})`);
  }

  /** Ends picking; the chips made so far stay. Safe to call more than once. */
  async stop() {
    if (this.ended) return;
    this.ended = true;
    if (this.offBinding) this.offBinding();
    const { cdp } = this.deps;
    // The page may have navigated away, taking the world with it; then there is nothing to remove.
    await this.inWorld("globalThis.__gaiaPicker && globalThis.__gaiaPicker.stop()").catch(() => {});
    await cdp.send("Runtime.removeBinding", { name: this.binding }).catch((err) => {
      this.logger.error(`[browser-pane] Could not remove the picker binding: ${err.message}`);
    });
    this.deps.onEnd();
  }

  async inWorld(expression) {
    const res = await this.deps.cdp.send("Runtime.evaluate", { expression, contextId: this.contextId, returnByValue: true });
    if (res && res.exceptionDetails) {
      const d = res.exceptionDetails;
      throw new Error((d.exception && d.exception.description) || d.text || "the picker script failed");
    }
    return res && res.result ? res.result.value : undefined;
  }

  onBinding(params) {
    if (this.ended || params.name !== this.binding || params.executionContextId !== this.contextId) return;
    let msg;
    try {
      msg = JSON.parse(params.payload);
    } catch (err) {
      this.logger.error(`[browser-pane] Ignored an unreadable message from the picker: ${err.message}`);
      return;
    }
    if (msg.type === "end") {
      this.stop().catch((err) => this.logger.error(`[browser-pane] Ending the picker failed: ${err.message}`));
    } else if (msg.type === "selection" && Array.isArray(msg.elements)) {
      // In order: a quick second click must not overtake the first.
      this.queue = this.queue
        .then(() => this.select(msg.elements))
        .catch((err) => this.logger.error(`[browser-pane] Could not build the picked elements: ${err.message}`));
    }
  }

  async select(raws) {
    const fresh = {};
    for (const raw of raws) {
      if (!this.refs.has(raw.token)) {
        const ref = this.deps.allocateRef();
        this.refs.set(raw.token, ref);
        fresh[raw.token] = ref;
      }
    }
    if (Object.keys(fresh).length) await this.inWorld(`globalThis.__gaiaPicker.stamp(${JSON.stringify(fresh)})`);
    const elements = [];
    for (const raw of raws) {
      const ref = this.refs.get(raw.token);
      if (!this.extras.has(ref)) this.extras.set(ref, await this.extrasFor(ref, raw));
      elements.push(buildPickedElement(raw, { ref, ...this.extras.get(ref) }));
    }
    if (!this.ended) this.deps.onSelection(elements);
  }

  async extrasFor(ref, raw) {
    const selector = `[${PICK_ATTR}="${ref}"]`;
    const found = await this.deps.wc.mainFrame
      .executeJavaScript(`(${REACT_LOOKUP_JS})(${JSON.stringify(selector)})`, false)
      .catch((err) => {
        this.logger.error(`[browser-pane] Could not read the React component of ${ref}: ${err.message}`);
        return null;
      });
    const crop = await this.crop(raw).catch((err) => {
      this.logger.error(`[browser-pane] Could not take a picture of ${ref}; its chip has no screenshot: ${err.message}`);
      return null;
    });
    return { component: parseComponent(found), crop };
  }

  async crop(raw) {
    const rect = cropRect(raw);
    if (!rect) return null;
    await this.inWorld("globalThis.__gaiaPicker.hide(true)");
    try {
      return this.deps.encodeCrop(await this.deps.cdp.screenshot(rect), CROP_MAX_WIDTH);
    } finally {
      await this.inWorld("globalThis.__gaiaPicker && globalThis.__gaiaPicker.hide(false)").catch(() => {});
    }
  }
}

module.exports = { PICK_ATTR, PickerSession, REACT_LOOKUP_JS, STYLE_PROPS, buildPickedElement, cropRect, parseComponent };
