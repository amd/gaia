// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { getApiBase } from '../../utils/apiBase';
import { parseMediaSrc } from '../../utils/renderCards.core';

/**
 * Resolve a generated-media reference (`/api/media/<kind>/<name>`) to the
 * backend URL that serves it, or null when `src` is anything else.
 *
 * The renderer loads from `file://` in the desktop app, so a root-relative
 * path must be pointed at the backend explicitly. A name that decodes to a
 * path (`..`, a separator) is rejected here as well as on the server.
 */
export function resolveMediaSrc(kind: 'image' | 'audio', src: string): string | null {
    const name = parseMediaSrc(kind, src);
    if (name === null) return null;
    return `${getApiBase()}/media/${kind}/${name}`;
}
