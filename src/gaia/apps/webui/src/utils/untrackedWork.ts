// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Wording for work a turn ran that GAIA could not copy for undo (#5225). */

import { tEn, type Translate } from '../i18n/useT';
import type { MessageKey, PluralKey } from '../i18n/types';
import type { UntrackedWork } from '../types/changes';

interface KindKeys {
    /** "{n} command" / "{n} commands". */
    count: PluralKey;
    /** "Command: {label}", for a single entry. */
    one: MessageKey;
    /** "Command", for a single entry with no description. */
    bare: MessageKey;
    /** "Commands", the heading over several entries. */
    many: MessageKey;
}

const KINDS: Record<string, KindKeys> = {
    shell: { count: 'untracked.count.shell', one: 'untracked.one.shell', bare: 'untracked.bare.shell', many: 'untracked.many.shell' },
    run_python: { count: 'untracked.count.python', one: 'untracked.one.python', bare: 'untracked.bare.python', many: 'untracked.many.python' },
    execute_python_file: { count: 'untracked.count.script', one: 'untracked.one.script', bare: 'untracked.bare.script', many: 'untracked.many.script' },
    mcp: { count: 'untracked.count.mcp', one: 'untracked.one.mcp', bare: 'untracked.bare.mcp', many: 'untracked.many.mcp' },
    install_cli: { count: 'untracked.count.install', one: 'untracked.one.install', bare: 'untracked.bare.install', many: 'untracked.many.install' },
    late_write: { count: 'untracked.count.lateWrite', one: 'untracked.one.lateWrite', bare: 'untracked.bare.lateWrite', many: 'untracked.many.lateWrite' },
};

const MAX_LABEL = 120;

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

/** A kind GAIA has no words for is shown by its own name. */
function countOf(kind: string, n: number, t: Translate): string {
    const keys = KINDS[kind];
    return keys ? t.plural(keys.count, n) : `${n} ${kind}`;
}

function titleOf(kind: string, count: number, entries: UntrackedEntry[], t: Translate): string {
    const keys = KINDS[kind];
    if (entries.length !== 1) return `${keys ? t(keys.many) : kind} ×${count}`;
    const only = entries[0];
    let title: string;
    if (!keys) title = only.label ? `${kind}: ${only.label}` : kind;
    else title = only.label ? t(keys.one, { label: only.label }) : t(keys.bare);
    return only.count > 1 ? `${title} ×${only.count}` : title;
}

/** Untracked work by kind, in the order it ran, identical entries merged. */
export function groupUntracked(list: UntrackedWork[], t: Translate = tEn): UntrackedGroup[] {
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
        const distinct = [...entries.values()];
        return { kind, title: titleOf(kind, count, distinct, t), count, entries: distinct };
    });
}

/** "7 Python snippets", "2 commands and 3 Python snippets". */
export function describeUntracked(list: UntrackedWork[], t: Translate = tEn): string {
    const parts = groupUntracked(list, t).map((g) => countOf(g.kind, g.count, t));
    if (parts.length === 0) return t('untracked.work');
    if (parts.length === 1) return parts[0];
    return t('untracked.and', { list: parts.slice(0, -1).join(t('untracked.sep')), last: parts[parts.length - 1] });
}

/**
 * The warning under a turn or a chat that ran work GAIA could not copy.
 *
 * @param also   the turn changed tracked files too, so this is in addition.
 * @param inChat the warning covers the whole chat, not one turn.
 */
export function untrackedWarning(
    list: UntrackedWork[],
    { also = false, inChat = false }: { also?: boolean; inChat?: boolean } = {},
    t: Translate = tEn,
): string {
    const base: PluralKey = inChat ? 'untracked.warningChat' : also ? 'untracked.warningAlso' : 'untracked.warning';
    return t.plural(base, list.length, { work: describeUntracked(list, t) });
}
