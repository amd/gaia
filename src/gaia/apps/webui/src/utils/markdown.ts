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

/**
 * One upload name as the browser link carries it: the characters the backend's
 * `quote(name, safe="")` leaves alone, or a byte escape. Spaces and non-Latin
 * text travel percent-encoded because a Markdown link ends at a space.
 */
const UPLOAD_NAME = String.raw`(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})+`;

/** A chat attachment the composer linked: one plain file name, no path. */
const UPLOAD_URL_RE = new RegExp(`^/api/files/uploads/(${UPLOAD_NAME})$`);

/** One attachment link the composer appends to a sent message. */
export interface AttachmentLink {
    name: string;
    markdown: string;
}

const ATTACHMENT_LINE_RE = new RegExp(
    `^!?\\[([^\\]\\n]*)\\]\\((/api/files/uploads/${UPLOAD_NAME})\\)$`,
);

/**
 * The encoded file name in an upload URL, or null when the URL is not one
 * plain file inside the uploads folder. The name is decoded before checking,
 * so `%2e%2e%2f` cannot smuggle a traversal step past the allow list.
 */
function uploadName(url: string): string | null {
    const match = UPLOAD_URL_RE.exec(url);
    if (!match) return null;
    let name: string;
    try {
        name = decodeURIComponent(match[1]);
    } catch {
        return null;
    }
    if (!name || name.startsWith('.') || name !== name.replace(/^[ .]+|[ .]+$/g, '')) return null;
    if (/[/\\:\u0000-\u001f\u007f]/.test(name)) return null;
    return match[1];
}

function attachmentLine(line: string): AttachmentLink | null {
    const m = ATTACHMENT_LINE_RE.exec(line);
    if (!m || uploadName(m[2]) === null) return null;
    return { name: m[1], markdown: line };
}

/**
 * Splits a sent message into the typed text and the attachment links the
 * composer wrote after it (`text`, a blank line, then one link per line).
 */
export function splitAttachmentLinks(content: string): { text: string; links: AttachmentLink[] } {
    const lines = content.split('\n');
    let start = lines.length;
    while (start > 0 && attachmentLine(lines[start - 1]) !== null) start--;
    if (start === lines.length || (start > 0 && lines[start - 1] !== '')) return { text: content, links: [] };
    const links = lines.slice(start).map((line) => attachmentLine(line)!);
    return { text: lines.slice(0, Math.max(0, start - 1)).join('\n'), links };
}

/** Puts attachment links back after edited text, the way the composer wrote them. */
export function joinAttachmentLinks(text: string, links: AttachmentLink[]): string {
    if (links.length === 0) return text;
    const block = links.map((l) => l.markdown).join('\n');
    return text ? `${text}\n\n${block}` : block;
}

/** True for a chat attachment link after `safeUrlTransform` has pointed it at the backend. */
export function isUploadUrl(url: string | undefined): url is string {
    return !!url && url.startsWith(`${getApiBase()}/files/uploads/`);
}

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

    const upload = uploadName(url);
    if (upload !== null) return `${getApiBase()}/files/uploads/${upload}`;

    // Extract scheme (case-insensitive). RFC 3986: scheme = ALPHA *(ALPHA / DIGIT / "+" / "-" / ".")
    const colonIdx = url.indexOf(':');
    if (colonIdx === -1) return ''; // scheme-less — resolves against file://

    const scheme = url.slice(0, colonIdx).toLowerCase();
    if (scheme === 'https' || scheme === 'http' || scheme === 'mailto') {
        return url;
    }
    return '';
}
