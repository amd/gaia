// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { UnsupportedCard } from './UnsupportedCard';
import { isOptionalString } from './primitiveShared';

interface DiffPayload {
    title?: string;
    /** File the diff is of; its name is the title when there is none. */
    path?: string;
    /** Short label next to the title, e.g. "Edited". */
    badge?: string;
    /** Shown instead of the body when there is no text diff to draw. */
    note?: string;
    unified: string;
}

function isDiffPayload(value: unknown): value is DiffPayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    return isOptionalString(v.title)
        && isOptionalString(v.path)
        && isOptionalString(v.badge)
        && isOptionalString(v.note)
        && typeof v.unified === 'string';
}

/** FIXED prefix -> class lookup — classes must never be derived from
 *  line content (content is untrusted). */
function lineClass(line: string): string {
    if (line.startsWith('+')) return 'render-diff__line render-diff__line--added';
    if (line.startsWith('-')) return 'render-diff__line render-diff__line--removed';
    return 'render-diff__line';
}

const HUNK_GAP = '⋯';

/** The changed lines: no file headers, and a gap mark between hunks instead of their line ranges. */
function bodyLines(unified: string): string[] {
    // A CRLF file's diff lines end in \r; drawn as-is it shows as a stray glyph.
    const lines = unified.replace(/\n$/, '').split('\n').map((l) => l.replace(/\r$/, ''));
    const firstHunk = lines.findIndex((l) => l.startsWith('@@'));
    if (firstHunk === -1) return lines;
    return lines.slice(firstHunk + 1).map((l) => (l.startsWith('@@') ? HUNK_GAP : l));
}

/** *path* with a break opportunity after each separator, so it wraps between folders. */
function wrappablePath(path: string) {
    return path.split(/(?<=[\\/])/).map((part, i) => (
        <span key={i}>{part}<wbr /></span>
    ));
}

export function DiffCard({ data }: { data: unknown }) {
    if (!isDiffPayload(data)) {
        return <UnsupportedCard variant="invalid" render="diff" data={data} />;
    }
    const lines = bodyLines(data.unified);
    const title = data.title ?? (data.path ? data.path.split(/[\\/]/).filter(Boolean).pop() : undefined);
    return (
        <div className="render-diff">
            {(title || data.badge) && (
                <div className="render-diff__title">
                    {data.badge && <span className="render-diff__badge">{data.badge}</span>}
                    {title}
                </div>
            )}
            {data.path && <div className="render-diff__path" dir="ltr">{wrappablePath(data.path)}</div>}
            {data.note && <div className="render-diff__note">{data.note}</div>}
            {data.unified && (
                <pre className="render-diff__body">
                    {lines.map((line, i) => (
                        <div
                            className={line === HUNK_GAP ? 'render-diff__line render-diff__line--hunk' : lineClass(line)}
                            key={i}
                        >
                            {line}
                        </div>
                    ))}
                </pre>
            )}
        </div>
    );
}
