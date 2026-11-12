// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Find library documents cited in an answer, and the pages cited after them.
 *
 * "According to report.pdf, pages 175 and 205" / "وفق `report.pdf`، الصفحتان 6–7".
 * Only filenames in the library match, so an unknown name stays plain text.
 */

export interface LibraryDoc {
    id: string;
    filename: string;
}

export interface CitationIndex {
    /** Library filenames, longest first, optionally behind a Windows folder path. */
    re: RegExp | null;
    byName: Map<string, LibraryDoc>;
}

export interface FoundCitation {
    start: number;
    end: number;
    doc: LibraryDoc;
}

export interface FoundPage {
    start: number;
    end: number;
    page: number;
}

/** How far after a filename its page reference may start. */
export const PAGE_GAP_CHARS = 40;

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// A Windows folder path in front of the name, so the whole path becomes the link.
const DRIVE_PREFIX = String.raw`(?:[A-Za-z]:[\\/](?:[^\s*?"<>|,;)}\]\\/]+[\\/])*)?`;
// A filename stands alone: no name character touches it on either side.
const NAME_BEFORE = String.raw`(?<![\p{L}\p{N}_.\-])`;
const NAME_AFTER = String.raw`(?![\p{L}\p{N}_\-]|\.[\p{L}\p{N}])`;

export function buildCitationIndex(docs: readonly LibraryDoc[]): CitationIndex {
    const byName = new Map<string, LibraryDoc>();
    for (const d of docs) if (d.filename) byName.set(d.filename.toLowerCase(), d);
    if (byName.size === 0) return { re: null, byName };
    const names = [...byName.keys()].sort((a, b) => b.length - a.length).map(escapeRe).join('|');
    return { re: new RegExp(`${DRIVE_PREFIX}${NAME_BEFORE}(${names})${NAME_AFTER}`, 'giu'), byName };
}

/** The library document a token (filename or full path) names, or null. */
export function docForToken(index: CitationIndex, token: string): LibraryDoc | null {
    const base = token.trim().split(/[\\/]/).pop() ?? '';
    return index.byName.get(base.toLowerCase()) ?? null;
}

/** Every library filename (or path to one) in `text`, in order. */
export function findCitations(index: CitationIndex, text: string): FoundCitation[] {
    if (!index.re) return [];
    const found: FoundCitation[] = [];
    for (const m of text.matchAll(index.re)) {
        const doc = index.byName.get(m[1].toLowerCase());
        if (doc) found.push({ start: m.index!, end: m.index! + m[0].length, doc });
    }
    return found;
}

const DIGITS = '[0-9\\u0660-\\u0669\\u06F0-\\u06F9]+';
const PAGE_WORDS = [
    'pages?', 'pp?\\.',
    // Arabic, with an optional attached "and" / "in" / "to" (و ب ف ل).
    '[وبفل]?(?:الصفحات|الصفحتان|الصفحتين|الصفحة|صفحات|صفحتي|صفحة)', 'ص\\.?',
].join('|');
const SEP = String.raw`\s*(?:,|،|&|and|to|و|إلى|[-–—])\s*`;
const PAGE_REF_RE = new RegExp(
    String.raw`(?<!\p{L})(?:${PAGE_WORDS})(?!\p{L})\s*(${DIGITS}(?:${SEP}${DIGITS})*)`,
    'iu',
);

/** ASCII, Arabic-Indic or Persian digits as a number. */
export function readDigits(digits: string): number {
    return Number(digits.replace(/[٠-٩۰-۹]/g, (d) => String(d.charCodeAt(0) & 0xf)));
}

/** The page numbers cited at the start of `text` (within PAGE_GAP_CHARS), with their positions. */
export function findPages(text: string): FoundPage[] {
    const m = PAGE_REF_RE.exec(text);
    if (!m || m.index > PAGE_GAP_CHARS) return [];
    const listStart = m.index + m[0].length - m[1].length;
    const pages: FoundPage[] = [];
    for (const d of m[1].matchAll(new RegExp(DIGITS, 'g'))) {
        const page = readDigits(d[0]);
        if (page > 0) pages.push({ start: listStart + d.index!, end: listStart + d.index! + d[0].length, page });
    }
    return pages;
}
