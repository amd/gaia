// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { UnsupportedCard } from './UnsupportedCard';
import { DIFF_HUNK_GAP, diffBodyLines, isDiffPayload, diffTitle } from '../../utils/renderCards.core';
import { visibleName } from '../CalendarApproval';

/** FIXED prefix -> class lookup — classes must never be derived from
 *  line content (content is untrusted). */
function lineClass(line: string): string {
    if (line.startsWith('+')) return 'render-diff__line render-diff__line--added';
    if (line.startsWith('-')) return 'render-diff__line render-diff__line--removed';
    return 'render-diff__line';
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
    const lines = diffBodyLines(data.unified);
    const title = diffTitle(data);
    return (
        <div className="render-diff">
            {(title || data.badge) && (
                <div className="render-diff__title">
                    {data.badge && <span className="render-diff__badge">{data.badge}</span>}
                    {title && visibleName(title)}
                </div>
            )}
            {data.path && <div className="render-diff__path" dir="ltr">{wrappablePath(visibleName(data.path))}</div>}
            {data.note && <div className="render-diff__note">{data.note}</div>}
            {data.unified && (
                <pre className="render-diff__body">
                    {lines.map((line, i) => (
                        <div
                            className={line === DIFF_HUNK_GAP ? 'render-diff__line render-diff__line--hunk' : lineClass(line)}
                            key={i}
                        >
                            {visibleName(line)}
                        </div>
                    ))}
                </pre>
            )}
        </div>
    );
}
