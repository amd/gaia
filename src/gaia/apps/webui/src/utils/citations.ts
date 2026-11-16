// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Find library documents cited in an answer, and the pages cited after them.
 *
 * "According to report.pdf, pages 175 and 205" / "وفق `report.pdf`، الصفحتان 6–7".
 * Only filenames in the library match, so an unknown name stays plain text.
 * A dropped file is stored as `<id>_report.pdf`, so its stored name and full
 * path match too. Two documents sharing a name are told apart by their path;
 * the bare name links neither.
 */

export interface LibraryDoc {
    id: string;
    filename: string;
    filepath?: string;
}

/** A cited document, with the name its link shows. */
export interface CitedDoc {
    id: string;
    filename: string;
    /** The filename, or for one another document shares, its last folders too. */
    label: string;
}

export interface CitationIndex {
    /** Library filenames, longest first, optionally behind a folder path. */
    re: RegExp | null;
    /** Lowercase filename or stored file name; null when two documents share it. */
    byName: Map<string, CitedDoc | null>;
    /** Full path, lowercase with forward slashes. */
    byPath: Map<string, CitedDoc>;
}

export interface FoundCitation {
    start: number;
    end: number;
    doc: CitedDoc;
}

export interface FoundPage {
    start: number;
    end: number;
    page: number;
}

/** How far after a filename its page reference may start. */
export const PAGE_GAP_CHARS = 40;

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// A Windows or Linux folder path in front of the name, so the whole path becomes the link.
const SEGMENT = String.raw`[^\s*?"<>|,;)}\]\\/]+[\\/]`;
const FOLDER_PREFIX = String.raw`(?:(?:[A-Za-z]:[\\/]|(?<![\p{L}\p{N}_.\-\\/])/)(?:${SEGMENT})*)?`;
// A filename stands alone: no name character touches it on either side.
const NAME_BEFORE = String.raw`(?<![\p{L}\p{N}_.\-])`;
const NAME_AFTER = String.raw`(?![\p{L}\p{N}_\-]|\.[\p{L}\p{N}])`;

const pathKey = (path: string) => path.trim().replace(/\\/g, '/').toLowerCase();
const baseName = (path: string) => path.trim().split(/[\\/]/).pop() ?? '';
const folders = (path: string) => path.split(/[\\/]/).slice(0, -1).filter(Boolean);

/** Each document's link text: a shared filename gets as many of its last folders as tell the copies apart. */
function linkLabels(docs: readonly LibraryDoc[]): Map<LibraryDoc, string> {
    const groups = new Map<string, LibraryDoc[]>();
    for (const d of docs) groups.set(d.filename.toLowerCase(), [...(groups.get(d.filename.toLowerCase()) ?? []), d]);
    const labels = new Map<LibraryDoc, string>();
    for (const group of groups.values()) {
        const depth = Math.max(...group.map((d) => folders(d.filepath ?? '').length));
        let picked: string[] | null = null;
        for (let k = 1; group.length > 1 && k <= depth && !picked; k++) {
            const tried = group.map((d) => [...folders(d.filepath ?? '').slice(-k), d.filename].join('/'));
            if (new Set(tried.map((s) => s.toLowerCase())).size === group.length) picked = tried;
        }
        group.forEach((d, i) => labels.set(d, picked ? picked[i] : d.filename));
    }
    return labels;
}

export function buildCitationIndex(docs: readonly LibraryDoc[]): CitationIndex {
    const named = docs.filter((d) => d.filename);
    const labels = linkLabels(named);
    const byName = new Map<string, CitedDoc | null>();
    const byPath = new Map<string, CitedDoc>();
    for (const d of named) {
        const cited: CitedDoc = { id: d.id, filename: d.filename, label: labels.get(d)! };
        const names = new Set([d.filename.toLowerCase()]);
        if (d.filepath) {
            names.add(baseName(d.filepath).toLowerCase());
            byPath.set(pathKey(d.filepath), cited);
        }
        for (const name of names) {
            const held = byName.get(name);
            byName.set(name, held === undefined || held?.id === d.id ? cited : null);
        }
    }
    if (byName.size === 0) return { re: null, byName, byPath };
    const names = [...byName.keys()].sort((a, b) => b.length - a.length).map(escapeRe).join('|');
    return { re: new RegExp(`${FOLDER_PREFIX}${NAME_BEFORE}(${names})${NAME_AFTER}`, 'giu'), byName, byPath };
}

/** The library document a token (filename or full path) names, or null. */
export function docForToken(index: CitationIndex, token: string): CitedDoc | null {
    return index.byPath.get(pathKey(token)) ?? index.byName.get(baseName(token).toLowerCase()) ?? null;
}

/** Every library filename (or path to one) in `text`, in order. */
export function findCitations(index: CitationIndex, text: string): FoundCitation[] {
    if (!index.re) return [];
    const found: FoundCitation[] = [];
    for (const m of text.matchAll(index.re)) {
        const end = m.index! + m[0].length;
        const doc = docForToken(index, m[0]);
        if (doc) {
            found.push({ start: m.index!, end, doc });
            continue;
        }
        const spaced = storedPathEndingAt(index, text, end, baseName(m[0]).toLowerCase());
        if (spaced) found.push({ start: spaced.start, end, doc: spaced.doc });
    }
    return found;
}

/** A stored path the text spells out in full up to `end`, for a shared name whose folders hold spaces. */
function storedPathEndingAt(
    index: CitationIndex, text: string, end: number, name: string,
): { start: number; doc: CitedDoc } | null {
    const before = text.slice(0, end).replace(/\\/g, '/').toLowerCase();
    for (const [key, doc] of index.byPath) {
        if (baseName(key) === name && before.endsWith(key)) return { start: end - key.length, doc };
    }
    return null;
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
