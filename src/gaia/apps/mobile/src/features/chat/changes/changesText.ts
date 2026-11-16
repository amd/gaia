// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SessionChanges } from '@/api/client.types';
import type { Translate } from '@/i18n/useT';
import { isolate, ref } from '@webui/i18n/registry';

/** "4 files" as a message param, so each language words its own count. */
export const filesRef = (n: number) => ref(n === 1 ? 'chg.files.one' : 'chg.files.other', { n });

/** "+120 −8" as one left-to-right island, so the signs stay with their numbers in right-to-left text. */
export function countsText(additions: number, deletions: number): string {
    return isolate(`+${additions} \u2212${deletions}`, 'ltr');
}

/** "4 files changed, +120 −8". */
export function totalText(changes: SessionChanges, t: Translate): string {
    const { files, additions, deletions } = changes.totals;
    return t('mobileApp.changes.chat.total', { files: filesRef(files), counts: countsText(additions, deletions) });
}

/** "4 files · +120 −8". */
export function summaryText(changes: SessionChanges, t: Translate): string {
    const { files, additions, deletions } = changes.totals;
    return t('mobileApp.changes.chat.summary', { files: filesRef(files), counts: countsText(additions, deletions) });
}
