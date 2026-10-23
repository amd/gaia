// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Remark plugin: show Windows paths in prose exactly as written.
 *
 * CommonMark reads `\` before ASCII punctuation as an escape, so
 * `C:\Users\me\.gaia` renders as `C:\Users\me.gaia`. This walks the parsed
 * text nodes and puts the source spelling of each drive (`C:\...`) or UNC
 * (`\\server\...`) path back. Inline code and code blocks are separate node
 * types and are never touched; escapes outside a path keep working.
 * Must run before `remarkSoftBreaks`, whose split text nodes carry no source position.
 */

interface MdNode {
    type: string;
    value?: string;
    children?: MdNode[];
    position?: { start: { offset?: number }; end: { offset?: number } };
}

/**
 * A drive or UNC path in markdown source, up to whitespace, a quote, backtick,
 * `]`, a char Windows forbids, or a `)` that closes no `(` inside the path.
 * An escaped `\*` stays in, so a glob like `C:\logs\*.txt` keeps its backslash;
 * a bare `*` ends the path so emphasis around it still parses.
 */
const RAW_WIN_PATH_RE = /(?:(?<![A-Za-z0-9])[A-Za-z]:\\|\\\\[A-Za-z0-9_.$-]+\\)(?:\\\*|[^\s"`()\]<>|*]|\((?:\\\*|[^\s"`()\]<>|*])*\))*/g;

const ESCAPE_RE = /\\([!-/:-@[-`{-~])/g;

/** What CommonMark makes of a path's source text: each `\` + punctuation becomes the punctuation. */
function unescaped(raw: string): string {
    return raw.replace(ESCAPE_RE, '$1');
}

function restorePaths(node: MdNode, source: string): void {
    if (node.type === 'text' && typeof node.value === 'string') {
        const start = node.position?.start.offset;
        const end = node.position?.end.offset;
        if (start === undefined || end === undefined) return;
        const raw = source.slice(start, end);
        if (!raw.includes('\\')) return;

        let value = node.value;
        let cursor = 0;
        for (const match of raw.matchAll(RAW_WIN_PATH_RE)) {
            const token = match[0];
            const shown = unescaped(token);
            if (shown === token) {
                // Step past it, or a later path's display form could match here instead.
                const same = value.indexOf(token, cursor);
                if (same !== -1) cursor = same + token.length;
                continue;
            }
            const at = value.indexOf(shown, cursor);
            // Not found means the parser read it differently (an entity, a hard break): leave it.
            if (at === -1) continue;
            value = value.slice(0, at) + token + value.slice(at + shown.length);
            cursor = at + token.length;
        }
        node.value = value;
        return;
    }
    node.children?.forEach((child) => restorePaths(child, source));
}

export default function remarkWindowsPaths() {
    return (tree: MdNode, file: { value?: unknown }) => {
        if (typeof file.value !== 'string') return;
        restorePaths(tree, file.value);
    };
}
