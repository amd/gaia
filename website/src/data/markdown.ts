// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Minimal, dependency-free Markdown → HTML renderer for the subset used in agent
// READMEs and CHANGELOGs: headings, fenced code blocks, inline code, bold,
// blockquotes, unordered lists, links, images, tables, horizontal rules, and
// paragraphs.
//
// PRESENTATION lives in the page stylesheet, not here. This renderer emits clean
// SEMANTIC HTML (<h2>, <p>, <pre>, <table>, …) with no styling classes; the
// `.doc` prose theme on the Hub agent page styles those tags. (Earlier this file
// baked in `gaia-*` utility classes that the Tailwind theme never defined, so
// they were dead no-ops — the fix is one stylesheet owning the look.)
//
// SECURITY CONTRACT (hub markdown is UNTRUSTED — it arrives from the live hub
// catalog via HUB_CATALOG_URL, authored by third-party publishers):
//   * Raw HTML is NEVER passed through. Every line of source text is run through
//     escapeHtml() before any markup is emitted, so a `<script>`/`<img onerror>`
//     in the source renders as inert escaped text, not a live tag. This renderer
//     only ever emits the fixed set of tags it generates itself.
//   * Link targets are scheme-allowlisted (no `javascript:`/`data:`), and quotes
//     are stripped from the href so the attribute can't be broken out of.
// This contract is proven by markdown.test.ts — keep that green. If the markdown
// ever needs richer features, swap this for a hardened renderer + a real
// sanitizer (e.g. markdown-it + DOMPurify) rather than widening the subset ad hoc.

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// Inline formatting: code spans, bold, then links. Order matters — code spans
// are extracted first so their contents are not re-processed.
function renderInline(text: string): string {
  const codeSpans: string[] = [];
  let out = text.replace(/`([^`]+)`/g, (_m, code) => {
    codeSpans.push(`<code>${escapeHtml(code)}</code>`);
    return ` ${codeSpans.length - 1} `;
  });

  out = escapeHtml(out);
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // Images: ![alt](src). Run BEFORE links so the inner `[alt](src)` isn't half
  // consumed by the link rule. Same untrusted-source stance as links: the src is
  // scheme-allowlisted to https or repo-relative paths (no data:/javascript:),
  // and quotes are kept out of both attributes. Raw <img> HTML stays escaped by
  // escapeHtml above — this `![]()` form is the ONLY path that emits an <img>.
  out = out.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, src) => {
    if (!/^(https:\/\/|\/|\.\/|\.\.\/)/i.test(src)) return match;
    const safeSrc = src.replace(/"/g, '%22');
    const safeAlt = alt.replace(/"/g, '&quot;');
    return `<img src="${safeSrc}" alt="${safeAlt}" loading="lazy" />`;
  });
  // Markdown can come from third-party publishers (HUB_CATALOG_URL mode):
  // allow only benign link schemes and keep quotes out of the href attribute.
  out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, href) => {
    if (!/^(https?:\/\/|\/|#|\.\/|\.\.\/|mailto:)/i.test(href)) return match;
    const safe = href.replace(/"/g, '%22');
    return `<a href="${safe}">${text}</a>`;
  });

  out = out.replace(/ (\d+) /g, (_m, i) => codeSpans[Number(i)]);
  return out;
}

// A horizontal rule line: `---`, `***`, or `___` (3+), nothing else. Kept
// distinct from a GFM table delimiter (which always contains pipes).
function isHr(line: string): boolean {
  return /^(-{3,}|\*{3,}|_{3,})$/.test(line.trim());
}

// A line that begins a non-paragraph block (or is blank). Used to stop
// paragraph accumulation so a paragraph never swallows a heading/list/table/etc.
function isBlockStart(line: string): boolean {
  return (
    line.trim() === '' ||
    line.startsWith('```') ||
    line.startsWith('>') ||
    /^(#{1,4})\s+/.test(line) ||
    /^[-*]\s+/.test(line) ||
    isHr(line) ||
    /^\s*\|.*\|\s*$/.test(line)
  );
}

// Split a `| a | b |` table row into trimmed cell strings, honoring `\|`
// escapes so an escaped pipe stays literal text inside a cell.
function splitTableRow(line: string): string[] {
  const inner = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  const cells: string[] = [];
  let cur = '';
  for (let k = 0; k < inner.length; k++) {
    if (inner[k] === '\\' && inner[k + 1] === '|') {
      cur += '|';
      k++;
    } else if (inner[k] === '|') {
      cells.push(cur.trim());
      cur = '';
    } else {
      cur += inner[k];
    }
  }
  cells.push(cur.trim());
  return cells;
}

