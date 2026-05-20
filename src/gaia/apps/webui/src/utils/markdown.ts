// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Hardened-markdown utilities (#976).
//
// `react-markdown` is used in two contexts in GAIA:
//
//   1. Chat output (LLM-generated content)            — UNTRUSTED.
//   2. Catalog metadata (`instructions_md`, `help_md`) — DEVELOPER-AUTHORED.
//
// Both routes through this module rather than carrying their own plugin
// stacks, so the security posture is uniform. The previous chat pipeline
// used `rehype-raw` without any disallow list or URL-scheme allow list;
// LLM-generated `<script>` tags or `javascript:` URLs would execute in the
// Electron renderer.
//
// Invariants enforced here:
//   * No raw HTML pass-through (no `rehype-raw`).
//   * `<script> <iframe> <object> <embed> <style>` are stripped if they
//     somehow appear in the AST.
//   * Anchor `href` values resolve only to `https:`, `http:`, or `mailto:`.
//     Anything else (including `javascript:`, `data:`, `vbscript:`) becomes
//     an empty string so React renders the anchor as an inert
//     `<a href="">` rather than a navigatable link.
//
// `mailto:` is acceptable for catalog/developer-authored content; it should
// be removed from this allow list if a future surface lets untrusted /
// LLM-generated content drive `<SafeMarkdown>` directly without an
// additional review pass.

export const SAFE_DISALLOWED_ELEMENTS: ReadonlyArray<string> = [
    'script',
    'iframe',
    'object',
    'embed',
    'style',
] as const;

/**
 * URL transformer for ReactMarkdown's `urlTransform` prop.
 * Returns the URL untouched if its scheme is in the allow list, otherwise
 * returns an empty string (renders an inert anchor).
 *
 * Schemes are matched case-insensitively. Relative URLs (no scheme) and
 * fragment-only URLs (`#anchor`) pass through unchanged — they cannot
 * navigate cross-origin.
 */
export function safeUrlTransform(url: string): string {
    if (!url) return '';

    // Allow fragment-only links and same-page anchors.
    if (url.startsWith('#') || url.startsWith('/')) return url;

    // Extract scheme (case-insensitive). RFC 3986: scheme = ALPHA *(ALPHA / DIGIT / "+" / "-" / ".")
    const colonIdx = url.indexOf(':');
    if (colonIdx === -1) return url; // relative URL

    const scheme = url.slice(0, colonIdx).toLowerCase();
    if (scheme === 'https' || scheme === 'http' || scheme === 'mailto') {
        return url;
    }
    return '';
}
