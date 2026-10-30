// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The element picker. picker.cjs runs this function in a CDP isolated world
// on the pane page, so page scripts can neither see its variables nor call
// its binding. The outline lives in a closed shadow root. Hover outlines an
// element, a click selects it, Shift-click adds it, ArrowUp and ArrowDown
// walk the last selected element to its parent and back to a child, and
// Escape or Enter ends picking. Page clicks are swallowed while it runs.
// Main gives each selected element a pick ref and calls stamp() to write it
// on the element as data-gaia-pick, the attribute the agent's clicks target.

(function startPicker(cfg) {
  "use strict";
  const { binding, pickAttr, styleProps, maxHtml, maxText, maxName } = cfg;
  const send = (msg) => globalThis[binding](JSON.stringify(msg));
  if (globalThis.__gaiaPicker) globalThis.__gaiaPicker.stop();

  const host = document.createElement("gaia-picker");
  host.style.cssText = "all:initial;position:fixed;inset:0;pointer-events:none;z-index:2147483647;display:block;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>
    .box{position:fixed;box-sizing:border-box;border:2px solid #2563eb;background:rgba(37,99,235,.12);pointer-events:none;border-radius:2px}
    .box.sel{border-color:#e11d48;background:rgba(225,29,72,.10)}
    .tag{position:absolute;left:-2px;bottom:100%;margin-bottom:2px;font:600 11px/1.4 ui-monospace,Menlo,monospace;color:#fff;background:#2563eb;padding:1px 5px;border-radius:3px;white-space:nowrap;max-width:360px;overflow:hidden;text-overflow:ellipsis}
    .box.sel .tag{background:#e11d48}
    .box.below .tag{bottom:auto;top:100%;margin:2px 0 0}
  </style>`;
  const layer = document.createElement("div");
  root.appendChild(layer);
  (document.documentElement || document).appendChild(host);

  /** @type {Element | null} */
  let hovered = null;
  /** @type {Element[]} */
  let selected = [];
  /** @type {Element[]} where ArrowDown returns to after ArrowUp */
  let walked = [];
  const tokens = new WeakMap();
  const byToken = new Map();
  let nextToken = 1;
  let frame = 0;

  function tokenOf(el) {
    let t = tokens.get(el);
    if (!t) {
      t = `t${nextToken++}`;
      tokens.set(el, t);
      byToken.set(t, el);
    }
    return t;
  }

  function labelOf(el) {
    const classes = [...el.classList].filter((c) => /^[\w-]+$/.test(c)).slice(0, 2);
    return [el.tagName.toLowerCase(), ...classes].join(".");
  }

  function oneLine(s, max) {
    return String(s || "").replace(/\s+/g, " ").trim().slice(0, max);
  }

  const IMPLICIT_ROLES = {
    a: (el) => (el.hasAttribute("href") ? "link" : "generic"),
    button: () => "button",
    select: () => "combobox",
    textarea: () => "textbox",
    img: () => "img",
    nav: () => "navigation",
    main: () => "main",
    header: () => "banner",
    footer: () => "contentinfo",
    form: () => "form",
    ul: () => "list",
    ol: () => "list",
    li: () => "listitem",
    table: () => "table",
    dialog: () => "dialog",
    input: (el) => {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "checkbox" || type === "radio") return type;
      if (type === "submit" || type === "button" || type === "reset") return "button";
      return "textbox";
    },
  };

  function roleOf(el) {
    const explicit = el.getAttribute("role");
    if (explicit) return oneLine(explicit, 40);
    const tag = el.tagName.toLowerCase();
    if (/^h[1-6]$/.test(tag)) return "heading";
    const implicit = IMPLICIT_ROLES[tag];
    return implicit ? implicit(el) : "generic";
  }

  function nameOf(el) {
    const labelled = el.getAttribute("aria-labelledby");
    const byId = labelled
      ? labelled.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map((n) => n.innerText).join(" ")
      : "";
    const fromLabel = el.labels && el.labels.length ? el.labels[0].innerText : "";
    return oneLine(
      el.getAttribute("aria-label") || byId || fromLabel || el.getAttribute("alt") || el.getAttribute("title") ||
        (["input", "select", "textarea"].includes(el.tagName.toLowerCase()) ? el.getAttribute("placeholder") || el.value : el.innerText),
      maxName
    );
  }

  function cssEscape(s) {
    return globalThis.CSS && CSS.escape ? CSS.escape(s) : s.replace(/[^\w-]/g, "\\$&");
  }

  function unique(sel) {
    try {
      return document.querySelectorAll(sel).length === 1;
    } catch {
      return false;
    }
  }

  /** The shortest selector that finds only this element: #id, [data-testid], then a tag:nth-of-type path. */
  function selectorOf(el) {
    if (el.id && unique(`#${cssEscape(el.id)}`)) return `#${cssEscape(el.id)}`;
    const testId = el.getAttribute("data-testid");
    if (testId && unique(`[data-testid="${testId.replace(/"/g, '\\"')}"]`)) return `[data-testid="${testId.replace(/"/g, '\\"')}"]`;
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
      let part = n.tagName.toLowerCase();
      if (n.id && unique(`#${cssEscape(n.id)}`)) {
        parts.unshift(`#${cssEscape(n.id)}`);
        break;
      }
      const cls = [...n.classList].filter((c) => /^[\w-]+$/.test(c)).slice(0, 2);
      if (cls.length) part += `.${cls.join(".")}`;
      const parent = n.parentElement;
      if (parent) {
        const same = [...parent.children].filter((c) => c.tagName === n.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(n) + 1})`;
      }
      parts.unshift(part);
      const sel = parts.join(" > ");
      if (unique(sel)) return sel;
    }
    return parts.join(" > ");
  }

  /** outerHTML with descendants past depth 2 folded to a comment and the pick stamps left out. */
  function htmlOf(el) {
    const copy = el.cloneNode(true);
    const fold = (node, depth) => {
      for (const child of [...node.children]) {
        child.removeAttribute(pickAttr);
        child.removeAttribute("data-gaia-ref");
        if (depth >= 2 && child.children.length) {
          const n = child.children.length;
          child.replaceChildren(document.createComment(` ${n} ${n === 1 ? "child" : "children"} `));
        } else {
          fold(child, depth + 1);
        }
      }
    };
    copy.removeAttribute(pickAttr);
    copy.removeAttribute("data-gaia-ref");
    fold(copy, 1);
    const html = copy.outerHTML;
    return html.length > maxHtml ? `${html.slice(0, maxHtml - 20)}<!-- truncated -->` : html;
  }

  // Values a property has on most elements, left out so the styles stay short.
  const DEFAULTS = {
    position: ["static"],
    margin: ["0px"],
    padding: ["0px"],
    gap: ["normal"],
    "background-color": ["rgba(0, 0, 0, 0)", "transparent"],
    "line-height": ["normal"],
    "border-radius": ["0px"],
    "box-shadow": ["none"],
    opacity: ["1"],
    "z-index": ["auto"],
  };

  function stylesOf(el) {
    const cs = getComputedStyle(el);
    const out = {};
    for (const prop of styleProps) {
      const value = cs.getPropertyValue(prop);
      if (!value || (DEFAULTS[prop] || []).includes(value)) continue;
      if (prop === "border" && /^0px /.test(value)) continue;
      out[prop] = value.slice(0, 200);
    }
    return out;
  }

  function describe(el) {
    const r = el.getBoundingClientRect();
    return {
      token: tokenOf(el),
      tag: el.tagName.toLowerCase(),
      label: labelOf(el),
      selector: selectorOf(el),
      role: roleOf(el),
      name: nameOf(el),
      text: oneLine(el.innerText || el.textContent, maxText),
      outerHTML: htmlOf(el),
      styles: stylesOf(el),
      rect: { x: r.x, y: r.y, width: r.width, height: r.height },
      scroll: { x: scrollX, y: scrollY },
      url: location.href,
      title: document.title,
    };
  }

  function emit() {
    send({ type: "selection", elements: selected.map(describe) });
  }

  function place(box, el, cls) {
    const r = el.getBoundingClientRect();
    box.className = `box ${cls}${r.top < 18 ? " below" : ""}`;
    box.style.left = `${r.left}px`;
    box.style.top = `${r.top}px`;
    box.style.width = `${r.width}px`;
    box.style.height = `${r.height}px`;
    box.firstChild.textContent = labelOf(el);
  }

  function draw() {
    frame = 0;
    const shown = [...selected.map((el) => [el, "sel"]), ...(hovered && !selected.includes(hovered) ? [[hovered, "hover"]] : [])];
    while (layer.children.length < shown.length) {
      const box = document.createElement("div");
      box.appendChild(document.createElement("span")).className = "tag";
      layer.appendChild(box);
    }
    while (layer.children.length > shown.length) layer.lastChild.remove();
    shown.forEach(([el, cls], i) => place(layer.children[i], el, cls));
  }

  function redraw() {
    if (!frame) frame = requestAnimationFrame(draw);
  }

  function targetOf(e) {
    const t = e.composedPath ? e.composedPath()[0] : e.target;
    const el = t && t.nodeType === 1 ? t : t && t.parentElement;
    return el && el !== host && el !== document.documentElement ? el : null;
  }

  function swallow(e) {
    e.preventDefault();
    e.stopImmediatePropagation();
  }

  function onMove(e) {
    const el = targetOf(e);
    if (el === hovered) return;
    hovered = el;
    redraw();
  }

  function onClick(e) {
    swallow(e);
    const el = targetOf(e);
    if (!el) return;
    walked = [];
    if (e.shiftKey) {
      if (!selected.includes(el)) selected = [...selected, el];
    } else {
      selected = [el];
    }
    redraw();
    emit();
  }

  function walk(up) {
    const last = selected[selected.length - 1];
    const from = last || hovered;
    if (!from) return;
    let to = null;
    if (up) {
      const parent = from.parentElement;
      if (parent && parent !== document.documentElement) {
        walked.push(from);
        to = parent;
      }
    } else {
      const back = walked.pop();
      to = back && from.contains(back) ? back : from.firstElementChild;
    }
    if (!to) return;
    if (last) {
      selected = [...selected.slice(0, -1).filter((el) => el !== to), to];
      emit();
    } else {
      hovered = to;
    }
    redraw();
  }

  function onKey(e) {
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      swallow(e);
      walk(e.key === "ArrowUp");
    } else if (e.key === "Escape" || e.key === "Enter") {
      swallow(e);
      send({ type: "end" });
      stop();
    }
  }

  const SWALLOWED = ["pointerdown", "pointerup", "mousedown", "mouseup", "dblclick", "auxclick", "contextmenu", "submit"];
  const listeners = [
    ["mousemove", onMove],
    ["click", onClick],
    ["keydown", onKey],
    ["keyup", (e) => { if (/^(Arrow(Up|Down)|Escape|Enter)$/.test(e.key)) swallow(e); }],
    ["scroll", redraw],
    ["resize", redraw],
    ...SWALLOWED.map((type) => [type, swallow]),
  ];
  for (const [type, fn] of listeners) window.addEventListener(type, fn, true);

  function stop() {
    for (const [type, fn] of listeners) window.removeEventListener(type, fn, true);
    if (frame) cancelAnimationFrame(frame);
    host.remove();
    if (globalThis.__gaiaPicker === api) delete globalThis.__gaiaPicker;
  }

  const api = {
    stop,
    /** Main's refs for the tokens it was sent: written on each element, where the agent's clicks find it. */
    stamp(refs) {
      for (const [token, ref] of Object.entries(refs)) {
        const el = byToken.get(token);
        if (el) el.setAttribute(pickAttr, ref);
      }
    },
    /** Hidden while main takes a crop, so the outline is not in the picture. */
    hide(hidden) {
      host.style.display = hidden ? "none" : "block";
    },
  };
  globalThis.__gaiaPicker = api;
  return true;
})
