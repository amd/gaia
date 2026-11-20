// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Finds Windows file paths in answer text. */

/** Regex to detect Windows file paths like C:\Users\... or C:/Users/... */
// Path separators are excluded from the segment class so each `(segment sep)`
// repetition has exactly one parse — the ambiguity CodeQL flagged as
// exponential-backtracking ReDoS (js/redos) on adversarial non-matching input.
// Exactly one separator between segments, so a last segment without an
// extension (`C:\Users\me\notes`) is kept as well as `...\a.txt` or `...\dir\`.
// A space stays inside a folder name a `\` follows (`Program Files (x86)\`) and
// inside a last name of at most three words ending in an extension (`Q3 report.txt`);
// otherwise it ends the path, so "C:\out\docs and look at a.pdf" stops at `docs`.
// A path in double quotes runs to the closing quote, spaces and all.
export const WIN_PATH_RE = /(?<=["\u201c])[A-Z]:[\\/][^*?"<>|\r\n\u201c\u201d]+(?=["\u201d])|\b[A-Z]:[\\/](?=[^\s*?"<>|,;)}\]\\/])(?:[^\s*?"<>|,;)}\]\\/]+(?:(?: +(?:[^\s*?"<>|,;:)}\]\\/]+|\([^\s*?"<>|,;:)}\]\\/]+\)))+\\|[\\/]))*(?:(?:[^\s*?"<>|,;:.)}\]\\/]+ +){1,2}[^\s*?"<>|,;)}\]\\/]+\.[A-Za-z0-9]{1,5}\b|[^\s*?"<>|,;)}\]\\/]+)?/gi;
/** Sentence punctuation the path regex swallows: "see C:\a\b." ends at `b`. */
const PATH_TRAILING_PUNCT_RE = /[.:!']+$/;
/** An inline-code span that is one absolute path and nothing else, e.g. "Saved to `C:\out\answer.md`". */
export const WIN_PATH_ONLY_RE = new RegExp(`^(?:${WIN_PATH_RE.source})$`, 'i');

/** Each Windows path in `text`; a quoted one is kept exactly, a trailing dot and all. */
export function findFilePaths(text: string): Array<{ start: number; path: string }> {
    return [...text.matchAll(new RegExp(WIN_PATH_RE.source, 'gi'))].map((m) => {
        const end = m.index + m[0].length;
        const quoted = '"“'.includes(text[m.index - 1] ?? '\n') && '"”'.includes(text[end] ?? '\n');
        return { start: m.index, path: quoted ? m[0] : m[0].replace(PATH_TRAILING_PUNCT_RE, '') };
    });
}
