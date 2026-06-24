// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { renderMarkdown } from './markdown';

// Hub READMEs are untrusted (HUB_CATALOG_URL → third-party publishers). These
// tests pin the security contract documented in markdown.ts: raw HTML, event
// handlers, and dangerous link schemes must never survive into the output as
// LIVE markup. (Appearing as inert, HTML-escaped *text* is fine and expected —
// that is exactly what neutralizing them looks like.)
describe('renderMarkdown sanitization', () => {
  it('neutralizes a raw <script> tag', () => {
    const html = renderMarkdown('Hello\n\n<script>alert("xss")</script>');
    // No live tag…
    expect(html).not.toContain('<script');
    expect(html).not.toContain('</script>');
    // …it is rendered as inert escaped text instead.
    expect(html).toContain('&lt;script&gt;');
  });

  it('neutralizes an <img onerror=...> handler', () => {
    const html = renderMarkdown('<img src=x onerror="alert(1)">');
    expect(html).not.toContain('<img'); // never a live element
    // No live tag carries an on* event handler attribute.
    expect(html).not.toMatch(/<[a-z][a-z0-9]*\b[^>]*\son\w+=/i);
    expect(html).toContain('&lt;img'); // present only as escaped text
  });

  it('drops a javascript: link target (scheme allowlist)', () => {
    const html = renderMarkdown('[click me](javascript:alert(1))');
    // The disallowed scheme must not become a real anchor href…
    expect(html).not.toMatch(/<a\s[^>]*href=["']?\s*javascript:/i);
    expect(html).not.toContain('href="javascript');
    // …it stays as inert source text (no anchor emitted at all here).
    expect(html).not.toContain('<a ');
  });

  it('escapes inline HTML smuggled inside a heading', () => {
    const html = renderMarkdown('# Title <img src=x onerror=alert(1)>');
    expect(html).toContain('<h1'); // the heading the renderer emits
    expect(html).not.toContain('<img'); // the smuggled tag is escaped
    expect(html).not.toMatch(/<[a-z][a-z0-9]*\b[^>]*\son\w+=/i);
    expect(html).toContain('&lt;img');
  });

  it('renders a markdown image with an https src and escaped alt', () => {
    const html = renderMarkdown('![arch diagram](https://raw.githubusercontent.com/amd/gaia/main/x.webp)');
    expect(html).toContain('<img src="https://raw.githubusercontent.com/amd/gaia/main/x.webp"');
    expect(html).toContain('alt="arch diagram"');
    expect(html).toContain('loading="lazy"');
  });

  it('allows a repo-relative image src', () => {
    const html = renderMarkdown('![local](./assets/architecture.webp)');
    expect(html).toContain('<img src="./assets/architecture.webp"');
  });

  it('drops a javascript:/data: image src (no live <img>)', () => {
    const js = renderMarkdown('![x](javascript:alert(1))');
    expect(js).not.toContain('<img');
    const data = renderMarkdown('![x](data:image/svg+xml;base64,PHN2Zz4=)');
    expect(data).not.toContain('<img');
  });

  it('keeps quotes out of the image src/alt attributes', () => {
    const html = renderMarkdown('![a" onerror="alert(1)](https://x/y".webp)');
    // The smuggled quotes are escaped, so neither attribute can be broken out of:
    // there is no live `onerror="` attribute, and the quote became an entity.
    expect(html).toContain('<img ');
    expect(html).not.toContain('onerror="');
    expect(html).toContain('&quot;');
  });

  it('renders a GFM table with header and body cells', () => {
    const md = '| Endpoint | Auth |\n|----------|------|\n| `/triage` | **Standalone** |\n| `/send` | Connector |';
    const html = renderMarkdown(md);
    expect(html).toContain('<table');
    expect(html).toContain('<th');
    expect(html).toContain('<td');
    expect(html).toContain('<code'); // inline formatting inside a cell
    expect(html).toContain('<strong'); // bold inside a cell
    expect((html.match(/<tr>/g) || []).length).toBe(3); // 1 header + 2 body rows
  });

  it('does NOT treat a lone pipe line (no delimiter) as a table', () => {
    const html = renderMarkdown('a | b | c is just prose');
    expect(html).not.toContain('<table');
    expect(html).toContain('<p');
  });

  // Regression: a pipe-wrapped line (isBlockStart === true) with no delimiter row
  // is consumed by neither the table branch nor the paragraph loop, so the
  // renderer used to spin forever on it — a hang on untrusted hub-README content.
  it('does not hang on a `| … |` line that has no following delimiter row', () => {
    const html = renderMarkdown('| some prose |\nnext line');
    expect(html).not.toContain('<table'); // no delimiter → not a table
    expect(html).toContain('next line'); // the loop advanced past the orphan
    expect(html).toContain('some prose'); // and the orphan content is preserved
  });

  it('escapes raw HTML smuggled inside a table cell', () => {
    const md = '| x |\n|---|\n| <img src=x onerror=alert(1)> |';
    const html = renderMarkdown(md);
    expect(html).toContain('<table');
    expect(html).not.toContain('<img'); // smuggled tag is escaped, not live
    expect(html).toContain('&lt;img');
  });

  it('still renders benign markdown (links, bold, code)', () => {
    const html = renderMarkdown('See **GAIA** at [the site](https://amd-gaia.ai) and run `gaia email`.');
    expect(html).toContain('<strong');
    expect(html).toContain('<a href="https://amd-gaia.ai"');
    expect(html).toContain('<code');
  });

  it('renders bold that spans a soft-wrapped line break (one paragraph)', () => {
    const html = renderMarkdown('leaves your mailbox — **send, forward,\nand RSVPs** — asks first.');
    expect(html).toContain('<strong');
    expect(html).not.toContain('**'); // no leftover literal markers
    expect((html.match(/<p[\s>]/g) || []).length).toBe(1); // joined into one paragraph
  });

  // Regression: a README's numbered steps (e.g. Prerequisites) must render as an
  // <ol>, not collapse into a run-on paragraph with literal "1." / "2." text.
  it('renders an ordered list as <ol>, not a paragraph', () => {
    const html = renderMarkdown('Before it works:\n\n1. Start Lemonade.\n2. Pull the model.\n');
    expect(html).toContain('<ol>');
    expect(html).toContain('</ol>');
    expect((html.match(/<li>/g) || []).length).toBe(2);
    expect(html).not.toMatch(/<p[^>]*>\s*1\./); // no literal "1." leaking into prose
    expect(html).not.toContain('<ul>'); // ordered, not unordered
  });

  it('keeps ordered and unordered lists separate (switching marker starts a new list)', () => {
    const html = renderMarkdown('- bullet a\n- bullet b\n\n1. step one\n2. step two\n');
    expect(html).toContain('<ul>');
    expect(html).toContain('<ol>');
    expect((html.match(/<li>/g) || []).length).toBe(4);
  });

  // Regression: single-asterisk italic (e.g. README's *Settings → Connectors*)
  // must become <em>, not show literal asterisks — without eating **bold** or
  // mangling snake_case identifiers.
  // Regression: a literal number that follows a code span (e.g. "`HttpError` 502")
  // must survive — the code-span placeholder must not collide with prose digits,
  // or "502"/"8" get replaced with codeSpans[502] = undefined.
  it('preserves literal numbers after a code span (no placeholder collision)', () => {
    const html = renderMarkdown('`HttpError` 502 from `triage`, also 8 GB and 0 retries.');
    expect(html).not.toContain('undefined');
    expect(html).toContain('502');
    expect(html).toContain('8 GB');
    expect(html).toContain('0 retries');
    expect(html).toContain('<code>HttpError</code>');
    expect(html).toContain('<code>triage</code>');
  });

  // Regression: in-doc anchor links (e.g. README's [...](#browser--electron-renderer))
  // must resolve on the hub — headings need GitHub-compatible id slugs.
  it('emits GitHub-compatible heading id slugs for in-doc anchors', () => {
    const html = renderMarkdown('### Browser / Electron renderer\n\nbody');
    expect(html).toContain('id="browser--electron-renderer"');
    const codeHeading = renderMarkdown('## The `fetch` CLI');
    expect(codeHeading).toContain('id="the-fetch-cli"'); // backticks dropped
  });

  it('renders *italic* as <em> without clashing with **bold** or underscores', () => {
    const html = renderMarkdown('Open *Settings* but keep **bold** and `suggested_action` and plain suggested_action.');
    expect(html).toContain('<em>Settings</em>');
    expect(html).toContain('<strong>bold</strong>');
    expect(html).not.toMatch(/\*[A-Za-z]/); // no leftover literal asterisk emphasis
    expect(html).toContain('suggested_action'); // underscores are not emphasis
    expect(html).not.toContain('<em>action'); // _action_ was NOT italicized
  });
});
