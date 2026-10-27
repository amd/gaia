// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * How the agent's clicks, typing and key presses reach a pane page: trusted
 * CDP input events at the target's on-screen point, never el.click(). A
 * target is a selector Python built from its snapshot ref, in the main frame
 * or any child frame, cross-site ones included. Before any input lands the
 * target is scrolled into view and checked: still on the page, visible,
 * enabled, and not covered by something else, in its own frame and in every
 * frame above it.
 */

"use strict";

const { BridgeError } = require("./bridge-error.cjs");

/** @typedef {import("./contract").ActAction} ActAction */
/** @typedef {import("./contract").ActTarget} ActTarget */
/** @typedef {import("./contract").AllowedKey} AllowedKey */

/** The only keys the agent may press; model text never becomes a raw key event. */
const KEYS = Object.freeze({
  Enter: { key: "Enter", code: "Enter", keyCode: 13, text: "\r" },
  Escape: { key: "Escape", code: "Escape", keyCode: 27 },
  Tab: { key: "Tab", code: "Tab", keyCode: 9 },
  Backspace: { key: "Backspace", code: "Backspace", keyCode: 8 },
  Space: { key: " ", code: "Space", keyCode: 32, text: " " },
  PageDown: { key: "PageDown", code: "PageDown", keyCode: 34 },
  PageUp: { key: "PageUp", code: "PageUp", keyCode: 33 },
  Home: { key: "Home", code: "Home", keyCode: 36 },
  End: { key: "End", code: "End", keyCode: 35 },
  ArrowLeft: { key: "ArrowLeft", code: "ArrowLeft", keyCode: 37 },
  ArrowUp: { key: "ArrowUp", code: "ArrowUp", keyCode: 38 },
  ArrowRight: { key: "ArrowRight", code: "ArrowRight", keyCode: 39 },
  ArrowDown: { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
});

/** Input types that take no typing: the agent clicks them instead. */
const UNTYPED_INPUTS = new Set(["checkbox", "radio", "submit", "button", "reset", "image", "file", "range", "color", "hidden"]);

const PROBE_TIMEOUT_MS = 5000;

// Runs in the target's frame. Waits two frames after scrolling (or 120 ms when
// a hidden view does not paint) so the point is where the element settled.
// The point is in that frame's viewport; toMainFrame moves it to the page's.
const PROBE_JS = `async (sel) => {
  const el = document.querySelector(sel);
  if (!el) return { fail: "gone" };
  el.scrollIntoView({ block: "center", inline: "center" });
  await new Promise((r) => { requestAnimationFrame(() => requestAnimationFrame(r)); setTimeout(r, 120); });
  const r = el.getBoundingClientRect();
  const st = getComputedStyle(el);
  if (r.width < 1 || r.height < 1 || st.visibility === "hidden" || st.display === "none" || el.closest("[inert]")) return { fail: "hidden" };
  if (el.disabled || el.closest("fieldset[disabled]") || el.getAttribute("aria-disabled") === "true") return { fail: "disabled" };
  const x = r.left + r.width / 2;
  const y = r.top + r.height / 2;
  const top = document.elementFromPoint(x, y);
  const mine = (n) => n === el || el.contains(n) || [...(el.labels || [])].some((l) => l.contains(n));
  if (!top) return { fail: "hidden" };
  if (!mine(top)) {
    const label = (top.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 60);
    return { fail: "covered", by: label ? '"' + label + '"' : "a <" + top.tagName.toLowerCase() + "> element" };
  }
  return { x, y, tag: el.tagName.toLowerCase(), type: (el.getAttribute("type") || "").toLowerCase(), editable: el.isContentEditable };
}`;

// Selects the field's current text, so the typing that follows replaces it.
const SELECT_TEXT_JS = `(sel) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  el.focus();
  if (typeof el.select === "function") el.select();
  else { const range = document.createRange(); range.selectNodeContents(el); const s = getSelection(); s.removeAllRanges(); s.addRange(range); }
  return true;
}`;

// A <select> opens a native popup that input events cannot drive, so the option is set the way a pick would set it.
const CHOOSE_OPTION_JS = `([sel, label]) => {
  const el = document.querySelector(sel);
  if (!el) return { fail: "gone" };
  const want = label.trim().toLowerCase();
  const opt = [...el.options].find((o) => (o.label || o.text).trim().toLowerCase() === want);
  if (!opt) return { fail: "option", options: [...el.options].map((o) => (o.label || o.text).trim()).slice(0, 20) };
  el.value = opt.value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return { chosen: opt.label || opt.text };
}`;

const FOCUS_JS = `(sel) => { const el = document.querySelector(sel); if (!el) return false; el.focus(); return true; }`;

// Scrolls an element into view in its frame and returns its box there; no actionability checks.
const BOX_JS = `async (sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  el.scrollIntoView({ block: "center", inline: "center" });
  await new Promise((r) => { requestAnimationFrame(() => requestAnimationFrame(r)); setTimeout(r, 120); });
  const r = el.getBoundingClientRect();
  return { x: r.left, y: r.top, width: r.width, height: r.height };
}`;

// Runs in a child frame: its index in window.parent, which works across sites.
const FRAME_INDEX_JS = `() => {
  if (window.parent === window) return -1;
  for (let i = 0; i < parent.length; i++) if (parent[i] === window) return i;
  return -1;
}`;

// Runs in the parent of a frame: where that frame's content box starts in this
// viewport, after scrolling it so the point (x, y) in the child shows, and
// whether something in this frame covers that point.
const FRAME_BOX_JS = `([index, x, y]) => {
  const fe = [...document.querySelectorAll("iframe, frame")].find((f) => f.contentWindow === window[index]);
  if (!fe) return { fail: "frame" };
  const corner = () => {
    const r = fe.getBoundingClientRect();
    const st = getComputedStyle(fe);
    return { left: r.left + fe.clientLeft + parseFloat(st.paddingLeft), top: r.top + fe.clientTop + parseFloat(st.paddingTop) };
  };
  const shows = (c) => c.left + x >= 0 && c.top + y >= 0 && c.left + x < innerWidth && c.top + y < innerHeight;
  let c = corner();
  if (!shows(c)) { fe.scrollIntoView({ block: "center", inline: "center" }); c = corner(); }
  if (!shows(c)) return { fail: "hidden" };
  const top = document.elementFromPoint(c.left + x, c.top + y);
  if (top !== fe) {
    if (!top) return { fail: "hidden" };
    const label = (top.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 60);
    return { fail: "covered", by: label ? '"' + label + '"' : "a <" + top.tagName.toLowerCase() + "> element" };
  }
  return c;
}`;

const call = (fn, arg) => `(${fn})(${JSON.stringify(arg)})`;

/** @param {string} name */
function keyFor(name) {
  if (!Object.prototype.hasOwnProperty.call(KEYS, name)) {
    throw new BridgeError("bad_request", `"${name}" is not a key GAIA may press. Keys: ${Object.keys(KEYS).join(", ")}.`);
  }
  return KEYS[name];
}

/**
 * The frame a target lives in, in this process or another.
 *
 * @param {Electron.WebContents} wc
 * @param {string | null} frameId "processId:routingId", or null for the main frame.
 */
function frameFor(wc, frameId) {
  const main = wc.mainFrame;
  if (frameId === null) return main;
  const frame = main.framesInSubtree.find((f) => `${f.processId}:${f.routingId}` === frameId);
  if (!frame) {
    throw new BridgeError("element_not_found", "The frame that element was in is gone. Call browser_snapshot() to get current refs.");
  }
  return frame;
}

/** The URL whose origin a frame acts as: its origin for about:srcdoc and about:blank frames, else its URL. */
function frameUrl(frame) {
  const origin = frame.origin;
  return typeof origin === "string" && /^https?:\/\//.test(origin) ? origin : frame.url;
}

function withTimeout(promise, what) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new BridgeError("timeout", `${what} took longer than ${PROBE_TIMEOUT_MS / 1000} s.`)), PROBE_TIMEOUT_MS);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

