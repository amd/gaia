// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Durations, step titles and workout sets read out of a guide's step text. */

import { NUMBER_SOURCE, parseNumber } from './quantity';

export interface DurationSpan {
    /** Offsets into the searched text; `end` is exclusive. */
    start: number;
    end: number;
    /** The low bound for a range ("10-15 min" is 600). */
    seconds: number;
}

const UNIT_SECONDS: ReadonlyArray<readonly [RegExp, number]> = [
    [/^h/i, 3600], [/^m/i, 60], [/^s/i, 1],
];

const UNIT_SOURCE = String.raw`(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)\b`;
const PART = new RegExp(
    String.raw`(?:(${NUMBER_SOURCE})(?:\s*(?:-|–|to)\s*(?:${NUMBER_SOURCE}))?|\b(an?))[\s-]*(${UNIT_SOURCE})`,
    'gi',
);
/** What may sit between the parts of one duration: "1 hr 20 min", "1 hour and 20 minutes". */
const JOINER = /^\s*(?:,\s*)?(?:and\s+)?$/i;

function unitSeconds(unit: string): number {
    return UNIT_SECONDS.find(([pattern]) => pattern.test(unit))![1];
}

/** Every duration in `text`, in order. Compound durations come back as one span. */
export function findDurations(text: string): DurationSpan[] {
    const parts: Array<DurationSpan & { unit: number }> = [];
    for (const m of text.matchAll(PART)) {
        const amount = m[1] ? parseNumber(m[1]) : 1;
        const unit = unitSeconds(m[3]);
        if (!(amount > 0)) continue;
        // "an hour" and "a minute" are durations; "a second batch" is not.
        if (m[2] && !/^(?:hour|minute)$/i.test(m[3])) continue;
        parts.push({ start: m.index!, end: m.index! + m[0].length, seconds: Math.round(amount * unit), unit });
    }
    const spans: Array<DurationSpan & { unit: number }> = [];
    for (const part of parts) {
        const last = spans[spans.length - 1];
        if (last && part.unit < last.unit && JOINER.test(text.slice(last.end, part.start))) {
            last.end = part.end;
            last.seconds += part.seconds;
            last.unit = part.unit;
            continue;
        }
        spans.push({ ...part });
    }
    return spans.map(({ start, end, seconds }) => ({ start, end, seconds }));
}

/** "15:00", "0:45", "1:20:00". */
export function formatClock(totalSeconds: number): string {
    const s = Math.max(0, Math.ceil(totalSeconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = String(s % 60).padStart(2, '0');
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

export interface SplitStep {
    title: string | null;
    body: string;
}

const MAX_TITLE_WORDS = 6;

/** "Sear the bird: Pat it dry..." splits into a title and body when the lead is short. */
export function splitTitle(step: string): SplitStep {
    const m = /^([^:\n]{1,80}):\s+([\s\S]+)$/.exec(step.trim());
    if (!m) return { title: null, body: step.trim() };
    const lead = m[1].trim();
    if (lead.split(/\s+/).length > MAX_TITLE_WORDS || /\d$/.test(lead)) return { title: null, body: step.trim() };
    return { title: lead, body: m[2].trim() };
}

export interface WorkoutSets {
    sets: number | null;
    reps: number | null;
    /** Index into findDurations(text) of the hold and the rest, if the line has them. */
    holdIndex: number | null;
    restIndex: number | null;
}

/** "3 sets of 12, rest 45 seconds", "3 x 12", "hold 45 seconds". */
export function parseSets(text: string): WorkoutSets {
    const setsReps = /(\d+)\s*(?:sets?\s*(?:of|x|×)\s*|[x×]\s*)(\d+)/i.exec(text);
    const setsOnly = /(\d+)\s*sets?\b/i.exec(text);
    let holdIndex: number | null = null;
    let restIndex: number | null = null;
    findDurations(text).forEach((span, i) => {
        const before = text.slice(Math.max(0, span.start - 16), span.start);
        if (restIndex === null && /\brest(?:\s+for)?\s*$/i.test(before)) restIndex = i;
        else if (holdIndex === null && /\bhold(?:\s+(?:it|for))*\s*$/i.test(before)) holdIndex = i;
    });
    return {
        sets: setsReps ? Number(setsReps[1]) : setsOnly ? Number(setsOnly[1]) : null,
        reps: setsReps ? Number(setsReps[2]) : null,
        holdIndex,
        restIndex,
    };
}
