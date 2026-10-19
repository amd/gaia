// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { UnsupportedCard } from './UnsupportedCard';
import { isOptionalString } from './primitiveShared';

interface DiffPayload {
    title?: string;
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
        && isOptionalString(v.badge)
        && isOptionalString(v.note)
        && typeof v.unified === 'string';
}

/** FIXED prefix -> class lookup — classes must never be derived from
 *  line content (content is untrusted). */
function lineClass(line: string): string {
    if (line.startsWith('@@')) return 'render-diff__line render-diff__line--hunk';
    if (
        line.startsWith('+++') ||
        line.startsWith('---') ||
        line.startsWith('diff ') ||
        line.startsWith('index ')
    )
        return 'render-diff__line';
    if (line.startsWith('+')) return 'render-diff__line render-diff__line--added';
    if (line.startsWith('-')) return 'render-diff__line render-diff__line--removed';
    return 'render-diff__line';
}

export function DiffCard({ data }: { data: unknown }) {
    if (!isDiffPayload(data)) {
        return <UnsupportedCard variant="invalid" render="diff" data={data} />;
    }
    // A CRLF file's diff lines end in \r; drawn as-is it shows as a stray glyph.
    const lines = data.unified.replace(/\n$/, '').split('\n').map((l) => l.replace(/\r$/, ''));
    return (
        <div className="render-diff">
            {(data.title || data.badge) && (
                <div className="render-diff__title">
                    {data.badge && <span className="render-diff__badge">{data.badge}</span>}
                    {data.title}
                </div>
            )}
            {data.note && <div className="render-diff__note">{data.note}</div>}
            {data.unified && (
                <pre className="render-diff__body">
                    {lines.map((line, i) => (
                        <div className={lineClass(line)} key={i}>
                            {line}
                        </div>
                    ))}
                </pre>
            )}
        </div>
    );
}
