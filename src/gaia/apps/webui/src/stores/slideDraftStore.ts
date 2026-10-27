// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A slide deck being made in a chat, followed from the turn's events: the
 * session panel draws it while the model is still writing it.
 */

import { create } from 'zustand';
import type { StreamEvent } from '../types';
import { clampWidth, useSessionPanelStore } from './sessionPanelStore';

export type SlideDraftStatus = 'preparing' | 'writing' | 'building' | 'retrying' | 'done' | 'failed';

export interface SlideDraft {
    status: SlideDraftStatus;
    markdown: string;
    /** The last complete draft, shown until a rewrite has caught up with it. */
    previous?: string;
    path?: string;
    error?: string;
}

const SLIDE_SKILL = 'slide-decks';
const DRAFT_TOOL = `${SLIDE_SKILL}/build_deck_from_markdown`;
const SLIDE_PANEL_WIDTH = 600;

interface SlideDraftState {
    drafts: Record<string, SlideDraft>;
    update: (sessionId: string, patch: Partial<SlideDraft>) => void;
}

const finished = (d?: SlideDraft) => !d || d.status === 'done' || d.status === 'failed';

export const useSlideDraftStore = create<SlideDraftState>((set) => ({
    drafts: {},
    update: (sessionId, patch) =>
        set((s) => {
            const prev = s.drafts[sessionId];
            const base: SlideDraft = finished(prev)
                ? { status: 'preparing', markdown: '', previous: prev?.markdown || undefined }
                : prev;
            return { drafts: { ...s.drafts, [sessionId]: { ...base, ...patch } } };
        }),
}));

function begin(sessionId: string, patch: Partial<SlideDraft>): void {
    const fresh = finished(useSlideDraftStore.getState().drafts[sessionId]);
    useSlideDraftStore.getState().update(sessionId, patch);
    if (!fresh) return;
    const panel = useSessionPanelStore.getState();
    panel.reveal('slide');
    // Wide enough to read a slide; not remembered, so the user's own width returns.
    if (panel.width < SLIDE_PANEL_WIDTH) useSessionPanelStore.setState({ width: clampWidth(SLIDE_PANEL_WIDTH) });
}

/** Follow one stream event; true when it was a draft event nothing else handles. */
export function followSlideEvent(sessionId: string, event: StreamEvent): boolean {
    const args = (event.args ?? {}) as Record<string, unknown>;
    if (event.type === 'draft') {
        if (event.tool === DRAFT_TOOL && event.text) {
            const cur = useSlideDraftStore.getState().drafts[sessionId];
            const rewrite = cur && (cur.status === 'building' || cur.status === 'retrying') && cur.markdown;
            begin(sessionId, { status: 'writing', markdown: event.text, ...(rewrite ? { previous: cur.markdown } : {}) });
        }
        return true;
    }
    if (event.type === 'tool_args' && event.tool === 'load_skill' && args.name === SLIDE_SKILL) {
        begin(sessionId, { status: 'preparing' });
    } else if (event.type === 'tool_args' && event.tool === DRAFT_TOOL && typeof args.markdown === 'string') {
        begin(sessionId, { status: 'building', markdown: args.markdown });
    } else if (event.type === 'tool_result' && event.tool === DRAFT_TOOL) {
        if (finished(useSlideDraftStore.getState().drafts[sessionId])) return false;
        const data = ((event as { data?: unknown }).data ?? {}) as { path?: string };
        useSlideDraftStore.getState().update(
            sessionId,
            // The model reads the error and builds again, so this is not the end.
            event.success === false
                ? { status: 'retrying', error: event.summary || 'The deck could not be built.' }
                : { status: 'done', path: data.path, error: undefined },
        );
    }
    return false;
}
