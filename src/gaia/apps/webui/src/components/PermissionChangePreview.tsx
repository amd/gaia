// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { useT } from '../i18n/useT';
import { CodeBlock } from './PermissionPrompt';
import { unseenWith, visibleWith } from './CalendarApproval';
import './render/render.css';
import './PermissionChangePreview.css';

/** Lines of a write or a change shown before "Show all". */
const CHANGE_PREVIEW_LINES = 20;

/** Unchanged lines kept around an edit so it reads in place. */
const CONTEXT_LINES = 2;

/**
 * Only tab and newline stay as written. Unlike calendar prose, a file keeps no joiner or
 * direction mark unshown: an RLM before a statement would draw a line of code reversed.
 */
const FILE_KEPT: ReadonlySet<string> = new Set(['\t', '\n']);

export const fileText = (value: string, multiline: boolean) => visibleWith(FILE_KEPT, value, multiline);
export const fileUnseen = (value: unknown) => unseenWith(FILE_KEPT, value);

type Change =
    | { kind: 'write'; text: string }
    | { kind: 'edit'; lines: string[] };

/** What a file write or edit will leave on disk, from the call's arguments; null for any other call. */
export function fileChange(args: Record<string, unknown>): Change | null {
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

/** Fixed prefix -> class; never derived from the line's own text. */
function lineClass(line: string): string {
    if (line.startsWith('+')) return 'render-diff__line render-diff__line--added';
    if (line.startsWith('-')) return 'render-diff__line render-diff__line--removed';
    return 'render-diff__line';
}

function EditDiff({ lines }: { lines: string[] }) {
    const t = useT();
    const [expanded, setExpanded] = useState(false);
    const long = lines.length > CHANGE_PREVIEW_LINES;
    const shown = long && !expanded ? lines.slice(0, CHANGE_PREVIEW_LINES) : lines;
    return (
        <>
            <pre dir="ltr" className="render-diff__body perm-card-diff" data-testid="perm-edit-diff">
                {shown.map((line, i) => <div key={i} className={lineClass(line)}>{fileText(line, true)}</div>)}
            </pre>
            {long && (
                <button type="button" className="perm-card-args-toggle" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
                    {expanded ? t('prompt.showLess') : t('prompt.showAllLines', { n: lines.length })}
                </button>
            )}
        </>
    );
}

/** The file content a write will save, or the lines an edit will change, as they will land. */
export function ChangePreview({ args }: { args: Record<string, unknown> }) {
    const t = useT();
    const change = fileChange(args);
    if (!change) return null;
    // A character that hides or reorders text would make the preview read unlike the file.
    const hidden = change.kind === 'edit' ? fileUnseen(args.old_content) || fileUnseen(args.new_content) : fileUnseen(change.text);
    return (
        <div className="perm-card-change">
            <p className="perm-card-change-label">{t(change.kind === 'edit' ? 'prompt.willChange' : 'prompt.willWrite')}</p>
            {change.kind === 'edit'
                ? <EditDiff lines={change.lines} />
                : change.text
                ? <CodeBlock code={fileText(change.text, true)} previewLines={CHANGE_PREVIEW_LINES} testId="perm-write-preview" exact />
                : <p className="perm-card-detail">{t('prompt.emptyFile')}</p>}
            {hidden && <p className="perm-card-risk" role="note" data-testid="perm-change-unseen">{t('prompt.unseenChars')}</p>}
        </div>
    );
}
