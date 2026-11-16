// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { StreamEvent } from '@/api/client.types';

/** Same order as RECORDING_STAGES in audio_tools.py: events carry the position. */
export const RECORDING_STAGES = ['download', 'decode', 'transcribe', 'voices', 'prepare', 'analyze'] as const;
export type RecordingStageName = (typeof RECORDING_STAGES)[number];

const DOWNLOAD = 0;
const ANALYZE = 5;
// Same as BOUNDARY_GAP_SECONDS in transcript_analysis.py: nearer than this, one item.
const SAME_ITEM_SECONDS = 120;

export type StageState = 'waiting' | 'running' | 'done' | 'skipped';

export interface StageRow {
    name: RecordingStageName;
    state: StageState;
    /** 0 to 100 while running; undefined is indeterminate. */
    percent?: number;
    etaSeconds?: number;
}

export interface FoundSection {
    section: number;
    sections: number;
    start: string;
    end: string;
    summary: string;
    items: { title: string; start: string; seconds: number }[];
}

export interface RecordingView {
    stages: StageRow[];
    found: FoundSection[];
    /** The section total the run reported, for "{done} of {total}". */
    total: number;
}

const STATES: StageState[] = ['running', 'done', 'skipped'];

/**
 * What the recording's `recording` and `recording_found` status events say so far,
 * or null when the turn has none (it attached no audio or video).
 */
export function recordingFromEvents(events: readonly StreamEvent[]): RecordingView | null {
    const reported: Record<number, { state: StageState; percent?: number; etaSeconds?: number }> = {};
    const sections: Record<number, FoundSection> = {};
    for (const e of events) {
        if (e.type !== 'status') continue;
        if (e.phase === 'recording_found' && e.section != null) {
            sections[e.section] = {
                section: e.section,
                sections: e.sections ?? e.section,
                start: e.start ?? '',
                end: e.end ?? '',
                summary: e.summary ?? '',
                items: e.items ?? [],
            };
        } else if (e.phase === 'recording' && e.stage != null) {
            // Reaching a stage means the earlier ones are behind it, reused or finished.
            for (let earlier = 1; earlier < e.stage; earlier++) {
                if (reported[earlier]?.state !== 'skipped') reported[earlier] = { state: 'done' };
            }
            reported[e.stage] = { state: STATES[e.state ?? 0] ?? 'running', percent: e.percent, etaSeconds: e.eta_seconds };
        }
    }
    if (Object.keys(reported).length === 0 && Object.keys(sections).length === 0) return null;
    const stages = RECORDING_STAGES.map((name, index): StageRow => ({ ...reported[index], name, state: reported[index]?.state ?? 'waiting' })).filter(
        (row, index) => (index !== DOWNLOAD && index !== ANALYZE) || index in reported,
    );
    const found = foundSoFar(sections);
    return { stages, found, total: found[0]?.sections ?? 0 };
}

/** Sections in recording order, each with only the items an earlier section hasn't shown: neighbours overlap at the seams. */
function foundSoFar(sections: Record<number, FoundSection>): FoundSection[] {
    const shown: { title: string; seconds: number }[] = [];
    return Object.values(sections)
        .sort((a, b) => a.section - b.section)
        .map((section) => ({
            ...section,
            items: section.items.filter((item) => {
                const title = item.title.trim().toLowerCase();
                const repeat = shown.some((s) => s.title === title && Math.abs(s.seconds - item.seconds) < SAME_ITEM_SECONDS);
                if (!repeat) shown.push({ title, seconds: item.seconds });
                return !repeat;
            }),
        }));
}
