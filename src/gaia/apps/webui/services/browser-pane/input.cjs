// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * How the agent's clicks, typing and key presses reach a pane page: trusted
 * CDP input events at the target's on-screen point, never el.click(). A
 * target is a selector Python built from its snapshot ref, in the main frame
 * or a same-process child frame. Before any input lands the target is
 * scrolled into view and checked: still on the page, visible, enabled, and
 * not covered by something else.
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
const PROBE_JS = `async (sel) => {
  const el = document.querySelector(sel);
  if (!el) return { fail: "gone" };
  el.scrollIntoView({ block: "center", inline: "center" });
  await new Promise((r) => { requestAnimationFrame(() => requestAnimationFrame(r)); setTimeout(r, 120); });
  const r = el.getBoundingClientRect();
  const st = getComputedStyle(el);
  if (r.width < 1 || r.height < 1 || st.visibility === "hidden" || st.display === "none" || el.closest("[inert]")) return { fail: "hidden" };
  if (el.disabled || el.closest("fieldset[disabled]") || el.getAttribute("aria-disabled") === "true") return { fail: "disabled" };
  let x = r.left + r.width / 2;
  let y = r.top + r.height / 2;
  const top = document.elementFromPoint(x, y);
  const mine = (n) => n === el || el.contains(n) || [...(el.labels || [])].some((l) => l.contains(n));
  if (!top) return { fail: "hidden" };
  if (!mine(top)) {
    const label = (top.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 60);
    return { fail: "covered", by: label ? '"' + label + '"' : "a <" + top.tagName.toLowerCase() + "> element" };
  }
  for (let w = window; w !== w.parent; w = w.parent) {
    const fe = w.frameElement;
    if (!fe) return { fail: "frame" };
    const fr = fe.getBoundingClientRect();
    x += fr.left + fe.clientLeft;
    y += fr.top + fe.clientTop;
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

const call = (fn, arg) => `(${fn})(${JSON.stringify(arg)})`;

/** @param {string} name */
function keyFor(name) {
  if (!Object.prototype.hasOwnProperty.call(KEYS, name)) {
    throw new BridgeError("bad_request", `"${name}" is not a key GAIA may press. Keys: ${Object.keys(KEYS).join(", ")}.`);
  }
  return KEYS[name];
}

/**
 * The frame a target lives in. A frame in another process (a cross-site
 * iframe) is refused loudly: its points are not in the main frame's space.
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
  if (frame.processId !== main.processId) {
    throw new BridgeError(
      "unsupported",
      "That element is inside a frame from another site, which GAIA can't click or type into yet. Ask the user to do this step in the browser pane."
    );
  }
  return frame;
}

function withTimeout(promise, what) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new BridgeError("timeout", `${what} took longer than ${PROBE_TIMEOUT_MS / 1000} s.`)), PROBE_TIMEOUT_MS);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

/**
 * Scroll the target into view and check it can take input.
 *
 * @param {Electron.WebFrameMain} frame
 * @param {string} selector
 * @returns {Promise<{ x: number, y: number, tag: string, type: string, editable: boolean }>}
 */
async function locate(frame, selector) {
  const found = await withTimeout(frame.executeJavaScript(call(PROBE_JS, selector), false), "Finding the element");
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
  if (found.fail === "frame") {
    throw new BridgeError(
      "unsupported",
      "That element is inside a frame GAIA can't reach from the page. Ask the user to do this step in the browser pane."
    );
  }
  return found;
}

/** @param {{ send: (method: string, params?: object) => Promise<any> }} cdp */
async function clickAt(cdp, { x, y }) {
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 });
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1 });
}

async function pressKey(cdp, name) {
  const def = keyFor(name);
  const base = { key: def.key, code: def.code, windowsVirtualKeyCode: def.keyCode, nativeVirtualKeyCode: def.keyCode };
  await cdp.send("Input.dispatchKeyEvent", def.text ? { type: "keyDown", ...base, text: def.text, unmodifiedText: def.text } : { type: "rawKeyDown", ...base });
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
}

async function fill(cdp, frame, selector, text, pressEnter) {
  const at = await locate(frame, selector);
  if (at.tag === "select") {
    const res = await frame.executeJavaScript(call(CHOOSE_OPTION_JS, [selector, text]), false);
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
  await clickAt(cdp, at);
  await frame.executeJavaScript(call(SELECT_TEXT_JS, selector), false);
  if (text) await cdp.send("Input.insertText", { text });
  else await pressKey(cdp, "Backspace");
  if (pressEnter) await pressKey(cdp, "Enter");
}

/**
 * Carry out one agent action as trusted input. The caller has already
 * checked the tab is the agent's to act in.
 *
 * @param {{ wc: Electron.WebContents, cdp: { send: (method: string, params?: object) => Promise<any> }, action: ActAction, target: ActTarget | null }} op
 */
async function perform({ wc, cdp, action, target }) {
  if (action.kind === "press") {
    keyFor(action.key);
    if (target) {
      const frame = frameFor(wc, target.frameId);
      await locate(frame, target.selector);
      await frame.executeJavaScript(call(FOCUS_JS, target.selector), false);
    }
    await pressKey(cdp, action.key);
    return;
  }
  if (!target) throw new BridgeError("bad_request", `A ${action.kind} needs a target element.`);
  const frame = frameFor(wc, target.frameId);
  if (action.kind === "click") await clickAt(cdp, await locate(frame, target.selector));
  else await fill(cdp, frame, target.selector, action.text, action.pressEnter);
}

module.exports = { KEYS, frameFor, keyFor, locate, perform };
