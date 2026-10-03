// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Group sidebar chats by how recently they were used. */

import type { Session } from '../types';

export interface RecencyGroup {
    label: 'Today' | 'Yesterday' | 'Previous 7 days' | 'Previous 30 days' | 'Older';
    sessions: Session[];
}

function updatedAtMs(s: Session): number {
    const t = new Date(s.updated_at).getTime();
    return Number.isNaN(t) ? 0 : t;
}

/** Newest first, bucketed by calendar day relative to `now`. Empty buckets are dropped. */
export function groupSessionsByRecency(sessions: Session[], now: Date = new Date()): RecencyGroup[] {
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const day = 24 * 60 * 60 * 1000;
    const buckets: RecencyGroup[] = [
        { label: 'Today', sessions: [] },
        { label: 'Yesterday', sessions: [] },
        { label: 'Previous 7 days', sessions: [] },
        { label: 'Previous 30 days', sessions: [] },
        { label: 'Older', sessions: [] },
    ];
    const sorted = [...sessions].sort((a, b) => updatedAtMs(b) - updatedAtMs(a));
    for (const s of sorted) {
        const t = updatedAtMs(s);
        const i = t >= startOfToday ? 0
            : t >= startOfToday - day ? 1
            : t >= startOfToday - 7 * day ? 2
            : t >= startOfToday - 30 * day ? 3
            : 4;
        buckets[i].sessions.push(s);
    }
    return buckets.filter((b) => b.sessions.length > 0);
}
