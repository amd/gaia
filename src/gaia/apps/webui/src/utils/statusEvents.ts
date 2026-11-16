// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A `status` event's effect on the live line and the turn's timeline. */

import { useChatStore } from '../stores/chatStore';
import type { StreamEvent } from '../types';
import { RECONNECTING_PHASE } from './constants';
import { agentEventToStep, isProgressTick, statusText } from './streamSteps';

/** Live phases that are a call's own label or the client's own notice, not the call's progress. */
const CALL_LABEL_PHASES = new Set(['tool', 'waiting', RECONNECTING_PHASE]);

/** A phase sent while a call runs comes from that call's own model calls, not the turn's. */
export function selectToolStatus(s: { livePhase: string | null; liveStatus: string | null }): string | null {
    return s.livePhase && !CALL_LABEL_PHASES.has(s.livePhase) ? s.liveStatus : null;
}

/** Show a phased status on the live line, and fold consecutive progress lines into one timeline step. */
export function applyStatusEvent(event: StreamEvent, stepIdRef: { current: number }): void {
    const store = useChatStore.getState();
    const status = event.status;
    const msg = statusText(event);
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
    // "Executing <tool>" repeats tool_start.
    if (msg.toLowerCase().startsWith('executing ')) return;
    if (status !== 'working' && status !== 'warning' && status !== 'info') return;
    const lastStep = store.agentSteps[store.agentSteps.length - 1];
    // A warning is its own row; written over a progress row, the next progress line erases it.
    if (lastStep && lastStep.type === 'status' && lastStep.active && status !== 'warning') {
        store.updateLastAgentStep({ label: msg || 'Working', detail: msg });
        return;
    }
    // Only the label: a thinking step's detail holds the reasoning so far.
    if (lastStep && lastStep.type === 'thinking' && lastStep.active) {
        store.updateLastAgentStep({ label: msg || 'Thinking' });
        return;
    }
    const step = agentEventToStep({ ...event, message: msg }, stepIdRef);
    if (step) store.addAgentStep(step);
}
