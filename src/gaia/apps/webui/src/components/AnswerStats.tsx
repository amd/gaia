// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { pluralKey, ref, type MessageKey, type Param, type Say } from '../i18n/registry';
import { tEn, useT, type Translate } from '../i18n/useT';
import type { InferenceStats } from '../types';
import { MarkedText, markLtr as marked } from './MarkedText';
import './AnswerStats.css';

/** Marks a number with its unit, so the renderer can keep it left to right. */
type Mark = (text: string) => string;
const plain: Mark = (text) => text;

/** `40200` -> "40 s", `400` -> "under a second", `95000` -> "1 min 35 s". */
export function spokenDuration(ms: number, say: Say = tEn.bare, mark: Mark = plain): string {
    if (ms < 1000) return say('answer.duration.underSecond');
    const total = Math.round(ms / 1000);
    if (total < 60) return mark(say('answer.duration.sec', { n: total }));
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return mark(secs ? say('answer.duration.minSec', { m: mins, s: secs }) : say('answer.duration.min', { n: mins }));
}

/** How a turn that never finished its answer ended. */
export type TurnEnding = 'stopped' | 'interrupted' | 'failed';

const LEAD: Record<TurnEnding | 'answered', readonly [MessageKey, MessageKey]> = {
    answered: ['answer.answeredIn', 'answer.answeredInPlace'],
    stopped: ['answer.stoppedAfter', 'answer.stoppedAfterPlace'],
    interrupted: ['answer.interruptedAfter', 'answer.interruptedAfterPlace'],
    failed: ['answer.failedAfter', 'answer.failedAfterPlace'],
};

const CHIPS: Record<string, MessageKey> = { GPU: 'device.gpu', NPU: 'device.npu', CPU: 'device.cpu' };

/** The backend's English place ("on your GPU", "via Fireworks AI") as a message each language words itself. */
function placeParam(place: string): Param {
    if (place === 'on this PC') return ref('answer.place.thisPc');
    const via = /^via (.+)$/.exec(place);
    if (via) return ref('answer.place.via', { provider: via[1] });
    const chips = /^on your (.+)$/.exec(place)?.[1].split(' and ').map((name) => CHIPS[name]);
    if (chips?.every(Boolean)) {
        const device = chips.slice(1).reduce<Param>((a, chip) => ref('answer.place.pair', { a, b: ref(chip) }), ref(chips[0]));
        return ref('answer.place.on', { device });
    }
    // A shape this list doesn't know yet shows as the backend wrote it.
    return place;
}

/** A failed turn's first token is the error, not a word of an answer. */
function wroteFirstWord(stats: InferenceStats | undefined, ended: TurnEnding | undefined): stats is InferenceStats {
    return ended !== 'failed' && stats?.time_to_first_token != null && stats.time_to_first_token > 0;
}

function summaryLine(
    latencyMs: number | undefined,
    stats: InferenceStats | undefined,
    ended: TurnEnding | undefined,
    say: Say,
    mark: Mark,
): string | null {
    const parts: string[] = [];
    const place = stats?.place ? placeParam(stats.place) : null;
    if (latencyMs != null && latencyMs > 0) {
        const [bare, at] = LEAD[ended ?? 'answered'];
        const time = spokenDuration(latencyMs, say, mark);
        parts.push(place ? say(at, { time, place }) : say(bare, { time }));
    } else if (place) {
        parts.push(say(ended ? 'answer.ranPlace' : 'answer.answeredPlace', { place }));
    }
    if (wroteFirstWord(stats, ended)) {
        const time = spokenDuration(stats.time_to_first_token * 1000, say, mark);
        parts.push(say(parts.length ? 'answer.firstWord' : 'answer.firstWordLead', { time }));
    }
    // The agent's own count, the figure the TUI and evals report.
    if (stats?.steps != null && stats.steps > 0) {
        parts.push(say(pluralKey('answer.steps', stats.steps), { n: stats.steps }));
    }
    return parts.length ? parts.join(' · ') : null;
}

function composeSummary(
    latencyMs: number | undefined,
    stats: InferenceStats | undefined,
    ended: TurnEnding | undefined,
    t: Translate,
    mark: Mark,
): string | null {
    if (summaryLine(latencyMs, stats, ended, tEn.bare, plain) === null) return null;
    return t.compose((say) => summaryLine(latencyMs, stats, ended, say, mark) ?? '');
}

/** "Answered in 40 s on your GPU · first word in 1 s", or null with nothing to say.
 *  A turn that never finished says how it ended instead of claiming an answer. */
export function answerSummary(
    latencyMs: number | undefined,
    stats: InferenceStats | undefined,
    ended?: TurnEnding,
    t: Translate = tEn,
): string | null {
    return composeSummary(latencyMs, stats, ended, t, plain);
}

/** The measured numbers behind the summary, one per line. */
export function exactStats(
    latencyMs: number | undefined,
    stats: InferenceStats | undefined,
    t: Translate = tEn,
    mark: Mark = plain,
    ended?: TurnEnding,
): string[] {
    const lines: string[] = [];
    if (latencyMs != null && latencyMs > 0) {
        lines.push(t.compose((say) => say('answer.exact.total', {
            time: mark(say('answer.duration.sec', { n: (latencyMs / 1000).toFixed(1) })),
        })));
    }
    if (wroteFirstWord(stats, ended)) {
        const ms = (stats.time_to_first_token * 1000).toFixed(0);
        lines.push(t.compose((say) => say('answer.exact.firstToken', { time: mark(say('answer.duration.ms', { n: ms })) })));
    }
    if (stats?.output_tokens != null && stats.output_tokens > 0) {
        const flow = mark(`${(stats.input_tokens ?? 0).toLocaleString()} → ${stats.output_tokens.toLocaleString()}`);
        lines.push(t.compose((say) => say('answer.exact.tokens', { flow })));
    }
    if (stats?.tokens_per_second != null && stats.tokens_per_second > 0) {
        lines.push(t('answer.exact.rate', { n: stats.tokens_per_second }));
    }
    return lines;
}

/** The answer footer in plain words; a click shows the exact numbers. */
export function AnswerStats({ latencyMs, stats, ended }: { latencyMs?: number; stats?: InferenceStats; ended?: TurnEnding }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const summary = composeSummary(latencyMs, stats, ended, t, marked);
    const exact = exactStats(latencyMs, stats, t, marked, ended);
    if (!summary && exact.length === 0) return null;
    return (
        <>
            <span>
                <button
                    type="button"
                    className="msg-stats-summary"
                    aria-expanded={open}
                    title={exactStats(latencyMs, stats, t, plain, ended).join('\n')}
                    onClick={() => setOpen((v) => !v)}
                >
                    {summary ? <MarkedText text={summary} /> : t('answer.details')}
                </button>
            </span>
            {open && exact.map((line) => <span key={line}><MarkedText text={line} /></span>)}
        </>
    );
}
