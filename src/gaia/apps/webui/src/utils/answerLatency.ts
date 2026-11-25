// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Message } from '../types';

/** How long each answer took, by message id: from the question before it, less the time the turn waited on the user. */
export function answerLatencies(messages: readonly Message[]): Map<number, number> {
    const map = new Map<number, number>();
    let lastUserTime: number | undefined;
    for (const msg of messages) {
        if (msg.role === 'user' && msg.created_at) {
            lastUserTime = new Date(msg.created_at).getTime();
        } else if (msg.role === 'assistant' && msg.created_at && lastUserTime !== undefined) {
            const took = new Date(msg.created_at).getTime() - lastUserTime - (msg.stats?.waiting_seconds ?? 0) * 1000;
            if (took > 0) map.set(msg.id, took);
            lastUserTime = undefined;
        }
    }
    return map;
}
