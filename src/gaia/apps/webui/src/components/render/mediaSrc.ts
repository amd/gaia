// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { getApiBase } from '../../utils/apiBase';

/** A file GAIA generated, as the backend's card names it: one encoded file name. */
const MEDIA_SRC_RE = /^\/api\/media\/(image|audio)\/([A-Za-z0-9%._~-]+)$/;

/**
 * Resolve a generated-media reference (`/api/media/<kind>/<name>`) to the
 * backend URL that serves it, or null when `src` is anything else.
 *
 * The renderer loads from `file://` in the desktop app, so a root-relative
 * path must be pointed at the backend explicitly. A name that decodes to a
 * path (`..`, a separator) is rejected here as well as on the server.
 */
export function resolveMediaSrc(kind: 'image' | 'audio', src: string): string | null {
    const match = MEDIA_SRC_RE.exec(src);
    if (!match || match[1] !== kind) return null;
    let name: string;
    try {
        name = decodeURIComponent(match[2]);
    } catch {
        return null;
    }
    if (name === '.' || name === '..' || /[/\\:\0]/.test(name)) return null;
    return `${getApiBase()}/media/${kind}/${match[2]}`;
}
