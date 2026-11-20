// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** What a permission prompt shows a file write or edit will leave on disk, with no DOM, so the phone app can import it. */

import { unseenWith, visibleWith } from './calendarApproval.core';

/** Lines of a write or a change shown before "Show all". */
export const CHANGE_PREVIEW_LINES = 20;

/** Unchanged lines kept around an edit so it reads in place. */
const CONTEXT_LINES = 2;

/**
 * Only tab and newline stay as written. Unlike calendar prose, a file keeps no joiner or
 * direction mark unshown: an RLM before a statement would draw a line of code reversed.
 */
const FILE_KEPT: ReadonlySet<string> = new Set(['\t', '\n']);

export const fileText = (value: string, multiline: boolean) => visibleWith(FILE_KEPT, value, multiline);
export const fileUnseen = (value: unknown) => unseenWith(FILE_KEPT, value);

export type FileChange =
    | { kind: 'write'; text: string }
    | { kind: 'edit'; lines: string[] };

/** What a file write or edit will leave on disk, from the call's arguments; null for any other call. */
export function fileChange(args: Record<string, unknown>): FileChange | null {
    if (typeof args.old_content === 'string' && typeof args.new_content === 'string') {
        return { kind: 'edit', lines: editLines(args.old_content, args.new_content) };
    }
    if (typeof args.content === 'string' && (typeof args.file_path === 'string' || typeof args.path === 'string')) {
        return { kind: 'write', text: args.content };
    }
    return null;
}

/** The edit as diff lines: `-` removed, `+` added, ` ` unchanged context. */
export function editLines(before: string, after: string): string[] {
    const split = (s: string) => s.replace(/\r\n/g, '\n').replace(/\n$/, '').split('\n');
    const a = before ? split(before) : [];
    const b = after ? split(after) : [];
    let head = 0;
    while (head < a.length && head < b.length && a[head] === b[head]) head++;
    let tail = 0;
    while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
    // Only a line ending or the final line break differs: show both sides whole, never "no change".
    if (head + tail >= a.length && head + tail >= b.length && before !== after) {
        return [...a.map((l) => `-${l}`), ...b.map((l) => `+${l}`)];
    }
    return [
        ...a.slice(Math.max(0, head - CONTEXT_LINES), head).map((l) => ` ${l}`),
        ...a.slice(head, a.length - tail).map((l) => `-${l}`),
        ...b.slice(head, b.length - tail).map((l) => `+${l}`),
        ...a.slice(a.length - tail, a.length - tail + CONTEXT_LINES).map((l) => ` ${l}`),
    ];
}
