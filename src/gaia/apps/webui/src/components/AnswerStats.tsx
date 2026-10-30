// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import type { InferenceStats } from '../types';

/** `40200` -> "40 s", `400` -> "under a second", `95000` -> "1 min 35 s". */
export function spokenDuration(ms: number): string {
    if (ms < 1000) return 'under a second';
    const total = Math.round(ms / 1000);
    if (total < 60) return `${total} s`;
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return secs ? `${mins} min ${secs} s` : `${mins} min`;
}

/** How a turn that never finished its answer ended. */
export type TurnEnding = 'stopped' | 'interrupted';

const ENDED_AFTER: Record<TurnEnding, string> = { stopped: 'Stopped after', interrupted: 'Interrupted after' };

/** "Answered in 40 s on your GPU · first word in 1 s", or null with nothing to say.
 *  A turn that never finished says how it ended instead of claiming an answer. */
export function answerSummary(
    latencyMs: number | undefined,
    stats: InferenceStats | undefined,
    ended?: TurnEnding,
): string | null {
    const parts: string[] = [];
    if (latencyMs != null && latencyMs > 0) {
        const lead = ended ? ENDED_AFTER[ended] : 'Answered in';
        parts.push(`${lead} ${spokenDuration(latencyMs)}${stats?.place ? ` ${stats.place}` : ''}`);
    } else if (stats?.place) {
        parts.push(`${ended ? 'Ran' : 'Answered'} ${stats.place}`);
    }
    if (stats?.time_to_first_token != null && stats.time_to_first_token > 0) {
        const first = `first word in ${spokenDuration(stats.time_to_first_token * 1000)}`;
        parts.push(parts.length ? first : first[0].toUpperCase() + first.slice(1));
    }
    // The agent's own count, the figure the TUI and evals report.
    if (stats?.steps != null && stats.steps > 0) {
        parts.push(`${stats.steps} step${stats.steps !== 1 ? 's' : ''}`);
    }
    return parts.length ? parts.join(' · ') : null;
}

/** The measured numbers behind the summary, one per line. */
export function exactStats(latencyMs: number | undefined, stats: InferenceStats | undefined): string[] {
    const lines: string[] = [];
    if (latencyMs != null && latencyMs > 0) lines.push(`Total time ${(latencyMs / 1000).toFixed(1)} s`);
    if (stats?.time_to_first_token != null && stats.time_to_first_token > 0) {
        lines.push(`First token ${(stats.time_to_first_token * 1000).toFixed(0)} ms`);
    }
    if (stats?.output_tokens != null && stats.output_tokens > 0) {
        lines.push(`${(stats.input_tokens ?? 0).toLocaleString()} → ${stats.output_tokens.toLocaleString()} tokens`);
    }
    if (stats?.tokens_per_second != null && stats.tokens_per_second > 0) {
        lines.push(`${stats.tokens_per_second} tokens/s`);
    }
    return lines;
}

/** The answer footer in plain words; a click shows the exact numbers. */
export function AnswerStats({ latencyMs, stats, ended }: { latencyMs?: number; stats?: InferenceStats; ended?: TurnEnding }) {
    const [open, setOpen] = useState(false);
    const summary = answerSummary(latencyMs, stats, ended);
    const exact = exactStats(latencyMs, stats);
    if (!summary && exact.length === 0) return null;
    return (
        <>
            <span>
                <button
                    type="button"
                    className="msg-stats-summary"
                    aria-expanded={open}
                    title={exact.join('\n')}
                    onClick={() => setOpen((v) => !v)}
                >
                    {summary ?? 'Details'}
                </button>
            </span>
            {open && exact.map((line) => <span key={line}>{line}</span>)}
        </>
    );
}
