// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Text split around a search hit, so the hit can be highlighted. */
export interface MatchParts {
    before: string;
    match: string;
    after: string;
}

// Close to Python's casefold() on the backend, not identical: JS has no full
// case folding, so ß is the one multi-letter fold mapped by hand. The backend
// result fills any gap once it answers.
function foldChar(ch: string): string {
    return ch.normalize('NFKD').replace(/\p{Mn}/gu, '').toLowerCase().replace(/ß/g, 'ss');
}

/** Fold case and accents, as the backend search does: "Résumé" → "resume". */
export function foldText(text: string): string {
    return Array.from(text, foldChar).join('');
}

/** Split `text` around the first accent- and case-insensitive match of `query`. */
export function splitMatch(text: string, query: string): MatchParts | null {
    const needle = foldText(query.trim());
    if (!needle) return null;
    let folded = '';
    const origin: number[] = [];
    let i = 0;
    for (const ch of text) {
        const f = foldChar(ch);
        folded += f;
        for (let k = 0; k < f.length; k++) origin.push(i);
        i += ch.length;
    }
    const at = folded.indexOf(needle);
    if (at < 0) return null;
    const start = origin[at];
    const lastStart = origin[at + needle.length - 1];
    const end = lastStart + String.fromCodePoint(text.codePointAt(lastStart)!).length;
    return { before: text.slice(0, start), match: text.slice(start, end), after: text.slice(end) };
}
