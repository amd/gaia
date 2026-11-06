// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Turning a reply's markdown into text a voice can read, in pieces. */

/** The first piece is short so speech starts quickly; the rest are longer. */
export const FIRST_PIECE_CHARS = 180;
export const PIECE_CHARS = 600;

/**
 * The words of a markdown reply, as a person would read them aloud.
 *
 * Code blocks, images, raw URLs and table rules are skipped; links read as
 * their text; list, heading and emphasis marks are dropped.
 */
export function speakableText(markdown: string): string {
    return markdown
        .replace(/```[\s\S]*?(```|$)/g, '\n')
        .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
        .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
        .replace(/<\/?[a-z][^>]*>/gi, '')
        .replace(/https?:\/\/\S+/g, '')
        .replace(/`([^`]*)`/g, '$1')
        .replace(/^[ \t]*([-*_][ \t]*){3,}$/gm, '')
        .replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, '')
        .replace(/^[ \t]*>[ \t]?/gm, '')
        .replace(/^[ \t]*([-*+]|\d+[.)])[ \t]+/gm, '')
        .replace(/^[ \t]*\|?[ \t:|-]+\|[ \t:|-]*$/gm, '')
        .replace(/^[ \t]*\|(.*?)\|?[ \t]*$/gm, (_, cells: string) => cells.split('|').map((c) => c.trim()).filter(Boolean).join(', '))
        .replace(/(\*\*|\*|~~)(\S(?:[\s\S]*?\S)?)\1/g, '$2')
        // Underscores mark emphasis only outside a word, never in snake_case.
        .replace(/(?<!\w)(__|_)(\S(?:[\s\S]*?\S)?)\1(?!\w)/g, '$2')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .join('\n')
        .trim();
}

/** *text* cut at sentence ends into pieces no longer than *max*. */
function cutLong(sentence: string, max: number): string[] {
    const pieces: string[] = [];
    let rest = sentence.trim();
    while (rest.length > max) {
        const window = rest.slice(0, max);
        const at = Math.max(window.lastIndexOf(', '), window.lastIndexOf('; '), window.lastIndexOf(' '));
        const cut = at > max / 3 ? at + 1 : max;
        pieces.push(rest.slice(0, cut).trim());
        rest = rest.slice(cut).trim();
    }
    if (rest) pieces.push(rest);
    return pieces;
}

/**
 * The reply as pieces of whole sentences to request one at a time.
 *
 * Every piece is at most *max* characters, the server's limit; a sentence
 * longer than that is cut at a comma or space.
 */
export function speechPieces(markdown: string, max = 1000): string[] {
    const text = speakableText(markdown);
    if (!text) return [];
    const sentences = text
        .split(/(?<=[.!?…؟])\s+|\n+/)
        .flatMap((s) => cutLong(s, Math.min(PIECE_CHARS, max)));
    const pieces: string[] = [];
    let current = '';
    for (const sentence of sentences) {
        const limit = Math.min(pieces.length === 0 ? FIRST_PIECE_CHARS : PIECE_CHARS, max);
        if (current && current.length + 1 + sentence.length > limit) {
            pieces.push(current);
            current = sentence;
        } else {
            current = current ? `${current} ${sentence}` : sentence;
        }
    }
    if (current) pieces.push(current);
    return pieces;
}
