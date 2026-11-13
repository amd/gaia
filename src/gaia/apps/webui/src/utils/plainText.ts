// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A markdown answer as the plain text a person sees, for the clipboard. */

const FENCE = /(^|\n)[ \t]*```[^\n]*\n([\s\S]*?)(?:(?:(?<=\n)|\n)[ \t]*```[ \t]*(?=\n|$)|$)/g;

function proseToPlain(prose: string): string {
    const code: string[] = [];
    const plain = prose
        .replace(/`([^`\n]+)`/g, (_, text: string) => `\u0000${code.push(text) - 1}\u0000`)
        .replace(/\\([\\`*_{}[\]()#+\-.!|~>])/g, (_, ch: string) => `\u0000${code.push(ch) - 1}\u0000`)
        .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
        .replace(/\[([^\]]+)\]\(([^)]*)\)/g, (_, text: string, url: string) => (text === url || !url ? text : `${text} (${url})`))
        .replace(/^[ \t]*([-*_][ \t]*){3,}$/gm, '')
        .replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, '')
        .replace(/^([ \t]*)>[ \t]?/gm, '$1')
        .replace(/^([ \t]*)[-*+][ \t]+/gm, '$1• ')
        .replace(/^[ \t]*\|?[ \t:|-]+\|[ \t:|-]*\n?/gm, '')
        .replace(/^[ \t]*\|(.*?)\|?[ \t]*$/gm, (_, cells: string) => cells.split('|').map((c) => c.trim()).join('\t'))
        .replace(/(\*\*|\*|~~)(\S(?:[^\n]*?\S)?)\1/g, '$2')
        .replace(/(?<!\w)(__|_)(\S(?:[^\n]*?\S)?)\1(?!\w)/g, '$2');
    return plain.replace(/\u0000(\d+)\u0000/g, (_, i: string) => code[Number(i)]);
}

/** *markdown* with its formatting marks removed; code blocks and links keep their text. */
export function plainAnswerText(markdown: string): string {
    let out = '';
    let last = 0;
    for (const m of markdown.matchAll(FENCE)) {
        const at = m.index ?? 0;
        out += proseToPlain(markdown.slice(last, at)) + m[1] + m[2];
        last = at + m[0].length;
    }
    out += proseToPlain(markdown.slice(last));
    return out.replace(/\n{3,}/g, '\n\n').trim();
}