// A GFM table delimiter row: every cell is dashes with optional alignment
// colons (`---`, `:--`, `:-:`, `--:`). This is what distinguishes a real table
// from an ordinary line that merely contains pipes.
function isTableDelimiter(line: string): boolean {
  if (!/^\s*\|.*\|\s*$/.test(line)) return false;
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((c) => /^:?-+:?$/.test(c));
}

/** Render a Markdown string to an HTML string (narrow, in-repo subset). */
export function renderMarkdown(md: string): string {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const html: string[] = [];
  let i = 0;
  let listOpen = false;

  const closeList = () => {
    if (listOpen) {
      html.push('</ul>');
      listOpen = false;
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block. The info string (```ts) becomes a `language-*` class for
    // the client-side highlighter; sanitized to [a-z0-9-] so it can't break out
    // of the attribute (the body is still escaped, as everywhere).
    if (line.startsWith('```')) {
      closeList();
      const lang = line.slice(3).trim().toLowerCase().replace(/[^a-z0-9-]/g, '');
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) {
        body.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      const cls = lang ? ` class="language-${lang}"` : '';
      html.push(`<pre><code${cls}>${escapeHtml(body.join('\n'))}</code></pre>`);
      continue;
    }

    // Headings
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    // Horizontal rule
    if (isHr(line)) {
      closeList();
      html.push('<hr />');
      i++;
      continue;
    }

    // Blockquote
    if (line.startsWith('>')) {
      closeList();
      const body: string[] = [];
      while (i < lines.length && lines[i].startsWith('>')) {
        body.push(lines[i].replace(/^>\s?/, ''));
        i++;
      }
      html.push(`<blockquote>${renderInline(body.join(' '))}</blockquote>`);
      continue;
    }

    // GFM table: a `| header |` row, a `|---|---|` delimiter, then body rows.
    // Cells are rendered through renderInline, so the escape/scheme-allowlist
    // security contract applies to cell content exactly as it does elsewhere.
    if (
      /^\s*\|.*\|\s*$/.test(line) &&
      i + 1 < lines.length &&
      isTableDelimiter(lines[i + 1])
    ) {
      closeList();
      const header = splitTableRow(line);
      i += 2; // consume the header row and the delimiter row
      const bodyRows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        bodyRows.push(splitTableRow(lines[i]));
        i++;
      }
      const th = header.map((c) => `<th>${renderInline(c)}</th>`).join('');
      const tbody = bodyRows
        .map((row) => {
          const tds = header
            .map((_h, ci) => `<td>${renderInline(row[ci] ?? '')}</td>`)
            .join('');
          return `<tr>${tds}</tr>`;
        })
        .join('');
      html.push(`<table><thead><tr>${th}</tr></thead><tbody>${tbody}</tbody></table>`);
      continue;
    }

    // Unordered list item (with lazy continuation: soft-wrapped follow-on lines
    // fold into the same <li> instead of leaking out as a sibling paragraph).
    if (/^[-*]\s+/.test(line)) {
      if (!listOpen) {
        html.push('<ul>');
        listOpen = true;
      }
      const item: string[] = [line.replace(/^[-*]\s+/, '')];
      i++;
      while (i < lines.length && !isBlockStart(lines[i])) {
        item.push(lines[i].trim());
        i++;
      }
      html.push(`<li>${renderInline(item.join(' '))}</li>`);
      continue;
    }

    // Blank line
    if (line.trim() === '') {
      closeList();
      i++;
      continue;
    }

    // Paragraph: join consecutive soft-wrapped lines into ONE paragraph (as
    // Markdown does), so inline formatting that spans a wrap — e.g. **bold**
    // broken across two source lines — still renders.
    closeList();
    const para: string[] = [];
    while (i < lines.length && !isBlockStart(lines[i])) {
      para.push(lines[i]);
      i++;
    }
    if (para.length === 0) {
      // A block-start line no branch consumed -- in practice a `| ... |` row with
      // no delimiter row, so the table branch skipped it. Render it as prose so
      // the content isn't lost, and ALWAYS advance i so the outer loop can't spin
      // forever on the same line.
      html.push(`<p>${renderInline(line)}</p>`);
      i++;
      continue;
    }
    html.push(`<p>${renderInline(para.join(' '))}</p>`);
  }

  closeList();
  return html.join('\n');
}
