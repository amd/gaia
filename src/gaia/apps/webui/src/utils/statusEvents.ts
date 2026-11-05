// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useChatStore } from '../stores/chatStore';
import type { StreamEvent } from '../types';
import { agentEventToStep, isProgressTick } from './streamSteps';

/** Applies a live turn's `status` event, other than `history_cut`, to the chat store. */
export function applyStatusEvent(event: StreamEvent, stepIdRef: { current: number }): void {
    const store = useChatStore.getState();
    const status = event.status;
    const msg = (event.message || '').trim();
    if (event.phase === 'recording_found' && event.section != null) {
        store.addRecordingSection({
            section: event.section,
            sections: event.sections ?? event.section,
            start: event.start ?? '',
            end: event.end ?? '',
            summary: event.summary ?? '',
            items: event.items ?? [],
        });
        return;
    }
    if (event.phase === 'recording' && event.stage != null) {
        store.setRecordingStage(event.stage, {
            state: event.state ?? 0,
            percent: event.percent,
            etaSeconds: event.eta_seconds,
        });
        return;
    }
    if (event.phase && msg) store.setLiveStatus(msg, event.phase);
    // The client's own wait notice is not a step of the turn.
    if (event.phase === 'waiting') return;
    if (isProgressTick(event)) return;
    // Skip "Executing <tool>" messages - redundant with tool_start
    if (msg.toLowerCase().startsWith('executing ')) return;
    if (status === 'working' || status === 'warning' || status === 'info') {
        const currentSteps = useChatStore.getState().agentSteps;
        const lastStep = currentSteps[currentSteps.length - 1];
        // Consolidate with previous status step (but NOT thinking —
        // overwriting a thinking step's detail would discard all
        // accumulated thinking text).
        if (lastStep && lastStep.type === 'status' && lastStep.active) {
            store.updateLastAgentStep({
                label: msg || 'Working',
                detail: msg,
            });
            return;
        }
        // If the last step is thinking, update only the label
        // so the summary bar shows the status, but preserve the
        // accumulated thinking detail.
        if (lastStep && lastStep.type === 'thinking' && lastStep.active) {
            store.updateLastAgentStep({ label: msg || 'Thinking' });
            return;
        }
        const step = agentEventToStep(event, stepIdRef);
        if (step) store.addAgentStep(step);
    }
}
