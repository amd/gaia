// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { localizeLiveStatus } from '@webui/utils/liveStatus';
import { agentEventToStep, isProgressTick, statusText } from '@webui/utils/streamSteps.core';

import type { StreamEvent } from '@/api/client.types';
import type { Translate } from '@/i18n/useT';

/**
 * The live line under a running reply: what the agent is doing now, or null when
 * it has said nothing (the bubble then says "Thinking..."). Once the answer's
 * text is arriving only a running tool is worth a line.
 */
export function liveStatusLine(
    events: readonly StreamEvent[],
    state: { waitingOnPerson: boolean; answering: boolean },
    t: Translate,
): string | null {
    if (state.waitingOnPerson) return t('activity.waiting');
    let movedOn = false;
    for (let i = events.length - 1; i >= 0; i--) {
        const event = events[i]!;
        // A retry notice is stale once anything but a status followed it; the latest one replaces the earlier.
        if (event.type === 'status' && event.phase === 'model_retry') return movedOn || state.answering ? null : statusText(event, t);
        if (event.type !== 'status' && event.type !== 'step') movedOn = true;
        if (event.type === 'tool_end' || event.type === 'tool_result') return null;
        if (event.type === 'tool_start') {
            const step = agentEventToStep(event, { current: 0 }, t);
            return step ? step.narration || step.label : null;
        }
        if (event.type !== 'status' || state.answering || isProgressTick(event) || event.message?.startsWith('Agent: ')) continue;
        const text = statusText(event, t);
        if (text) return localizeLiveStatus(t, text);
    }
    return null;
}
