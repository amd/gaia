// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A slide deck being made in a chat, followed from the turn's events: the
 * session panel draws it while the model is still writing it.
 */

import { create } from 'zustand';
import type { StreamEvent } from '../types';
import { clampWidth, useSessionPanelStore } from './sessionPanelStore';
import { outlineToMarkdown } from '../utils/slideMarkdown';

export type SlideDraftStatus = 'preparing' | 'writing' | 'building' | 'retrying' | 'done' | 'failed';

export interface SlideDraft {
    status: SlideDraftStatus;
    markdown: string;
    /** The last complete draft, shown until a rewrite has caught up with it. */
    previous?: string;
    path?: string;
    error?: string;
}

/**
 * The live slide preview in the session panel. Off for now: set to true to
 * mount <SlidePreview> and follow the turn's slide events again.
 */
export const SLIDE_PREVIEW_ENABLED = false;

const SLIDE_SKILL = 'slide-decks';
const DRAFT_TOOL = `${SLIDE_SKILL}/build_deck_from_markdown`;
/** The JSON-outline build: drawn from its outline once the call is made. */
const OUTLINE_TOOL = `${SLIDE_SKILL}/build_deck`;
const BUILD_TOOLS = [DRAFT_TOOL, OUTLINE_TOOL];
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

/** A tool_result as either wire shape: the handler's flat one, or the relayed one under `data`. */
function resultOf(event: StreamEvent): { success: boolean; path?: string; summary?: string } {
    const raw = (event as { data?: unknown }).data;
    const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
    const flat = event as unknown as Record<string, unknown>;
    const pick = (key: string) => flat[key] ?? data[key];
    const path = pick('path');
    const summary = pick('summary');
    return {
        success: pick('success') !== false,
        path: typeof path === 'string' ? path : undefined,
        summary: typeof summary === 'string' && summary ? summary : undefined,
    };
}

/** Follow one stream event; true when it was a draft event nothing else handles. */
export function followSlideEvent(sessionId: string, event: StreamEvent): boolean {
    const args = (event.args ?? {}) as Record<string, unknown>;
    if (event.type === 'draft') {
        if (event.tool === DRAFT_TOOL && typeof event.text === 'string' && event.text) {
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
    } else if (event.type === 'tool_args' && event.tool === OUTLINE_TOOL) {
        const markdown = outlineToMarkdown(args.outline);
        if (markdown) begin(sessionId, { status: 'building', markdown });
    } else if (event.type === 'tool_result') {
        const cur = useSlideDraftStore.getState().drafts[sessionId];
        if (finished(cur)) return false;
        // A server that does not name the tool on a result: tools run one at a
        // time, so the result after a build call is that build's.
        const ours = event.tool ? BUILD_TOOLS.includes(event.tool) : cur?.status === 'building';
        if (!ours) return false;
        const result = resultOf(event);
        useSlideDraftStore.getState().update(
            sessionId,
            // The model reads the error and builds again, so this is not the end.
            result.success
                ? { status: 'done', path: result.path, error: undefined }
                : { status: 'retrying', error: result.summary },
        );
    }
    return false;
}

/** The turn ended, failed or was stopped: a draft still in progress will not be finished. */
export function settleSlideDraft(sessionId: string): void {
    const cur = useSlideDraftStore.getState().drafts[sessionId];
    if (finished(cur)) return;
    useSlideDraftStore.getState().update(sessionId, { status: 'failed' });
}