/**
 * What one action sends to the page. Once `stop.reason` is set (a box opened
 * on the page mid-action) every later step throws instead of reaching the page.
 *
 * @param {{ send: (method: string, params?: object) => Promise<any> }} cdp
 * @param {{ reason: string | null }} [stop]
 */
function context(cdp, stop = { reason: null }) {
  const guard = () => {
    if (stop.reason) throw new BridgeError("dialog_open", stop.reason);
  };
  return {
    send: (method, params) => {
      guard();
      return cdp.send(method, params);
    },
    exec: (frame, code, what = "Reading the page") => {
      guard();
      return withTimeout(frame.executeJavaScript(code, false), what);
    },
  };
}

const unreachableFrame = () =>
  new BridgeError("unsupported", "That element is inside a frame GAIA can't reach from the page. Ask the user to do this step in the browser pane.");

/**
 * Move a point in `frame`'s viewport into the main frame's, one parent at a
 * time: each parent finds the frame's element, scrolls it into view if the
 * point is off screen, and adds where its content box starts.
 *
 * @returns {Promise<{ x: number, y: number }>}
 */
async function toMainFrame(ctx, frame, x, y) {
  for (let f = frame; f.parent; f = f.parent) {
    let box;
    try {
      const index = await ctx.exec(f, `(${FRAME_INDEX_JS})()`);
      box = index >= 0 ? await ctx.exec(f.parent, call(FRAME_BOX_JS, [index, x, y])) : { fail: "frame" };
    } catch (err) {
      if (err instanceof BridgeError && err.code !== "timeout") throw err;
      throw unreachableFrame();
    }
    if (!box || box.fail === "frame") throw unreachableFrame();
    if (box.fail === "hidden") {
      throw new BridgeError("not_actionable", "That element's frame is not visible on the page, so it can't be clicked. Call browser_snapshot() to see what is there now.");
    }
    if (box.fail === "covered") {
      throw new BridgeError("not_actionable", `That element is covered by ${box.by}. Close or dismiss that first, then try again.`, { coveredBy: box.by });
    }
    x += box.left;
    y += box.top;
  }
  return { x, y };
}

