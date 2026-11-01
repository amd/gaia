// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { UnsupportedCard } from './UnsupportedCard';
import { isOptionalString } from './primitiveShared';
import { resolveMediaSrc } from './mediaSrc';

/** Audio GAIA generated, played from `/api/media/audio/<name>` only. */
interface AudioPayload {
    src: string;
    caption?: string;
    path?: string;
    duration_seconds?: number;
}

function isAudioPayload(value: unknown): value is AudioPayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    return (
        typeof v.src === 'string' &&
        resolveMediaSrc('audio', v.src) !== null &&
        isOptionalString(v.caption) &&
        isOptionalString(v.path) &&
        (v.duration_seconds === undefined ||
            (typeof v.duration_seconds === 'number' &&
                Number.isFinite(v.duration_seconds) &&
                v.duration_seconds >= 0))
    );
}

export function formatDuration(seconds: number): string {
    const total = Math.round(seconds);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
}

export function AudioCard({ data }: { data: unknown }) {
    if (!isAudioPayload(data)) {
        return <UnsupportedCard variant="invalid" render="audio" data={data} />;
    }
    // SECURITY: `src` binds ONLY to this <audio> attribute.
    const src = resolveMediaSrc('audio', data.src)!;
    const fileName = data.path ? data.path.split(/[\\/]/).pop() : undefined;
    return (
        <figure className="render-audio">
            <audio className="render-audio__player" controls preload="metadata" src={src} />
            <figcaption className="render-audio__caption">
                {data.caption && <span className="render-audio__text">{data.caption}</span>}
                {(fileName || data.duration_seconds !== undefined) && (
                    <span className="render-audio__meta">
                        {fileName}
                        {fileName && data.duration_seconds !== undefined && ' · '}
                        {data.duration_seconds !== undefined && formatDuration(data.duration_seconds)}
                    </span>
                )}
            </figcaption>
        </figure>
    );
}
