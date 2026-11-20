// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useConnectionStore } from '@/state/connection';

// Scheme and authority, lowercased; enough to tell the paired PC's address from any other.
export function originOf(url: string): string | null {
    return /^https?:\/\/[^/?#]+/i.exec(url)?.[0].toLowerCase() ?? null;
}

/** Whether `url` is the paired PC's: same origin as its address, or relative (it resolves against that address). Never true for an external link. */
export function isPcUrl(url: string): boolean {
    const baseUrl = useConnectionStore.getState().connection?.baseUrl;
    if (!baseUrl) return false;
    const origin = originOf(url);
    if (origin === null) return url.startsWith('/') && !url.startsWith('//');
    return origin === originOf(baseUrl);
}
