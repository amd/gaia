# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Page → compact ref-addressable snapshot.

The observation format is the load-bearing performance decision. The model
prefills at ~387 tok/s on the reference box, so every token in a snapshot is
~2.6 ms of wall clock; a raw DOM dump or a screenshot costs thousands of
tokens per step where this costs a few hundred.

So the snapshot carries only what a decision needs: interactive elements,
visible, each with a stable ``ref`` the model cites back. Clicking resolves
``ref`` to a real selector — never coordinates, which a 4B model cannot
predict reliably.

The ref is stamped onto the element as ``data-gaia-ref`` so the follow-up
``click``/``type`` is an exact selector match rather than a re-scrape.
"""

from __future__ import annotations

from typing import Any, Dict, List

#: Attribute stamped on each snapshotted element; also the click selector.
REF_ATTR = "data-gaia-ref"

#: Ceiling on elements returned in one snapshot. A page with more than this is
#: almost always a list/feed where the first N carry the affordances; the
#: snapshot says it truncated so the model knows to scroll or refine.
MAX_ELEMENTS = 120

#: Max characters of accessible name kept per element.
MAX_NAME_CHARS = 120

#: Max characters of readable page text returned with a snapshot. Separate from
#: the name cap on purpose — see the two helpers in the injected script.
MAX_TEXT_CHARS = 4000

#: Table extraction limits. A data table is worth its tokens; a page of them is
#: not, and a 200-row table would swamp the step on its own.
MAX_TABLES = 3
MAX_TABLE_ROWS = 30
MAX_TABLE_COLS = 12


# Collects interactive elements, stamps a ref on each, returns compact records.
#
# Runs in the page, not in Python, so the whole traversal is one CDP round trip
# instead of one per element. Skips hidden, zero-size, and `inert` subtrees the
# way a user's eye would.
_SNAPSHOT_JS = """
(args) => {
  const { attr, maxElements, maxNameChars, maxTextChars,
          maxTables, maxTableRows, maxTableCols } = args;
  const SELECTOR = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]',
    '[role=tab]', '[role=menuitem]', '[role=option]', '[role=switch]',
    '[role=textbox]', '[role=combobox]', '[role=searchbox]',
    '[contenteditable=""]', '[contenteditable=true]', '[onclick]',
  ].join(',');

  // Two caps, deliberately separate. `clean` is for an element's accessible
  // name; `squash` only normalises whitespace. Sharing one helper capped the
  // whole page text at the NAME length — 120 chars of a 63,000-char article —
  // because the later slice(0, maxTextChars) had nothing left to trim.
  const squash = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const clean = (s) => squash(s).slice(0, maxNameChars);

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return false;
    if (parseFloat(st.opacity || '1') < 0.05) return false;
    if (el.closest('[inert]')) return false;
    // Ancestor, not just self: a11y hiding is inherited, and modals /
    // carousels routinely wrap whole subtrees in aria-hidden.
    if (el.closest('[aria-hidden="true"]')) return false;
    return true;
  };

  // Accessible name, in roughly the order a screen reader resolves it.
  const nameOf = (el) => {
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const parts = labelledby.split(/\\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((n) => n.innerText || n.textContent || '');
      const joined = clean(parts.join(' '));
      if (joined) return joined;
    }
    const aria = clean(el.getAttribute('aria-label'));
    if (aria) return aria;
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) { const t = clean(lab.innerText); if (t) return t; }
    }
    const wrapping = el.closest('label');
    if (wrapping) { const t = clean(wrapping.innerText); if (t) return t; }
    const text = clean(el.innerText || el.textContent);
    if (text) return text;
    for (const a of ['placeholder', 'title', 'alt', 'name', 'value']) {
      const v = clean(el.getAttribute(a));
      if (v) return v;
    }
    return '';
  };

  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'select';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (['submit', 'button', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'password') return 'password';
      if (['file', 'range', 'color', 'date', 'time'].includes(t)) return t;
      return 'textbox';
    }
    return 'clickable';
  };

  // Clear refs from any previous snapshot so stale ids never resolve.
  document.querySelectorAll('[' + attr + ']').forEach((el) => el.removeAttribute(attr));

  const out = [];
  let truncated = false;
  let n = 0;
  for (const el of document.querySelectorAll(SELECTOR)) {
    if (!visible(el)) continue;
    if (out.length >= maxElements) { truncated = true; break; }
    const ref = 'e' + (++n);
    el.setAttribute(attr, ref);
    const rec = { ref, role: roleOf(el), name: nameOf(el) };
    if (el.disabled) rec.disabled = true;
    if (el.checked) rec.checked = true;
    const tag = el.tagName.toLowerCase();
    const role = rec.role;
    if (tag === 'input' || tag === 'textarea') {
      const v = clean(el.value);
      // Never echo a password back into the model's context. For a checkbox
      // or radio the `checked` flag already carries the state, and the value
      // ('on') is pure noise.
      const noisy = role === 'password' || role === 'checkbox' || role === 'radio';
      if (v && !noisy) rec.value = v;
    }
    if (tag === 'select') {
      rec.options = Array.from(el.options).slice(0, 20).map((o) => clean(o.label || o.value));
      // Report what is currently chosen, so the model can verify a selection
      // took rather than guessing from the options list.
      const chosen = el.options[el.selectedIndex];
      if (chosen) rec.value = clean(chosen.label || chosen.value);
    }
    out.push(rec);
  }

  // Tables, kept as a grid. Flattening a forecast or price table into prose
  // destroys the row/column association the numbers only mean anything inside
  // — which is exactly where a model starts inventing values. `fetch_page`
  // has had an extract="tables" mode all along; a live browser should not be
  // worse at the thing it was opened for.
  const tables = [];
  for (const t of document.querySelectorAll('table')) {
    if (!visible(t)) continue;
    const rows = [];
    for (const r of [...t.rows].slice(0, maxTableRows)) {
      const cells = [...r.cells].slice(0, maxTableCols).map((c) => clean(c.innerText));
      if (cells.some((c) => c)) rows.push(cells);
    }
    // Two rows is the floor for a data table; one row is usually layout.
    if (rows.length >= 2) tables.push(rows);
    if (tables.length >= maxTables) break;
  }

  return {
    url: location.href,
    title: document.title || '',
    tables,
    elements: out,
    truncated,
    // Readable page text, capped. Gives the model page content without a
    // second round trip, and without the markup a raw DOM dump would carry.
    text: squash((document.body && document.body.innerText) || '').slice(0, maxTextChars),
  };
}
"""


def snapshot_args() -> Dict[str, Any]:
    """Arguments passed into :data:`_SNAPSHOT_JS`."""
    return {
        "attr": REF_ATTR,
        "maxElements": MAX_ELEMENTS,
        "maxNameChars": MAX_NAME_CHARS,
        "maxTextChars": MAX_TEXT_CHARS,
        "maxTables": MAX_TABLES,
        "maxTableRows": MAX_TABLE_ROWS,
        "maxTableCols": MAX_TABLE_COLS,
    }


def ref_selector(ref: str) -> str:
    """CSS selector addressing the element stamped with ``ref``."""
    # Refs are generated in-page as e<digits>; reject anything else rather than
    # interpolate caller-supplied text into a selector.
    if not ref or not ref.startswith("e") or not ref[1:].isdigit():
        raise ValueError(
            f"Invalid element ref {ref!r}. Refs look like 'e12' and come from "
            "browser_snapshot()."
        )
    return f'[{REF_ATTR}="{ref}"]'


def render(snap: Dict[str, Any], include_text: bool = True) -> str:
    """Render a snapshot for the model.

    One line per element — ``ref role "name"`` — which is the cheapest form
    that still lets the model say which thing it means.
    """
    lines: List[str] = [
        f"Page: {snap.get('title') or '(untitled)'}",
        f"URL: {snap.get('url', '')}",
        "",
    ]

    elements = snap.get("elements") or []
    if elements:
        lines.append(f"Interactive elements ({len(elements)}):")
        for el in elements:
            bits = [f"  {el['ref']} {el.get('role', '?')}"]
            name = el.get("name")
            if name:
                bits.append(f'"{name}"')
            if el.get("value"):
                bits.append(f"(value: {el['value']})")
            if el.get("options"):
                opts = ", ".join(el["options"][:8])
                bits.append(f"(options: {opts})")
            if el.get("checked"):
                bits.append("(checked)")
            if el.get("disabled"):
                bits.append("(disabled)")
            lines.append(" ".join(bits))
    else:
        lines.append("Interactive elements: none found.")

    if snap.get("truncated"):
        lines.append(f"  ... truncated at {MAX_ELEMENTS} elements — the page has more.")

    for i, rows in enumerate(snap.get("tables") or [], 1):
        lines.extend(["", f"Table {i} ({len(rows)} rows):"])
        for row in rows:
            lines.append("  " + " | ".join(row))
        if len(rows) >= 30:
            lines.append("  ... more rows not shown")

    if include_text and snap.get("text"):
        lines.extend(["", "Page text:", snap["text"]])

    return "\n".join(lines)
