// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { UnsupportedCard } from './UnsupportedCard';
import { DATA_IMAGE_RE, isImagePayload } from '../../utils/renderCards.core';
import { resolveMediaSrc } from './mediaSrc';
import { useT } from '../../i18n/useT';

export function ImageCard({ data }: { data: unknown }) {
    const t = useT();
    const [expanded, setExpanded] = useState(false);
    if (!isImagePayload(data)) {
        return <UnsupportedCard variant="invalid" render="image" data={data} />;
    }
    const src = DATA_IMAGE_RE.test(data.src) ? data.src : resolveMediaSrc('image', data.src)!;
    // SECURITY: `src` binds ONLY to this <img> attribute — never to
    // window.open, location, an anchor href, or any electronAPI.invoke.
    return (
        <figure className={`render-image${expanded ? ' render-image--expanded' : ''}`}>
            <button
                type="button"
                className="render-image__toggle"
                aria-expanded={expanded}
                aria-label={expanded ? t('media.showSmaller') : t('media.showFull')}
                onClick={() => setExpanded((v) => !v)}
            >
                <img className="render-image__img" src={src} alt={data.alt ?? ''} />
            </button>
            {data.caption && (
                <figcaption className="render-image__caption">{data.caption}</figcaption>
            )}
        </figure>
    );
}
