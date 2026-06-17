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
