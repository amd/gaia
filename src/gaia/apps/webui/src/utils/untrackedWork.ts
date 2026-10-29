// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Wording for work a turn ran that GAIA could not copy for undo (#5225). */

import type { UntrackedWork } from '../types/changes';

interface KindWords {
    one: string;
    many: string;
    /** Prefix for a single entry's label. */
    prefix: string;
}

const KINDS: Record<string, KindWords> = {
    shell: { one: 'command', many: 'commands', prefix: 'Command' },
    run_python: { one: 'Python snippet', many: 'Python snippets', prefix: 'Python' },
    execute_python_file: { one: 'Python script', many: 'Python scripts', prefix: 'Python script' },
    mcp: { one: 'connected tool call', many: 'connected tool calls', prefix: 'Connected tool' },
    install_cli: { one: 'install command', many: 'install commands', prefix: 'Install' },
    late_write: { one: 'late file write', many: 'late file writes', prefix: 'File write' },
};

const MAX_LABEL = 120;

function words(kind: string): KindWords {
    return KINDS[kind] ?? { one: kind, many: kind, prefix: kind };
}

function capitalize(text: string): string {
    return text.charAt(0).toUpperCase() + text.slice(1);
}

function truncate(text: string): string {
    const flat = text.replace(/\s+/g, ' ').trim();
    return flat.length > MAX_LABEL ? `${flat.slice(0, MAX_LABEL - 1).trimEnd()}…` : flat;
}

export interface UntrackedEntry {
    /** The entry's description, cut to one short line; '' when it has none. */
    label: string;
    /** Its full text, for a tooltip. */
    detail: string;
    count: number;
}

export interface UntrackedGroup {
    kind: string;
    /** "Python snippets ×7", or "Python: counts errors by hour" for one entry. */
    title: string;
    count: number;
    /** Distinct entries, identical ones counted once. */
    entries: UntrackedEntry[];
}

function countOf(n: number, w: KindWords): string {
    return `${n} ${n === 1 ? w.one : w.many}`;
}

/** Untracked work by kind, in the order it ran, identical entries merged. */
export function groupUntracked(list: UntrackedWork[]): UntrackedGroup[] {
    const groups = new Map<string, { kind: string; count: number; entries: Map<string, UntrackedEntry> }>();
    for (const u of list) {
        let group = groups.get(u.kind);
        if (!group) {
            group = { kind: u.kind, count: 0, entries: new Map() };
            groups.set(u.kind, group);
        }
        group.count += 1;
        const detail = (u.detail ?? '').trim();
        const entry = group.entries.get(detail);
        if (entry) entry.count += 1;
        else group.entries.set(detail, { label: truncate(detail), detail, count: 1 });
    }
    return [...groups.values()].map(({ kind, count, entries }) => {
        const w = words(kind);
        const distinct = [...entries.values()];
        let title: string;
        if (distinct.length === 1) {
            const only = distinct[0];
            title = only.label ? `${w.prefix}: ${only.label}` : capitalize(w.one);
            if (only.count > 1) title += ` ×${only.count}`;
        } else {
            title = `${capitalize(w.many)} ×${count}`;
        }
        return { kind, title, count, entries: distinct };
    });
}

/** "7 Python snippets", "2 commands and 3 Python snippets". */
export function describeUntracked(list: UntrackedWork[]): string {
    const parts = groupUntracked(list).map((g) => countOf(g.count, words(g.kind)));
    if (parts.length === 0) return 'work';
    if (parts.length === 1) return parts[0];
    return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
}

/**
 * The warning under a turn or a chat that ran work GAIA could not copy.
 *
 * @param also   the turn changed tracked files too, so this is in addition.
 * @param inChat the warning covers the whole chat, not one turn.
 * @param undo   the button the sentence refers to.
 */
export function untrackedWarning(
    list: UntrackedWork[],
    { also = false, inChat = false, undo = 'Undo' }: { also?: boolean; inChat?: boolean; undo?: string } = {},
): string {
    const one = list.length === 1;
    return (
        `${inChat ? 'In this chat, GAIA' : 'GAIA'} ${also ? 'also ran' : 'ran'} ${describeUntracked(list)} it can't track. ` +
        `If ${one ? 'it' : 'they'} changed any files, ${undo} won't restore them.`
    );
}
