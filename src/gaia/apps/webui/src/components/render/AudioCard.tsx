// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { useT } from '../../i18n/useT';
import { UnsupportedCard } from './UnsupportedCard';
import { resolveMediaSrc } from './mediaSrc';
import { formatDuration, isAudioPayload } from '../../utils/renderCards.core';

export { formatDuration } from '../../utils/renderCards.core';

export function AudioCard({ data }: { data: unknown }) {
    const t = useT();
    const [unplayable, setUnplayable] = useState(false);
    if (!isAudioPayload(data)) {
        return <UnsupportedCard variant="invalid" render="audio" data={data} />;
    }
    // SECURITY: `src` binds ONLY to this <audio> attribute.
    const src = resolveMediaSrc('audio', data.src)!;
    const fileName = data.path ? data.path.split(/[\\/]/).pop() : undefined;
    return (
        <figure className="render-audio">
            <audio className="render-audio__player" controls preload="metadata" src={src} onError={() => setUnplayable(true)} />
            <figcaption className="render-audio__caption">
                {unplayable && <span className="render-audio__error" role="alert">{t('media.audioUnplayable')}</span>}
                {data.caption && <span className="render-audio__text">{data.caption}</span>}
                {(fileName || data.duration_seconds !== undefined) && (
                    <span className="render-audio__meta">
                        {fileName}
                        {fileName && data.duration_seconds !== undefined && ' · '}
                        {data.duration_seconds !== undefined && <bdi dir="ltr">{formatDuration(data.duration_seconds)}</bdi>}
                    </span>
                )}
            </figcaption>
        </figure>
    );
}
