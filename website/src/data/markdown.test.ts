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
});
