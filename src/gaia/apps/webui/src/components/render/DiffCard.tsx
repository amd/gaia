// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { UnsupportedCard } from './UnsupportedCard';
import { isOptionalString } from './primitiveShared';

interface DiffPayload {
    title?: string;
    unified: string;
}

function isDiffPayload(value: unknown): value is DiffPayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    return isOptionalString(v.title) && typeof v.unified === 'string';
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
    return (
        <div className="render-diff">
            {data.title && <div className="render-diff__title">{data.title}</div>}
            <pre className="render-diff__body">
                {data.unified.split('\n').map((line, i) => (
                    <div className={lineClass(line)} key={i}>
                        {line}
                    </div>
                ))}
            </pre>
        </div>
    );
}