/**
 * Scroll the target into view and check it can take input. The point it
 * returns is in the main frame's viewport, where trusted input lands.
 *
 * @returns {Promise<{ x: number, y: number, tag: string, type: string, editable: boolean }>}
 */
async function locate(ctx, frame, selector) {
  const found = await ctx.exec(frame, call(PROBE_JS, selector), "Finding the element");
  if (!found || found.fail === "gone") {
    throw new BridgeError("element_not_found", "That element is not on the page any more. Call browser_snapshot() to get current refs, then retry.");
  }
  if (found.fail === "hidden") {
    throw new BridgeError("not_actionable", "That element is not visible on the page, so it can't be clicked. Call browser_snapshot() to see what is there now.");
  }
  if (found.fail === "disabled") {
    throw new BridgeError("not_actionable", "That element is disabled (greyed out). Fill in what the page still needs first, then try again.");
  }
  if (found.fail === "covered") {
    throw new BridgeError("not_actionable", `That element is covered by ${found.by}. Close or dismiss that first, then try again.`, { coveredBy: found.by });
  }
  return { ...found, ...(await toMainFrame(ctx, frame, found.x, found.y)) };
}

/**
 * An element's box in the main frame's viewport, scrolled into view, for a picture of it.
 *
 * @returns {Promise<{ x: number, y: number, width: number, height: number }>}
 */
async function elementBox(ctx, frame, selector) {
  const box = await ctx.exec(frame, call(BOX_JS, selector), "Finding the element");
  if (!box || box.width < 1 || box.height < 1) {
    throw new BridgeError("element_not_found", "That element is not on the page any more, or has no size. Call browser_snapshot() to get current refs.");
  }
  const centre = await toMainFrame(ctx, frame, box.x + box.width / 2, box.y + box.height / 2);
  return { x: centre.x - box.width / 2, y: centre.y - box.height / 2, width: box.width, height: box.height };
}

async function clickAt(ctx, { x, y }) {
  await ctx.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await ctx.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 });
  await ctx.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1 });
}

async function pressKey(ctx, name) {
  const def = keyFor(name);
  const base = { key: def.key, code: def.code, windowsVirtualKeyCode: def.keyCode, nativeVirtualKeyCode: def.keyCode };
  await ctx.send("Input.dispatchKeyEvent", def.text ? { type: "keyDown", ...base, text: def.text, unmodifiedText: def.text } : { type: "rawKeyDown", ...base });
  await ctx.send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
}

async function fill(ctx, frame, selector, text, pressEnter) {
  const at = await locate(ctx, frame, selector);
  if (at.tag === "select") {
    const res = await ctx.exec(frame, call(CHOOSE_OPTION_JS, [selector, text]));
    if (res && res.fail === "option") {
      throw new BridgeError("not_actionable", `That dropdown has no option "${text}". Its options: ${res.options.join(", ")}.`);
    }
    if (!res || res.fail) throw new BridgeError("element_not_found", "That dropdown is not on the page any more. Call browser_snapshot() to get current refs.");
    return;
  }
  if (at.tag === "input" && at.type === "password") {
    throw new BridgeError(
      "not_actionable",
      "That is a password field. GAIA never types passwords. Ask the user to sign in by hand in the browser pane, then continue."
    );
  }
  const typeable = at.tag === "textarea" || at.editable || (at.tag === "input" && !UNTYPED_INPUTS.has(at.type));
  if (!typeable) {
    throw new BridgeError("not_actionable", "That element doesn't take typing (it is a button, link, checkbox or similar). Use browser_click on it instead.");
  }
  await clickAt(ctx, at);
  await ctx.exec(frame, call(SELECT_TEXT_JS, selector));
  if (text) await ctx.send("Input.insertText", { text });
  else await pressKey(ctx, "Backspace");
  if (pressEnter) await pressKey(ctx, "Enter");
}

/**
 * Carry out one agent action as trusted input. The caller has already
 * checked the tab is the agent's to act in, and the frame's site.
 *
 * @param {{ ctx: ReturnType<typeof context>, action: ActAction, frame: Electron.WebFrameMain | null, selector: string | null }} op
 */
async function perform({ ctx, action, frame, selector }) {
  if (action.kind === "press") {
    keyFor(action.key);
    if (frame) {
      await locate(ctx, frame, selector);
      await ctx.exec(frame, call(FOCUS_JS, selector));
    }
    await pressKey(ctx, action.key);
    return;
  }
  if (!frame) throw new BridgeError("bad_request", `A ${action.kind} needs a target element.`);
  if (action.kind === "click") await clickAt(ctx, await locate(ctx, frame, selector));
  else await fill(ctx, frame, selector, action.text, action.pressEnter);
}

module.exports = { FRAME_BOX_JS, FRAME_INDEX_JS, KEYS, PROBE_JS, context, elementBox, frameFor, frameUrl, keyFor, locate, perform };
