// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { StreamEvent } from '@/api/client.types';
import { Text } from '@/ui';

/** The PC's text when a turn pushed older turns out of the model's view; null when none did. */
export function historyNoticeText(events: readonly StreamEvent[]): string | null {
    for (let i = events.length - 1; i >= 0; i--) {
        const e = events[i] as { type: string; status?: string; message?: string };
        if (e.type === 'status' && e.status === 'history_cut') return (e.message || '').trim() || null;
    }
    return null;
}

/** One quiet line before the reply. The text comes from the PC and is in English. */
export function HistoryNotice({ text }: { text: string }) {
    return (
        <Text role="caption" tone="secondary" testID="history-notice">
            {text}
        </Text>
    );
}
