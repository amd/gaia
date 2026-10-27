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
//   * Anchor `href` values resolve only to `https:`, `http:`, or `mailto:`,
//     plus the backend's own chat-upload URLs (`/api/files/uploads/<file>`).
//     Anything else (including `javascript:`, `data:`, `vbscript:`) becomes
//     an empty string so React renders the anchor as an inert
//     `<a href="">` rather than a navigatable link.
//
// `mailto:` is acceptable for catalog/developer-authored content; it should
// be removed from this allow list if a future surface lets untrusted /
// LLM-generated content drive `<SafeMarkdown>` directly without an
// additional review pass.

import { getApiBase } from './apiBase';

export const SAFE_DISALLOWED_ELEMENTS: ReadonlyArray<string> = [
    'script',
    'iframe',
    'object',
    'embed',
    'style',
] as const;

/** A chat attachment the composer linked: one plain file name, no path. */
const UPLOAD_URL_RE = /^\/api\/files\/uploads\/([A-Za-z0-9][A-Za-z0-9._-]*)$/;

/**
 * URL transformer for ReactMarkdown's `urlTransform` prop.
 * Returns the URL untouched if its scheme is in the allow list, otherwise
 * returns an empty string (renders an inert anchor).
 *
 * Schemes are matched case-insensitively. Apart from the upload links below,
 * only fragment-only URLs (`#anchor`) pass through without a scheme: the
 * renderer is loaded from `file://`, so a root-relative
 * (`/Windows/System32/calc.exe`), UNC (`//host/share/x.exe`) or plain
 * relative href resolves to a `file:` URL that the shell would hand to
 * ShellExecute. Those are inert here.
 *
 * The one scheme-less exception is a chat attachment the composer linked:
 * `/api/files/uploads/<file>`, where `<file>` is a single plain file name.
 * It is pointed at the backend (`getApiBase()`), never at `file://`.
 */
export function safeUrlTransform(url: string): string {
    if (!url) return '';

    // Same-page anchors.
    if (url.startsWith('#')) return url;

    const upload = UPLOAD_URL_RE.exec(url);
    if (upload) return `${getApiBase()}/files/uploads/${upload[1]}`;

    // Extract scheme (case-insensitive). RFC 3986: scheme = ALPHA *(ALPHA / DIGIT / "+" / "-" / ".")
    const colonIdx = url.indexOf(':');
    if (colonIdx === -1) return ''; // scheme-less — resolves against file://

    const scheme = url.slice(0, colonIdx).toLowerCase();
    if (scheme === 'https' || scheme === 'http' || scheme === 'mailto') {
        return url;
    }
    return '';
}
