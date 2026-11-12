// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { safeUrlTransform } from './markdown';

export interface MessageImage {
    src: string;
    name: string;
}

/** A line that is only an image the composer uploaded: `![name](/api/files/uploads/<file>)`. */
const UPLOADED_IMAGE_LINE = /^!\[([^\]]*)\]\((\/api\/files\/uploads\/[^)\s]+)\)$/;

/**
 * Pull the uploaded images out of what the user sent, so they show as thumbnails
 * instead of full-size pictures. Everything else stays in `text` as written.
 */
export function splitUploadedImages(content: string): { text: string; images: MessageImage[] } {
    const images: MessageImage[] = [];
    const kept: string[] = [];
    for (const line of content.split('\n')) {
        const match = UPLOADED_IMAGE_LINE.exec(line.trim());
        const src = match ? safeUrlTransform(match[2]) : '';
        if (match && src) images.push({ src, name: match[1] || match[2].split('/').pop() || '' });
        else kept.push(line);
    }
    if (images.length === 0) return { text: content, images };
    return { text: kept.join('\n').replace(/\n{3,}/g, '\n\n').trim(), images };
}
