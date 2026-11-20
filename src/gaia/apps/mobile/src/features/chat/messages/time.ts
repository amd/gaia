// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { dateLocale } from '@webui/i18n/translator';
import type { Translate } from '@/i18n/useT';

/** "Nov 13, 2026, 2:05:09 PM" in the UI language; the sheet's title. `code` writes it in another language, for a bracketed half. */
export function formatFullTimestamp(iso: string, t: Translate, code: string = t.locale): string {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString(code === t.locale ? dateLocale(t) : `${code}-u-nu-latn`, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
    });
}
