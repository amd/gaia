// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { UnsupportedCard } from './UnsupportedCard';
import { isOptionalString } from './primitiveShared';

/**
 * Inline base64 raster images only — SVG is deliberately excluded (it can
 * carry script), and remote/other schemes are rejected outright. There is
 * no CSP in this app, so this allowlist is the actual security boundary.
 */
const DATA_IMAGE_RE = /^data:image\/(png|jpe?g|gif|webp);base64,/;

interface ImagePayload {
    src: string;
    alt?: string;
    caption?: string;
}

function isImagePayload(value: unknown): value is ImagePayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    return (
        typeof v.src === 'string' &&
        DATA_IMAGE_RE.test(v.src) &&
        isOptionalString(v.alt) &&
        isOptionalString(v.caption)
    );
}

export function ImageCard({ data }: { data: unknown }) {
    if (!isImagePayload(data)) {
        return <UnsupportedCard variant="invalid" render="image" data={data} />;
    }
    // SECURITY: `src` binds ONLY to this <img> attribute — never to
    // window.open, location, an anchor href, or any electronAPI.invoke.
    return (
        <figure className="render-image">
            <img className="render-image__img" src={data.src} alt={data.alt ?? ''} />
            {data.caption && (
                <figcaption className="render-image__caption">{data.caption}</figcaption>
            )}
        </figure>
    );
}
