// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey, Params } from '@webui/i18n/registry';

/** "3 min ago" from a UTC `YYYY-MM-DD HH:MM:SS` as a message, a plain date once older than a day, or null. */
export function whenOf(utc: string | null, now: number, locale: string | undefined): { key: MessageKey; params?: Params } | string | null {
    if (!utc) return null;
    const at = Date.parse(`${utc.replace(' ', 'T')}Z`);
    if (Number.isNaN(at)) return null;
    const s = Math.max(0, Math.round((now - at) / 1000));
    if (s < 60) return { key: 'out.justNow' };
    if (s < 3600) return { key: 'out.minAgo', params: { n: Math.floor(s / 60) } };
    if (s < 86400) return { key: 'out.hAgo', params: { n: Math.floor(s / 3600) } };
    return new Date(at).toLocaleDateString(locale, { month: 'short', day: 'numeric' });
}
