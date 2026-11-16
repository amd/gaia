// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The answer footer's words, shared by the Agent UI and the phone app. */

import { pluralKey, ref, type MessageKey, type Param, type Say } from '../i18n/registry';
import { tEn, type Translate } from '../i18n/translator';
import type { InferenceStats, Message } from '../types';
import type { TurnEnding } from './turnEnding.core';
import { same, spokenDuration, type Mark, type Variant } from './spokenDuration.core';

const plain: Mark = (text) => text;

const LEAD: Record<TurnEnding | 'answered', readonly [MessageKey, MessageKey]> = {
    answered: ['answer.answeredIn', 'answer.answeredInPlace'],
    stopped: ['answer.stoppedAfter', 'answer.stoppedAfterPlace'],
    interrupted: ['answer.interruptedAfter', 'answer.interruptedAfterPlace'],
    failed: ['answer.failedAfter', 'answer.failedAfterPlace'],
};

const CHIPS: Record<string, MessageKey> = { GPU: 'device.gpu', NPU: 'device.npu', CPU: 'device.cpu' };

/** The backend's English place ("on your GPU", "via Fireworks AI") as a message each language words itself. */
function placeParam(place: string, variant: Variant): Param {
    if (place === 'on this PC') return ref(variant('answer.place.thisPc'));
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
export function wroteFirstWord(stats: InferenceStats | undefined, ended: TurnEnding | undefined): stats is InferenceStats {
    return ended !== 'failed' && stats?.time_to_first_token != null && stats.time_to_first_token > 0;
}

function summaryLine(
    latencyMs: number | undefined,
    stats: InferenceStats | undefined,
    ended: TurnEnding | undefined,
    say: Say,
    mark: Mark,
    variant: Variant,
    brief = false,
): string | null {
    const parts: string[] = [];
    const place = stats?.place ? placeParam(stats.place, variant) : null;
    if (latencyMs != null && latencyMs > 0) {
        const [bare, at] = LEAD[ended ?? 'answered'];
        const time = spokenDuration(latencyMs, say, mark, variant);
        parts.push(place ? say(at, { time, place }) : say(bare, { time }));
    } else if (place) {
        parts.push(say(ended ? 'answer.ranPlace' : 'answer.answeredPlace', { place }));
    }
    if (wroteFirstWord(stats, ended)) {
        const time = spokenDuration(stats.time_to_first_token * 1000, say, mark, variant);
        parts.push(say(parts.length ? 'answer.firstWord' : 'answer.firstWordLead', { time }));
    }
    // The agent's own count, the figure the TUI and evals report.
    if (stats?.steps != null && stats.steps > 0) {
        parts.push(say(pluralKey('answer.steps', stats.steps), { n: stats.steps }));
    }
    if (!parts.length) return null;
    return brief ? parts[0] : parts.join(' · ');
}

export function composeSummary(
    latencyMs: number | undefined,
    stats: InferenceStats | undefined,
    ended: TurnEnding | undefined,
    t: Translate,
    mark: Mark,
    variant: Variant = same,
    brief = false,
): string | null {
    if (summaryLine(latencyMs, stats, ended, tEn.bare, plain, same) === null) return null;
    return t.compose((say) => summaryLine(latencyMs, stats, ended, say, mark, variant, brief) ?? '');
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
    variant: Variant = same,
): string[] {
    const lines: string[] = [];
    if (latencyMs != null && latencyMs > 0) {
        lines.push(t.compose((say) => say('answer.exact.total', {
            time: mark(say(variant('answer.duration.sec'), { n: (latencyMs / 1000).toFixed(1) })),
        })));
    }
    if (wroteFirstWord(stats, ended)) {
        const ms = (stats.time_to_first_token * 1000).toFixed(0);
        lines.push(t.compose((say) => say('answer.exact.firstToken', { time: mark(say(variant('answer.duration.ms'), { n: ms })) })));
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

/** Time from each question to the answer after it, by the answer's message id. */
export function latencyByMessage(messages: readonly Message[]): Map<number, number> {
    const map = new Map<number, number>();
    let lastUserTime: number | undefined;
    for (const msg of messages) {
        if (msg.role === 'user' && msg.created_at) {
            lastUserTime = new Date(msg.created_at).getTime();
        } else if (msg.role === 'assistant' && msg.created_at && lastUserTime !== undefined) {
            const assistTime = new Date(msg.created_at).getTime();
            if (assistTime > lastUserTime) map.set(msg.id, assistTime - lastUserTime);
            lastUserTime = undefined;
        }
    }
    return map;
}
